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

    from flexmeasures_client.s2.const import FILL_LEVEL_SCALE
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


from flexmeasures_client.s2.utils import get_unique_id


def op_mode_compute_factor(op_mode_elem: FRBCOperationModeElement, fill_rate, logger):
    """
    Compute the operation mode factor for a fill_rate
    """
    logger.debug(f"op_mode_elem: {op_mode_elem}")
    logger.debug(f"fill_rate: {fill_rate}")

    start_fill_rate = op_mode_elem.fill_rate.start_of_range * FILL_LEVEL_SCALE
    end_fill_rate = op_mode_elem.fill_rate.end_of_range * FILL_LEVEL_SCALE
    delta_fill_rate = end_fill_rate - start_fill_rate

    fill_rate = max(fill_rate, start_fill_rate)

    # Case that start_fill_rate == end_fill_rate
    if np.isclose(delta_fill_rate, 0):
        return 1

    omf = (fill_rate - start_fill_rate) / delta_fill_rate
    if omf < 0 or omf > 1:
        logger.error(f"Invalid operation mode factor {omf} computed from fill_rate {fill_rate}")
    return omf


def op_mode_range(op_mode: FRBCOperationMode):
    start_of_range = (
        op_mode.elements[0].fill_level_range.start_of_range * FILL_LEVEL_SCALE
    )
    end_of_range = op_mode.elements[0].fill_level_range.end_of_range * FILL_LEVEL_SCALE

    for op_elem in op_mode.elements:
        start_of_range = min(
            start_of_range, op_elem.fill_level_range.start_of_range * FILL_LEVEL_SCALE
        )
        end_of_range = max(
            end_of_range, op_elem.fill_level_range.end_of_range * FILL_LEVEL_SCALE
        )

    return start_of_range, end_of_range


def op_mode_max_fill_rate(op_mode: FRBCOperationMode):
    return max(
        op_elem.fill_rate.end_of_range * FILL_LEVEL_SCALE
        for op_elem in op_mode.elements
    )


def op_mode_elem_is_fill_level_in_range(
    op_mode_elem: FRBCOperationModeElement, fill_level: float
) -> bool:
    return (
        fill_level >= op_mode_elem.fill_level_range.start_of_range * FILL_LEVEL_SCALE
        and fill_level <= op_mode_elem.fill_level_range.end_of_range * FILL_LEVEL_SCALE
    )


def op_mode_elem_efficiency(op_mode_elem: FRBCOperationModeElement):
    # TODO: take into account both start and end of range. This is a bit tricky
    if op_mode_elem.power_ranges[0].end_of_range == 0:
        return 1

    return (
        op_mode_elem.fill_rate.end_of_range
        * FILL_LEVEL_SCALE
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
        fill_level * storage_eff
        + fill_rate * storage_eff
        - usage * deltaT
    )
    return next_fill_level


