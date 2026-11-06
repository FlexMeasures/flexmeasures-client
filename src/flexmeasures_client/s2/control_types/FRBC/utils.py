import bisect
import logging
import math
from datetime import timedelta
from math import isclose
from typing import List, Sequence, TypeVar

import pandas as pd

try:
    from s2python.common import NumberRange
    from s2python.frbc import (
        FRBCInstruction,
        FRBCLeakageBehaviour,
        FRBCLeakageBehaviourElement,
        FRBCOperationMode,
        FRBCSystemDescription,
        FRBCUsageForecast,
    )
    from s2python.frbc.frbc_operation_mode_element import FRBCOperationModeElement
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


from flexmeasures_client.s2.utils import get_unique_id

LOGGER = logging.getLogger(__name__)


def compute_next_fill_level(
    fill_level: float,
    fill_rate: float,
    step: timedelta,
    leakage_behaviour: FRBCLeakageBehaviour | None = None,
    usage_rate: float = 0.0,
) -> float:
    """The fill level one step later.

    This is the unit contract for every rate it takes, and it is the one S2 states:
    each is a change in fill level per second, in the resource manager's own fill-level units.
    A fill rate raises the fill level, while a usage rate and a leakage rate both lower it where they are positive.
    The step is a duration, rather than a number of some unit, so that it cannot be taken in the wrong one.

    :param leakage_behaviour: what the storage loses by itself, from the element that applies at this fill level;
                              none where the resource manager has not said.
    :param usage_rate:        what is drawn from the storage, held over this step.
    """
    leakage_rate = 0.0
    if leakage_behaviour is not None:
        leakage_rate = element_for_fill_level(
            leakage_behaviour.elements, fill_level
        ).leakage_rate
    return fill_level + (fill_rate - usage_rate - leakage_rate) * step.total_seconds()


#: How close two factors are to count as the same instruction.
#: It absorbs the float noise of converting a schedule from MW to W, which is not a new instruction.
FACTOR_TOLERANCE = 1e-6


class UsageProfile:
    """What a usage forecast expects, per second, at any moment: piecewise constant, looked up by bisection.

    A forecast is a run of elements, each lasting its own duration from where the one before it ended.
    Outside that run, nothing is forecast, and nothing is assumed: the rate is zero.
    Built once per schedule, since a forecast may hold hundreds of elements and a schedule asks about it often.
    """

    def __init__(
        self, usage_forecast: FRBCUsageForecast | None = None, rate: float | None = None
    ):
        #: The moments the rate changes at, and the rate from each of them on; one constant rate has none.
        self._changes: list[pd.Timestamp] = []
        self._rates: list[float] = []
        self._before = 0.0 if rate is None else rate
        if usage_forecast is None:
            return
        moment = pd.Timestamp(usage_forecast.start_time)
        for element in usage_forecast.elements:
            self._changes.append(moment)
            self._rates.append(element.usage_rate_expected)
            moment = moment + timedelta(milliseconds=element.duration.root)
        self._changes.append(moment)
        self._rates.append(0.0)

    def rate_at(self, moment: pd.Timestamp) -> float:
        index = bisect.bisect_right(self._changes, moment) - 1
        return self._before if index < 0 else self._rates[index]

    def next_change_after(self, moment: pd.Timestamp) -> pd.Timestamp | None:
        index = bisect.bisect_right(self._changes, moment)
        return self._changes[index] if index < len(self._changes) else None


def usage_rate_at(
    usage_forecast: FRBCUsageForecast | None, moment: pd.Timestamp
) -> float:
    """The usage rate a forecast expects at a moment, per second, as S2 states it (see UsageProfile)."""
    return UsageProfile(usage_forecast).rate_at(moment)


def _sides(elements, fill_level: float):
    """The element that applies just below a fill level, and the one just above it.

    Inside an element both are that element; at a boundary two elements share, they are the two neighbours.
    """
    above = element_for_fill_level(elements, fill_level)
    below_candidates = [
        e
        for e in elements
        if e.fill_level_range.start_of_range
        < fill_level
        <= e.fill_level_range.end_of_range
    ]
    below = (
        max(below_candidates, key=lambda e: e.fill_level_range.start_of_range)
        if below_candidates
        else above
    )
    return below, above


