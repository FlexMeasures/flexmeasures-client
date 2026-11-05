"""
This control type is in a very EXPERIMENTAL stage.
Used it at your own risk :)
"""

import asyncio
import math
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
from flexmeasures_client.exceptions import ContentTypeError, JobFailedError
from flexmeasures_client.s2.control_types.FRBC import FRBC, STORAGE_STATUS_REQUESTS
from flexmeasures_client.s2.control_types.FRBC.utils import (
    WATTS_PER_ENERGY_UNIT_PER_SECOND,
    clip_fill_level_target_profile,
    element_for_fill_level,
    fm_schedule_to_instructions,
    get_soc_min_max,
    om_label,
    operation_mode_conversion_efficiency,
)
from flexmeasures_client.s2.control_types.translations import (
    leakage_behaviour_to_storage_efficiency,
    translate_fill_level_target_profile,
    translate_usage_forecast_to_fm,
)

#: The unit S2 states a power value in, and so the unit of an operation mode's power ranges.
#: A schedule is read back in this unit, whatever unit the power sensor holding it is recorded in,
#: and what goes out to FlexMeasures in it is what came in from a resource manager in it.
S2_POWER_UNIT = "W"

#: How many times a schedule is asked for before the storage is left without an instruction for its window.
#: Every attempt costs the server a solve, so this is a handful rather than a vigil.
SCHEDULE_ATTEMPTS = 5

#: How long to wait between two attempts at a schedule.
#: Flat rather than backed off: what it waits out is a post landing or a worker taking the job up,
#: and a resource manager is waiting on an instruction for the whole of it.
SCHEDULE_RETRY_INTERVAL = timedelta(seconds=2)

#: Causes of a failed scheduling job that fail the same way however often the job is asked for again.
#: Asking again only multiplies failed jobs over the server's workers, so these are never asked again.
NON_RETRYABLE_SCHEDULE_FAILURES = (
    # A scheduler that records a belief twice collides with its own first write on every attempt.
    "UniqueViolation",
    "duplicate key value violates unique constraint",
    # An infeasible problem is planned from the same flex model and context every time, so it stays infeasible.
    # FlexMeasures answered one with a fallback scheduler until v1.0.0 retired it, and fails the job itself since.
    "InfeasibleProblemException",
)

#: The refusal of a trigger, rather than a failure of the job behind it, that is worth asking again about.
#: A schedule can be asked for before the state of charge it is to be planned from has landed,
#: and FlexMeasures refuses to plan from one it considers too old.
RETRYABLE_TRIGGER_REFUSALS = ("No recent state-of-charge value found",)


def _operation_mode_power_bands(
    power_ranges: list[tuple[float, float]], power_unit: str
) -> list[dict[str, list[str]]]:
    """Turn the power ranges an actuator states into the bands FlexMeasures plans within.

    A device with operation modes may only run inside one of its bands,
    so an on/off heater is asked for as {0 W} and {883.7 W} rather than as everything in between,
    which the resource manager would have to round into a full-on block,
    overshooting whatever capacity the site is held to.

    S2 states one signed range, where positive means consumption,
    while FlexMeasures asks for the two directions separately, each non-negative.
    So a range is split at zero: its non-negative part is the consumption range,
    its negative part is the production range with the sign flipped,
    and a range spanning zero becomes both, each starting at zero.

    :param power_ranges: lower and upper bound of each range the actuator states, signed as S2 states them.
    :param power_unit:   the unit those bounds are in.
    :returns:            one band per distinct range, in the order the actuator stated them.
    """

    # Zero is the first argument of each max() below, so that a bound of exactly zero is stated as zero,
    # rather than as the negative zero that flipping a sign leaves behind.
    bands: list[dict[str, list[str]]] = []
    for lower, upper in power_ranges:
        band: dict[str, list[str]] = {}
        if upper > 0:
            band["consumption-range"] = [
                f"{max(0.0, lower)} {power_unit}",
                f"{upper} {power_unit}",
            ]
        if lower < 0:
            band["production-range"] = [
                f"{max(0.0, -upper)} {power_unit}",
                f"{-lower} {power_unit}",
            ]
        if not band:
            # A range of exactly {0}: the mode in which the actuator draws nothing either way.
            band["consumption-range"] = [f"0 {power_unit}", f"0 {power_unit}"]
        if band not in bands:
            bands.append(band)
    return bands


