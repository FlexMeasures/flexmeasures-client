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
from aiohttp import ClientError, ClientResponseError

from flexmeasures_client.client import convert_units
from flexmeasures_client.exceptions import (
    ContentTypeError,
    JobFailedError,
    JobTimeoutError,
)
from flexmeasures_client.s2.control_types.FRBC import FRBC, STORAGE_STATUS_REQUESTS
from flexmeasures_client.s2.control_types.FRBC.utils import (
    WATTS_PER_ENERGY_UNIT_PER_SECOND,
    WATTS_PER_FILL_RATE_UNIT,
    clip_fill_level_target_profile,
    element_for_fill_level,
    fm_schedule_to_instructions,
    get_soc_min_max,
    normal_operation_modes,
    om_label,
    operation_mode_conversion_efficiency,
)
from flexmeasures_client.s2.control_types.translations import (
    leakage_behaviour_to_storage_efficiency,
    translate_fill_level_target_profile,
    translate_usage_forecast_to_fm,
)

#: The unit of power values in S2, and so the unit of an operation mode's power ranges.
#: A schedule is read back in this unit, whatever unit its power sensor records in.
#: Values sent to FlexMeasures in this unit are passed on as they came in from the resource manager.
S2_POWER_UNIT = "W"

#: How many times to request a schedule before leaving the storage without an instruction for its window.
#: Every attempt costs the server a solve, so this number is kept small.
SCHEDULE_ATTEMPTS = 5

#: How long to wait between two attempts at a schedule.
#: The wait is fixed, without backoff.
#: It only needs to cover a post landing or a worker picking up the job, and meanwhile a resource manager is waiting for an instruction.
SCHEDULE_RETRY_INTERVAL = timedelta(seconds=2)

#: Causes of a failed scheduling job that fail the same way, however often the job is requested again.
#: Requesting again only multiplies failed jobs across the server's workers, so these are never retried.
NON_RETRYABLE_SCHEDULE_FAILURES = (
    # A scheduler that records a belief twice collides with its own first write, on every attempt.
    "UniqueViolation",
    "duplicate key value violates unique constraint",
    # An infeasible problem is planned from the same flex model and context every time, so it stays infeasible.
    # Until v1.0.0, FlexMeasures handled one with a fallback scheduler.
    # Since v1.0.0 retired that fallback, the job itself fails.
    "InfeasibleProblemException",
)

#: Refusals of a trigger (not failures of the job behind it) that are worth retrying.
#: A schedule can be requested before the state of charge to plan from has landed.
#: FlexMeasures refuses to plan from a state of charge that it considers too old.
RETRYABLE_TRIGGER_REFUSALS = ("No recent state-of-charge value found",)

#: The exceptions a request to FlexMeasures can raise when there is no defect in this code.
#: The request may be refused (ValueError), the server unreachable (ConnectionError, TimeoutError, ClientError),
#: or the response unexpected (ContentTypeError).
#: A queued job may also fail, or never be picked up (JobFailedError, JobTimeoutError).
REQUEST_FAILURES = (
    JobFailedError,
    JobTimeoutError,
    ValueError,
    ConnectionError,
    TimeoutError,
    ContentTypeError,
    ClientError,
)

#: How FlexMeasures before v0.33.0 reports a failed scheduling job, since it has no job-status API.
#: It responds with an HTTP 400, which the client raises as a ValueError with only this message.
BARE_REFUSAL = "Request failed with status code 400"


