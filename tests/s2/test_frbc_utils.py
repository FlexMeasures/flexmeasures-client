from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
from s2python.common import Commodity, CommodityQuantity, NumberRange, PowerRange
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCInstruction,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCSystemDescription,
)

from flexmeasures_client.s2.control_types.FRBC.utils import (
    fm_schedule_to_instructions,
    get_unique_id,
    op_mode_compute_factor,
    op_mode_elem_efficiency,
    op_mode_elem_is_fill_level_in_range,
    op_mode_max_fill_rate,
    op_mode_range,
)


@pytest.fixture
def default_power_range():
    return [
        PowerRange(
            start_of_range=100.0,
            end_of_range=200.0,
            commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
        )
    ]


@pytest.fixture
def example_op_mode_elem(default_power_range):
    return FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=0.0, end_of_range=0.5),
        fill_rate=NumberRange(start_of_range=0.0, end_of_range=1.0),
        power_ranges=default_power_range,
        running_costs=NumberRange(start_of_range=1.0, end_of_range=1.0),
    )


@pytest.mark.parametrize(
    "fill_rate_range, input_fill_rate, expected_factor",
    [
        ((0.0, 1.0), 0.0, 0.0),
        ((0.0, 1.0), 0.5, 0.5),
        ((0.0, 1.0), 1.0, 1.0),
        ((2.0, 4.0), 3.0, 0.5),
        ((1.0, 1.0), 1.0, 1.0),
    ],
)
def test_op_mode_compute_factor(
    fill_rate_range, input_fill_rate, expected_factor, default_power_range
):
    op_mode_elem = FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=0.0, end_of_range=1.0),
        fill_rate=NumberRange(
            start_of_range=fill_rate_range[0], end_of_range=fill_rate_range[1]
        ),
        power_ranges=default_power_range,
        running_costs=NumberRange(start_of_range=1.0, end_of_range=1.0),
    )

    factor = op_mode_compute_factor(op_mode_elem, fill_rate=input_fill_rate)
    assert np.isclose(factor, expected_factor)


@pytest.mark.parametrize(
    "ranges, expected",
    [
        ([(0.1, 0.3), (0.3, 0.8)], (0.1, 0.8)),
        ([(0.0, 0.1), (0.1, 0.2), (0.2, 0.3)], (0.0, 0.3)),
    ],
)
def test_op_mode_range(ranges, expected, default_power_range):
    elements = [
        FRBCOperationModeElement(
            fill_level_range=NumberRange(start_of_range=start, end_of_range=end),
            fill_rate=NumberRange(start_of_range=0.0, end_of_range=1.0),
            power_ranges=default_power_range,
            running_costs=NumberRange(start_of_range=1.0, end_of_range=1.0),
        )
        for start, end in ranges
    ]
    op_mode_id = get_unique_id()
    op_mode = FRBCOperationMode(
        id=op_mode_id,
        elements=elements,
        abnormal_condition_only=False,
        diagnostic_label="test",
    )
    assert op_mode_range(op_mode) == expected


@pytest.mark.parametrize(
    "fill_rates, expected_max",
    [
        ([(0.0, 1.0), (0.0, 2.0)], 2.0),
        ([(0.0, 0.5), (0.0, 0.9)], 0.9),
    ],
)
def test_op_mode_max_fill_rate(fill_rates, expected_max, default_power_range):
    elements = [
        FRBCOperationModeElement(
            fill_level_range=NumberRange(start_of_range=0.0, end_of_range=1.0),
            fill_rate=NumberRange(start_of_range=start, end_of_range=end),
            power_ranges=default_power_range,
            running_costs=NumberRange(start_of_range=1.0, end_of_range=1.0),
        )
        for start, end in fill_rates
    ]
    op_mode_id = get_unique_id()
    op_mode = FRBCOperationMode(
        id=op_mode_id,
        elements=elements,
        abnormal_condition_only=False,
        diagnostic_label="test",
    )
    assert op_mode_max_fill_rate(op_mode) == expected_max


@pytest.mark.parametrize(
    "fill_level, expected",
    [
        (0.25, True),
        (0.5, True),
        (0.51, False),
        (-0.1, False),
    ],
)
def test_op_mode_elem_is_fill_level_in_range(
    example_op_mode_elem, fill_level, expected
):
    assert (
        op_mode_elem_is_fill_level_in_range(example_op_mode_elem, fill_level)
        is expected
    )


def test_op_mode_elem_efficiency(example_op_mode_elem):
    assert np.isclose(op_mode_elem_efficiency(example_op_mode_elem), 200.0 / 1.0)


def test_fm_schedule_to_instructions(default_power_range):
    elem = FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=0.0, end_of_range=1.0),
        fill_rate=NumberRange(start_of_range=0.0, end_of_range=2.0),
        power_ranges=default_power_range,
        running_costs=NumberRange(start_of_range=1.0, end_of_range=1.0),
    )

    idle_elem = FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=0.0, end_of_range=1.0),
        fill_rate=NumberRange(start_of_range=0.0, end_of_range=0.0),
        power_ranges=[
            PowerRange(
                start_of_range=0.0,
                end_of_range=0.0,
                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
            )
        ],
        running_costs=NumberRange(start_of_range=1.0, end_of_range=1.0),
    )

    op_mode_id = get_unique_id()
    idle_mode_id = get_unique_id()
    actuator_id = get_unique_id()

    op_mode = FRBCOperationMode(
        id=op_mode_id,
        elements=[elem],
        abnormal_condition_only=False,
        diagnostic_label="Active",
    )
    idle_mode = FRBCOperationMode(
        id=idle_mode_id,
        elements=[idle_elem],
        abnormal_condition_only=False,
        diagnostic_label="Idle",
    )

    actuator = FRBCActuatorDescription(
        id=actuator_id,
        operation_modes=[idle_mode, op_mode],
        supported_commodities=[Commodity.ELECTRICITY],
        transitions=[],
        timers=[],
    )

    storage = FRBCStorageDescription(
        provides_leakage_behaviour=True,
        provides_fill_level_target_profile=True,
        provides_usage_forecast=True,
        fill_level_range=NumberRange(start_of_range=0, end_of_range=1),
    )

    system_description = FRBCSystemDescription(
        actuators=[actuator],
        storage=storage,
        message_id=get_unique_id(),
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    schedule = {
        "start": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
        "duration": "PT1H",
        "values": [0.0, 0.5, 1.5, 0.0],
    }

    instructions = fm_schedule_to_instructions(
        schedule, system_description, initial_fill_level=0.5
    )

    assert len(instructions) == 4
    assert all(isinstance(instr, FRBCInstruction) for instr in instructions)

    assert str(instructions[0].operation_mode) == idle_mode_id
    assert str(instructions[1].operation_mode) == op_mode_id
