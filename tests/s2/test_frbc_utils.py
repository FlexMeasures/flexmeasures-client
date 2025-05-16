from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from s2python.common import CommodityQuantity, NumberRange, PowerRange
from s2python.frbc import FRBCOperationMode, FRBCOperationModeElement

import flexmeasures_client.s2.control_types.FRBC.utils as utils
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
    fill_rate_range, input_fill_rate, expected_factor, default_power_range, monkeypatch
):
    monkeypatch.setattr(utils, "FILL_LEVEL_SCALE", 1)
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
def test_op_mode_range(ranges, expected, default_power_range, monkeypatch):
    monkeypatch.setattr(utils, "FILL_LEVEL_SCALE", 1)

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
def test_op_mode_max_fill_rate(
    fill_rates, expected_max, default_power_range, monkeypatch
):
    monkeypatch.setattr(utils, "FILL_LEVEL_SCALE", 1)

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
    example_op_mode_elem, fill_level, expected, monkeypatch
):
    monkeypatch.setattr(utils, "FILL_LEVEL_SCALE", 1)

    assert (
        op_mode_elem_is_fill_level_in_range(example_op_mode_elem, fill_level)
        is expected
    )


def test_op_mode_elem_efficiency(example_op_mode_elem, monkeypatch):
    monkeypatch.setattr(utils, "FILL_LEVEL_SCALE", 1)

    assert np.isclose(op_mode_elem_efficiency(example_op_mode_elem), 0.005)


def test_compounded_fill_level_and_mode_selection(system_with_transitions, monkeypatch):
    monkeypatch.setattr(utils, "FILL_LEVEL_SCALE", 1)

    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    index = pd.date_range(start=start, periods=4, freq="15min")

    operation_modes = {
        op_mode.id: op_mode.diagnostic_label
        for op_mode in system_with_transitions.actuators[0].operation_modes
    }

    schedule_df = pd.DataFrame(
        {
            "schedule": [0.0, 1.5, 1.5, 0.0],
            "usage_forecast": [0.0] * 4,
            "leakage_behaviour": [1.0] * 4,
            "thp_efficiency": [1.0] * 4,
            "nes_efficiency": [1.0] * 4,
        },
        index=index,
    )

    instructions = fm_schedule_to_instructions(
        schedule=schedule_df,
        system_description=system_with_transitions,
        initial_fill_level=0.6,
    )

    # --- Check number of instructions ---
    assert len(instructions) == 4

    # --- Collect operation modes ---
    modes = [operation_modes[instr.operation_mode] for instr in instructions]

    # --- Expected order based on fill level rising ---
    assert modes[0] == "IDLE"  # value = 0.0 -> idle
    assert modes[1] == "TARNOC"  # value = 1.5, fill
    assert modes[2] == "NEWTON"  # fill passes 0.9 → NEWTON
    assert modes[3] == "IDLE"  # value = 0.0 → idle again

    # --- Check operation mode factors ---
    factors = [instr.operation_mode_factor for instr in instructions]
    assert factors[0] == 0
    assert factors[1] > 0
    assert factors[2] > 0
    assert factors[3] == 0