def follow_operation_mode(
    fill_level: float,
    operation_mode: FRBCOperationMode,
    factor: float,
    start: pd.Timestamp,
    step: timedelta,
    leakage_behaviour: FRBCLeakageBehaviour | None = None,
    usage: UsageProfile | None = None,
    bounds: tuple[float, float] | None = None,
) -> float:
    """The fill level after following one operation mode at one factor for a step, as a resource manager does.

    An instruction fixes the mode and the factor, not the element:
    within the step the fill level can pass from one element into another, of the mode or of the leakage behaviour,
    and the usage a forecast expects can change.
    Every rate is constant between such moments, so the step is followed exactly, from one of them to the next.
    A level that reaches a boundary its far side would push it back from, as where a heat pump cannot heat above it,
    stays at that boundary, as a real storage does, rather than flipping across it.
    So does a level that reaches one of the bounds.

    :param usage:  what is drawn from the storage, per second; none where nothing is known.
    :param bounds: the fill levels the storage is held between.
    """
    usage = usage or UsageProfile()
    leakage_elements = leakage_behaviour.elements if leakage_behaviour else []
    lowest, highest = bounds if bounds is not None else (-math.inf, math.inf)
    boundaries = sorted(
        {
            edge
            for e in list(operation_mode.elements) + list(leakage_elements)
            for edge in (
                e.fill_level_range.start_of_range,
                e.fill_level_range.end_of_range,
            )
        }
        | ({lowest, highest} - {-math.inf, math.inf})
    )

    def net_rate(mode_element, leakage_element, moment) -> float:
        low, high = (
            mode_element.fill_rate.start_of_range,
            mode_element.fill_rate.end_of_range,
        )
        leakage = leakage_element.leakage_rate if leakage_element is not None else 0.0
        return low + (high - low) * factor - usage.rate_at(moment) - leakage

    moment, end = pd.Timestamp(start), pd.Timestamp(start) + step
    fill_level = min(max(fill_level, lowest), highest)
    while moment < end:
        mode_below, mode_above = _sides(operation_mode.elements, fill_level)
        leak_below, leak_above = (
            _sides(leakage_elements, fill_level) if leakage_elements else (None, None)
        )
        rising = net_rate(mode_above, leak_above, moment)
        falling = net_rate(mode_below, leak_below, moment)
        if rising > 0 and fill_level < highest:
            rate = rising
        elif falling < 0 and fill_level > lowest:
            rate = falling
        else:
            rate = 0.0  # held where it is, at a boundary or a bound, until something changes
        horizon = end
        change = usage.next_change_after(moment)
        if change is not None and change < horizon:
            horizon = change
        target = None
        if rate > 0:
            target = next((b for b in boundaries if b > fill_level), None)
        elif rate < 0:
            target = next((b for b in reversed(boundaries) if b < fill_level), None)
        if target is not None:
            # Compared in seconds first: a slow rate can put a boundary further off than a timedelta can say.
            seconds_to_target = (target - fill_level) / rate
            if seconds_to_target < (horizon - moment).total_seconds():
                fill_level = target
                moment = moment + pd.Timedelta(seconds=seconds_to_target)
                continue
        fill_level += rate * (horizon - moment).total_seconds()
        moment = horizon
    return min(max(fill_level, lowest), highest)


def normal_operation_modes(actuator) -> list[FRBCOperationMode]:
    """The operation modes an actuator may be instructed into under normal conditions.

    S2 marks a mode abnormal_condition_only where it is reserved for an abnormal condition, such as an emergency boost,
    and a resource manager refuses an instruction into one that is not marked abnormal itself,
    which every instruction a schedule is turned into is not.
    So such a mode is neither planned in nor instructed into.
    """
    return [
        mode for mode in actuator.operation_modes if not mode.abnormal_condition_only
    ]


