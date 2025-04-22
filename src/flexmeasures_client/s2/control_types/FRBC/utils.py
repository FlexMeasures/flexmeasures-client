from datetime import datetime
from math import isclose
from typing import List

import numpy as np
import pandas as pd

try:
    from s2python.frbc import FRBCInstruction, FRBCOperationMode, FRBCSystemDescription
    from s2python.frbc.frbc_operation_mode_element import FRBCOperationModeElement
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


from flexmeasures_client.s2.utils import get_unique_id


def op_mode_compute_factor(op_mode_elem: FRBCOperationModeElement, fill_rate):
    """
    Compute the operation mode factor for a fill_rate
    """

    start_fill_rate = op_mode_elem.fill_rate.start_of_range
    end_fill_rate = op_mode_elem.fill_rate.end_of_range
    delta_fill_rate = end_fill_rate - start_fill_rate

    # Case that start_fill_rate == end_fill_rate
    if np.isclose(delta_fill_rate, 0):
        return 1

    return (fill_rate - start_fill_rate) / delta_fill_rate


def op_mode_range(op_mode: FRBCOperationMode):
    start_of_range = op_mode.elements[0].fill_level_range.start_of_range
    end_of_range = op_mode.elements[0].fill_level_range.end_of_range

    for op_elem in op_mode.elements:
        start_of_range = min(start_of_range, op_elem.fill_level_range.start_of_range)
        end_of_range = max(end_of_range, op_elem.fill_level_range.end_of_range)

    return start_of_range, end_of_range


def op_mode_max_fill_rate(op_mode: FRBCOperationMode):
    return max(op_elem.fill_rate.end_of_range for op_elem in op_mode.elements)


def op_mode_elem_is_fill_level_in_range(
    op_mode_elem: FRBCOperationModeElement, fill_level: float
) -> bool:
    return (
        fill_level >= op_mode_elem.fill_level_range.start_of_range
        and fill_level <= op_mode_elem.fill_level_range.end_of_range
    )


def op_mode_elem_efficiency(op_mode_elem: FRBCOperationModeElement):
    # TODO: take into account both start and end of range. This is a bit tricky
    return (
        op_mode_elem.power_ranges[0].end_of_range / op_mode_elem.fill_rate.end_of_range
    )


def fm_schedule_to_instructions(
    schedule, system_description: FRBCSystemDescription, initial_fill_level: float
) -> List[FRBCInstruction]:
    values = schedule.get("values")
    actuators = system_description.actuators

    if len(values) == 0 or len(actuators) == 0:
        return []

    print(schedule)

    previous_value = None
    instructions = []

    start = datetime.fromisoformat(schedule.get("start"))
    deltaT = pd.Timedelta(schedule.get("duration")) / len(values)

    # assuming there's only 1 actuator
    if len(actuators) != 1:
        raise NotImplementedError(
            f"This CEM only supports 1 actuator but {len(actuators)} where provided"
        )

    actuator = actuators[0]

    operation_modes: list[FRBCOperationMode] = actuator.operation_modes

    idle_operation_mode = None

    # Search for the NES or THP
    for _operation_mode in operation_modes:
        if "idle" in _operation_mode.diagnostic_label.lower():
            idle_operation_mode = _operation_mode

    if idle_operation_mode is None:
        print("No valid operation mode was found")
        return []

    fill_level = initial_fill_level

    for value in values:
        if (previous_value is None) or not isclose(previous_value, value):
            if np.isclose(value, 0):
                operation_mode = idle_operation_mode
                operation_mode_factor = 0
            else:
                # Get Operation modes that can be used with the current fill_level
                valid_operation_modes = []

                for op_mode in operation_modes:
                    start_of_range, end_of_range = op_mode_range(op_mode)

                    if fill_level >= start_of_range and fill_level <= end_of_range:
                        valid_operation_modes.append(op_mode)

                # TODO: what if valid_operation_modes is empty?
                # Make max out value to the max fill_rate
                max_fill_rate = max(
                    op_mode_max_fill_rate(op_mode) for op_mode in valid_operation_modes
                )

                if value > max_fill_rate:
                    print(
                        f"""Schedule fill_rate=`{value}` is larger than max.
                        fill_rate ({max_fill_rate})"""
                    )
                    value = max_fill_rate

                valid_operation_modes = [
                    op_mode
                    for op_mode in valid_operation_modes
                    if op_mode_max_fill_rate(op_mode) >= value
                ]

                op_mode_elements = []

                for op_mode in valid_operation_modes:
                    for op_mode_elem in op_mode.elements:
                        if op_mode_elem_is_fill_level_in_range(
                            op_mode_elem, fill_level
                        ):
                            op_mode_elements.append(
                                (
                                    op_mode_elem_efficiency(op_mode_elem),
                                    op_mode,
                                    op_mode_elem,
                                )
                            )

                # Sort operation modes by efficiency
                _, operation_mode, op_mode_elem = sorted(
                    op_mode_elements, key=lambda x: x[0]
                )[0]
                operation_mode_factor = op_mode_compute_factor(op_mode_elem, value)

            instruction = FRBCInstruction(
                message_id=get_unique_id(),
                id=get_unique_id(),
                actuator_id=actuator.id,
                operation_mode=operation_mode.id,
                operation_mode_factor=operation_mode_factor,
                execution_time=start,
                abnormal_condition=False,
            )

            instructions.append(instruction)

        start = start + deltaT
        previous_value = value
        # TODO: Add usage forecast, leakage behaviour and efficieny. Move to function.
        # fill_level = fill_level + value * deltaT / timedelta(
        #     hours=1
        # )  # what is the sign criterion?

    return instructions
