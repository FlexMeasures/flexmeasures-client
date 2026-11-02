"""
This control type is in a very EXPERIMENTAL stage.
Used it at your own risk :)
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    from s2python.frbc import (
        FRBCActuatorStatus,
        FRBCStorageStatus,
        FRBCSystemDescription,
    )
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


from flexmeasures_client.s2.control_types.FRBC import FRBC, SYSTEM_DESCRIPTION_REQUESTS
from flexmeasures_client.s2.control_types.FRBC.utils import (
    fm_schedule_to_instructions,
    get_soc_min_max,
)


class FRBCSimple(FRBC):
    _power_sensor_id: int
    _price_sensor_id: int
    _soc_sensor_id: int
    _rm_discharge_sensor_id: int
    _fill_rate_sensor_id: int | None
    _resolution: timedelta
    _schedule_duration: timedelta
    _valid_from_shift: timedelta
    _fill_level_scale: float

    def __init__(
        self,
        power_sensor_id: int,
        soc_sensor_id: int,
        rm_discharge_sensor_id: int,
        price_sensor_id: int,
        # Optional so existing callers keep working; when absent the actuator's fill rate is
        # simply not recorded, rather than being posted onto the power sensor.
        fill_rate_sensor_id: int | None = None,
        timezone: str = "UTC",
        schedule_duration: timedelta = timedelta(hours=12),
        # The resolution a schedule is planned on, which a schedule's start has to line up with.
        resolution: timedelta = timedelta(minutes=15),
        max_size: int = 100,
        valid_from_shift: timedelta = timedelta(days=1),
        fill_level_scale: float = 1,
        power_unit: str = "W",
    ) -> None:
        super().__init__(max_size)
        self._power_sensor_id = power_sensor_id
        self._price_sensor_id = price_sensor_id
        self._schedule_duration = schedule_duration
        self._resolution = resolution
        self._soc_sensor_id = soc_sensor_id
        self._rm_discharge_sensor_id = rm_discharge_sensor_id
        self._fill_rate_sensor_id = fill_rate_sensor_id
        self._timezone = ZoneInfo(timezone)
        self._fill_level_scale = fill_level_scale
        self.power_unit = power_unit

        # delay the start of the schedule from the time `valid_from`
        # of the FRBC.SystemDescription.
        self._valid_from_shift = valid_from_shift

    def now(self):
        return datetime.now(self._timezone)

    def schedule_start(self, valid_from: datetime) -> datetime:
        """Where a schedule asked for at valid_from starts: shifted, then floored to a boundary.

        FlexMeasures plans on whole resolution steps, and reads the prices for the window on the
        same steps, so a start that falls between two of them leaves it with an empty price
        series and the job fails with UnknownPricesException.
        A resource manager's valid_from is whatever moment it sent its description, so it is
        almost never on a boundary of its own accord.
        """
        start = valid_from + self._valid_from_shift
        resolution_seconds = int(self._resolution.total_seconds())
        if resolution_seconds <= 0:
            return start
        seconds_into_the_day = (
            start.hour * 3600 + start.minute * 60 + start.second
        ) % resolution_seconds
        return start.replace(microsecond=0) - timedelta(seconds=seconds_into_the_day)

    async def send_storage_status(self, status: FRBCStorageStatus):
        await self._fm_client.post_measurements(
            self._soc_sensor_id,
            start=self.now(),
            values=[status.present_fill_level],
            unit="MWh",
            duration=timedelta(minutes=1),
        )

    async def send_actuator_status(self, status: FRBCActuatorStatus):
        factor = status.operation_mode_factor
        sd: FRBCSystemDescription = list(self._system_description_history.values())[-1]

        # Report the mode the actuator is ACTUALLY in, not whichever the system description happens
        # to list first. An FRBC actuator declares its modes with degenerate power ranges - on is
        # [883.7, 883.7] W and off is [0, 0] - so interpolating by the factor cannot express the
        # difference between them; only the choice of mode can. Taking operation_modes[0] pinned
        # every status to the on-mode value, so the power sensor showed a constant draw and could
        # never show the heater switching off, whatever the factor said. frbc_tunes already selects
        # by active_operation_mode_id; this brings frbc_simple in line.
        actuator = next(
            (a for a in sd.actuators if a.id == status.actuator_id), sd.actuators[0]
        )
        operation_mode = next(
            (
                om
                for om in actuator.operation_modes
                if om.id == status.active_operation_mode_id
            ),
            None,
        )
        if operation_mode is None:
            # A status naming a mode absent from the newest system description means the two have
            # drifted apart. Fall back to the first mode rather than dropping the status, but say
            # so: silently reporting the wrong mode is what this change exists to stop.
            self._logger.warning(
                "FRBC.ActuatorStatus names operation mode %s, absent from the latest system "
                "description for actuator %s; falling back to its first mode.",
                status.active_operation_mode_id,
                status.actuator_id,
            )
            operation_mode = actuator.operation_modes[0]
        element = operation_mode.elements[0]

        # Interpolate the element's POWER range, not its fill rate. A fill rate is the thermal
        # output a heat pump delivers into its buffer; the power range is what it draws
        # electrically, smaller by the coefficient of performance. Posting the fill rate onto a
        # power sensor made an apartment appear to draw 3800 W where its electrical maximum is
        # 883.7 W, and being the most recent belief that is what the UI drew.
        # The fill-level scale is deliberately absent here: it converts fill levels and fill rates,
        # and has no business in a power value.
        power_range = element.power_ranges[0]
        power = (
            power_range.start_of_range
            + (power_range.end_of_range - power_range.start_of_range) * factor
        )

        start = status.transition_timestamp or self.now()

        await self._fm_client.post_sensor_data(
            self._power_sensor_id,
            start=start,
            prior=self.now(),
            values=[power],
            unit=self.power_unit,
            duration=timedelta(minutes=15),
        )

        # The fill rate goes to its own sensor rather than being discarded. It is a real and
        # useful quantity - what the actuator delivers into its buffer - it simply is not power.
        if self._fill_rate_sensor_id is not None:
            fill_rate = element.fill_rate
            rate = (
                fill_rate.start_of_range
                + (fill_rate.end_of_range - fill_rate.start_of_range) * factor
            ) * self._fill_level_scale
            await self._fm_client.post_sensor_data(
                self._fill_rate_sensor_id,
                start=start,
                prior=self.now(),
                values=[rate],
                unit=self.power_unit,
                duration=timedelta(minutes=15),
            )

    async def trigger_schedule(
        self, system_description_id: str, request_number: int | None = None
    ):
        """Translates S2 System Description into FM API calls.

        :param system_description_id: system description to base the schedule on
        :param request_number:        which request this schedule answers, which is checked once
                                      more before the instructions go out
        """

        system_description: FRBCSystemDescription = self._system_description_history[
            system_description_id
        ]

        if len(self._storage_status_history) > 0:
            soc_at_start = list(self._storage_status_history.values())[
                -1
            ].present_fill_level
        else:
            print("Can't trigger schedule without knowing the status of the storage...")
            return

        soc_min, soc_max = get_soc_min_max(system_description)

        # call schedule
        schedule = await self._fm_client.trigger_and_get_schedule(
            start=self.schedule_start(system_description.valid_from),
            sensor_id=self._power_sensor_id,
            flex_context={
                "production-price": {"sensor": self._price_sensor_id},
                "consumption-price": {"sensor": self._price_sensor_id},
                "site-power-capacity": f"{3 * 25 * 230} VA",
            },
            flex_model={
                "soc-unit": "MWh",
                "soc-at-start": soc_at_start,  # TODO: use forecast of the SOC instead
                "soc-min": soc_min,
                "soc-max": soc_max,
            },
            duration=self._schedule_duration,  # next 12 hours
            prior=self.now(),
            # TODO: add SOC MAX AND SOC MIN FROM fill_level_range,
            # this needs changes on the client
        )

        # translate FlexMeasures schedule into instructions. SOC -> Power -> PowerFactor
        instructions = fm_schedule_to_instructions(
            schedule, system_description, initial_fill_level=soc_at_start
        )

        await self.send_instruction_batch(
            instructions,
            request_kind=SYSTEM_DESCRIPTION_REQUESTS,
            request_number=request_number,
        )