def fm_schedule_to_instructions(
    schedule: pd.DataFrame | dict,
    system_description: FRBCSystemDescription,
    initial_fill_level: float,
    logger: logging.Logger = LOGGER,
    fill_level_bounds: tuple[float, float] | None = None,
    leakage_behaviour: FRBCLeakageBehaviour | None = None,
    usage_forecast: FRBCUsageForecast | None = None,
) -> List[FRBCInstruction]:
    """Turn a FlexMeasures schedule into the instructions that follow it.

    Each step is answered with the operation mode, and the factor within it, that draws the power the schedule asks for
    at the fill level the storage is then at, so a running fill level is kept alongside, under the contract of compute_next_fill_level.
    An instruction is sent where that answer changes, which can happen at a constant power too,
    where the fill level passes from one element of a mode into another.

    :param schedule:          a schedule as FlexMeasures returns it, or a frame indexed by time with a "schedule" column.
                              A frame may also carry a "usage_forecast" column, per second as S2 states it,
                              which then takes the place of usage_forecast.
    :param fill_level_bounds: the fill levels this conversion holds its own running fill level between,
                              in the resource manager's units.
                              These have to be the hard bounds the schedule was planned against,
                              or a step is answered with the operation mode for a fill level the scheduler never planned.
                              Without them the declared fill-level range is used,
                              which is right only for a caller that asked for a schedule against that same range.
    :param leakage_behaviour: what the storage loses by itself, as the resource manager last said.
    :param usage_forecast:    what is drawn from the storage, as the resource manager last forecast it.

    The step is the schedule's own: a schedule from FlexMeasures states it as its duration over its number of values,
    which is the resolution of the sensor it was asked for, whatever resolution the caller plans on.
    """

    if fill_level_bounds is not None and fill_level_bounds[0] > fill_level_bounds[1]:
        # A mistake in the caller, which computes these, rather than anything a resource manager sent:
        # held between a lower bound above its upper one, the running fill level would sit at the lower one,
        # and every step would be answered with the operation mode for a fill level the storage is never at.
        raise ValueError(
            f"fill_level_bounds runs from its lower bound to its upper one, but {fill_level_bounds} was given."
        )

    if len(schedule) == 0 or len(system_description.actuators) == 0:
        return []

    if isinstance(schedule, dict):
        values = schedule["values"]
        if len(values) == 0:
            return []
        start = pd.Timestamp(schedule["start"])
        step = pd.Timedelta(schedule["duration"]) / len(values)
        idx = pd.date_range(start=start, periods=len(values), freq=step)
        schedule = pd.Series(values, index=idx, name="schedule").to_frame()
    else:
        # A frame of one row has no later step for its fill level to matter to.
        step = (
            pd.Timedelta(schedule.index[1] - schedule.index[0])
            if len(schedule) > 1
            else pd.Timedelta(0)
        )

    if len(system_description.actuators) != 1:
        raise NotImplementedError(
            f"This CEM only supports 1 actuator but"
            f"{len(system_description.actuators)} were provided"
        )
    actuator = system_description.actuators[0]
    operation_modes: list[FRBCOperationMode] = normal_operation_modes(actuator)
    if not operation_modes:
        raise ValueError(
            "The actuator states no operation mode it may be instructed into under normal conditions."
        )
    modes_by_id = {str(mode.id): mode for mode in operation_modes}

    # Held between the hard bounds the schedule was planned against,
    # so that each step is answered with the operation mode for the fill level the scheduler planned.
    # Falling back to the declared range keeps every other caller as it was.
    if fill_level_bounds is None:
        fill_level_range: NumberRange = system_description.storage.fill_level_range
        fill_level_min = fill_level_range.start_of_range
        fill_level_max = fill_level_range.end_of_range
    else:
        fill_level_min, fill_level_max = fill_level_bounds

    instructions: list[FRBCInstruction] = []
    last_instructed: tuple[str, float] | None = None
    forecast_usage = UsageProfile(usage_forecast)
    fill_level = initial_fill_level
    has_usage_column = "usage_forecast" in schedule.columns

    for timestamp, row in schedule.iterrows():
        power = row["schedule"]

        # The mode and factor for this power at this fill level, every step,
        # since the element that applies can change while the power does not.
        results = [
            (om, *power_to_fill_rate_with_metrics(om, power, fill_level))
            for om in operation_modes
        ]

        # Step 1: minimize fill-level penalty (primary)
        min_fill_penalty = min(r[2] for r in results)
        fill_level_candidates = [r for r in results if r[2] == min_fill_penalty]

        # Step 2: among those, minimize power penalty (secondary)
        min_power_penalty = min(r[3] for r in fill_level_candidates)
        power_candidates = [
            r for r in fill_level_candidates if r[3] == min_power_penalty
        ]

        # Step 3: among those, pick the highest efficiency
        best = max(power_candidates, key=lambda r: r[4])

        (
            best_operation_mode,
            best_fill_rate,
            best_fill_penalty,
            best_power_penalty,
            best_efficiency,
            best_element,
            operation_mode_factor,
        ) = best

        logger.debug(
            "Selected FRBC operation mode '%s' for power=%.3f, fill_level=%.3f: "
            "factor=%.6f, fill_rate=%.6f, fill_penalty=%.6f, power_penalty=%.6f, efficiency=%.6f. Details: %s",
            om_label(best_operation_mode),
            power,
            fill_level,
            operation_mode_factor,
            best_fill_rate,
            best_fill_penalty,
            best_power_penalty,
            best_efficiency,
            explain_choice(results, best),
        )

        # The factor is the one that draws the power asked for, taken from the power range itself.
        # It used to be worked back out of the fill rate instead,
        # which fails wherever the fill-rate range is a single value and the power range is not:
        # asked for 1200 W from a mode running 800 to 1200 W at one fill rate, that asked for 800 W.
        answer = (str(best_operation_mode.id), operation_mode_factor)
        if last_instructed is None or not (
            answer[0] == last_instructed[0]
            and isclose(answer[1], last_instructed[1], abs_tol=FACTOR_TOLERANCE)
        ):
            instruction = FRBCInstruction(
                message_id=get_unique_id(),
                id=get_unique_id(),
                actuator_id=actuator.id,
                operation_mode=best_operation_mode.id,
                operation_mode_factor=operation_mode_factor,
                execution_time=timestamp,
                abnormal_condition=False,
            )
            logger.info(
                f"Instruction created: at {timestamp} set {actuator.diagnostic_label if isinstance(actuator.diagnostic_label, str) else actuator} to {best_operation_mode.diagnostic_label if isinstance(best_operation_mode.diagnostic_label, str) else best_operation_mode} with factor {operation_mode_factor}"
            )
            instructions.append(instruction)
            last_instructed = answer

        # A gap in a usage column, as FlexMeasures returns for a moment it holds nothing for, is no usage known, not NaN:
        # a NaN fill level would be held at the top of the bounds, and answered there from then on.
        usage = forecast_usage
        if has_usage_column:
            usage = UsageProfile(
                rate=(
                    0.0
                    if pd.isna(row["usage_forecast"])
                    else float(row["usage_forecast"])
                )
            )
        fill_level = follow_operation_mode(
            fill_level,
            best_operation_mode,
            operation_mode_factor,
            pd.Timestamp(timestamp),
            step,
            leakage_behaviour=leakage_behaviour,
            usage=usage,
            bounds=(fill_level_min, fill_level_max),
        )

    if logger.isEnabledFor(logging.DEBUG):
        # The whole batch in one line, by mode name, which the per-step lines above do not give.
        logger.debug(
            "Instructions: %s",
            [
                (
                    instruction.execution_time.isoformat(),
                    om_label(modes_by_id[str(instruction.operation_mode)]),
                    round(instruction.operation_mode_factor, 6),
                )
                for instruction in instructions
            ],
        )
    return instructions


