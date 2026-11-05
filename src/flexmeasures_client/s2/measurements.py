"""Recording what a resource manager measures on FlexMeasures sensors, one value per step of each sensor's resolution."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from logging import Logger, getLogger

import pandas as pd

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.exceptions import ContentTypeError, JobFailedError

_LOGGER = getLogger(__name__)

#: The unit S2 states every power in, measured ones included.
S2_POWER_UNIT = "W"

#: How long a recorded step is remembered, so that a resource manager sending it again is not recorded twice.
REMEMBERED_FOR = timedelta(days=7)


class MeasurementRecorder:
    """Records measured power on a sensor as the average of what was measured in each step of its resolution.

    A step is recorded once it is over by the CEM's clock, with the moment it ended as the moment it became known,
    so a measurement is a belief formed at the end of what it measures, rather than a forecast of it.
    Steps are taken by the time each measurement is stamped with, not by when it arrives,
    since a co-simulation's resource manager sends a window's measurements together once the window has passed,
    each stamped at the start of its quarter hour, while a live one sends them as they are taken.
    Consecutive steps that are over are recorded in one request.

    Made once per server and handed to each CEM, it outlives the reconnections of a resource manager,
    which a co-simulation makes every round, and so remembers what was recorded:
    a step sent again is not recorded again, and the first value recorded for a step stands.
    """

    def __init__(self, fm_client: FlexMeasuresClient, logger: Logger | None = None):
        self._fm_client = fm_client
        self._logger = logger or _LOGGER
        self._resolutions: dict[int, timedelta] = {}
        self._pending: dict[int, dict[datetime, list[float]]] = defaultdict(dict)
        self._recorded: dict[int, set[datetime]] = defaultdict(set)

    async def add(
        self, sensor_id: int, measured_at: datetime, watts: float, now: datetime
    ) -> None:
        """Take one measurement, and record every step of this sensor that is over by now."""
        resolution = await self._resolution(sensor_id)
        step = self._step_of(measured_at, resolution)
        if step in self._recorded[sensor_id]:
            self._logger.debug(
                "Not recording %s W on sensor %s for %s again.", watts, sensor_id, step
            )
        else:
            self._pending[sensor_id].setdefault(step, []).append(watts)
        await self.record(sensor_id, now)

    async def record(self, sensor_id: int, now: datetime) -> None:
        """Record every pending step of this sensor that is over by now, consecutive ones in one request each."""
        resolution = await self._resolution(sensor_id)
        over = sorted(
            step for step in self._pending[sensor_id] if step + resolution <= now
        )
        for run in _consecutive(over, resolution):
            values = [
                sum(samples) / len(samples)
                for samples in (self._pending[sensor_id][step] for step in run)
            ]
            ended = run[-1] + resolution
            try:
                await self._fm_client.post_sensor_data(
                    sensor_id,
                    start=run[0],
                    duration=resolution * len(run) if resolution else timedelta(0),
                    values=values,
                    unit=S2_POWER_UNIT,
                    prior=ended,
                )
            except (
                ValueError,
                ConnectionError,
                ContentTypeError,
                JobFailedError,
            ) as exc:
                # Kept, and tried again with the next measurement.
                self._logger.warning(
                    "Could not record measured power on sensor %s from %s to %s; trying again later: %s",
                    sensor_id,
                    run[0],
                    ended,
                    exc,
                )
                continue
            for step in run:
                del self._pending[sensor_id][step]
                self._recorded[sensor_id].add(step)
        self._recorded[sensor_id] = {
            step for step in self._recorded[sensor_id] if step >= now - REMEMBERED_FOR
        }

    async def _resolution(self, sensor_id: int) -> timedelta:
        """The sensor's resolution, asked once, since it is a property of how the sensor was set up."""
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

    @staticmethod
    def _step_of(moment: datetime, resolution: timedelta) -> datetime:
        """The start of the step a moment falls in, on the moment's own clock, as FlexMeasures floors it."""
        if not resolution:
            return moment
        return pd.Timestamp(moment).floor(resolution).to_pydatetime()


def _consecutive(steps: list[datetime], resolution: timedelta) -> list[list[datetime]]:
    """Sorted steps, in runs of steps that follow one another; with no resolution, each on its own."""
    runs: list[list[datetime]] = []
    for step in steps:
        if runs and resolution and step == runs[-1][-1] + resolution:
            runs[-1].append(step)
        else:
            runs.append([step])
    return runs
