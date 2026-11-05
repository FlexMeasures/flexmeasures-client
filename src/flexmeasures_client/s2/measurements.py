"""Recording what a resource manager measures on FlexMeasures sensors, one value per step of each sensor's resolution."""

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

#: The unit S2 states every power in, measured ones included.
S2_POWER_UNIT = "W"

#: How long a recorded step is remembered, so that a resource manager sending it again is not recorded twice.
REMEMBERED_FOR = timedelta(days=7)

#: How often a step is tried before it is given up on, and how long to wait between tries.
ATTEMPTS = 3
RETRY_AFTER = timedelta(seconds=30)

#: What a post can fail with, short of a defect in this code.
POST_FAILURES = (
    ValueError,
    ConnectionError,
    ContentTypeError,
    JobFailedError,
    InsufficientServerVersionError,
    WrongAPIVersionError,
)


class MeasurementRecorder:
    """Records measured power on a sensor as the average of what was measured in each step of its resolution.

    A step is recorded once it is over by the CEM's clock, as it stands when the step's last measurement arrives,
    with the moment it ended as the moment it became known,
    so a measurement is a belief formed at the end of what it measures, rather than a forecast of it.
    Steps are taken by the time each measurement is stamped with, not by when it arrives,
    since a co-simulation's resource manager sends a window's measurements together once the window has passed,
    each stamped at the start of its quarter hour, while a live one sends them as they are taken,
    in which case a step is recorded with the first measurement after it is over.

    Recording happens in the background, so that a resource manager is not kept waiting for FlexMeasures
    before its measurement is acknowledged, one post at a time per sensor.
    A step that could not be recorded is tried again a while later, and given up on after a few tries.

    Made once per server and handed to each CEM, it outlives the reconnections of a resource manager,
    which a co-simulation makes every round, and so remembers what was recorded:
    a step sent again is not recorded again, and the first value recorded for a step stands.
    A sensor is meant to be one resource manager's: one that several share has their measurements averaged together.
    """

    def __init__(self, fm_client: FlexMeasuresClient, logger: Logger | None = None):
        self._fm_client = fm_client
        self._logger = logger or _LOGGER
        self._resolutions: dict[int, timedelta] = {}
        self._pending: dict[int, dict[datetime, list[float]]] = defaultdict(dict)
        self._attempts: dict[int, dict[datetime, int]] = defaultdict(dict)
        #: By sensor, the steps recorded, in the order they were, which is roughly that of time.
        self._recorded: dict[int, dict[datetime, None]] = defaultdict(dict)
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: set[asyncio.Task] = set()

    async def resolution(self, sensor_id: int) -> timedelta:
        """The sensor's resolution, asked once, since it is a property of how the sensor was set up.

        A caller taking several measurements at once asks this for each sensor first,
        so that none is taken where one of them cannot be.
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
        """Take one measurement, and record in the background every step of this sensor that is over by now."""
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
        """Record every pending step of this sensor that is over by now, each in a request of its own.

        Each with its own end as the moment it became known, which one request for several steps cannot say.
        One at a time per sensor, so a step is not posted twice, nor taken from under a post in flight.
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
        """Wait for every recording in the background to finish, retries included."""
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
        """Forget recorded steps from before the cutoff, oldest first, so that this costs little when nothing is old."""
        recorded = self._recorded[sensor_id]
        while recorded:
            oldest = next(iter(recorded))
            if oldest >= cutoff:
                break
            del recorded[oldest]


def step_of(moment: datetime, resolution: timedelta) -> datetime:
    """The start of the step a moment falls in, on the moment's own clock, as FlexMeasures floors it.

    In the repeated hour of an autumn DST change a boundary exists twice, and the step is the later of the two
    that is not after the moment; taken from the moment's pass alone, flooring cannot always be done at all.
    """
    if not resolution:
        return moment
    at = pd.Timestamp(moment)
    candidates = [
        at.floor(resolution, ambiguous=first_pass, nonexistent="shift_forward")
        for first_pass in (True, False)
    ]
    return max(c for c in candidates if c <= at).to_pydatetime()