def get_soc_min_max(
    system_description: FRBCSystemDescription, fill_level_scale: float = 1
) -> tuple[float, float]:
    """From the system description, get the minimum and maximum State of Charge for the flex-model."""

    fill_level_range: NumberRange = system_description.storage.fill_level_range

    # get SOC Max and Min to be sent on the Flex Model
    soc_min = fill_level_range.start_of_range * fill_level_scale
    soc_max = fill_level_range.end_of_range * fill_level_scale

    return soc_min, soc_max


#: What one unit of energy per second is in watts, for the units a fill level can be recorded in.
#: A joule per second is a watt by definition,
#: and an hour's worth of a watt is 3600 times as much energy as a watt-second.
WATTS_PER_ENERGY_UNIT_PER_SECOND = {
    "J": 1.0,
    "kJ": 1e3,
    "MJ": 1e6,
    "Wh": 3600.0,
    "kWh": 3.6e6,
    "MWh": 3.6e9,
}


def fill_rate_in_watts(fill_rate: float, energy_unit: str) -> float:
    """A fill rate, stated per second in the unit a fill level is recorded in, as watts.

    S2 states a fill rate as fill level per second, so what it is in watts depends on the unit the fill level itself is in:
    a kilowatt hour per second is 3.6 million watts.
    The conversion is needed wherever a fill rate is compared against a power value,
    which S2 always states in watts.

    :raises NotImplementedError: for an energy unit this does not know,
                                 rather than returning a number that is wrong by a factor of thousands.
    """
    try:
        return fill_rate * WATTS_PER_ENERGY_UNIT_PER_SECOND[energy_unit]
    except KeyError:
        raise NotImplementedError(
            f"A fill rate in {energy_unit}/s cannot be stated in watts: "
            f"the energy units this supports are {', '.join(WATTS_PER_ENERGY_UNIT_PER_SECOND)}."
        )


