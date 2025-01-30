from __future__ import annotations

import math

import pytest
from s2python.common import CommodityQuantity, NumberRange, PowerRange
from s2python.frbc import FRBCOperationMode, FRBCOperationModeElement

from flexmeasures_client.s2.control_types.FRBC.utils import (
    compute_factor,
    get_unique_id,
)


@pytest.mark.parametrize(
    "fill_level, fill_rate, expected_factor",
    [
        (-1, 0.1, None),
        (10, 0.1, None),
        (0.1, 0.1, 0.1),
        (0.26, 0.1, 0.05),
        (0.75, 0.1, 0.05),
    ],
)
def test_compute_factor(fill_level: float, fill_rate: float, expected_factor: float):
    """_summary_

    :param fill_level: _description_
    :type fill_level: float
    :param fill_rate: _description_
    :type fill_rate: float
    :param expected_factor: _description_
    :type expected_factor: float
    """

    fill_rate_start = 0

    power_range = PowerRange(
        start_of_range=1000,
        end_of_range=1000,
        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
    )  # assume constant power consumption

    operation_mode_elements = []

    fill_level_range = [(0, 0.25), (0.25, 0.75), (0.75, 1)]
    fill_rate_range = [(0, 1), (0, 2), (0, 1)]

    for (fill_level_start, fill_level_end), (fill_rate_start, fill_rate_end) in zip(
        fill_level_range, fill_rate_range
    ):
        operation_mode_element = FRBCOperationModeElement(
            fill_level_range=NumberRange(
                start_of_range=fill_level_start, end_of_range=fill_level_end
            ),
            fill_rate=NumberRange(
                start_of_range=fill_rate_start, end_of_range=fill_rate_end
            ),
            power_ranges=[power_range],
            running_costs=NumberRange(start_of_range=1, end_of_range=1),
        )

        operation_mode_elements.append(operation_mode_element)

    operation_mode = FRBCOperationMode(
        id=get_unique_id(),
        elements=operation_mode_elements,
        abnormal_condition_only=False,
        diagnostic_label="",
    )

    factor = compute_factor(operation_mode, fill_level, fill_rate)

    if expected_factor:
        assert math.isclose(factor, expected_factor)
    else:
        assert factor is None