def fm_schedule_to_instructions(
    schedule: pd.DataFrame,
    system_description: FRBCSystemDescription,
    initial_fill_level: float,
    logger,
) -> List[FRBCInstruction]:
    logger.debug(schedule.to_json())
    logger.debug(system_description.to_json())
    logger.debug(initial_fill_level)

    if len(schedule) == 0 or len(system_description.actuators) == 0:
        return []

    previous_value = None
    instructions = []

    actuator = system_description.actuators[0]
    fill_level_range: NumberRange = system_description.storage.fill_level_range

    # get SOC Max and Min to be sent on the Flex Model
    logger.debug("setting soc constraints")
    soc_min = fill_level_range.start_of_range * FILL_LEVEL_SCALE
    soc_max = fill_level_range.end_of_range * FILL_LEVEL_SCALE
    logger.debug(f"soc_min: {soc_min}")
    logger.debug(f"soc_max: {soc_max}")

    if len(system_description.actuators) != 1:
        raise NotImplementedError(
            f"This CEM only supports 1 actuator but"
            f"{len(system_description.actuators)} were provided"
        )

    operation_modes: list[FRBCOperationMode] = actuator.operation_modes
    logger.debug(f"operation_modes: {[mode.diagnostic_label.lower() for mode in operation_modes]}")

    # Find idle operation mode
    idle_operation_mode = next(
        (mode for mode in operation_modes if "idle" in mode.diagnostic_label.lower()),
        None,
    )
    logger.debug(f"idle_operation_mode: {idle_operation_mode}")
    active_operation_mode = next(
        (mode for mode in operation_modes if "idle" not in mode.diagnostic_label.lower()),
        None,
    )
    logger.debug(f"active_operation_mode: {active_operation_mode}")

    if idle_operation_mode is None:
        print("No valid idle operation mode found.")
        return []

    fill_level = initial_fill_level

    deltaT = timedelta(minutes=15) / timedelta(hours=1)
    charging_efficiency = 1

    max_eff = max(
        [
            op_mode_elem_efficiency(elem)
            for op_mode in operation_modes
            for elem in op_mode.elements
            if "idle" not in op_mode.diagnostic_label.lower()
        ]
    )
    logger.debug(f"max_eff: {max_eff}")

    for timestamp, row in schedule.iterrows():
        if pd.Timestamp(timestamp) >= pd.Timestamp("2025-10-14 15:00:00+02:00") and pd.Timestamp(timestamp) < pd.Timestamp("2025-10-14 15:15:00+02:00"):
            operation_mode = active_operation_mode
            operation_mode_factor = 0.
            instruction = FRBCInstruction(
                message_id=get_unique_id(),
                id=get_unique_id(),
                actuator_id=actuator.id,
                operation_mode=operation_mode.id,
                operation_mode_factor=operation_mode_factor,
                execution_time=timestamp,
                abnormal_condition=False,
            )
            logger.info(f"Instruction created: at {timestamp} set {actuator.diagnostic_label} to {operation_mode.diagnostic_label} with factor {operation_mode_factor}")
            instructions.append(instruction)
            continue

        value = row["schedule"]
        usage = row["usage_forecast"]
        if pd.isnull(usage):
            usage = 0
        storage_efficiency = row["leakage_behaviour"]
        if "THP" in active_operation_mode.diagnostic_label:
            charging_efficiency = row["thp_efficiency"]
        elif "NES" in active_operation_mode.diagnostic_label:
            charging_efficiency = row["nes_efficiency"]
        else:
            charging_efficiency = 1

        if previous_value is None or not isclose(previous_value, value):
            if np.isclose(value, 0):
                operation_mode = idle_operation_mode
                operation_mode_factor = 0
                charging_efficiency = 1
            else:
                valid_operation_modes = [
                    op_mode
                    for op_mode in operation_modes
                    if op_mode_range(op_mode)[0]
                    <= fill_level
                    <= op_mode_range(op_mode)[1]
                ]

                if not valid_operation_modes:
                    logger.warning(f"Schedule does not map to a valid operation mode for {timestamp}.")
                    continue

                max_fill_rate = max(
                    op_mode_max_fill_rate(op_mode) for op_mode in valid_operation_modes
                )

                logger.debug(f"value: {value}")
                logger.debug(f"max_fill_rate: {max_fill_rate}")
                value = min(value * max_eff, max_fill_rate)
                logger.debug(f"value after min(): {value}")

                valid_operation_modes = [
                    op_mode
                    for op_mode in valid_operation_modes
                    if op_mode_max_fill_rate(op_mode) >= value
                ]

                op_mode_elements = [
                    (
                        op_mode_elem_efficiency(elem),
                        op_mode,
                        elem,
                    )
                    for op_mode in valid_operation_modes
                    for elem in op_mode.elements
                    if op_mode_elem_is_fill_level_in_range(elem, fill_level)
                ]

                if not op_mode_elements:
                    continue

                _, operation_mode, op_mode_elem = sorted(
                    op_mode_elements, key=lambda x: x[0]
                )[0]

                value = -value

                operation_mode_factor = op_mode_compute_factor(op_mode_elem, value, logger=logger)

            logger.debug(f"Creating instruction for operation_mode_factor {operation_mode_factor}..")
            instruction = FRBCInstruction(
                message_id=get_unique_id(),
                id=get_unique_id(),
                actuator_id=actuator.id,
                operation_mode=operation_mode.id,
                operation_mode_factor=operation_mode_factor,
                execution_time=timestamp,
                abnormal_condition=False,
            )
            logger.info(f"Instruction created: at {timestamp} set {actuator.diagnostic_label} to {operation_mode.diagnostic_label} with factor {operation_mode_factor}")
            instructions.append(instruction)

        logger.debug(f"computing storage_eff from {storage_efficiency}")
        if pd.isnull(storage_efficiency):
            logger.debug("leakage behaviour is unknown")
            storage_eff = 1
        else:
            logger.debug("leakage behaviour is known")
            storage_eff = (storage_efficiency - 1) / math.log(storage_efficiency)
        logger.debug(f"storage_eff: {storage_eff}")
        if pd.isnull(storage_eff):
            storage_eff = 1
        if pd.isnull(charging_efficiency):
            charging_efficiency = 1

        # Update fill level
        logger.debug(f"Updating fill level for {timestamp}..")
        logger.debug(f"storage_efficiency: {storage_efficiency}")
        logger.debug(f"storage_eff: {storage_eff}")
        logger.debug(f"fill_level: {fill_level}")
        logger.debug(f"schedule: {row['schedule']}")
        logger.debug(f"deltaT: {deltaT}")
        logger.debug(f"usage: {usage}")
        logger.debug(f"charging_efficiency: {charging_efficiency}")
        fill_level = compute_next_fill_level(
            fill_level=fill_level,
            storage_eff=storage_eff,
            power=row["schedule"],
            charging_efficiency=charging_efficiency,
            deltaT=deltaT,
            usage=usage,
        )

        logger.debug(f"fill_level: {fill_level}")
        fill_level = min(fill_level, soc_max)
        fill_level = max(fill_level, soc_min)
        logger.debug(f"clipped fill_level: {fill_level}")

        previous_value = value

    return instructions