def operation_mode_conversion_efficiency(
    operation_mode: FRBCOperationMode,
    fill_level_scale: float,
    energy_unit: str,
    fill_level: float,
) -> float | None:
    """How much energy an operation mode puts into the storage for each unit of power it draws, at a fill level.

    An operation mode states a fill rate and a power range,
    so the ratio between the two is what the mode converts drawn power into stored energy at:
    a heat pump's coefficient of performance, or a battery's charging efficiency.
    Both sides are taken in watts, since FlexMeasures asks for a charging efficiency as a dimensionless ratio,
    and for a thermal actuator that ratio is well above one.

    A mode may convert differently at different fill levels, as a heat pump does when its buffer warms,
    and FlexMeasures takes one charging efficiency for a whole schedule.
    The element that applies at the fill level the schedule starts from answers,
    since the start of a schedule is the part that runs before the next storage status asks for a new one.
    Not the lowest element over the whole range:
    that overstates what the actuator needs to fill the storage, so the plan overshoots its upper band and buys more than it uses,
    and against a usage the plan cannot relax it can turn a problem the actuator can meet into one the scheduler finds infeasible.

    The ratio is taken at the top of that element's range, rather than as the slope over it.
    An actuator may well declare degenerate ranges,
    where a mode runs at one power and one fill rate only (the heaters in our own smoke tests state on as [883.7, 883.7] W),
    and a slope is zero over such a range while the ratio is the efficiency the mode really runs at.

    :param fill_level_scale: converts a fill level, and so a fill rate,
                             from the resource manager's own units into the unit it is recorded in.
    :param energy_unit:      the unit a fill level is recorded in, which the fill rate is per second of.
    :param fill_level:       where the schedule starts, in the resource manager's own units, as the elements' ranges are.
    :returns: the efficiency, or None where the element that applies converts no drawn power into stored energy.
              It converts nothing where it draws no power, as an idle mode does,
              where it only feeds power back, which FlexMeasures takes as a separate discharging efficiency,
              or where it draws power while the storage does not fill, as a standby or circulation mode does:
              no single ratio states that, and a ratio of zero would tell the scheduler the storage cannot be filled at all.
    """
    element = element_for_fill_level(operation_mode.elements, fill_level)
    # todo: an element states one power range per commodity quantity,
    # and no distinction is made between them here,
    # as the rest of this handler does not make one either (see power_to_fill_rate_with_metrics).
    power = element.power_ranges[0].end_of_range
    fill_rate = element.fill_rate.end_of_range * fill_level_scale
    if power <= 0 or fill_rate <= 0:
        return None
    return fill_rate_in_watts(fill_rate, energy_unit) / power


