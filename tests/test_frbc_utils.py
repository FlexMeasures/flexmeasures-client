import pytest
from python_s2_protocol.common.schemas import CommodityQuantity, NumberRange, PowerRange
from python_s2_protocol.FRBC.schemas import FRBCOperationMode, FRBCOperationModeElement

from flexmeasures_client.s2.control_types.FRBC.utils import compute_factor


@pytest.mark.parametrize("fill_level, fill_rate, factor, fails", [(0.1, 0.3, 1, False)])
def test_compute_factor(
    fill_level: float, fill_rate: float, factor: float, fails: bool
):
    fill_rate_start = 0

    power_range = PowerRange(
        start_of_range=1000,
        end_of_range=1000,
        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
    )  # assume constant power consumption

    operation_mode_elements = []

    for level in range(10):
        # constant fill_level ranges of length 10
        fill_level_start = level * 10
        fill_level_end = fill_level_start + 10

        # slope of line will be `level`, 10, 20, 30, ..
        fill_rate_start = 0
        fill_rate_end = (level + 1) * 10

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
        id="10",
        elements=operation_mode_elements,
        abnormal_condition_only=False,
        diagnostic_label="",
    )

    compute_factor(operation_mode, 0.1, 10)
