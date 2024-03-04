from datetime import datetime, timedelta
from math import isclose
from typing import List

import pandas as pd
from s2python.frbc import FRBCInstruction, FRBCOperationMode, FRBCSystemDescription

from flexmeasures_client.s2.utils import get_unique_id


def compute_factor(operation_mode: FRBCOperationMode, fill_level, fill_rate):
    """
    Compute the operation mode factor for a fill_rate
    """

    for element in operation_mode.elements:
        start_fill_level = element.fill_level_range.start_of_range
        end_fill_level = element.fill_level_range.end_of_range

        # potential optimization if we assume that the operation_mode_elements are
        # sorted by fill_level
        # if fill_level < start_fill_rate:
        #   return  # TODO: raise error -> cannot find

        # TODO: IMPLEMENT A METHOD `in` to check if a value is within a range
        if (fill_level >= start_fill_level) and (fill_level <= end_fill_level):
            start_fill_rate = element.fill_rate.start_of_range
            end_fill_rate = element.fill_rate.end_of_range
            delta_fill_rate = end_fill_rate - start_fill_rate

            if (fill_rate >= start_fill_rate) and (fill_rate <= end_fill_rate):
                operation_mode_factor = (fill_rate - start_fill_rate) / delta_fill_rate
                return operation_mode_factor

            else:
                print("Requested fill rate out of range")
                return

    print("Couldn't find a valid fill_level")

    return  # handle the case where we are given an invalid fill_level


def fm_schedule_to_instructions(
    schedule, system_description: FRBCSystemDescription, initial_fill_level: float
) -> List[FRBCInstruction]:
    values = schedule.get("values")

    if len(values) == 0:
        return []

    print(schedule)

    previous_value = None
    instructions = []

    start = datetime.fromisoformat(schedule.get("start"))
    deltaT = pd.Timedelta(schedule.get("duration")) / len(values)

    actuators = system_description.actuators

    # assuming there's only 1 actuator
    if len(actuators) != 1:
        print(f"This CEM only supports 1 actuator but {len(actuators)} where provided")
        return []

    actuator = actuators[0]

    operation_modes = actuator.operation_modes

    # assuming there's only 1 operation_modes
    if len(operation_modes) != 1:
        print(
            f"This CEM only supports 1 operation_modes but {len(operation_modes)} where provided"  # noqa: E501
        )
        return []

    # assuming there's only 1 operation mode in the actuator
    operation_mode = operation_modes[0]

    fill_level = initial_fill_level

    for value in values:
        print("VALUE: ", value)
        if (previous_value is None) or not isclose(previous_value, value):
            operation_mode_factor = compute_factor(operation_mode, fill_level, value)

            print("OPERATION MODE FACTOR: ", operation_mode_factor)

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
        fill_level = fill_level + value * deltaT / timedelta(
            hours=1
        )  # what is the sign criterion?

    return instructions
