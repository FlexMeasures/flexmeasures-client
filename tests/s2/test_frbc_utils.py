from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from s2python.common import CommodityQuantity, NumberRange, PowerRange
from s2python.frbc import FRBCOperationMode, FRBCOperationModeElement

from flexmeasures_client.s2.control_types.FRBC.utils import (
    fm_schedule_to_instructions,
    get_unique_id,
    op_mode_compute_factor,
    power_to_fill_rate_with_metrics,
    clamp_distance,
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
        ((1.0, 1.0), 1.0, 0.0),
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
    "fill_level_ranges, power_ranges, test_fill_level, test_power, description",
    [
        # Single element, fill & power inside range → no penalties
        ([(0.0, 2.0)], [(0.0, 5.0)], 1.0, 2.5, "inside range, single element"),
        # Single element, fill level below range → fill-level penalty
        ([(0.2, 2.0)], [(0.0, 5.0)], 0.1, 2.5, "fill level below element"),
        # Single element, fill level above range → fill-level penalty
        ([(0.0, 1.5)], [(0.0, 5.0)], 2.0, 3.0, "fill level above element"),
        # Single element, power below range → power penalty
        ([(0.0, 2.0)], [(1.0, 5.0)], 0.5, 0.5, "power below range"),
        # Single element, power above range → power penalty
        ([(0.0, 2.0)], [(0.0, 4.0)], 0.5, 5.0, "power above range"),
        # Two elements, fill level selects correct element
        ([(0.0, 1.0), (1.0, 2.0)], [(0.0, 5.0)], 1.5, 3.0, "multiple elements, middle fill level"),
        # Fill level below all elements → closest element chosen
        ([(0.0, 1.0), (1.0, 2.0)], [(0.0, 5.0)], -0.5, 2.0, "fill level below all elements"),
        # Fill level above all elements → closest element chosen
        ([(0.0, 1.0), (1.0, 2.0)], [(0.0, 5.0)], 2.5, 2.0, "fill level above all elements"),
        # Multiple power ranges, power selects correct range
        ([(0.0, 1.0)], [(0.0, 2.0), (2.0, 5.0)], 0.5, 3.0, "multiple power ranges, select upper range"),
        # Edge case: power exactly at start of range
        ([(0.0, 1.0)], [(0.0, 5.0)], 0.5, 0.0, "power at start of range"),
        # Edge case: fill level exactly at end of element range
        ([(0.0, 1.0)], [(0.0, 5.0)], 1.0, 2.5, "fill level at end of element range"),
    ],
)
def test_power_to_fill_rate_with_metrics_comprehensive(
    fill_level_ranges, power_ranges, test_fill_level, test_power, description
):
    """
    Comprehensive test for `power_to_fill_rate_with_metrics`.
    Verifies element selection, power range selection, clamping,
    factor-to-fill-rate mapping, and penalties.
    """

    # --- Create FRBC elements with contiguous fill-level ranges ---
    elements = [
        FRBCOperationModeElement(
            fill_level_range=NumberRange(start_of_range=start, end_of_range=end),
            fill_rate=NumberRange(start_of_range=start, end_of_range=end),
            power_ranges=[
                PowerRange(
                    start_of_range=pr_start,
                    end_of_range=pr_end,
                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
                )
                for pr_start, pr_end in power_ranges
            ],
            running_costs=NumberRange(start_of_range=1.0, end_of_range=1.0),
        )
        for start, end in fill_level_ranges
    ]

    op_mode = FRBCOperationMode(
        id=get_unique_id(),
        elements=elements,
        abnormal_condition_only=False,
        diagnostic_label="test",
    )

    # --- Call the method under test ---
    fill_rate, fill_penalty, power_penalty, efficiency, element = power_to_fill_rate_with_metrics(
        op_mode, test_power, test_fill_level
    )

    # --- Assertions ---
    # Fill rate must lie within element's fill_rate range
    assert element.fill_rate.start_of_range <= fill_rate <= element.fill_rate.end_of_range

    # Fill-level penalty is zero if in element range, else positive
    in_fill_range = element.fill_level_range.start_of_range <= test_fill_level <= element.fill_level_range.end_of_range
    if in_fill_range:
        assert fill_penalty == 0.0
    else:
        assert fill_penalty > 0.0

    # Power penalty is zero if power in range, else positive
    pr = min(
        element.power_ranges,
        key=lambda r: clamp_distance(test_power, r.start_of_range, r.end_of_range)
    )
    in_power_range = pr.start_of_range <= test_power <= pr.end_of_range
    if in_power_range:
        assert power_penalty == 0.0
    else:
        assert power_penalty > 0.0

    # Efficiency is non-negative if power range is valid
    assert efficiency >= 0.0

    # Optional debug output
    print(f"{description}: fill_rate={fill_rate:.3f}, fill_penalty={fill_penalty:.3f}, "
          f"power_penalty={power_penalty:.3f}, efficiency={efficiency:.3f}, "
          f"element_range=({element.fill_level_range.start_of_range},{element.fill_level_range.end_of_range})")


def test_compounded_fill_level_and_mode_selection(system_with_transitions):
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    index = pd.date_range(start=start, periods=4, freq="15min")

    operation_modes = {
        op_mode.id: op_mode.diagnostic_label
        for op_mode in system_with_transitions.actuators[0].operation_modes
    }

    schedule_df = pd.DataFrame(
        {
            "schedule": [0.0, 742.5, 442.5, 0.0],  # i.e. corresponding fill rates of 0, 1.5, 1.5, 0
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
