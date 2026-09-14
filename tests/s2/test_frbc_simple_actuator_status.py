"""The power posted from an FRBC.ActuatorStatus must follow the ACTIVE operation mode.

An FRBC actuator here declares its operation modes with degenerate power ranges - on is
[883.7, 883.7] W, off is [0, 0] - so the operation_mode_factor cannot express the difference
between them. Only the choice of mode can. Selecting operation_modes[0] therefore pinned every
status to whichever mode the system description happened to list first, and the apartment's power
sensor showed a constant draw that could never fall to zero.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCStorageDescription,
    FRBCActuatorStatus,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCSystemDescription,
)
from s2python.common import Commodity, CommodityQuantity, NumberRange, PowerRange

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.utils import get_unique_id

ON_W = 883.7209302325582
POWER_SENSOR_ID = 1


def _operation_mode(power_w: float) -> FRBCOperationMode:
    """One mode whose power range is degenerate at power_w, as the real RM declares them."""
    return FRBCOperationMode(
        id=get_unique_id(),
        diagnostic_label="on" if power_w else "off",
        elements=[
            FRBCOperationModeElement(
                fill_level_range=NumberRange(start_of_range=0, end_of_range=1),
                fill_rate=NumberRange(start_of_range=0, end_of_range=power_w),
                power_ranges=[
                    PowerRange(
                        start_of_range=power_w,
                        end_of_range=power_w,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                    )
                ],
            )
        ],
        abnormal_condition_only=False,
    )


@pytest.fixture
def system_description() -> FRBCSystemDescription:
    """An actuator listing ON FIRST, then OFF - the ordering the real RM sends."""
    on_mode, off_mode = _operation_mode(ON_W), _operation_mode(0.0)
    actuator = FRBCActuatorDescription(
        id=get_unique_id(),
        supported_commodities=[Commodity.ELECTRICITY],
        operation_modes=[on_mode, off_mode],
        transitions=[],
        timers=[],
    )
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from="2022-12-02T00:00:00+00:00",
        actuators=[actuator],
        storage=FRBCStorageDescription(
            provides_leakage_behaviour=False,
            provides_fill_level_target_profile=False,
            provides_usage_forecast=False,
            fill_level_range=NumberRange(start_of_range=0, end_of_range=1),
        ),
    )


def _frbc(system_description) -> FRBCSimple:
    frbc = FRBCSimple(
        power_sensor_id=POWER_SENSOR_ID,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        production_price_sensor_id=5,
        soc_minima_sensor_id=6,
        soc_maxima_sensor_id=7,
        usage_forecast_sensor_id=8,
        leakage_behaviour_sensor_id=9,
        charging_efficiency_sensor_id=10,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    return frbc


def _status(system_description, mode_index: int, factor: float) -> FRBCActuatorStatus:
    actuator = system_description.actuators[0]
    return FRBCActuatorStatus(
        message_id=get_unique_id(),
        actuator_id=actuator.id,
        active_operation_mode_id=actuator.operation_modes[mode_index].id,
        operation_mode_factor=factor,
    )


def _posted_power(frbc: FRBCSimple) -> float:
    """The value posted to the power sensor, from the mocked client call."""
    for call in frbc._fm_client.post_sensor_data.await_args_list:
        if (
            call.kwargs.get("sensor_id", call.args[0] if call.args else None)
            == POWER_SENSOR_ID
        ):
            return call.kwargs["values"][0]
    raise AssertionError("nothing was posted to the power sensor")


@pytest.mark.asyncio
async def test_off_mode_posts_zero(system_description):
    """The regression: with the OFF mode active, the posted power must be 0, not the on value.

    Before the fix this returned ON_W, because operation_modes[0] is the on mode.
    """
    frbc = _frbc(system_description)
    await frbc.send_actuator_status(
        _status(system_description, mode_index=1, factor=0.0)
    )
    assert _posted_power(frbc) == 0.0


@pytest.mark.asyncio
async def test_on_mode_still_posts_the_on_value(system_description):
    """The mode that was previously always selected must keep working."""
    frbc = _frbc(system_description)
    await frbc.send_actuator_status(
        _status(system_description, mode_index=0, factor=1.0)
    )
    assert _posted_power(frbc) == pytest.approx(ON_W)


@pytest.mark.asyncio
async def test_factor_cannot_rescue_a_degenerate_range(system_description):
    """A factor of 0 on the ON mode still draws full power - only the mode choice matters.

    This is why the operation_mode_factor alone could never make the sensor show switching.
    """
    frbc = _frbc(system_description)
    await frbc.send_actuator_status(
        _status(system_description, mode_index=0, factor=0.0)
    )
    assert _posted_power(frbc) == pytest.approx(ON_W)


@pytest.mark.asyncio
async def test_unknown_mode_falls_back_and_warns(system_description, caplog):
    """A status naming an absent mode must not be dropped silently."""
    frbc = _frbc(system_description)
    status = _status(system_description, mode_index=0, factor=1.0)
    status.active_operation_mode_id = get_unique_id()
    await frbc.send_actuator_status(status)
    assert _posted_power(frbc) == pytest.approx(ON_W)
    assert any(
        "absent from the latest system description" in record.getMessage()
        for record in caplog.records
    ), "the fallback must be logged, not silent"
