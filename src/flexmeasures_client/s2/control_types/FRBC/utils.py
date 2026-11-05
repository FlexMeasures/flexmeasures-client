import logging
import math
from datetime import timedelta
from math import isclose
from typing import List, Sequence, TypeVar

import numpy as np
import pandas as pd

try:
    from s2python.common import NumberRange
    from s2python.frbc import (
        FRBCInstruction,
        FRBCLeakageBehaviour,
        FRBCLeakageBehaviourElement,
        FRBCOperationMode,
        FRBCSystemDescription,
    )
    from s2python.frbc.frbc_operation_mode_element import FRBCOperationModeElement
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


from flexmeasures_client.s2.utils import get_unique_id

LOGGER = logging.getLogger(__name__)


def op_mode_compute_factor(
    op_mode_elem: FRBCOperationModeElement,
    fill_rate: float,
    logger: logging.Logger = LOGGER,
) -> float:
    """Compute the operation mode factor for a given fill rate."""
    start_fill_rate = op_mode_elem.fill_rate.start_of_range
    end_fill_rate = op_mode_elem.fill_rate.end_of_range
    delta_fill_rate = end_fill_rate - start_fill_rate

    fill_rate = max(fill_rate, start_fill_rate)
    fill_rate = min(fill_rate, end_fill_rate)

    # Case that start_fill_rate == end_fill_rate
    if np.isclose(delta_fill_rate, 0):
        return 0

    omf = (fill_rate - start_fill_rate) / delta_fill_rate
    if omf < 0 or omf > 1:
        logger.error(
            f"Invalid operation mode factor {omf} computed from fill_rate {fill_rate}"
        )
    return omf


def compute_next_fill_level(
    fill_level: float,
    fill_rate: float,
    leakage_behaviour: FRBCLeakageBehaviour | None,
    deltaT: float,
    usage: float = 0.0,
) -> float:
    """
    Compute the next fill level of a storage system over a time step.

    Parameters
    ----------
    fill_level
        Current fill level in system units (e.g., kWh, °C, height)
    fill_rate
        Fill rate chosen from the operation mode (units per hour)
    leakage_behaviour
        FRBC LeakageBehaviour; may be None
    deltaT
        Time step in hours
    usage
        Consumption/usage over the time step (in system units per hour)

    Returns
    -------
    next_fill_level : float
        Fill level after applying fill rate, leakage, and usage
    """

    # --- Apply fill rate over deltaT ---
    delta_fill = fill_rate * deltaT

    # --- Apply leakage over deltaT ---
    total_leakage = 0.0
    if leakage_behaviour and leakage_behaviour.elements:
        # Sum contributions from all relevant elements (or pick one based on fill level)
        element = next(
            (
                e
                for e in leakage_behaviour.elements
                if e.fill_level_range.start_of_range
                <= fill_level
                <= e.fill_level_range.end_of_range
            ),
            None,
        )
        if element is None:
            # Take closest element
            element = min(
                leakage_behaviour.elements,
                key=lambda e: max(
                    0.0,
                    e.fill_level_range.start_of_range - fill_level,
                    fill_level - e.fill_level_range.end_of_range,
                ),
            )

        # leakage_rate_per_second is in system units lost per second
        total_leakage = (
            getattr(element, "leakage_rate_per_second", 0.0) * deltaT * 3600
        )  # convert hours → seconds

    # --- Apply usage ---
    total_usage = usage * deltaT

    # --- Next fill level ---
    next_fill_level = fill_level + delta_fill - total_leakage - total_usage
    return next_fill_level


def fm_schedule_to_instructions(
    schedule: pd.DataFrame | dict,
    system_description: FRBCSystemDescription,
    initial_fill_level: float,
    logger: logging.Logger = LOGGER,
    fill_level_bounds: tuple[float, float] | None = None,
) -> List[FRBCInstruction]:
    """Turn a FlexMeasures schedule into the instructions that follow it.

    :param fill_level_bounds: the fill levels this conversion holds its own running fill level between,
                              in the resource manager's units.
                              These have to be the hard bounds the schedule was planned against,
                              or a step is answered with the operation mode for a fill level the scheduler never planned.
                              Without them the declared fill-level range is used,
                              which is right only for a caller that asked for a schedule against that same range.
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
        start = pd.Timestamp(schedule["start"])
        schedule_duration = pd.Timedelta(schedule["duration"])
        idx = pd.DatetimeIndex(
            pd.date_range(
                start=start,
                end=start + schedule_duration - timedelta(minutes=15),
                freq="15min",
            )
        )
        schedule = pd.Series(schedule["values"], index=idx, name="schedule").to_frame()

    previous_power = None
    instructions = []

    actuator = system_description.actuators[0]

    if len(system_description.actuators) != 1:
        raise NotImplementedError(
            f"This CEM only supports 1 actuator but"
            f"{len(system_description.actuators)} were provided"
        )

    operation_modes: list[FRBCOperationMode] = actuator.operation_modes

    fill_level = initial_fill_level

    deltaT = timedelta(minutes=15) / timedelta(hours=1)

    for timestamp, row in schedule.iterrows():
        power = row["schedule"]
        usage = row.get("usage_forecast", 0)

        if previous_power is None or not isclose(previous_power, power):
            # Convert from power to fill rate
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
            ) = best

            logger.debug(
                "Selected FRBC operation mode '%s' (element='%s') for power=%.3f, fill_level=%.3f: "
                "fill_rate=%.6f, fill_penalty=%.6f, power_penalty=%.6f, efficiency=%.6f. Details: %s",
                om_label(best_operation_mode),
                getattr(best_element, "id", "unknown"),
                power,
                fill_level,
                best_fill_rate,
                best_fill_penalty,
                best_power_penalty,
                best_efficiency,
                explain_choice(results, best),
            )

            operation_mode_factor = op_mode_compute_factor(
                best_element, fill_rate=best_fill_rate, logger=logger
            )

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

        # Update fill level
        fill_level = compute_next_fill_level(
            fill_level=fill_level,
            fill_rate=best_fill_rate,  # S2-derived fill rate
            leakage_behaviour=getattr(actuator, "leakage_behaviour", None),
            deltaT=deltaT,
            usage=usage,
        )

        # Hold the running fill level between the hard bounds the schedule was planned against,
        # so that each step is answered with the operation mode for the fill level the scheduler planned.
        # Falling back to the declared range keeps every other caller as it was.
        if fill_level_bounds is None:
            fill_level_range: NumberRange = system_description.storage.fill_level_range
            fill_level_min = fill_level_range.start_of_range
            fill_level_max = fill_level_range.end_of_range
        else:
            fill_level_min, fill_level_max = fill_level_bounds
        fill_level = max(fill_level_min, min(fill_level_max, fill_level))

        previous_power = power

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
) -> tuple[float, float, float, float, FRBCOperationModeElement]:
    """
    Evaluate an operation mode for given power and fill level.

    Returns:
        fill_rate: float
        fill_level_penalty: float
        power_penalty: float
        efficiency: float
        element: FRBCOperationModeElement that produced fill_rate
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
        factor = 0.0
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

    return fill_rate, fill_penalty, power_penalty, efficiency, best_element


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
        tuple[FRBCOperationMode, float, float, float, float, FRBCOperationModeElement]
    ],
    best: tuple[
        FRBCOperationMode, float, float, float, float, FRBCOperationModeElement
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
    ) = best

    lines: list[str] = []

    for om, fr, fill_pen, power_pen, eff, element in results:
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
