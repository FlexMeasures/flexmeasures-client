"""What the handler records about an actuator means what the sensor holding it means.

Three things decide that, and each of them was wrong in its own way.
S2 fixes one sign convention, positive for consumption,
while a FlexMeasures power sensor carries its own and defaults to the other,
so a measurement and the plan beside it pointed in opposite directions.
A power value was labelled with a unit nothing converted it into.
And two statuses inside one quarter-hour collided on the same belief,
so the later one was refused and, being posted from a task nobody awaits, lost without a word.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from s2python.common import Commodity, CommodityQuantity, NumberRange, PowerRange
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCActuatorStatus,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCSystemDescription,
)

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.utils import get_unique_id

from .conftest import power_sensor_recording

POWER_SENSOR_ID = 1
# The power the actuator draws when it runs, in watts, as S2 states it.
ON_W = 883.7


@pytest.fixture
def system_description() -> FRBCSystemDescription:
    """An actuator that is either off or drawing 883.7 W."""
    modes = [
        FRBCOperationMode(
            id=get_unique_id(),
            diagnostic_label=label,
            elements=[
                FRBCOperationModeElement(
                    fill_level_range=NumberRange(start_of_range=0, end_of_range=100),
                    fill_rate=NumberRange(start_of_range=rate, end_of_range=rate),
                    power_ranges=[
                        PowerRange(
                            start_of_range=power,
                            end_of_range=power,
                            commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                        )
                    ],
                )
            ],
            abnormal_condition_only=False,
        )
        for label, power, rate in (("off", 0.0, 0.0), ("on", ON_W, 0.005))
    ]
    actuator = FRBCActuatorDescription(
        id=get_unique_id(),
        supported_commodities=[Commodity.ELECTRICITY],
        operation_modes=modes,
        transitions=[],
        timers=[],
    )
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from="2024-01-01T00:00:00+00:00",
        actuators=[actuator],
        storage=FRBCStorageDescription(
            provides_leakage_behaviour=False,
            provides_fill_level_target_profile=False,
            provides_usage_forecast=False,
            fill_level_range=NumberRange(start_of_range=0, end_of_range=100),
        ),
    )


def _frbc(system_description, *, consumption_as_positive: bool, **kwargs) -> FRBCSimple:
    """A handler whose power sensor records consumption in the given sign."""
    frbc = FRBCSimple(
        power_sensor_id=POWER_SENSOR_ID,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        **kwargs,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._fm_client.get_sensor = AsyncMock(
        return_value=power_sensor_recording(
            consumption_as_positive, sensor_id=POWER_SENSOR_ID
        )
    )
    frbc._logger = logging.getLogger(__name__)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    return frbc


def _status(system_description, mode_index: int) -> FRBCActuatorStatus:
    actuator = system_description.actuators[0]
    return FRBCActuatorStatus(
        message_id=get_unique_id(),
        actuator_id=actuator.id,
        active_operation_mode_id=actuator.operation_modes[mode_index].id,
        operation_mode_factor=1.0,
    )


def _posts_to_the_power_sensor(frbc: FRBCSimple) -> list[dict]:
    return [
        call.kwargs
        for call in frbc._fm_client.post_sensor_data.await_args_list
        if call.kwargs.get("sensor_id", call.args[0] if call.args else None)
        == POWER_SENSOR_ID
    ]


@pytest.mark.asyncio
async def test_a_production_positive_sensor_is_posted_the_other_way_round(
    system_description,
):
    """FlexMeasures stores a plan to consume as negative on such a sensor, so a measurement of consuming is negative too.

    Posted as S2 states it, the measurement and the plan beside it point in opposite directions,
    which reads as a device doing the reverse of what it was asked.
    """
    frbc = _frbc(system_description, consumption_as_positive=False)

    await frbc.send_actuator_status(_status(system_description, 1))

    assert _posts_to_the_power_sensor(frbc)[0]["values"] == [-ON_W]


@pytest.mark.asyncio
async def test_a_consumption_positive_sensor_is_posted_as_s2_states_it(
    system_description,
):
    """A sensor that says it records consumption as positive needs no conversion."""
    frbc = _frbc(system_description, consumption_as_positive=True)

    await frbc.send_actuator_status(_status(system_description, 1))

    assert _posts_to_the_power_sensor(frbc)[0]["values"] == [ON_W]


@pytest.mark.asyncio
async def test_the_sensor_is_asked_once(system_description):
    """A sensor's convention is how it was set up, not something that changes between statuses."""
    frbc = _frbc(system_description, consumption_as_positive=False)

    await frbc.send_actuator_status(_status(system_description, 1))
    await frbc.send_actuator_status(_status(system_description, 0))

    assert frbc._fm_client.get_sensor.await_count == 1


@pytest.mark.asyncio
async def test_a_sensor_that_cannot_be_read_is_posted_as_s2_states_it(
    system_description, caplog
):
    """Better this handler's own convention, said out loud, than no measurement at all."""
    frbc = _frbc(system_description, consumption_as_positive=False)
    frbc._fm_client.get_sensor = AsyncMock(side_effect=ValueError("no such sensor"))

    with caplog.at_level(logging.WARNING):
        await frbc.send_actuator_status(_status(system_description, 1))

    assert _posts_to_the_power_sensor(frbc)[0]["values"] == [ON_W]
    assert "could not read what sign" in caplog.text.lower()
