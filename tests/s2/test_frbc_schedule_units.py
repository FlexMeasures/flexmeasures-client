"""A schedule is read back in the unit the operation modes state power in.

S2 states an operation mode's power ranges in watts,
while a FlexMeasures power sensor is in whatever unit it was created in,
and the example server creates its power sensor in kW.
Read back in the sensor's unit, every scheduled power is a thousandth of what it is,
so it sits nearest the operation mode that draws nothing,
and the resource manager is told to idle through a schedule that asked it to run.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from s2python.common import Commodity, CommodityQuantity, NumberRange, PowerRange
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCStorageStatus,
    FRBCSystemDescription,
)

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.utils import get_unique_id

RANGE_BOTTOM = 20.0
RANGE_TOP = 80.0
FILL_LEVEL_AT_START = 30.0
# The power the actuator runs at when it runs at all, in watts, as S2 states it.
CHARGE_POWER = 100.0


@pytest.fixture
def system_description() -> FRBCSystemDescription:
    """An actuator that is either off or drawing 100 W."""
    modes = [
        FRBCOperationMode(
            id=get_unique_id(),
            diagnostic_label=label,
            elements=[
                FRBCOperationModeElement(
                    fill_level_range=NumberRange(
                        start_of_range=RANGE_BOTTOM, end_of_range=RANGE_TOP
                    ),
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
        for label, power, rate in (("off", 0.0, 0.0), ("charge", CHARGE_POWER, 0.005))
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
            fill_level_range=NumberRange(
                start_of_range=RANGE_BOTTOM, end_of_range=RANGE_TOP
            ),
        ),
    )


async def _schedule_from_a_kilowatt_sensor(**kwargs) -> dict:
    """A server answering for a power sensor recorded in kW, as the example server creates it.

    It asks the actuator for the charge mode's power at every step,
    and answers in the sensor's own unit unless the caller asks for another one,
    which is what the real client does, server-side or by converting.
    """

    asked_for = kwargs.get("unit")
    values = [CHARGE_POWER / 1000] * 48
    unit = "kW"
    if asked_for == "W":
        values = [value * 1000 for value in values]
        unit = "W"
    return {
        "values": values,
        "start": "2024-01-01T00:00:00+00:00",
        "duration": "PT12H",
        "unit": unit,
    }


def _frbc(system_description, **kwargs) -> FRBCSimple:
    """A handler that has had a system description and a storage status, ready to plan."""
    frbc = FRBCSimple(
        power_sensor_id=1,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        **kwargs,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._logger = logging.getLogger(__name__)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=FILL_LEVEL_AT_START
    )
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        side_effect=_schedule_from_a_kilowatt_sensor
    )
    frbc.send_instruction_batch = AsyncMock(return_value=0)
    return frbc


@pytest.mark.asyncio
async def test_a_schedule_is_asked_for_in_watts(system_description):
    """S2 states power in watts, so that is what this handler compares a schedule against."""
    frbc = _frbc(system_description)

    await frbc.trigger_schedule(system_description.message_id)

    kwargs = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs
    assert kwargs["unit"] == "W"


@pytest.mark.asyncio
async def test_a_handler_posting_power_in_another_unit_still_asks_for_watts(
    system_description,
):
    """power_unit says how a posted power value is labelled, not what S2 states its ranges in.

    A handler recording its power sensor in kW still has to compare a schedule against power
    ranges stated in watts, so the unit asked for does not follow that one.
    """
    frbc = _frbc(system_description, power_unit="kW")

    await frbc.trigger_schedule(system_description.message_id)

    kwargs = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs
    assert kwargs["unit"] == "W"


@pytest.mark.asyncio
async def test_a_schedule_in_those_units_reaches_the_mode_that_runs(system_description):
    """A scheduled 100 W is the charge mode; the same schedule read as 0.1 W is the mode that is off.

    The server answers for a sensor in kW here,
    so this is the whole of what the unit costs: a resource manager told to idle through a schedule that asked it to run.
    """
    frbc = _frbc(system_description)

    await frbc.trigger_schedule(system_description.message_id)

    instructions = frbc.send_instruction_batch.await_args.args[0]
    labels = {
        mode.id: mode.diagnostic_label
        for mode in system_description.actuators[0].operation_modes
    }
    assert [labels[instruction.operation_mode] for instruction in instructions] == [
        "charge"
    ]
