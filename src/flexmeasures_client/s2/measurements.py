"""Record what a resource manager measures on FlexMeasures sensors.

Each sensor gets one value per step of its resolution.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from logging import Logger, getLogger

import pandas as pd

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.exceptions import (
    ContentTypeError,
    InsufficientServerVersionError,
    JobFailedError,
    WrongAPIVersionError,
)

_LOGGER = getLogger(__name__)

#: The unit of every power value in S2, including measured power.
S2_POWER_UNIT = "W"

#: How long a recorded step is remembered. A step that a resource manager sends again within this time is not recorded twice.
REMEMBERED_FOR = timedelta(days=7)

#: How many times to try recording a step before giving up, and how long to wait between tries.
ATTEMPTS = 3
RETRY_AFTER = timedelta(seconds=30)

#: The exceptions a post can raise when there is no defect in this code.
POST_FAILURES = (
    ValueError,
    ConnectionError,
    ContentTypeError,
    JobFailedError,
    InsufficientServerVersionError,
    WrongAPIVersionError,
)


class MeasurementRecorder:
    """Record measured power on a sensor, as the average of the measurements in each step of its resolution.

    A step is recorded once it is over, according to the CEM's clock when the step's last measurement arrives.
    The recorded belief time is the moment the step ended.
    So a measurement is recorded as a belief formed at the end of the period it measures, not as a forecast of it.

    Each measurement is assigned to a step by its timestamp, not by its arrival time.
    This matters because resource managers send measurements in different ways.
    A co-simulation's resource manager sends a window's measurements together, once the window has passed.
    It stamps each one at the start of its quarter hour.
    A live resource manager sends each measurement when it is taken.
    In that case, a step is recorded when the first measurement after the step arrives.

    Recording happens in the background, so the resource manager does not wait for FlexMeasures before its measurement is acknowledged.
    Per sensor, only one post runs at a time.
    If a step could not be recorded, it is tried again a while later.
    After a few failed tries, it is given up on.

    The server creates one recorder and hands it to each CEM.
    So the recorder outlives the reconnections of a resource manager, which a co-simulation makes every round.
    The recorder therefore remembers what it recorded.
    A step that is sent again is not recorded again, and the first value recorded for a step stands.
    Each sensor is meant to belong to one resource manager.
    If several resource managers share a sensor, their measurements are averaged together.
    """

    def __init__(self, fm_client: FlexMeasuresClient, logger: Logger | None = None):
        self._fm_client = fm_client
        self._logger = logger or _LOGGER
        self._resolutions: dict[int, timedelta] = {}
        self._pending: dict[int, dict[datetime, list[float]]] = defaultdict(dict)
        self._attempts: dict[int, dict[datetime, int]] = defaultdict(dict)
        #: The recorded steps per sensor, in recording order, which is roughly chronological.
        self._recorded: dict[int, dict[datetime, None]] = defaultdict(dict)
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: set[asyncio.Task] = set()

    async def resolution(self, sensor_id: int) -> timedelta:
        """Return the sensor's resolution.

        The resolution is fetched from FlexMeasures only once, because it is fixed when the sensor is set up.
        A caller that takes several measurements at once should first call this for each sensor.
        Then, if any one sensor fails, no measurement is taken at all.
        """
        if sensor_id not in self._resolutions:
            sensor = await self._fm_client.get_sensor(sensor_id)
            resolution = (
                sensor.get("event_resolution") if isinstance(sensor, dict) else None
            )
            if resolution is None:
                raise ValueError(
                    f"Sensor {sensor_id} states no resolution, so its steps are unknown."
                )
            self._resolutions[sensor_id] = pd.Timedelta(resolution).to_pytimedelta()
        return self._resolutions[sensor_id]

    async def add(
        self, sensor_id: int, measured_at: datetime, watts: float, now: datetime
    ) -> None:
        """Take one measurement, then record every step of this sensor that is over by now, in the background."""
        resolution = await self.resolution(sensor_id)
        step = step_of(measured_at, resolution)
        if step in self._recorded[sensor_id]:
            self._logger.debug(
                "Not recording %s W on sensor %s for %s again.", watts, sensor_id, step
            )
        else:
            self._pending[sensor_id].setdefault(step, []).append(watts)
        self._in_background(self.record(sensor_id, now))

    async def record(self, sensor_id: int, now: datetime) -> None:
        """Record every pending step of this sensor that is over by now, each in its own request.

        Each step needs its own end as its belief time, and a single request for several steps cannot express that.
        Per sensor, only one call runs at a time.
        This way, a step is never posted twice, and no step is removed while a post for it is in flight.
        """
        async with self._locks[sensor_id]:
            resolution = await self.resolution(sensor_id)
            over = sorted(
                step for step in self._pending[sensor_id] if step + resolution <= now
            )
            failed = False
            for step in over:
                samples = self._pending[sensor_id][step]
                try:
                    await self._fm_client.post_sensor_data(
                        sensor_id,
                        start=step,
                        duration=resolution,
                        values=[sum(samples) / len(samples)],
                        unit=S2_POWER_UNIT,
                        prior=step + resolution,
                    )
                except POST_FAILURES as exc:
                    failed = True
                    attempts = self._attempts[sensor_id].get(step, 0) + 1
                    if attempts < ATTEMPTS:
                        self._attempts[sensor_id][step] = attempts
                        self._logger.warning(
                            "Could not record measured power on sensor %s for %s; trying again later: %s",
                            sensor_id,
                            step,
                            exc,
                        )
                        continue
                    self._logger.error(
                        "Giving up recording %s W of measured power on sensor %s for %s after %s tries: %s",
                        samples,
                        sensor_id,
                        step,
                        attempts,
                        exc,
                    )
                del self._pending[sensor_id][step]
                self._attempts[sensor_id].pop(step, None)
                self._recorded[sensor_id][step] = None
            self._forget_before(sensor_id, now - REMEMBERED_FOR)
        if failed:
            self._in_background(self._retry(sensor_id, now))

    async def _retry(self, sensor_id: int, now: datetime) -> None:
        await asyncio.sleep(RETRY_AFTER.total_seconds())
        await self.record(sensor_id, now)

    async def settle(self) -> None:
        """Wait for every background recording to finish, including retries."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _in_background(self, recording) -> None:
        task = asyncio.create_task(recording)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self._logger.error(
                "Recording measured power failed.", exc_info=task.exception()
            )

    def _forget_before(self, sensor_id: int, cutoff: datetime) -> None:
        """Forget recorded steps from before the cutoff.

        Steps are checked oldest first, so this costs little when no step is old.
        """
        recorded = self._recorded[sensor_id]
        while recorded:
            oldest = next(iter(recorded))
            if oldest >= cutoff:
                break
            del recorded[oldest]


def step_of(moment: datetime, resolution: timedelta) -> datetime:
    """Return the start of the step that a moment falls in.

    The step start is in the moment's own timezone, floored the way FlexMeasures floors it.
    During the repeated hour of an autumn DST change, a step boundary occurs twice.
    The step then starts at the later of the two boundaries that is not after the moment.
    Flooring based only on which of the two passes the moment is in does not always work.
    """
    if not resolution:
        return moment
    at = pd.Timestamp(moment)
    candidates = [
        at.floor(resolution, ambiguous=first_pass, nonexistent="shift_forward")
        for first_pass in (True, False)
    ]
    return max(c for c in candidates if c <= at).to_pydatetime()