def _merged(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return the union of closed ranges, as the fewest ranges that cover it, in order."""
    merged: list[tuple[float, float]] = []
    for lower, upper in sorted(ranges):
        if merged and lower <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
        else:
            merged.append((lower, upper))
    return merged


def _without_negative_usage(usage_forecast):
    """Return a usage forecast in which each negative expected rate is replaced by zero.

    This is how a usage forecast is recorded and planned against.
    """
    if usage_forecast is None or all(
        element.usage_rate_expected >= 0 for element in usage_forecast.elements
    ):
        return usage_forecast
    return usage_forecast.model_copy(
        update={
            "elements": [
                element.model_copy(
                    update={
                        "usage_rate_expected": max(0.0, element.usage_rate_expected)
                    }
                )
                for element in usage_forecast.elements
            ]
        }
    )


def _operation_mode_power_bands(
    power_ranges: list[tuple[float, float]], power_unit: str
) -> list[dict[str, list[str]]]:
    """Turn the power ranges an actuator declares into the bands FlexMeasures plans within.

    A device with operation modes may only run inside one of its bands.
    So an on/off heater is requested as {0 W} and {883.7 W}, not as everything in between.
    Otherwise, the resource manager would have to round a value in between into a full-on block.
    That block would overshoot any capacity limit the site is held to.

    S2 gives one signed range, where positive means consumption.
    FlexMeasures expects the two directions separately, each non-negative.
    So a range is split at zero.
    Its non-negative part becomes the consumption range.
    Its negative part, with the sign flipped, becomes the production range.
    A range that spans zero becomes both, each starting at zero.

    :param power_ranges: lower and upper bound of each range the actuator declares, signed as in S2.
    :param power_unit:   the unit of those bounds.
    :returns:            one band per distinct range, in the order the actuator declared them.
    """

    # Zero is the first argument of each max() below, so that a bound of exactly zero is written as zero,
    # not as the negative zero that flipping a sign leaves behind.
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
            # A range of exactly {0}: the mode in which the actuator neither consumes nor produces.
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
    #: The sensors to record the resource manager's declarations about its storage on. Each one is optional.
    #: Without a sensor, that declaration is not recorded at all, instead of being posted somewhere else.
    _soc_minima_sensor_id: int | None
    _soc_maxima_sensor_id: int | None
    _usage_forecast_sensor_id: int | None
    _leakage_behaviour_sensor_id: int | None
    #: The sensor to record the operation modes' conversion of drawn power into stored energy on.
    #: The scheduler uses it to plan the storage's conversion.
    _charging_efficiency_sensor_id: int | None
    _resolution: timedelta
    _schedule_duration: timedelta
    _valid_from_shift: timedelta
    _fill_level_scale: float
    _hard_bound_factor: float
    #: Whether the power sensor records consumption as positive, as S2 does.
    #: This is read from the sensor itself the first time a power value is posted, and remembered after that.
    #: It is None until it has been read.
    _power_sensor_takes_consumption_as_positive: bool | None
    #: For each moment, when a power value was last recorded for the actuator.
    #: This prevents a second status within one event from colliding with the first.
    _power_recorded_at: dict[datetime, datetime]
    #: The last recorded charging efficiency, and the window it was recorded for.
    #: This prevents recording the same value for the same window again on every schedule.
    _charging_efficiency_recorded: tuple[datetime, float] | None
    #: How many times to request a schedule, and how long to wait between two attempts.
    _schedule_attempts: int
    _schedule_retry_interval: timedelta

    def __init__(
        self,
        power_sensor_id: int,
        soc_sensor_id: int,
        rm_discharge_sensor_id: int,
        price_sensor_id: int,
        # Use this if feeding power back is priced differently from drawing it, as with a feed-in tariff.
        # Without it, both directions are priced by price_sensor_id.
        production_price_sensor_id: int | None = None,
        # Whether the site's connection capacity may be breached at a price (True) or never (False).
        # If unset, FlexMeasures decides.
        # Since v1.0, FlexMeasures relaxes it at a high default breach price, under its relax-constraints umbrella.
        # Before v1.0, it did not relax it.
        # A home whose connection is a physical limit can pass False.
        # A site whose capacity is a target set by others, as in a community that steers its members, can pass True.
        relax_site_capacity: bool | None = None,
        # Optional, so that existing callers keep working.
        # Without it, the actuator's fill rate is not recorded at all, instead of being posted onto the power sensor.
        fill_rate_sensor_id: int | None = None,
        # Also optional: the sensors for three declarations a resource manager makes about its storage.
        soc_minima_sensor_id: int | None = None,
        soc_maxima_sensor_id: int | None = None,
        usage_forecast_sensor_id: int | None = None,
        # The resolution of this sensor must equal the handler's resolution.
        # A storage efficiency is what remains after one step, and this handler computes it for a step of its own resolution.
        # FlexMeasures reads it as one step of the sensor's resolution.
        leakage_behaviour_sensor_id: int | None = None,
        # Also optional.
        # Without a sensor to record it on, no charging efficiency is declared.
        # Then the scheduler plans the storage as if the actuator converted power into stored energy one to one.
        charging_efficiency_sensor_id: int | None = None,
        timezone: str = "UTC",
        schedule_duration: timedelta = timedelta(hours=12),
        # How many times to request a schedule before leaving the storage without an instruction,
        # and how long to wait between two attempts.
        # One attempt means a failure is never retried,
        # which is how this handler behaved before retries were added.
        schedule_attempts: int = SCHEDULE_ATTEMPTS,
        schedule_retry_interval: timedelta = SCHEDULE_RETRY_INTERVAL,
        # The resolution that schedules are planned on; a schedule's start must line up with it.
        resolution: timedelta = timedelta(minutes=15),
        max_size: int = 100,
        valid_from_shift: timedelta = timedelta(days=1),
        fill_level_scale: float = 1,
        # How far above the declared fill-level range a schedule's hard upper bound sits.
        # The default is 1.5 times as high, so that a storage that overshoots its declared range stays feasible.
        # A deployment whose storage cannot overshoot at all can lower this to 1.
        hard_bound_factor: float = 1.5,
        # The unit that this handler converts posted power values into, and labels them with.
        # These values come from the operation modes' power ranges, which S2 gives in watts.
        power_unit: str = "W",
        # The unit that fill levels are recorded in.
        # A state of charge, and the minima and maxima that bound it, are all sent in this one unit, so that they can be compared.
        # The default is kilowatt-hours, because S2 is a residential protocol, and a home battery or heat buffer is of that order.
        # A deployment at another scale can set a different unit here.
        energy_unit: str = "kWh",
        # The unit that rates of fill level are recorded in: the fill rate and the usage forecast.
        # S2 states them per second, which in kWh/s makes tiny numbers, so they are recorded in a power unit: W, kW or MW.
        # The default is watts, in which a residential heat buffer's rates read as such.
        fill_rate_unit: str = "W",
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
            # FlexMeasures only returns whole steps of the power sensor's resolution.
            # So a duration that is not a whole number of steps comes back with fewer values than it spans.
            # The schedule's step is computed as its duration divided by its number of values, so it would come out wrong.
            raise ValueError(
                f"schedule_duration has to be a whole number of resolution steps, but {schedule_duration} is not a multiple of {resolution}."
            )
        if schedule_attempts < 1:
            # A schedule must be requested at least once, or there is no schedule at all.
            raise ValueError(
                f"schedule_attempts has to be at least one, since it counts the attempts at a schedule rather than the retries, but {schedule_attempts} was given."
            )
        self._schedule_attempts = schedule_attempts
        self._schedule_retry_interval = schedule_retry_interval
        self._timezone = ZoneInfo(timezone)
        if fill_level_scale <= 0:
            # Every fill level this handler records is multiplied by the scale.
            # The hard bounds that a schedule was planned against are divided by it again.
            # So a scale of zero would divide by zero, and a negative scale would turn the bounds upside down.
            raise ValueError(
                f"fill_level_scale has to be greater than zero, since it converts a fill level into the unit it is recorded in, but {fill_level_scale} was given."
            )
        self._fill_level_scale = fill_level_scale
        self._power_sensor_takes_consumption_as_positive = None
        self._power_recorded_at = {}
        self._soc_sensor_records_instants: bool | None = None
        self._planning = asyncio.Lock()
        if hard_bound_factor < 1:
            # Below one, the hard upper bound sits inside the declared range.
            # FlexMeasures clips the band to the hard bounds.
            # So the band would be narrowed to something the resource manager never asked for.
            raise ValueError(
                f"hard_bound_factor has to be at least 1, so that the hard upper bound sits at or above the declared fill-level range, but {hard_bound_factor} was given."
            )
        self._hard_bound_factor = hard_bound_factor
        self.power_unit = power_unit
        if energy_unit not in WATTS_PER_ENERGY_UNIT_PER_SECOND:
            # A fill rate is in this unit per second, and must be compared with power values, which S2 gives in watts.
            # So a unit that cannot be converted to watts cannot be planned with.
            raise ValueError(
                f"energy_unit has to be one of {', '.join(WATTS_PER_ENERGY_UNIT_PER_SECOND)}, but {energy_unit!r} was given."
            )
        self.energy_unit = energy_unit
        if fill_rate_unit not in WATTS_PER_FILL_RATE_UNIT:
            raise ValueError(
                f"fill_rate_unit has to be one of {', '.join(WATTS_PER_FILL_RATE_UNIT)}, but {fill_rate_unit!r} was given."
            )
        self._fill_rate_unit = fill_rate_unit

        # delay the start of the schedule from the time `valid_from`
        # of the FRBC.SystemDescription.
        self._valid_from_shift = valid_from_shift

    def fill_rate_unit(self) -> str:
        """The unit a rate of change of the fill level is recorded in: the fill_rate_unit given, by default W.

        S2 states a fill rate and a usage forecast as fill level per second.
        Per second, a residential heat buffer's rates are tiny numbers in kWh/s, so they are recorded in a power unit instead (see _per_second_in_fill_rate_unit).
        It is not the power unit: a fill rate is what an actuator delivers into its buffer, which for a heat buffer is not electricity at all.

        A method rather than a property, because S2 Handler's discover() walks dir(self) and getattrs every name during __init__, before a subclass has assigned its own attributes.
        A property would be evaluated there and raise on the energy_unit it reads.
        """
        return self._fill_rate_unit

    def _per_second_in_fill_rate_unit(self, rate):
        """Return a rate of fill level per second, as S2 states it, in the fill-rate unit; a number or a series."""
        return rate * (
            WATTS_PER_ENERGY_UNIT_PER_SECOND[self.energy_unit]
            / WATTS_PER_FILL_RATE_UNIT[self._fill_rate_unit]
        )

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
        # Floor in the moment's own timezone offset, which is how FlexMeasures floors a series posted with that offset.
        # So an hourly step in a timezone that is half an hour off UTC, or a daily step, starts where FlexMeasures' steps start.
        # During the repeated hour of an autumn DST change, a boundary occurs twice.
        # The intended boundary is the later of the two that is not after the moment.
        # Always taking the first pass put the boundary an hour or more too early.
        # A boundary in a spring DST gap (which only a resolution of more than an hour can hit) moves to the end of the gap.
        # Compare as pandas timestamps, which compare instants.
        # Two datetimes in the same local timezone compare by wall time, whichever pass each is on.
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
        """Return the start of a schedule requested for valid_from: shifted and floored to a boundary, but never before now.

        Planning happens on every storage status, while a description may be days old.
        If the start were based on that description alone, every status in a long session would get the same window.
        That window would slide into the past, so all its instructions would already be due, and arrive as one burst.
        Hence the start is never before now.
        If now is past that boundary, the schedule starts at the next boundary, not at the start of the current step.
        A schedule is planned from the fill level reported now, and FlexMeasures takes that as the fill level at the schedule's start.
        So a schedule that started at a step already under way was planned as if the whole step remained.
        Every replan within that step then asked for less power over the rest of it, and delivered less than planned.
        The step under way keeps the instruction it is already following.
        """
        now = self.now()
        start = self.align_to_resolution(valid_from + self._valid_from_shift)
        if start >= now:
            return start
        current = self.align_to_resolution(now)
        return current if current == now else current + self._resolution

    async def _soc_duration(self) -> timedelta:
        """Return the duration to post a reported fill level with.

        If the state-of-charge sensor records instants, the duration is zero.
        On such a sensor, a reading posted over the following minute was taken as a forecast of that minute.
        However, FlexMeasures refuses a single value without duration on a sensor that has a resolution.
        So on any other sensor, or on a sensor that cannot be read, the reading is posted over a minute, as before.
        The sensor is queried until it responds, and the response is kept, because its resolution is fixed when the sensor is set up.
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
        # Use this handler's clock for both the reading time and the belief time (in a co-simulation, that is the simulated time).
        # Without a prior, FlexMeasures used the moment the request reached it as the belief time.
        # A simulated reading was then recorded as known years after it was taken.
        now = self.now()
        # Catch failures here, because otherwise a failed post would exit this method before the planning below.
        # Planning does not depend on this post: a schedule starts from the state of charge in this status, which is sent inline.
        try:
            await self._fm_client.post_measurements(
                self._soc_sensor_id,
                start=now,
                # Scale this like every other fill level this handler records.
                # Otherwise, if fill_level_scale is not 1, the fill level and the band around it would be on different scales.
                values=[status.present_fill_level * self._fill_level_scale],
                unit=self.energy_unit,
                duration=await self._soc_duration(),
                prior=now,
            )
        except REQUEST_FAILURES:
            self._logger.exception(
                "Could not record the state of charge %s on sensor %s for %s; planning from it all the same.",
                status.present_fill_level,
                self._soc_sensor_id,
                now,
            )

        # A schedule starts from the state of charge in this status, so this is where a schedule is requested.
        # Turning that schedule into instructions requires a system description.
        # If none has arrived yet, nothing is planned here.
        # Then the description triggers planning from this status when it arrives.
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
        """Return the last message that arrived in a history, or None if the history is empty."""
        return list(history.values())[-1] if history else None

    def _fill_level_band(
        self,
        start: datetime,
        system_description: FRBCSystemDescription,
        soc_unit: str,
        to_soc_unit: float,
    ) -> tuple[list[dict], list[dict]]:
        """Return the fill levels to keep the storage between over the planned window, as time segments for FlexMeasures.

        The declared fill-level range applies to every step.
        The latest fill-level target profile, held inside that range, applies to the steps it covers.
        The range used to reach FlexMeasures only through the band sensors, and only where a target profile was recorded on them.
        So a storage without those sensors, or past the profile's end, was planned without any band.
        It could then be planned down to empty, and up to the hard upper bound (by default, half again above its range).
        The values are clipped at zero, because FlexMeasures does not accept negative minima.
        """
        steps = int(
            pd.Timedelta(self._schedule_duration) / pd.Timedelta(self._resolution)
        )
        index = pd.date_range(start, periods=steps, freq=self._resolution)
        range_bottom, range_top = get_soc_min_max(
            system_description, self._fill_level_scale
        )
        minima = pd.Series(range_bottom, index=index, dtype=float)
        maxima = pd.Series(range_top, index=index, dtype=float)
        profile = self._latest(self._fill_level_target_profile_history)
        if profile is not None:
            profile_minima, profile_maxima = translate_fill_level_target_profile(
                fill_level_target_profile=profile,
                resolution=self._resolution,
                fill_level_scale=self._fill_level_scale,
            )
            profile_minima, profile_maxima, _ = clip_fill_level_target_profile(
                profile_minima, profile_maxima, range_bottom, range_top
            )
            minima.update(self._on_the_window(profile_minima, index))
            maxima.update(self._on_the_window(profile_maxima, index))
        return (
            self._as_segments(minima.clip(lower=0) * to_soc_unit, soc_unit),
            self._as_segments(maxima.clip(lower=0) * to_soc_unit, soc_unit),
        )

    def _on_the_window(self, profile: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
        """Return a target profile's values at the window's steps, for the steps that the profile covers.

        Steps are matched by the instant at which they start.
        The profile is floored to steps in its own timezone, and the window in the description's timezone.
        If the two timezones are half an hour apart, the two sets of steps never share an instant.
        Matching them by label then silently dropped the whole profile.
        """
        if profile.empty:
            return profile
        covered_until = profile.index[-1] + pd.Timedelta(self._resolution)
        at_steps = profile.reindex(index.union(profile.index)).ffill().reindex(index)
        return at_steps[(index >= profile.index[0]) & (index < covered_until)]

    def _as_segments(self, series: pd.Series, unit: str) -> list[dict]:
        """Convert a series on this handler's resolution into FlexMeasures time segments, one per run of equal values."""
        segments: list[dict] = []
        for moment, value in series.items():
            end = moment + pd.Timedelta(self._resolution)
            if segments and segments[-1]["_value"] == value:
                segments[-1]["end"] = end.isoformat()
                continue
            segments.append(
                {
                    "start": moment.isoformat(),
                    "end": end.isoformat(),
                    "value": f"{value} {unit}",
                    "_value": value,
                }
            )
        for segment in segments:
            del segment["_value"]
        return segments

    def _soc_unit(self) -> tuple[str, float]:
        """Return the unit for sending a state of charge to FlexMeasures, and the factor that converts a fill level into it."""
        if self.energy_unit in ("kWh", "MWh"):
            return self.energy_unit, 1.0
        return "kWh", (
            WATTS_PER_ENERGY_UNIT_PER_SECOND[self.energy_unit]
            / WATTS_PER_ENERGY_UNIT_PER_SECOND["kWh"]
        )

    def _latest_fill_level(self) -> float | None:
        """Return the fill level from the latest storage status, in the resource manager's own units.

        Return None if there is no storage status, or if its fill level is not a number.
        """
        if not self._storage_status_history:
            return None
        fill_level = list(self._storage_status_history.values())[-1].present_fill_level
        return None if math.isnan(fill_level) else fill_level

    async def send_actuator_status(self, status: FRBCActuatorStatus):
        factor = status.operation_mode_factor
        # After a reconnection, an actuator status can arrive before any description.
        # This method needs a description to tell what power the status stands for.
        if not self._system_description_history:
            # Kept instead, and recorded once a description arrives (see handle_system_description).
            self._statuses_awaiting_a_description[str(status.actuator_id)] = status
            self._logger.info(
                "Not recording the power of an FRBC.ActuatorStatus yet: no FRBC.SystemDescription has arrived to tell what its operation mode draws, so it is recorded once one does."
            )
            return
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

        # The element the actuator runs on depends on the fill level; for example, a heat pump derates while its buffer warms up.
        # So use the element that applies at the latest reported fill level, not the element that the mode happens to list first.
        fill_level = self._latest_fill_level()
        if fill_level is not None:
            element = element_for_fill_level(operation_mode.elements, fill_level)
        else:
            # Assume the bottom of the range, which is as good a guess as any, and log below that it is a guess.
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

        # Pick a belief time that supersedes the previous one, instead of colliding with it.
        # FlexMeasures keys a belief on its sensor, its source, the event, and the moment the belief was formed.
        # It floors the event to the sensor's resolution.
        # In practice, two statuses within one resolution step, milliseconds apart, were refused.
        # The error was a duplicate key, with a belief time that had lost its sub-second part.
        # Posting two such beliefs by hand does not reproduce that, so only the server can say what the exact rule is.
        # Either way, belief times a second apart are unambiguous: the later status supersedes the earlier one.
        # That is what a resource manager means when it reports twice within a second.
        # A resource manager sends a status for each instruction it applies, and this handler sends several instructions at once.
        # So this is the ordinary case, not an edge case.
        # The comparison uses the event as FlexMeasures keys it, because two statuses seconds apart still land on the same resolution step.
        # Keep the times per event, because statuses for different steps can interleave within a second.
        # Comparing with the latest status alone then missed the earlier status for the same step.
        event = self.align_to_resolution(start)
        recorded_at = self.now()
        last_recorded_at = self._power_recorded_at.get(event)
        if last_recorded_at is not None and recorded_at - last_recorded_at < timedelta(
            seconds=1
        ):
            recorded_at = last_recorded_at + timedelta(seconds=1)
        self._power_recorded_at[event] = recorded_at
        # Forget steps more than a day older than this one, to keep this bounded. A status that late is not expected.
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
            rate = self._per_second_in_fill_rate_unit(
                (
                    fill_rate.start_of_range
                    + (fill_rate.end_of_range - fill_rate.start_of_range) * factor
                )
                * self._fill_level_scale
            )
            # Catch failures, like for the power above, because this runs in a task that nobody awaits.
            try:
                await self._fm_client.post_sensor_data(
                    self._fill_rate_sensor_id,
                    start=start,
                    prior=self.now(),
                    values=[rate],
                    unit=self.fill_rate_unit(),
                    duration=self._resolution,
                )
            except REQUEST_FAILURES:
                self._logger.exception(
                    "Could not record the fill rate %s on sensor %s for %s.",
                    rate,
                    self._fill_rate_sensor_id,
                    start,
                )

    async def _record_charging_efficiency(
        self,
        system_description: FRBCSystemDescription,
        start: datetime,
        fill_level: float,
    ) -> bool:
        """Record the efficiency at which the actuator converts drawn power into stored energy, over the window a schedule covers.

        FlexMeasures reads a charging efficiency from a sensor, instead of taking a value in the flex model.
        So the efficiency that the operation modes imply is recorded here, by the call that is about to plan with it.
        It is recorded before the schedule is requested, not at the same time.
        A schedule planned against a sensor that holds nothing for its window is planned as if the actuator converted power one to one.

        :param start:      where the schedule starts; the value is recorded from there.
        :param fill_level: the fill level the schedule starts from, in the resource manager's own units.
                           It decides which element of each mode is used.
        :returns: whether the schedule may be planned against the sensor.
                  It may not, if nothing is recorded on the sensor for this window.
        """
        if self._charging_efficiency_sensor_id is None:
            return False

        # One actuator, as everywhere else in this handler.
        actuator = system_description.actuators[0]
        efficiencies: dict[str, float] = {}
        # The constructor already checked that fill_rate_in_watts knows the energy unit.
        # Only use the modes the actuator may be instructed into under normal conditions, because the schedule is planned in those.
        for operation_mode in normal_operation_modes(actuator):
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
                # Below, the value is remembered as recorded, and is then never posted again for this window.
                # So an ingestion that is still pending at the timeout must not count as recorded.
                confirm_ingestion=True,
            )
        except (*REQUEST_FAILURES, NotImplementedError):
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

    async def _is_worth_asking_again(
        self, failure: Exception, attempt: int = 1
    ) -> bool:
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
        # A job that nobody picked up in time, or a server that could not be reached, may both work on the next attempt.
        # The likeliest causes are a scheduling queue without a live worker, and a gateway that is restarting.
        # A timeout has already waited for the job's whole timeout, and a dead queue will not come back two seconds later.
        # So after a timeout, retry only once, not on every allowed attempt.
        if isinstance(failure, JobTimeoutError):
            return attempt == 1
        # The client wraps every HTTP error in a ConnectionError.
        # A 4xx response, such as for an endpoint that an old server lacks, will be the same next time.
        cause = failure.__cause__
        if isinstance(cause, ClientResponseError) and cause.status < 500:
            return False
        if isinstance(
            failure,
            (JobFailedError, ConnectionError, TimeoutError, ClientError),
        ):
            return True
        if any(marker in message for marker in RETRYABLE_TRIGGER_REFUSALS):
            return True
        # FlexMeasures before v0.33.0 reports a failed job only as a bare HTTP 400.
        # That cannot be told apart from a refused trigger, so retry it, within the allowed number of attempts.
        if message != BARE_REFUSAL:
            return False
        # This lookup happens while handling another failure, so a failure of the lookup itself must not replace that one.
        try:
            return not await self._fm_client.server_version_at_least("0.33.0")
        except REQUEST_FAILURES:
            return False

    async def _ask_for_a_schedule(
        self,
        start: datetime,
        flex_context: dict,
        flex_model: dict,
        request_number: int | None = None,
    ) -> dict | list[dict] | None:
        """Ask FlexMeasures for a schedule, and retry if the failure is worth retrying.

        Without retries, one failed scheduling job leaves the storage without an instruction for its window.
        The resource manager is not told, and keeps polling.
        This runs as a task whose exception is logged and dropped, so nothing else notices either.
        That happened in a co-simulation, after a worker crashed inside its solver.
        The affected resource manager polled until its round timed out, in every round, for the rest of the run.

        :param start:          the start of the window to request the schedule for; every attempt requests the same window.
        :param request_number: the planning request this call serves.
                               If a newer request arrives between attempts, no further attempt is made, and None is returned.
                               That way, the newer request is planned sooner.
        :raises JobFailedError:  if the last attempt worth making failed inside the server.
        :raises JobTimeoutError: if the last attempt worth making was not picked up in time.
        :raises ConnectionError: if the server could not be reached on the last attempt worth making.
        :raises ValueError:      if the trigger itself was refused.
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
            except (*REQUEST_FAILURES, NotImplementedError) as failure:
                if (
                    attempt >= self._schedule_attempts
                    or not await self._is_worth_asking_again(failure, attempt)
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
                if request_number is not None and not self._is_latest_request(
                    STORAGE_STATUS_REQUESTS, request_number
                ):
                    self._logger.info(
                        "Not asking again for planning request %s: a newer one arrived, and is planned instead.",
                        request_number,
                    )
                    return None

    def _warn_where_a_fallback_scheduler_answered(
        self, schedule: dict | list[dict], start: datetime
    ) -> None:
        """Log an error if a schedule was planned by a scheduler that this handler did not ask for.

        Until v1.0.0 retired it, FlexMeasures handled an infeasible problem with a fallback scheduler.
        On an older server, the schedule endpoint follows that fallback job instead of reporting the failure.
        Or, if the server sets FLEXMEASURES_FALLBACK_REDIRECT, it redirects to the fallback job, and this client follows the redirect.
        Either way, a schedule that nobody asked for arrives looking like the requested one.
        It is a coarse charging policy, not a plan.
        It ignores the band, the efficiencies and the power bands that this handler sent with its request.

        :param schedule: the response, which names the scheduler that planned it.
        :param start:    the start of the window it was requested for.
        """
        if not isinstance(schedule, dict):
            # A schedule per device, which is only returned for an asset-wide trigger.
            return
        scheduler = str((schedule.get("scheduler_info") or {}).get("scheduler", ""))
        if "fallback" not in scheduler.lower():
            return
        self._logger.error(
            "The schedule for the window starting %s was planned by %s, which is a fallback scheduler: the problem this handler asked about was infeasible. The schedule is a coarse charging policy, not a plan.",
            start,
            scheduler,
        )

    async def trigger_schedule(
        self, system_description_id: str, request_number: int | None = None
    ):
        """Plan one schedule at a time, and when it is this request's turn, only plan if it is the newest request.

        Each storage status is a planning request.
        A request that arrived while a schedule was being planned used to start a second solve in parallel.
        The second result then replaced the first.
        So a resource manager that reported faster than FlexMeasures could plan kept several solves running for nothing.
        Now a request waits for the one in flight.
        It is dropped if a newer request has arrived in the meantime.
        Once the request in flight is out of date, it makes no further attempt after a failed one.
        So a burst of statuses costs at most the attempt in flight, plus one more plan for the newest request.
        That plan starts after the attempt in flight is over, not in parallel.

        :param system_description_id: system description to base the schedule on
        :param request_number:        the planning request this schedule serves.
                                      It is checked when its turn comes, between attempts, and before the schedule is translated and sent.
        """
        async with self._planning:
            if request_number is not None and not self._is_latest_request(
                STORAGE_STATUS_REQUESTS, request_number
            ):
                self._logger.info(
                    "Not planning for request %s: a newer one arrived while the schedule before it was being planned.",
                    request_number,
                )
                return
            await self._plan(system_description_id, request_number)

    async def _plan(
        self, system_description_id: str, request_number: int | None = None
    ):
        """Translates S2 System Description into FM API calls.

        :param system_description_id: system description to base the schedule on
        :param request_number:        the planning request this schedule serves
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
        if not normal_operation_modes(actuator):
            self._logger.warning(
                "Not asking for a schedule: the actuator states no operation mode it may be instructed into under normal conditions."
            )
            return
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
            # Only use the modes the actuator may be instructed into under normal conditions.
            # This is because the actuator may then be instructed into any band that the scheduler plans in.
            for operation_mode in normal_operation_modes(actuator)
            for element in operation_mode.elements
            for power_range in element.power_ranges
        ]

        # The declared range is where a resource manager wants its storage kept, not the limits of what its storage can reach.
        # S2 calls it the range in which the fill level should remain.
        # While the fill level is outside it, S2 lets a resource manager ignore instructions.
        # So the range is sent as a band, and FlexMeasures puts a price on breaching it (see _fill_level_band).
        # Sending the range as the hard soc-min and soc-max made states that a storage really reaches infeasible.
        # Hard bounds are still sent, but wide ones.
        _, range_top = get_soc_min_max(system_description, self._fill_level_scale)
        # Zero below, since a stock cannot be negative and FlexMeasures accepts no lower floor.
        soc_min = 0.0
        # S2 allows a top below zero, and multiplying it would pull it further down.
        # But below, the bound widens to include the state of charge at the start, which is never below zero.
        soc_max = range_top * self._hard_bound_factor
        # A state of charge outside the hard bounds is infeasible before the first step.
        # So the bounds widen to include it.
        # A storage outside its declared range is not an error.
        # A resource manager is free to report one, and a schedule must still be planned from the reported fill level.
        # The bounds only widen upward, because FlexMeasures does not accept a negative soc-min.
        # A storage reported below zero is planned from zero, with a warning, instead of being refused without a schedule.
        if soc_at_start < 0:
            self._logger.warning(
                "The storage is reported at %s, below zero, which FlexMeasures cannot plan from; planning from zero instead.",
                soc_at_start,
            )
            soc_at_start = 0.0
            # Also translate the schedule from zero, so that the instructions follow the plan from where it started.
            fill_level_at_start = 0.0
        soc_max = max(soc_max, soc_at_start)

        # The start of this schedule.
        # It is both the start of the requested window and the start of the window that a charging efficiency must be recorded over.
        start = self.schedule_start(system_description.valid_from)

        # FlexMeasures only accepts a state of charge in kWh or MWh.
        # So a fill level recorded in another energy unit is converted here, at the edge, instead of being refused.
        # The bounds used for translating the schedule below stay in the recorded unit.
        # The usage and the leakage are read from sensors, which FlexMeasures converts itself.
        # The band is converted here.
        soc_unit, to_soc_unit = self._soc_unit()
        flex_model: dict = {
            "soc-unit": soc_unit,
            # TODO: use forecast of the SOC instead
            "soc-at-start": soc_at_start * to_soc_unit,
            "soc-min": soc_min * to_soc_unit,
            "soc-max": soc_max * to_soc_unit,
        }
        # Send the band the resource manager declared within the request itself, instead of referring to a sensor.
        # That way, the band is included whether or not the sensors it is also recorded on exist.
        soc_minima, soc_maxima = self._fill_level_band(
            start, system_description, soc_unit, to_soc_unit
        )
        # FlexMeasures has relax-soc-constraints, which makes this a band, from v0.26.0 on.
        # A server refuses a flex context with a field it does not know, which would leave no schedule at all.
        if await self._fm_client.server_version_at_least("0.26.0"):
            flex_model["soc-minima"] = soc_minima
            flex_model["soc-maxima"] = soc_maxima
        else:
            self._logger.info(
                "Not asking for a schedule against the declared fill-level band: FlexMeasures can relax it from v0.26.0, and this server reports %s.",
                self._fm_client.server_version,
            )
        if self._usage_forecast_sensor_id is not None:
            # Skip this if the resource manager says it provides no usage forecast,
            # because then nothing is recorded on the sensor for the scheduler to read.
            # But use it if the resource manager sent a forecast anyway, since sending one says it provides one, whatever its flag says.
            if (
                system_description.storage.provides_usage_forecast
                or self._usage_forecast_history
            ):
                flex_model["soc-usage"] = [{"sensor": self._usage_forecast_sensor_id}]
            else:
                self._logger.info(
                    "Planning without usage: the resource manager says it provides no usage forecast, and has sent none."
                )
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
            # Only send the bands if they add information to the two capacities above.
            # The capacities say that the actuator can run at any power from minus its production capacity up to its consumption capacity.
            # Bands that together cover exactly that range, without a gap, add nothing.
            # An example is an idle mode at zero next to a mode running from zero up.
            # Each band costs the solver a binary variable per step.
            # Bands with a gap between them do add information.
            # An actuator that runs at 200 W or not at all rules out everything in between.
            # So does an actuator that only ever feeds back.
            # Also, only send the bands to a server that has the field.
            # FlexMeasures added operation-modes in v1.0.0.
            # A server refuses a flex model with a field it does not know, which would leave the resource manager with no schedule at all.
            says_more_than_the_capacities = _merged(power_ranges) != [
                (-production_capacity, consumption_capacity)
            ]
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
        # The band is always sent, and without this setting its minima and maxima are hard bounds.
        # Hard bounds make states that a storage really reaches infeasible.
        # The breach price is left to FlexMeasures' own default.
        # The value of a comfort breach is for the deployment to judge, not for the protocol.
        if "soc-minima" in flex_model:
            flex_context["relax-soc-constraints"] = True
        if self._relax_site_capacity is not None:
            flex_context["relax-site-capacity-constraints"] = self._relax_site_capacity

        schedule = await self._ask_for_a_schedule(
            start, flex_context, flex_model, request_number=request_number
        )
        if schedule is None:
            return
        self._warn_where_a_fallback_scheduler_answered(schedule, start)

        # send_instruction_batch checks this again, but translating a schedule goes through every step of it.
        # That work is wasted on a request that is already out of date.
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
            # Pass what the storage loses by itself and what is drawn from it, as the resource manager last reported.
            # Then the fill level used for each step can fall as well as rise.
            leakage_behaviour=self._latest(self._leakage_behaviour_history),
            # Leave out negative usage, as when the forecast was recorded and planned against (see send_usage_forecast).
            usage_forecast=_without_negative_usage(
                self._latest(self._usage_forecast_history)
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
        # These are recorded for inspection only, because the band that a schedule is planned against is sent inline.
        # So a failure is logged, and the other bound is still recorded, instead of raising in a task that nobody awaits.
        for sensor_id, values in (
            (self._soc_minima_sensor_id, soc_minima),
            (self._soc_maxima_sensor_id, soc_maxima),
        ):
            try:
                await self._fm_client.post_sensor_data(
                    sensor_id=sensor_id,
                    start=start,
                    prior=now,
                    values=values.tolist(),
                    unit=self.energy_unit,
                    duration=duration,
                )
            except REQUEST_FAILURES:
                self._logger.exception(
                    "Could not record a bound of the fill-level target profile on sensor %s from %s.",
                    sensor_id,
                    start,
                )

    async def send_usage_forecast(self, usage_forecast: FRBCUsageForecast):
        """Record what a resource manager expects its storage to be drawn down by."""
        if self._usage_forecast_sensor_id is None:
            self._logger.info(
                "Not recording an FRBC.UsageForecast: no usage-forecast sensor is configured."
            )
            return

        translated = self._per_second_in_fill_rate_unit(
            translate_usage_forecast_to_fm(
                usage_forecast,
                self._resolution,
                strategy="mean",
                fill_level_scale=self._fill_level_scale,
            )
        )
        # A negative usage is a gain that the storage is forced to take, and FlexMeasures cannot relax it.
        # Above the hard ceiling, it makes every schedule for the window infeasible, so the storage gets no instruction at all.
        # S2 allows the negative sign, but no storage that this handler controls fills up by being drawn from.
        # So a negative value is treated as an artefact of the forecast: it is counted, and recorded as no usage.
        negative = translated < 0
        if negative.any():
            self._logger.warning(
                "An FRBC.UsageForecast expects negative usage on %s of %s steps, down to %s; recorded as no usage there, since FlexMeasures would plan it as a gain the storage cannot refuse.",
                int(negative.sum()),
                len(translated),
                float(translated.min()),
            )
            translated = translated.clip(lower=0)

        await self._fm_client.post_sensor_data(
            sensor_id=self._usage_forecast_sensor_id,
            # Aligned, because FlexMeasures reads a series on whole resolution steps, and a
            # resource manager's start_time is whatever moment it sent the message.
            start=self.align_to_resolution(usage_forecast.start_time),
            prior=self.now(),
            values=translated.tolist(),
            unit=self.fill_rate_unit(),
            duration=pd.Timedelta(self._resolution) * len(translated),
        )

    async def send_leakage_behaviour(self, leakage_behaviour: FRBCLeakageBehaviour):
        """Keep a resource manager's leakage behaviour for planning; it is recorded when a schedule is requested.

        It used to be recorded on arrival.
        Then it covered the hours after the message, instead of the window being planned.
        It was also computed at a fill level that nobody had reported yet.
        And once those hours had passed, planning went on without it.
        """
        if self._leakage_behaviour_sensor_id is None:
            self._logger.info(
                "Not recording an FRBC.LeakageBehaviour: no leakage-behaviour sensor is configured."
            )

    async def _record_storage_efficiency(
        self, start: datetime, fill_level: float
    ) -> bool:
        """Record the share of its stock that the storage keeps per step, over the window a schedule covers, from the schedule's start.

        :returns: whether the schedule may be planned against the sensor. It may not, if nothing is recorded on the sensor.
        """
        leakage_behaviour = self._latest(self._leakage_behaviour_history)
        if (
            self._leakage_behaviour_sensor_id is None
            or leakage_behaviour is None
            or not self._system_description_history
        ):
            return False
        # S2 gives leakage as a rate, but FlexMeasures takes a share of the stock.
        # So one share matches the rate at only one fill level; at other fill levels, the loss scales with the stock.
        # That fill level is where the schedule starts, as long as that is at least the middle of the storage's range.
        # From a near-empty start, a share of the start level would let almost nothing remain per step.
        # The loss would then be overstated many times over, as soon as the plan filled the storage.
        # Below the middle, the loss is understated instead, by at most the ratio of the middle to the stock.
        # That is the milder error.
        storage_range = list(self._system_description_history.values())[
            -1
        ].storage.fill_level_range
        middle = (storage_range.start_of_range + storage_range.end_of_range) / 2
        # The share that remains after one step of this handler's resolution.
        # FlexMeasures reads it as one step of the leakage sensor's resolution, so the two resolutions must be equal.
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
                # As for the charging efficiency: the value is remembered below as recorded, so it must really be recorded.
                confirm_ingestion=True,
            )
        except REQUEST_FAILURES:
            self._logger.exception(
                "Not asking for a schedule against a storage efficiency: could not record %s %% on sensor %s for %s.",
                efficiency,
                self._leakage_behaviour_sensor_id,
                start,
            )
            return False
        self._storage_efficiency_recorded = (start, efficiency)
        return True