def clip_fill_level_target_profile(
    soc_minima: pd.Series,
    soc_maxima: pd.Series,
    range_bottom: float,
    range_top: float,
) -> tuple[pd.Series, pd.Series, int]:
    """Hold a translated fill-level target profile inside the declared fill-level range.

    A resource manager declares a range its storage may be in, and separately asks for a target
    profile within it. The two do not have to agree: a night setback can aim below the range,
    and a profile sent before a description that narrowed the range can fall outside it.
    Where they disagree, the range wins, since that is what the resource manager says its
    storage can actually do, so minima are raised to the bottom of it and maxima lowered to
    the top.

    That alone can leave a minimum above its maximum, where the whole target band lies outside
    the range. Those two bounds collapse to their midpoint, which keeps minima at or below
    maxima at every step, as whatever consumes them will expect. The midpoint of a crossed pair
    lies outside the range itself, though, since it sits between a bound that was clipped and
    one that was not, so it is held inside the range too: a band wholly below the range collapses
    onto the bottom of it, and one wholly above onto the top.

    :returns: the clipped minima and maxima, and on how many steps the two had crossed,
              so that a caller can report it.
    """
    soc_minima = soc_minima.clip(lower=range_bottom)
    soc_maxima = soc_maxima.clip(upper=range_top)
    crossed = soc_maxima < soc_minima
    number_crossed = int(crossed.sum())
    if number_crossed:
        midpoint = ((soc_minima + soc_maxima) / 2).clip(
            lower=range_bottom, upper=range_top
        )
        soc_minima = soc_minima.mask(crossed, midpoint)
        soc_maxima = soc_maxima.mask(crossed, midpoint)
    return soc_minima, soc_maxima, number_crossed


def power_to_fill_rate_with_metrics(
    operation_mode: FRBCOperationMode,
    power: float,
    fill_level: float,
) -> tuple[float, float, float, float, FRBCOperationModeElement, float]:
    """
    Evaluate an operation mode for given power and fill level.

    Returns:
        fill_rate: float
        fill_level_penalty: float
        power_penalty: float
        efficiency: float
        element: FRBCOperationModeElement that produced fill_rate
        factor: the operation mode factor that draws this power, which is what an instruction asks for
    """
    # --- 1. Select the element that applies at this fill level ---
    best_element = element_for_fill_level(operation_mode.elements, fill_level)
    fill_penalty = clamp_distance(
        fill_level,
        best_element.fill_level_range.start_of_range,
        best_element.fill_level_range.end_of_range,
    )

    # --- 2. Select power range with minimal power penalty ---
    power_range, power_penalty = min(
        (
            (pr, clamp_distance(power, pr.start_of_range, pr.end_of_range))
            for pr in best_element.power_ranges
        ),
        key=lambda x: x[1],
    )

    # --- 3. Compute factor (clamped to [0,1]) ---
    pr_start = power_range.start_of_range
    pr_end = power_range.end_of_range

    if pr_end == pr_start:
        # Every factor draws this one power, so the power cannot say which factor is meant.
        # The top of the fill-rate range is asked for, which is where operation_mode_conversion_efficiency declares the efficiency FlexMeasures plans with, so the plan and the instruction agree.
        # Where the fill-rate range is a single value too, the factor makes no difference and stays at zero.
        fr_start = best_element.fill_rate.start_of_range
        fr_end = best_element.fill_rate.end_of_range
        factor = 0.0 if fr_end == fr_start else 1.0
        efficiency = 0.0
    else:
        factor = (power - pr_start) / (pr_end - pr_start)
        factor = max(0.0, min(1.0, factor))
        efficiency = (
            best_element.fill_rate.end_of_range - best_element.fill_rate.start_of_range
        ) / (pr_end - pr_start)

    # --- 4. Interpolate fill rate ---
    fr_start = best_element.fill_rate.start_of_range
    fr_end = best_element.fill_rate.end_of_range
    fill_rate = fr_start + factor * (fr_end - fr_start)

    return fill_rate, fill_penalty, power_penalty, efficiency, best_element, factor


