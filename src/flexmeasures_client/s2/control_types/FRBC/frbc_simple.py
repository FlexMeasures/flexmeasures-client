"""
This control type is in a very EXPERIMENTAL stage.
Used it at your own risk :)
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    from s2python.frbc import (
        FRBCActuatorStatus,
        FRBCFillLevelTargetProfile,
        FRBCLeakageBehaviour,
        FRBCStorageStatus,
        FRBCSystemDescription,
        FRBCUsageForecast,
    )
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


import pandas as pd

from flexmeasures_client.client import convert_units
from flexmeasures_client.exceptions import JobFailedError
from flexmeasures_client.s2.control_types.FRBC import FRBC, STORAGE_STATUS_REQUESTS
from flexmeasures_client.s2.control_types.FRBC.utils import (
    clip_fill_level_target_profile,
    fm_schedule_to_instructions,
    get_soc_min_max,
)
from flexmeasures_client.s2.control_types.translations import (
    leakage_behaviour_to_storage_efficiency,
    translate_fill_level_target_profile,
    translate_usage_forecast_to_fm,
)

#: The unit S2 states a power value in, and so the unit of an operation mode's power ranges.
#: A schedule is read back in this unit, whatever unit the power sensor holding it is recorded in.
S2_POWER_UNIT = "W"


class FRBCSimple(FRBC):
    _power_sensor_id: int
    _price_sensor_id: int
    _soc_sensor_id: int
    _rm_discharge_sensor_id: int
    _fill_rate_sensor_id: int | None
    #: Where what a resource manager declares about its storage is recorded, each optional:
    #: without one, that declaration is not recorded, rather than being posted somewhere else.
    _soc_minima_sensor_id: int | None
    _soc_maxima_sensor_id: int | None
    _usage_forecast_sensor_id: int | None
    _leakage_behaviour_sensor_id: int | None
    _resolution: timedelta
    _schedule_duration: timedelta
    _valid_from_shift: timedelta
    _fill_level_scale: float
    _hard_bound_factor: float
    #: Whether the power sensor records consumption as positive, as S2 does.
    #: Read from the sensor itself the first time a power value is posted, and remembered after that.
    #: None until it has been read.
    _power_sensor_takes_consumption_as_positive: bool | None
    #: When a power value was last recorded for the actuator, and for which moment,
    #: so that a second status inside one event does not collide with the first.
    _last_power_recorded: tuple[datetime, datetime] | None

    def __init__(
        self,
        power_sensor_id: int,
        soc_sensor_id: int,
        rm_discharge_sensor_id: int,
        price_sensor_id: int,
        # Optional so existing callers keep working; when absent the actuator's fill rate is
        # simply not recorded, rather than being posted onto the power sensor.
        fill_rate_sensor_id: int | None = None,
        # Likewise optional, for the three things a resource manager declares about its storage.
        soc_minima_sensor_id: int | None = None,
        soc_maxima_sensor_id: int | None = None,
        usage_forecast_sensor_id: int | None = None,
        leakage_behaviour_sensor_id: int | None = None,
        timezone: str = "UTC",
        schedule_duration: timedelta = timedelta(hours=12),
        # The resolution a schedule is planned on, which a schedule's start has to line up with.
        resolution: timedelta = timedelta(minutes=15),
        max_size: int = 100,
        valid_from_shift: timedelta = timedelta(days=1),
        fill_level_scale: float = 1,
        # How far above the declared fill-level range the hard upper bound of a schedule sits.
        # Half again as high, so that a storage overshooting its declared range stays feasible.
        # A deployment whose storage cannot overshoot at all can bring this down to 1.
        hard_bound_factor: float = 1.5,
        # The unit a power value this handler posts is labelled with.
        # Those values come straight from the operation modes' power ranges, which S2 states in watts,
        # and nothing converts them on the way out,
        # so a deployment that sets another unit here has them recorded at the wrong magnitude.
        # TODO: either convert a power value into this unit before posting it, or drop this and post watts, as S2 states them.
        power_unit: str = "W",
        # The unit a fill level is recorded in: a state of charge and the minima and maxima
        # bounding it all go out in this one unit, so that they can be compared at all.
        # Kilowatt hours, because S2 is a residential protocol and a home battery or heat buffer
        # is of that order; a deployment at another scale says so here.
        energy_unit: str = "kWh",
    ) -> None:
        super().__init__(max_size)
        self._power_sensor_id = power_sensor_id
        self._price_sensor_id = price_sensor_id
        self._schedule_duration = schedule_duration
        self._resolution = resolution
        self._soc_sensor_id = soc_sensor_id
        self._rm_discharge_sensor_id = rm_discharge_sensor_id
        self._fill_rate_sensor_id = fill_rate_sensor_id
        self._soc_minima_sensor_id = soc_minima_sensor_id
        self._soc_maxima_sensor_id = soc_maxima_sensor_id
        self._usage_forecast_sensor_id = usage_forecast_sensor_id
        self._leakage_behaviour_sensor_id = leakage_behaviour_sensor_id
        self._timezone = ZoneInfo(timezone)
        if fill_level_scale <= 0:
            # Every fill level this handler records is multiplied by the scale,
            # and the hard bounds a schedule was planned against are divided back by it,
            # so zero divides by zero and a negative scale turns the bounds upside down.
            raise ValueError(
                f"fill_level_scale has to be greater than zero, since it converts a fill level into the unit it is recorded in, but {fill_level_scale} was given."
            )
        self._fill_level_scale = fill_level_scale
        self._power_sensor_takes_consumption_as_positive = None
        self._last_power_recorded = None
        if hard_bound_factor < 1:
            # Below one, the hard upper bound sits inside the declared range,
            # and FlexMeasures clips the band into the hard bounds,
            # so the band would be narrowed to something the resource manager never asked for.
            raise ValueError(
                f"hard_bound_factor has to be at least 1, so that the hard upper bound sits at or above the declared fill-level range, but {hard_bound_factor} was given."
            )
        self._hard_bound_factor = hard_bound_factor
        self.power_unit = power_unit
        self.energy_unit = energy_unit

        # delay the start of the schedule from the time `valid_from`
        # of the FRBC.SystemDescription.
        self._valid_from_shift = valid_from_shift

    def now(self):
        return datetime.now(self._timezone)

    def fill_rate_unit(self) -> str:
        """The unit a rate of change of the fill level is recorded in.

        S2 states a fill rate, and a usage forecast, as fill level per second, so this follows
        whatever unit the fill level itself is recorded in. It is not the power unit: a fill rate
        is what an actuator delivers into its buffer, which for a heat buffer is not electricity
        at all, and which even for a battery differs from kilowatts by the seconds in an hour.

        A method rather than a property, because S2 Handler's discover() walks dir(self) and
        getattrs every name during __init__, before a subclass has assigned its own attributes.
        A property would be evaluated there and raise on the energy_unit it reads.
        """
        return f"{self.energy_unit}/s"

    def align_to_resolution(self, moment: datetime) -> datetime:
        """The last resolution boundary at or before that moment.

        FlexMeasures plans on whole resolution steps, and reads a series for a window on the
        same steps, so a moment that falls between two of them leaves it with nothing to read.
        For a schedule that means an empty price series and a job failing with
        UnknownPricesException; for a series being posted it means data off the grid everything
        else sits on. No moment a resource manager sends is on a boundary of its own accord,
        whether it is a description's valid_from or a profile's start_time, so each one a series
        is anchored at goes through here.
        """
        resolution_seconds = int(self._resolution.total_seconds())
        if resolution_seconds <= 0:
            return moment
        seconds_into_the_day = (
            moment.hour * 3600 + moment.minute * 60 + moment.second
        ) % resolution_seconds
        return moment.replace(microsecond=0) - timedelta(seconds=seconds_into_the_day)

    def schedule_start(self, valid_from: datetime) -> datetime:
        """Where a schedule asked for at valid_from starts: shifted, then floored to a boundary."""
        return self.align_to_resolution(valid_from + self._valid_from_shift)

    async def send_storage_status(
        self, status: FRBCStorageStatus, request_number: int | None = None
    ):
        await self._fm_client.post_measurements(
            self._soc_sensor_id,
            start=self.now(),
            # Scaled like every other fill level this handler records, or the level and the
            # band around it would be in different scales wherever fill_level_scale is not 1.
            values=[status.present_fill_level * self._fill_level_scale],
            unit=self.energy_unit,
            duration=timedelta(minutes=1),
        )

        # The state of charge this status reports is what a schedule starts from, so this is
        # where a schedule is asked for.
        # A system description is needed to turn that schedule into instructions, and a resource
        # manager sends one before its first status, so its absence here means the two have gone
        # out of step rather than that nothing has arrived yet.
        if not self._system_description_history:
            self._logger.warning(
                "Cannot ask for a schedule: no FRBC.SystemDescription has arrived yet."
            )
            return

        # The measurement above is posted whatever happens, because it is data.
        # The planning below is skipped where a newer status has arrived in the meantime,
        # since that newer status reports the state of charge a schedule should start from.
        if request_number is not None and not self._is_latest_request(
            STORAGE_STATUS_REQUESTS, request_number
        ):
            self._logger.info(
                f"Skipping a superseded planning request (number {request_number})."
            )
            return

        system_description_id = list(self._system_description_history)[-1]
        await self.trigger_schedule(
            system_description_id, request_number=request_number
        )

    async def _as_the_power_sensor_reads_it(self, power: float) -> float:
        """Turn a power value S2 states into one the power sensor means the same thing by.

        S2 fixes one sign convention, where positive means consumption.
        FlexMeasures leaves it to the sensor, through its ``consumption_is_positive`` attribute,
        and for a plain power sensor the default is the other way round:
        a schedule saved onto such a sensor has its sign flipped on the way in,
        so a plan to consume is stored negative.
        Posting a consumption as positive onto that same sensor puts the measurement and the plan in opposite directions,
        which reads as a device doing the reverse of what was planned.

        The sensor is asked once, and the answer is kept:
        a sensor's convention is a property of how it was set up, not of the moment.
        Where the sensor cannot be read, the value is posted as S2 states it, which is this handler's own convention,
        and the reason is logged.
        """

        if self._power_sensor_takes_consumption_as_positive is None:
            try:
                sensor = await self._fm_client.get_sensor(
                    self._power_sensor_id, parse_json_fields=True
                )
                attributes = sensor.get("attributes") or {}
                self._power_sensor_takes_consumption_as_positive = bool(
                    attributes.get("consumption_is_positive", False)
                )
                self._logger.info(
                    "Power sensor %s records %s as positive, so that is how an actuator's power is posted.",
                    self._power_sensor_id,
                    (
                        "consumption"
                        if self._power_sensor_takes_consumption_as_positive
                        else "production"
                    ),
                )
            except (ValueError, KeyError, ConnectionError) as exc:
                self._logger.warning(
                    "Could not read what sign power sensor %s records, so posting an actuator's power as S2 states it, with consumption positive: %s",
                    self._power_sensor_id,
                    exc,
                )
                return power
        if self._power_sensor_takes_consumption_as_positive or not power:
            return power
        return -power

    def _in_the_power_unit(self, power: float) -> float:
        """Convert a power value S2 states, which is in watts, into the unit this handler posts in.

        S2 states a power value in watts, while power_unit says what a posted value is labelled with,
        and nothing converted between the two:
        a deployment recording its power sensor in kW had 883.7 W posted as 883.7 kW,
        which FlexMeasures stored as the thousandfold it says it is.
        """

        if self.power_unit == S2_POWER_UNIT:
            return power
        return convert_units([power], S2_POWER_UNIT, self.power_unit)[0]

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

        # A belief time that supersedes the one before it, rather than colliding with it.
        # FlexMeasures keys a belief on its sensor, its source, the event and the moment it was formed.
        # It floors the event to the sensor's resolution and that moment to the second,
        # so two statuses inside one resolution step and one second are a single belief,
        # and the later one is refused.
        # A resource manager sends a status for each instruction it applies,
        # and this handler sends several at once, so that is the ordinary case rather than an edge.
        # The comparison is on the event as FlexMeasures keys it,
        # since two statuses seconds apart still land on the same resolution step.
        event = self.align_to_resolution(start)
        recorded_at = self.now()
        if self._last_power_recorded is not None:
            last_event, last_recorded_at = self._last_power_recorded
            if last_event == event and recorded_at - last_recorded_at < timedelta(
                seconds=1
            ):
                recorded_at = last_recorded_at + timedelta(seconds=1)
        self._last_power_recorded = (event, recorded_at)

        value = self._in_the_power_unit(await self._as_the_power_sensor_reads_it(power))
        try:
            await self._fm_client.post_sensor_data(
                self._power_sensor_id,
                start=start,
                prior=recorded_at,
                values=[value],
                unit=self.power_unit,
                duration=timedelta(minutes=15),
            )
        except (JobFailedError, ValueError):
            # This runs in a task nobody awaits,
            # so an exception here is logged by asyncio at best and lost at worst.
            # Say which measurement went missing,
            # since a power sensor silently holding a plan and no measurement is the hardest state to notice.
            self._logger.exception(
                "Could not record %s %s on power sensor %s for %s.",
                value,
                self.power_unit,
                self._power_sensor_id,
                start,
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
                unit=self.fill_rate_unit(),
                duration=timedelta(minutes=15),
            )

    async def trigger_schedule(
        self, system_description_id: str, request_number: int | None = None
    ):
        """Translates S2 System Description into FM API calls.

        :param system_description_id: system description to base the schedule on
        :param request_number:        which planning request this schedule answers, which is
                                      checked once more before the instructions go out
        """

        system_description: FRBCSystemDescription = self._system_description_history[
            system_description_id
        ]

        if len(self._storage_status_history) > 0:
            # In the resource manager's own fill-level units. The scale is applied at the
            # FlexMeasures edge below, and nowhere else: the instruction translation further down
            # matches this against the operation modes' fill-level ranges, which the resource
            # manager also states in its own units.
            fill_level_at_start = list(self._storage_status_history.values())[
                -1
            ].present_fill_level
        else:
            print("Can't trigger schedule without knowing the status of the storage...")
            return

        # Scaled at the FlexMeasures edge, like every bound and level below it.
        soc_at_start = fill_level_at_start * self._fill_level_scale

        # The declared range is where a resource manager wants its storage kept, not where its storage can be:
        # S2 calls it the range in which the fill level should remain,
        # and has a resource manager free to ignore instructions while the fill level is outside it.
        # So the range goes out as a band whose breach FlexMeasures prices,
        # rather than as the hard soc-min and soc-max, which made states a storage really reaches infeasible,
        # and an infeasible job is answered by the fallback scheduler, which ignores the band altogether.
        # Hard bounds are still sent, but wide ones:
        # a fill-level target profile need not cover the whole schedule,
        # and over the part it does not cover there is nothing to price a breach of,
        # so nothing else keeps a plan within reach of the storage it is planned for.
        _, range_top = get_soc_min_max(system_description, self._fill_level_scale)
        # Zero below, since a stock cannot be negative and FlexMeasures accepts no lower floor.
        soc_min = 0.0
        soc_max = range_top * self._hard_bound_factor
        # A state of charge outside the hard bounds is infeasible before the first step,
        # so the bounds widen to take it in.
        # A storage outside its declared range is not an error:
        # a resource manager is free to report one, and a schedule still has to be planned from where it says its storage is.
        soc_min = min(soc_min, soc_at_start)
        soc_max = max(soc_max, soc_at_start)

        flex_model: dict = {
            # The same unit the fill level is recorded in, or the scheduler would plan against bounds a thousandfold off the measurements it plans from.
            "soc-unit": self.energy_unit,
            "soc-at-start": soc_at_start,  # TODO: use forecast of the SOC instead
            "soc-min": soc_min,
            "soc-max": soc_max,
        }
        # What the resource manager declared about its storage, each entry only where a sensor holds it:
        # an entry naming a sensor that was never configured has the scheduler plan against nothing at all.
        # Both band sensors or neither, as when the band is recorded:
        # one side alone is an open-ended push in one direction, not the band the resource manager declared.
        has_soc_band = (
            self._soc_minima_sensor_id is not None
            and self._soc_maxima_sensor_id is not None
        )
        if has_soc_band:
            flex_model["soc-minima"] = {"sensor": self._soc_minima_sensor_id}
            flex_model["soc-maxima"] = {"sensor": self._soc_maxima_sensor_id}
        elif (
            self._soc_minima_sensor_id is not None
            or self._soc_maxima_sensor_id is not None
        ):
            self._logger.info(
                "Not asking for a schedule against a fill-level band: a band needs both a soc-minima and a soc-maxima sensor, and this handler was given %s and %s.",
                self._soc_minima_sensor_id,
                self._soc_maxima_sensor_id,
            )
        if self._usage_forecast_sensor_id is not None:
            flex_model["soc-usage"] = [{"sensor": self._usage_forecast_sensor_id}]
        if self._leakage_behaviour_sensor_id is not None:
            flex_model["storage-efficiency"] = {
                "sensor": self._leakage_behaviour_sensor_id
            }
        self._logger.debug("flex model: %s", flex_model)

        flex_context: dict = {
            "production-price": {"sensor": self._price_sensor_id},
            "consumption-price": {"sensor": self._price_sensor_id},
            "site-power-capacity": f"{3 * 25 * 230} VA",
        }
        if has_soc_band:
            # Without this, the minima and maxima above are hard bounds again,
            # and the band is back to making reachable states infeasible.
            # The breach price is left to FlexMeasures' own default,
            # since what a breach of comfort is worth is the deployment's judgement, not the protocol's.
            flex_context["relax-soc-constraints"] = True

        # call schedule
        schedule = await self._fm_client.trigger_and_get_schedule(
            start=self.schedule_start(system_description.valid_from),
            sensor_id=self._power_sensor_id,
            flex_context=flex_context,
            flex_model=flex_model,
            duration=self._schedule_duration,
            prior=self.now(),
            # In watts, which is what S2 states an operation mode's power ranges in,
            # and the translation below matches a schedule against those ranges.
            # Not in power_unit, which says how a power value this handler posts is labelled,
            # and not in the power sensor's own unit, which is what a schedule comes back in by default.
            unit=S2_POWER_UNIT,
        )

        # translate FlexMeasures schedule into instructions. SOC -> Power -> PowerFactor
        instructions = fm_schedule_to_instructions(
            schedule,
            system_description,
            initial_fill_level=fill_level_at_start,
            # The hard bounds this schedule was planned against, back in the resource manager's own units.
            # Read back against the declared range instead,
            # a schedule planned outside that range would be answered with the operation modes for a fill level pinned to the range's nearest edge,
            # which is not where the scheduler planned the storage to be.
            fill_level_bounds=(
                soc_min / self._fill_level_scale,
                soc_max / self._fill_level_scale,
            ),
        )

        await self.send_instruction_batch(
            instructions,
            request_kind=STORAGE_STATUS_REQUESTS,
            request_number=request_number,
        )

    async def send_fill_level_target_profile(
        self, fill_level_target_profile: FRBCFillLevelTargetProfile
    ):
        """Record the fill levels a resource manager asks its storage to be kept between.

        The profile is translated onto the resolution a schedule is planned on, and held inside
        the fill-level range the latest system description declared, for the reason given in
        clip_fill_level_target_profile.

        Both series are needed to express a band, so this records nothing at all unless both a
        minima and a maxima sensor are configured.
        """
        if self._soc_minima_sensor_id is None or self._soc_maxima_sensor_id is None:
            self._logger.info(
                "Not recording an FRBC.FillLevelTargetProfile: a fill-level band needs both a soc-minima and a soc-maxima sensor, and this handler was given %s and %s.",
                self._soc_minima_sensor_id,
                self._soc_maxima_sensor_id,
            )
            return

        soc_minima, soc_maxima = translate_fill_level_target_profile(
            fill_level_target_profile=fill_level_target_profile,
            resolution=self._resolution,
            fill_level_scale=self._fill_level_scale,
        )

        system_descriptions = list(self._system_description_history.values())
        if system_descriptions:
            range_bottom, range_top = get_soc_min_max(
                system_descriptions[-1], self._fill_level_scale
            )
            soc_minima, soc_maxima, number_crossed = clip_fill_level_target_profile(
                soc_minima, soc_maxima, range_bottom, range_top
            )
            if number_crossed:
                self._logger.warning(
                    "An FRBC.FillLevelTargetProfile asks for fill levels outside the declared range [%s, %s] on %s of %s steps; the minima and maxima there were collapsed to their midpoint.",
                    range_bottom,
                    range_top,
                    number_crossed,
                    len(soc_minima),
                )

        now = self.now()
        duration = pd.Timedelta(self._resolution) * len(soc_maxima)
        # The translation floors the profile's start to a resolution boundary, so the series
        # that comes back begins there, not at the start_time the resource manager sent. Posting
        # it at the raw start_time would shift every value by the difference.
        start = self.align_to_resolution(fill_level_target_profile.start_time)
        for sensor_id, values in (
            (self._soc_minima_sensor_id, soc_minima),
            (self._soc_maxima_sensor_id, soc_maxima),
        ):
            await self._fm_client.post_sensor_data(
                sensor_id=sensor_id,
                start=start,
                prior=now,
                values=values.tolist(),
                unit=self.energy_unit,
                duration=duration,
            )

    async def send_usage_forecast(self, usage_forecast: FRBCUsageForecast):
        """Record what a resource manager expects its storage to be drawn down by."""
        if self._usage_forecast_sensor_id is None:
            self._logger.info(
                "Not recording an FRBC.UsageForecast: no usage-forecast sensor is configured."
            )
            return

        translated = translate_usage_forecast_to_fm(
            usage_forecast,
            self._resolution,
            strategy="mean",
            fill_level_scale=self._fill_level_scale,
        )

        await self._fm_client.post_sensor_data(
            sensor_id=self._usage_forecast_sensor_id,
            # Aligned, because FlexMeasures reads a series on whole resolution steps, and a
            # resource manager's start_time is whatever moment it sent the message.
            start=self.align_to_resolution(usage_forecast.start_time),
            prior=self.now(),
            values=translated.tolist(),
            # translate_usage_forecast_to_fm returns fill level per second, scaled.
            unit=self.fill_rate_unit(),
            duration=pd.Timedelta(self._resolution) * len(translated),
        )

    async def send_leakage_behaviour(self, leakage_behaviour: FRBCLeakageBehaviour):
        """Record what a resource manager's storage loses on its own, as a storage efficiency."""
        if self._leakage_behaviour_sensor_id is None:
            self._logger.info(
                "Not recording an FRBC.LeakageBehaviour: no leakage-behaviour sensor is configured."
            )
            return

        storage_efficiency = leakage_behaviour_to_storage_efficiency(
            message=leakage_behaviour,
            resolution=self._resolution,
            fill_level_scale=self._fill_level_scale,
        )

        now = self.now()
        await self._fm_client.post_sensor_data(
            sensor_id=self._leakage_behaviour_sensor_id,
            start=self.align_to_resolution(leakage_behaviour.valid_from or now),
            prior=now,
            values=[storage_efficiency],
            unit="%",
            duration=self._schedule_duration,
        )
