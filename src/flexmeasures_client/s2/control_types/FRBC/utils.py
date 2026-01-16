import logging
from datetime import timedelta
from math import isclose
from typing import List

import numpy as np
import pandas as pd

try:
    from s2python.common import NumberRange
    from s2python.frbc import (
        FRBCInstruction,
        FRBCLeakageBehaviour,
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
) -> List[FRBCInstruction]:

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

        # Clamp to fill level limits
        fill_level_range: NumberRange = system_description.storage.fill_level_range
        fill_level_min = fill_level_range.start_of_range
        fill_level_max = fill_level_range.end_of_range
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
    elements = operation_mode.elements

    # --- 1. Select element with minimal fill-level penalty ---
    best_element, fill_penalty = min(
        (
            (
                e,
                clamp_distance(
                    fill_level,
                    e.fill_level_range.start_of_range,
                    e.fill_level_range.end_of_range,
                ),
            )
            for e in elements
        ),
        key=lambda x: x[1],
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


def om_label(operation_mode: FRBCOperationMode) -> str:
    label = getattr(operation_mode, "diagnostic_label", None)
    return label if isinstance(label, str) else operation_mode.id


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