def clamp_distance(value: float, start: float, end: float) -> float:
    if value < start:
        return start - value
    if value > end:
        return value - end
    return 0.0


ElementWithFillLevelRange = TypeVar(
    "ElementWithFillLevelRange", FRBCOperationModeElement, FRBCLeakageBehaviourElement
)


def element_for_fill_level(
    elements: Sequence[ElementWithFillLevelRange], fill_level: float
) -> ElementWithFillLevelRange:
    """The element that applies at a fill level, whatever order the elements were listed in.

    S2 lets the elements of an operation mode, or of a leakage behaviour, cover the fill-level range in pieces, in any order.
    s2python checks that an operation mode's elements are contiguous, on a sorted copy, and leaves the list as it was sent;
    it checks nothing of the kind for a leakage behaviour, whose elements may leave gaps or overlap.

    Two neighbouring elements share a boundary, which belongs to the upper of the two:
    of every element whose range holds the fill level, the one starting highest applies, and of two starting together, the one reaching higher.
    A range that is a single point therefore applies only at the very top.
    The standard says a range starts below where it ends, so such a range is outside it, though s2python lets it through.
    Two elements with the same range are ambiguous, which overlapping elements always are, and the first listed answers.

    A fill level outside the range the elements cover is answered as the nearest end of that range is,
    since a storage may be outside its declared range, and the answer should not jump as it crosses that end.
    A fill level in a gap between elements is answered by the nearer, and on a tie the lower.

    :raises ValueError: for a fill level that is not a number, which no element applies at.
    """
    if math.isnan(fill_level):
        raise ValueError("No element applies at a fill level that is not a number.")

    def bounds(e: ElementWithFillLevelRange) -> tuple[float, float]:
        return e.fill_level_range.start_of_range, e.fill_level_range.end_of_range

    covered_from = min(bounds(e)[0] for e in elements)
    covered_to = max(bounds(e)[1] for e in elements)
    clamped = min(max(fill_level, covered_from), covered_to)
    holding = [e for e in elements if bounds(e)[0] <= clamped <= bounds(e)[1]]
    if holding:
        return max(holding, key=bounds)
    return min(
        elements, key=lambda e: (clamp_distance(clamped, *bounds(e)), *bounds(e))
    )


def om_label(operation_mode: FRBCOperationMode) -> str:
    label = getattr(operation_mode, "diagnostic_label", None)
    return label if isinstance(label, str) else str(operation_mode.id)


def explain_choice(
    results: list[
        tuple[
            FRBCOperationMode,
            float,
            float,
            float,
            float,
            FRBCOperationModeElement,
            float,
        ]
    ],
    best: tuple[
        FRBCOperationMode, float, float, float, float, FRBCOperationModeElement, float
    ],
) -> str:
    """
    Generate a human-readable explanation for why a mode was selected or rejected.
    """
    (
        best_om,
        best_fill_rate,
        best_fill_penalty,
        best_power_penalty,
        best_efficiency,
        best_element,
        _best_factor,
    ) = best

    lines: list[str] = []

    for om, fr, fill_pen, power_pen, eff, element, _factor in results:
        label = om_label(om)
        element_label = getattr(element, "id", "unknown")

        if om is best_om:
            lines.append(
                f"{label} (element={element_label}): selected "
                f"(fill_penalty={fill_pen:.6g}, power_penalty={power_pen:.6g}, efficiency={eff:.6g})"
            )
        else:
            if fill_pen > best_fill_penalty:
                reason = f"higher fill-level penalty ({fill_pen:.6g} > {best_fill_penalty:.6g})"
            elif power_pen > best_power_penalty:
                reason = (
                    f"higher power penalty ({power_pen:.6g} > {best_power_penalty:.6g})"
                )
            else:
                reason = f"lower efficiency ({eff:.6g} < {best_efficiency:.6g})"

            lines.append(f"{label} (element={element_label}): rejected due to {reason}")

    return "; ".join(lines)