class FRBCSimple(FRBC):
    _power_sensor_id: int
    _price_sensor_id: int
    _production_price_sensor_id: int
    _relax_site_capacity: bool | None
    _soc_sensor_id: int
    _rm_discharge_sensor_id: int
    _fill_rate_sensor_id: int | None
    #: Where what a resource manager declares about its storage is recorded, each optional:
    #: without one, that declaration is not recorded, rather than being posted somewhere else.
    _soc_minima_sensor_id: int | None
    _soc_maxima_sensor_id: int | None
    _usage_forecast_sensor_id: int | None
    _leakage_behaviour_sensor_id: int | None
    #: Where what the operation modes say about converting drawn power into stored energy is recorded,
    #: for the scheduler to plan the storage's conversion with.
    _charging_efficiency_sensor_id: int | None
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
    _power_recorded_at: dict[datetime, datetime]
    #: The charging efficiency last recorded, and the window it was recorded for,
    #: so that the same value for the same window is not recorded again on every schedule.
    _charging_efficiency_recorded: tuple[datetime, float] | None
    #: How many times a schedule is asked for, and how long between two attempts.
    _schedule_attempts: int
    _schedule_retry_interval: timedelta

    def __init__(
        self,
        power_sensor_id: int,
        soc_sensor_id: int,
        rm_discharge_sensor_id: int,
        price_sensor_id: int,
        # Where feeding power back is priced differently from drawing it, as a feed-in tariff is.
        # Without one, both directions are priced by price_sensor_id.
        production_price_sensor_id: int | None = None,
        # Whether the site's connection capacity may be breached at a price (True) or never (False).
        # Unset, it is left to FlexMeasures, which since v1.0 relaxes it at a high default breach price,
        # under its relax-constraints umbrella, and before that did not.
        # A home whose connection is a physical limit can pass False;
        # a site whose capacity is a target set by others, as in a community steering its members, can pass True.
        relax_site_capacity: bool | None = None,
        # Optional so existing callers keep working; when absent the actuator's fill rate is
        # simply not recorded, rather than being posted onto the power sensor.
        fill_rate_sensor_id: int | None = None,
        # Likewise optional, for the three things a resource manager declares about its storage.
        soc_minima_sensor_id: int | None = None,
        soc_maxima_sensor_id: int | None = None,
        usage_forecast_sensor_id: int | None = None,
        # Its resolution has to be the handler's resolution: a storage efficiency is what remains after one step,
        # computed here for a step of the handler's resolution,
        # and FlexMeasures reads it as one step of the sensor's own resolution.
        leakage_behaviour_sensor_id: int | None = None,
        # Likewise optional: without a sensor to record it on, no charging efficiency is declared,
        # and the scheduler plans the storage as if the actuator converted power into stored energy one for one.
        charging_efficiency_sensor_id: int | None = None,
        timezone: str = "UTC",
        schedule_duration: timedelta = timedelta(hours=12),
        # How many times a schedule is asked for before the storage is left without an instruction,
        # and how long between two attempts.
        # One attempt means a failure is never answered by another, as it was before this was bounded at all.
        schedule_attempts: int = SCHEDULE_ATTEMPTS,
        schedule_retry_interval: timedelta = SCHEDULE_RETRY_INTERVAL,
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
        self._production_price_sensor_id = (
            production_price_sensor_id
            if production_price_sensor_id is not None
            else price_sensor_id
        )
        self._relax_site_capacity = relax_site_capacity
        self._schedule_duration = schedule_duration
        self._resolution = resolution
        self._soc_sensor_id = soc_sensor_id
        self._rm_discharge_sensor_id = rm_discharge_sensor_id
        self._fill_rate_sensor_id = fill_rate_sensor_id
        self._soc_minima_sensor_id = soc_minima_sensor_id
        self._soc_maxima_sensor_id = soc_maxima_sensor_id
        self._usage_forecast_sensor_id = usage_forecast_sensor_id
        self._leakage_behaviour_sensor_id = leakage_behaviour_sensor_id
        self._charging_efficiency_sensor_id = charging_efficiency_sensor_id
        self._charging_efficiency_recorded = None
        self._storage_efficiency_recorded: tuple[datetime, float] | None = None
        if resolution <= timedelta(0) or schedule_duration <= timedelta(0):
            raise ValueError(
                f"resolution and schedule_duration have to be positive, but {resolution} and {schedule_duration} were given."
            )
        if schedule_duration % resolution:
            # FlexMeasures answers with whole steps of the power sensor's resolution only,
            # so a duration between two whole numbers of steps comes back with fewer values than it spans,
            # and the schedule's step, read as its duration over its number of values, would come out wrong.
            raise ValueError(
                f"schedule_duration has to be a whole number of resolution steps, but {schedule_duration} is not a multiple of {resolution}."
            )
        if schedule_attempts < 1:
            # A schedule is asked for at least once, or no schedule is asked for at all.
            raise ValueError(
                f"schedule_attempts has to be at least one, since it counts the attempts at a schedule rather than the retries, but {schedule_attempts} was given."
            )
        self._schedule_attempts = schedule_attempts
        self._schedule_retry_interval = schedule_retry_interval
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
        self._power_recorded_at = {}
        self._soc_sensor_records_instants: bool | None = None
        if hard_bound_factor < 1:
            # Below one, the hard upper bound sits inside the declared range,
            # and FlexMeasures clips the band into the hard bounds,
            # so the band would be narrowed to something the resource manager never asked for.
            raise ValueError(
                f"hard_bound_factor has to be at least 1, so that the hard upper bound sits at or above the declared fill-level range, but {hard_bound_factor} was given."
            )
        self._hard_bound_factor = hard_bound_factor
        self.power_unit = power_unit
        if energy_unit not in WATTS_PER_ENERGY_UNIT_PER_SECOND:
            # A fill rate is per second of this unit, and has to be compared with the watts S2 states a power in,
            # so a unit that cannot be converted to watts cannot be planned with.
            raise ValueError(
                f"energy_unit has to be one of {', '.join(WATTS_PER_ENERGY_UNIT_PER_SECOND)}, but {energy_unit!r} was given."
            )
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
        # Floored on the moment's own clock, in the offset it carries, which is how FlexMeasures floors a series posted with it,
        # so an hourly step in a zone half an hour off UTC, or a daily one, starts where FlexMeasures' steps do.
        # A boundary in the repeated hour of an autumn DST change exists twice, and the one meant is the later of the two
        # that is not after the moment: taking the first pass every time put a boundary an hour or more too early.
        # A boundary in a spring gap, which only a resolution of more than an hour can land on, moves to the end of the gap.
        # Compared as pandas timestamps, which compare instants: two datetimes on one local clock compare by wall time,
        # whichever pass each is on.
        at = pd.Timestamp(moment)
        candidates = [
            at.floor(
                self._resolution, ambiguous=first_pass, nonexistent="shift_forward"
            )
            for first_pass in (True, False)
        ]
        boundary = max(c for c in candidates if c <= at)
        return boundary.to_pydatetime()

    def schedule_start(self, valid_from: datetime) -> datetime:
        """Where a schedule asked for at valid_from starts: shifted and floored to a boundary, but never before now.

        Never before now, since planning happens on every storage status while a description may be days old:
        anchored to that description alone, every status in a long session was answered with the same window,
        which slid into the past, so its instructions were all due already and arrived as one burst.
        Where now is past that boundary, the schedule starts at the next one, not at the start of the step now is in.
        A schedule is planned from the fill level reported now, and FlexMeasures takes that to be the level at its start,
        so a schedule starting at a step already under way was planned as if the whole step were left,
        and every replan within it asked for less power over what remained of it, delivering less than planned.
        The step under way keeps the instruction it is already following.
        """
        now = self.now()
        start = self.align_to_resolution(valid_from + self._valid_from_shift)
        if start >= now:
            return start
        current = self.align_to_resolution(now)
        return current if current == now else current + self._resolution

    async def _soc_duration(self) -> timedelta:
        """What a reported fill level is posted as lasting: an instant, where the state-of-charge sensor records instants.

        Posted over the minute after it, a reading on such a sensor was taken for a forecast of that minute.
        FlexMeasures refuses a single value of no duration on a sensor with a resolution, though,
        so on any other sensor, or one that cannot be read, it is posted over a minute, as before.
        The sensor is asked until it answers, and its answer kept, since its resolution is a property of how it was set up.
        """
        if self._soc_sensor_records_instants is None:
            try:
                sensor = await self._fm_client.get_sensor(self._soc_sensor_id)
            except (ValueError, ConnectionError, ContentTypeError) as exc:
                self._logger.warning(
                    "Could not read the resolution of state-of-charge sensor %s, so a fill level is posted over a minute: %s",
                    self._soc_sensor_id,
                    exc,
                )
                return timedelta(minutes=1)
            resolution = (
                sensor.get("event_resolution") if isinstance(sensor, dict) else None
            )
            try:
                self._soc_sensor_records_instants = resolution is not None and (
                    pd.Timedelta(resolution) == pd.Timedelta(0)
                )
            except ValueError:
                self._logger.warning(
                    "State-of-charge sensor %s states a resolution of %r, which is not a duration, so a fill level is posted over a minute.",
                    self._soc_sensor_id,
                    resolution,
                )
                self._soc_sensor_records_instants = False
        return (
            timedelta(0) if self._soc_sensor_records_instants else timedelta(minutes=1)
        )

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
            duration=await self._soc_duration(),
        )

        # The state of charge this status reports is what a schedule starts from, so this is
        # where a schedule is asked for.
        # A system description is needed to turn that schedule into instructions.
        # Without one yet, nothing is planned here, and the description plans against this status when it arrives.
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
            except ConnectionError as exc:
                # Nothing is remembered from a connection that failed:
                # the next status asks again, since the sensor may well answer then.
                self._logger.warning(
                    "Could not reach power sensor %s to read which sign it records, so posting an actuator's power as S2 states it, with consumption positive: %s",
                    self._power_sensor_id,
                    exc,
                )
                return power
            except (ValueError, KeyError) as exc:
                # A sensor that cannot be read at all, or whose answer makes no sense,
                # will not read any better on the next status,
                # so S2's own convention is remembered and the reason is said once.
                self._power_sensor_takes_consumption_as_positive = True
                self._logger.warning(
                    "Could not read which sign power sensor %s records, so an actuator's power is posted as S2 states it, with consumption positive: %s",
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

    @staticmethod
    def _latest(history: dict):
        """The message a history holds that arrived last, or None while it holds none."""
        return list(history.values())[-1] if history else None

    def _soc_unit(self) -> tuple[str, float]:
        """The unit a state of charge goes to FlexMeasures in, and what a fill level is multiplied by to be in it."""
        if self.energy_unit in ("kWh", "MWh"):
            return self.energy_unit, 1.0
        return "kWh", (
            WATTS_PER_ENERGY_UNIT_PER_SECOND[self.energy_unit]
            / WATTS_PER_ENERGY_UNIT_PER_SECOND["kWh"]
        )

    def _latest_fill_level(self) -> float | None:
        """The fill level the latest storage status reported, in the resource manager's own units, if it reported a number."""
        if not self._storage_status_history:
            return None
        fill_level = list(self._storage_status_history.values())[-1].present_fill_level
        return None if math.isnan(fill_level) else fill_level

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

        # The element the actuator runs on depends on the fill level, as a heat pump derates while its buffer warms,
        # so it is the one that applies at the latest fill level reported, not whichever the mode happens to list first.
        fill_level = self._latest_fill_level()
        if fill_level is not None:
            element = element_for_fill_level(operation_mode.elements, fill_level)
        else:
            # As if at the bottom of the range, which is as good a guess as any, and is said to be one below.
            element = element_for_fill_level(operation_mode.elements, float("-inf"))
            if len(operation_mode.elements) > 1:
                self._logger.warning(
                    "FRBC.ActuatorStatus for operation mode %s arrived with no usable FRBC.StorageStatus yet, "
                    "so its power is recorded as the element at the bottom of the fill-level range states it, "
                    "which need not be the element the actuator runs on.",
                    om_label(operation_mode),
                )

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
        # FlexMeasures keys a belief on its sensor, its source, the event and the moment it was formed,
        # and it floors the event to the sensor's resolution.
        # Two statuses inside one resolution step, milliseconds apart, were refused in practice,
        # with a duplicate key whose belief time had lost its sub-second part.
        # Posting two such beliefs by hand does not reproduce that,
        # so the exact rule is the server's to state.
        # Either way, a second apart is unambiguous: the later status supersedes the earlier one,
        # which is what a resource manager reporting twice in a second means.
        # A resource manager sends a status for each instruction it applies,
        # and this handler sends several at once, so that is the ordinary case rather than an edge.
        # The comparison is on the event as FlexMeasures keys it,
        # since two statuses seconds apart still land on the same resolution step.
        # Kept per event, since statuses for different steps can interleave within a second,
        # and a comparison with the latest status alone then missed the one before it about the same step.
        event = self.align_to_resolution(start)
        recorded_at = self.now()
        last_recorded_at = self._power_recorded_at.get(event)
        if last_recorded_at is not None and recorded_at - last_recorded_at < timedelta(
            seconds=1
        ):
            recorded_at = last_recorded_at + timedelta(seconds=1)
        self._power_recorded_at[event] = recorded_at
        # Steps more than a day older than this one are forgotten, to keep this bounded: a status that late is not expected.
        for old_event in [
            e for e in self._power_recorded_at if e < event - timedelta(days=1)
        ]:
            del self._power_recorded_at[old_event]

        # The value and the unit it is in travel together,
        # so that what is reported below is a pair that agrees even where the conversion is what failed.
        value, unit = power, S2_POWER_UNIT
        try:
            # Inside the try, because turning a power into what the sensor means by it can fail too:
            # a unit nothing converts between raises, and so does a sensor that cannot be reached.
            value = self._in_the_power_unit(
                await self._as_the_power_sensor_reads_it(power)
            )
            unit = self.power_unit
            await self._fm_client.post_sensor_data(
                self._power_sensor_id,
                start=start,
                prior=recorded_at,
                values=[value],
                unit=unit,
                duration=self._resolution,
            )
        except (JobFailedError, ValueError, ConnectionError, NotImplementedError):
            # This runs in a task nobody awaits,
            # so an exception here is logged by asyncio at best and lost at worst.
            # Say which measurement went missing,
            # since a power sensor silently holding a plan and no measurement is the hardest state to notice.
            self._logger.exception(
                "Could not record %s %s on power sensor %s for %s.",
                value,
                unit,
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
                duration=self._resolution,
            )

    async def _record_charging_efficiency(
        self,
        system_description: FRBCSystemDescription,
        start: datetime,
        fill_level: float,
    ) -> bool:
        """Record what the actuator converts drawn power into stored energy at, over the window a schedule covers.

        FlexMeasures reads a charging efficiency from a sensor rather than taking a value in the flex model,
        so what the operation modes say about it is recorded here, by the call that is about to plan with it.
        Recorded before the schedule is asked for, and not alongside it:
        a schedule planned against a sensor holding nothing for its window is planned as if the actuator converted power one for one.

        :param start: where the schedule starts, which is where the value is recorded from.
        :param fill_level: the fill level the schedule starts from, in the resource manager's own units,
                           which picks the element of each mode that answers.
        :returns: whether that schedule may be planned against the sensor,
                  which it may not where nothing is recorded on it for this window.
        """
        if self._charging_efficiency_sensor_id is None:
            return False

        # One actuator, as everywhere else in this handler.
        actuator = system_description.actuators[0]
        efficiencies: dict[str, float] = {}
        # The energy unit is one fill_rate_in_watts knows, which the constructor saw to.
        for operation_mode in actuator.operation_modes:
            efficiency = operation_mode_conversion_efficiency(
                operation_mode, self._fill_level_scale, self.energy_unit, fill_level
            )
            if efficiency is not None:
                efficiencies[str(operation_mode.id)] = efficiency

        if not efficiencies:
            # Every mode is idle, or every mode only feeds back, and neither converts drawn power into anything.
            self._logger.info(
                "Not asking for a schedule against a charging efficiency: no operation mode of actuator %s draws power to convert.",
                actuator.id,
            )
            return False

        efficiency = min(efficiencies.values())
        if len(set(efficiencies.values())) > 1:
            # FlexMeasures takes one charging efficiency for the whole device, and the modes disagree.
            # The lowest goes out: a plan that converts less than the actuator really does reaches its target early,
            # where one that converts more reaches it late, and late is what a fill-level band is there to prevent.
            self._logger.warning(
                "The operation modes of actuator %s convert power at different efficiencies (%s), and FlexMeasures takes one for the whole device, so the schedule is asked for against the lowest of them, %s.",
                actuator.id,
                efficiencies,
                efficiency,
            )

        if self._charging_efficiency_recorded == (start, efficiency):
            # The same value for the same window is already on the sensor.
            # Recording it again would add a belief saying exactly what the one before it says,
            # and two of those inside one second collide on the belief's own key.
            return True

        try:
            await self._fm_client.post_sensor_data(
                self._charging_efficiency_sensor_id,
                start=start,
                prior=self.now(),
                values=[efficiency],
                # Dimensionless, not a percentage: it is the ratio between two powers,
                # and for a heat pump, whose fill rate is the heat it delivers, it is well above one.
                unit="dimensionless",
                duration=self._schedule_duration,
            )
        except (JobFailedError, ValueError, ConnectionError, NotImplementedError):
            # A schedule planned as if the actuator converted power one for one beats no schedule at all,
            # as long as it is said out loud:
            # that plan overstates what a lossy storage gains from a given power, and understates what a heat pump puts in.
            self._logger.exception(
                "Not asking for a schedule against a charging efficiency: could not record %s on sensor %s for %s.",
                efficiency,
                self._charging_efficiency_sensor_id,
                start,
            )
            return False

        self._charging_efficiency_recorded = (start, efficiency)
        return True

    def _is_worth_asking_again(self, failure: Exception) -> bool:
        """Whether a failed attempt at a schedule is worth making again.

        A job that reached the server and failed inside it is,
        unless its cause is one that fails identically on every attempt (see NON_RETRYABLE_SCHEDULE_FAILURES).
        A job can fail on a transient of its own, from a worker crashing out of its solver,
        to a pandas error over windows that overlap for as long as two of these schedules do.

        A trigger the server refused outright is not, bar the one refusal that answers a race:
        a flex model or context FlexMeasures will not take is not one it will take two seconds later.

        :param failure: what the attempt raised.
        """
        message = str(failure)
        if any(marker in message for marker in NON_RETRYABLE_SCHEDULE_FAILURES):
            return False
        if isinstance(failure, JobFailedError):
            return True
        return any(marker in message for marker in RETRYABLE_TRIGGER_REFUSALS)

    async def _ask_for_a_schedule(
        self, start: datetime, flex_context: dict, flex_model: dict
    ) -> dict | list[dict]:
        """Ask FlexMeasures for a schedule, and ask again where the attempt is worth making again.

        One failed scheduling job otherwise leaves the storage without an instruction for its window.
        The resource manager is told nothing, and goes on polling,
        and since this runs as a task whose exception is logged and dropped, nothing else notices either.
        That is what one failed job did in a co-simulation:
        a worker crashed inside its solver,
        and the resource manager it belonged to polled until its round timed out, every round, for the rest of the run.

        :param start: the window the schedule is asked for, which every attempt asks for again.
        :raises JobFailedError: where the last attempt worth making failed inside the server.
        :raises ValueError:     where the trigger itself was refused.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._fm_client.trigger_and_get_schedule(
                    start=start,
                    sensor_id=self._power_sensor_id,
                    flex_context=flex_context,
                    flex_model=flex_model,
                    duration=self._schedule_duration,
                    # Each attempt says what is known now, rather than what was known when the first was made.
                    prior=self.now(),
                    # In watts, which is what S2 states an operation mode's power ranges in,
                    # and the translation below matches a schedule against those ranges.
                    # Not in power_unit, which says how a power value this handler posts is labelled,
                    # and not in the power sensor's own unit, which is what a schedule comes back in by default.
                    unit=S2_POWER_UNIT,
                )
            except (JobFailedError, ValueError) as failure:
                if (
                    attempt >= self._schedule_attempts
                    or not self._is_worth_asking_again(failure)
                ):
                    self._logger.exception(
                        "The storage is left without an instruction for the window starting %s: asking for a schedule failed on attempt %s of %s.",
                        start,
                        attempt,
                        self._schedule_attempts,
                    )
                    raise
                self._logger.warning(
                    "Asking for a schedule for the window starting %s failed on attempt %s of %s, and is worth asking again: %s",
                    start,
                    attempt,
                    self._schedule_attempts,
                    failure,
                )
                await asyncio.sleep(self._schedule_retry_interval.total_seconds())

    def _warn_where_a_fallback_scheduler_answered(
        self, schedule: dict | list[dict], start: datetime
    ) -> None:
        """Say so where a schedule was planned by a scheduler this handler did not ask for.

        FlexMeasures answered an infeasible problem with a fallback scheduler until v1.0.0 retired it.
        Against an older server, the schedule endpoint follows that fallback job rather than reporting the failure,
        or redirects to it where the server sets FLEXMEASURES_FALLBACK_REDIRECT, which this client follows,
        so either way a schedule nobody asked for arrives looking like the one that was.
        It is a coarse charging policy rather than a plan,
        and it records no state of charge, so a later window has nothing to resolve its soc-at-start against:
        that is how a house in a co-simulation starved mid-run.

        :param schedule: what came back, which names the scheduler that planned it.
        :param start:    the window it was asked for.
        """
        if not isinstance(schedule, dict):
            # A schedule per device, which only an asset-wide trigger is answered with.
            return
        scheduler = str((schedule.get("scheduler_info") or {}).get("scheduler", ""))
        if "fallback" not in scheduler.lower():
            return
        self._logger.error(
            "The schedule for the window starting %s was planned by %s, which is a fallback scheduler: the problem this handler asked about was infeasible. The schedule is a coarse charging policy, and no state of charge was recorded for a later window to be planned from.",
            start,
            scheduler,
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
            self._logger.warning(
                "Cannot ask for a schedule: no FRBC.StorageStatus has arrived yet, so there is no state of charge to plan from."
            )
            return

        # Scaled at the FlexMeasures edge, like every bound and level below it.
        soc_at_start = fill_level_at_start * self._fill_level_scale

        # One actuator, as everywhere else in this handler.
        actuator = system_description.actuators[0]
        # Every power range the actuator states, over all of its operation modes and their elements.
        # An S2 power range runs from its lower bound to its upper one, and S2 fixes one sign convention,
        # where positive means consumption,
        # so an actuator can only produce where one of its ranges reaches below zero.
        # TODO: an element states one power range per commodity quantity, and no distinction is made between them here,
        # as the schedule translation does not make one either (see power_to_fill_rate_with_metrics).
        power_ranges = [
            (
                min(power_range.start_of_range, power_range.end_of_range),
                max(power_range.start_of_range, power_range.end_of_range),
            )
            for operation_mode in actuator.operation_modes
            for element in operation_mode.elements
            for power_range in element.power_ranges
        ]

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

        # Where this schedule starts, which is both what it is asked for,
        # and the window a charging efficiency has to be recorded over to be planned against.
        start = self.schedule_start(system_description.valid_from)

        # FlexMeasures takes a state of charge in kWh or MWh only.
        # So a fill level recorded in another energy unit is converted here, at the edge, rather than refused,
        # and the bounds read back below stay in the recorded unit.
        # The band, the usage and the leakage are read from sensors, which FlexMeasures converts itself.
        soc_unit, to_soc_unit = self._soc_unit()
        flex_model: dict = {
            "soc-unit": soc_unit,
            # TODO: use forecast of the SOC instead
            "soc-at-start": soc_at_start * to_soc_unit,
            "soc-min": soc_min * to_soc_unit,
            "soc-max": soc_max * to_soc_unit,
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
        if await self._record_storage_efficiency(start, fill_level_at_start):
            flex_model["storage-efficiency"] = {
                "sensor": self._leakage_behaviour_sensor_id
            }
        # What the operation modes say about converting drawn power into stored energy.
        # Recorded first, since FlexMeasures reads it from the sensor named here and not from the flex model,
        # and named only where it was recorded.
        if await self._record_charging_efficiency(
            system_description, start, fill_level_at_start
        ):
            flex_model["charging-efficiency"] = {
                "sensor": self._charging_efficiency_sensor_id
            }
        if power_ranges:
            # What the actuator can draw and what it can feed back, each non-negative,
            # as FlexMeasures states the two directions separately.
            # A resource manager that cannot produce at all has to say so:
            # left out, the capacity falls back to whatever the asset carries,
            # and a heat pump is then planned as if its buffer could be discharged into the grid.
            consumption_capacity = max(0.0, max(upper for _, upper in power_ranges))
            production_capacity = max(0.0, -min(lower for lower, _ in power_ranges))
            # In watts, since these are the resource manager's own numbers and S2 states them in watts.
            # Not in power_unit, which says how a power value this handler posts is labelled.
            flex_model["consumption-capacity"] = (
                f"{consumption_capacity} {S2_POWER_UNIT}"
            )
            flex_model["production-capacity"] = f"{production_capacity} {S2_POWER_UNIT}"
            operation_modes = _operation_mode_power_bands(power_ranges, S2_POWER_UNIT)
            # Only where the bands say more than the two capacities above already do.
            # The capacities say the actuator can run at any power from minus its production capacity to its consumption capacity,
            # so a single band running through zero is exactly that and says nothing new.
            # Any other single band does: an actuator that runs at 200 W or not at all rules out everything in between,
            # and so does one that only ever feeds back.
            # And only to a server that has the field.
            # FlexMeasures added operation-modes in v1.0.0,
            # and a flex model naming a field the server does not know is refused outright,
            # which would leave the resource manager with no schedule at all.
            says_more_than_the_capacities = len(operation_modes) > 1 or not any(
                lower <= 0 <= upper for lower, upper in power_ranges
            )
            if says_more_than_the_capacities:
                if await self._fm_client.server_version_at_least("1.0.0"):
                    flex_model["operation-modes"] = operation_modes
                else:
                    self._logger.info(
                        "Not asking for a schedule against the operation modes' power bands: FlexMeasures has the operation-modes flex-model field from v1.0.0, and this server reports %s.",
                        self._fm_client.server_version,
                    )
        self._logger.debug("flex model: %s", flex_model)

        flex_context: dict = {
            "production-price": {"sensor": self._production_price_sensor_id},
            "consumption-price": {"sensor": self._price_sensor_id},
            "site-power-capacity": f"{3 * 25 * 230} VA",
        }
        if has_soc_band:
            # Without this, the minima and maxima above are hard bounds again,
            # and the band is back to making reachable states infeasible.
            # The breach price is left to FlexMeasures' own default,
            # since what a breach of comfort is worth is the deployment's judgement, not the protocol's.
            flex_context["relax-soc-constraints"] = True
        if self._relax_site_capacity is not None:
            flex_context["relax-site-capacity-constraints"] = self._relax_site_capacity

        schedule = await self._ask_for_a_schedule(start, flex_context, flex_model)
        self._warn_where_a_fallback_scheduler_answered(schedule, start)

        # send_instruction_batch checks this once more, but translating a schedule follows every step of it,
        # which is wasted on a request that is already out of date.
        if request_number is not None and not self._is_latest_request(
            STORAGE_STATUS_REQUESTS, request_number
        ):
            self._logger.info(
                "Not turning the schedule for planning request %s into instructions: a newer storage status arrived while it was being planned.",
                request_number,
            )
            return

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
            # What the storage loses by itself and what is drawn from it, as the resource manager last said,
            # so that the fill level each step is answered at falls as well as rises.
            leakage_behaviour=self._latest(self._leakage_behaviour_history),
            usage_forecast=self._latest(self._usage_forecast_history),
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
        """Keep a resource manager's leakage behaviour for planning, which records it when it asks for a schedule.

        Recorded on arrival instead, it covered the hours after the message rather than the window being planned,
        at a fill level nobody had reported yet, and planning went on without it once those hours had passed.
        """
        if self._leakage_behaviour_sensor_id is None:
            self._logger.info(
                "Not recording an FRBC.LeakageBehaviour: no leakage-behaviour sensor is configured."
            )

    async def _record_storage_efficiency(
        self, start: datetime, fill_level: float
    ) -> bool:
        """Record what the storage keeps of itself per step, over the window a schedule covers, from where it starts.

        :returns: whether the schedule may be planned against the sensor, which it may not where nothing is recorded on it.
        """
        leakage_behaviour = self._latest(self._leakage_behaviour_history)
        if (
            self._leakage_behaviour_sensor_id is None
            or leakage_behaviour is None
            or not self._system_description_history
        ):
            return False
        # S2 states a leakage as a rate, and FlexMeasures takes a share of the stock, so one share stands for the rate
        # only at one fill level, and the loss scales with the stock away from it.
        # That fill level is where the schedule starts, as long as that is at least the middle of the storage's range:
        # from a near-empty start, a share of the start level would let almost nothing remain per step,
        # and the loss would be overstated many times over as soon as the plan filled the storage.
        # Below the middle the loss is understated instead, by at most the ratio of the middle to the stock, which is the milder error.
        storage_range = list(self._system_description_history.values())[
            -1
        ].storage.fill_level_range
        middle = (storage_range.start_of_range + storage_range.end_of_range) / 2
        # What remains after one step of this handler's resolution,
        # which FlexMeasures reads as one step of the leakage sensor's resolution, so the two have to agree.
        efficiency = leakage_behaviour_to_storage_efficiency(
            message=leakage_behaviour,
            resolution=self._resolution,
            fill_level_scale=self._fill_level_scale,
            fill_level=fill_level,
            share_of=max(fill_level, middle),
        )
        if self._storage_efficiency_recorded == (start, efficiency):
            return True
        try:
            await self._fm_client.post_sensor_data(
                sensor_id=self._leakage_behaviour_sensor_id,
                start=start,
                prior=self.now(),
                values=[efficiency],
                unit="%",
                duration=self._schedule_duration,
            )
        except (JobFailedError, ValueError, ConnectionError):
            self._logger.exception(
                "Not asking for a schedule against a storage efficiency: could not record %s %% on sensor %s for %s.",
                efficiency,
                self._leakage_behaviour_sensor_id,
                start,
            )
            return False
        self._storage_efficiency_recorded = (start, efficiency)
        return True
