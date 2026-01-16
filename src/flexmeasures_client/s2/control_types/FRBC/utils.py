import logging
import math
from datetime import timedelta
from math import isclose
from typing import List

import numpy as np
import pandas as pd

try:
    from s2python.common import NumberRange
    from s2python.frbc import FRBCInstruction, FRBCOperationMode, FRBCSystemDescription
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
    fill_level_scale: float = 1,
    logger: logging.Logger = LOGGER,
) -> float:
    """Compute the operation mode factor for a given fill rate."""
    start_fill_rate = op_mode_elem.fill_rate.start_of_range * fill_level_scale
    end_fill_rate = op_mode_elem.fill_rate.end_of_range * fill_level_scale
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


def op_mode_range(op_mode: FRBCOperationMode, fill_level_scale: float = 1):
    start_of_range = (
        op_mode.elements[0].fill_level_range.start_of_range * fill_level_scale
    )
    end_of_range = op_mode.elements[0].fill_level_range.end_of_range * fill_level_scale

    for op_elem in op_mode.elements:
        start_of_range = min(
            start_of_range, op_elem.fill_level_range.start_of_range * fill_level_scale
        )
        end_of_range = max(
            end_of_range, op_elem.fill_level_range.end_of_range * fill_level_scale
        )

    return start_of_range, end_of_range


def op_mode_elem_efficiency(op_mode_elem: FRBCOperationModeElement, fill_level_scale: float = 1):
    # TODO: take into account both start and end of range. This is a bit tricky
    if op_mode_elem.power_ranges[0].end_of_range == 0:
        return 1

    return (
        op_mode_elem.fill_rate.end_of_range
        * fill_level_scale
        / op_mode_elem.power_ranges[0].end_of_range
    )


def compute_next_fill_level(
    fill_level: float,
    storage_eff: float,
    power: float,
    charging_efficiency: float,
    deltaT: float,
    usage: float,
) -> float:
    fill_rate = power * charging_efficiency * deltaT
    next_fill_level = (
        fill_level * storage_eff + fill_rate * storage_eff - usage * deltaT
    )
    return next_fill_level


def fm_schedule_to_instructions(
    schedule: pd.DataFrame | dict,
    system_description: FRBCSystemDescription,
    initial_fill_level: float,
    fill_level_scale: float = 1,
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
    soc_min, soc_max = get_soc_min_max(system_description, fill_level_scale)

    if len(system_description.actuators) != 1:
        raise NotImplementedError(
            f"This CEM only supports 1 actuator but"
            f"{len(system_description.actuators)} were provided"
        )

    operation_modes: list[FRBCOperationMode] = actuator.operation_modes

    # Find active operation mode
    active_operation_mode = next(
        (
            mode
            for mode in operation_modes
            if isinstance(mode.diagnostic_label, str) and "idle" not in mode.diagnostic_label.lower()
        ),
        None,
    )

    fill_level = initial_fill_level

    deltaT = timedelta(minutes=15) / timedelta(hours=1)

    for timestamp, row in schedule.iterrows():
        power = row["schedule"]
        usage = row["usage_forecast"] if "usage_forecast" in row else None
        if pd.isnull(usage):
            usage = 0
        storage_efficiency = row["leakage_behaviour"] if "leakage_behaviour" in row else None
        if active_operation_mode is None:
            charging_efficiency = 1
        elif isinstance(active_operation_mode.diagnostic_label, str) and "THP" in active_operation_mode.diagnostic_label:
            charging_efficiency = row["thp_efficiency"] if "thp_efficiency" in row else 1
        elif isinstance(active_operation_mode.diagnostic_label, str) and "NES" in active_operation_mode.diagnostic_label:
            charging_efficiency = row["nes_efficiency"] if "nes_efficiency" in row else 1
        else:
            logger.warning(
                f"The diagnostic label of the active operation mode ('{active_operation_mode.diagnostic_label if isinstance(active_operation_mode.diagnostic_label, str) else active_operation_mode}') could not be used to find out which charging efficiency to use. Assuming 100% charging efficiency."
            )
            charging_efficiency = 1

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
            power_candidates = [r for r in fill_level_candidates if r[3] == min_power_penalty]

            # Step 3: among those, pick the highest efficiency
            best = max(power_candidates, key=lambda r: r[4])

            best_operation_mode, best_fill_rate, best_fill_penalty, best_power_penalty, best_efficiency, best_element = best

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
                best_element, fill_rate=best_fill_rate, fill_level_scale=fill_level_scale, logger=logger
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

        if pd.isnull(storage_efficiency):
            storage_eff = 1
        else:
            storage_eff = (storage_efficiency - 1) / math.log(storage_efficiency)
        if pd.isnull(storage_eff):
            storage_eff = 1
        if pd.isnull(charging_efficiency):
            charging_efficiency = 1

        # Update fill level
        fill_level = compute_next_fill_level(
            fill_level=fill_level,
            storage_eff=storage_eff,
            power=row["schedule"],
            charging_efficiency=charging_efficiency,
            deltaT=deltaT,
            usage=usage,
        )

        fill_level = min(fill_level, soc_max)
        fill_level = max(fill_level, soc_min)

        previous_power = power

    return instructions


def get_soc_min_max(system_description: FRBCSystemDescription, fill_level_scale: float = 1) -> tuple[float, float]:
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
            (e, clamp_distance(fill_level, e.fill_level_range.start_of_range, e.fill_level_range.end_of_range))
            for e in elements
        ),
        key=lambda x: x[1]
    )

    # --- 2. Select power range with minimal power penalty ---
    power_range, power_penalty = min(
        (
            (pr, clamp_distance(power, pr.start_of_range, pr.end_of_range))
            for pr in best_element.power_ranges
        ),
        key=lambda x: x[1]
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
        efficiency = (best_element.fill_rate.end_of_range - best_element.fill_rate.start_of_range) / (
            pr_end - pr_start
        )

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
    results: list[tuple[FRBCOperationMode, float, float, float, float, FRBCOperationModeElement]],
    best: tuple[FRBCOperationMode, float, float, float, float, FRBCOperationModeElement],
) -> str:
    """
    Generate a human-readable explanation for why a mode was selected or rejected.
    """
    best_om, best_fill_rate, best_fill_penalty, best_power_penalty, best_efficiency, best_element = best

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
                reason = f"higher power penalty ({power_pen:.6g} > {best_power_penalty:.6g})"
            else:
                reason = f"lower efficiency ({eff:.6g} < {best_efficiency:.6g})"

            lines.append(
                f"{label} (element={element_label}): rejected due to {reason}"
            )

    return "; ".join(lines)
