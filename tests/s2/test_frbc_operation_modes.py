"""A schedule is asked for in the power values the actuator can actually run at.

An S2 actuator states, per operation mode and per element of it, the power ranges it can run in.
A heater that is either off or at 883.7 W has no power value in between,
and a heat pump has no negative one at all.
Asked for a schedule without saying so, FlexMeasures plans the whole range as if it were continuous,
and a resource manager then rounds a fractional power into a full-on block,
overshooting the capacity its site is held to.
These tests pin what the flex model now carries about that.
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

# The server this handler talks to by default in these tests: one that has the field.
A_SERVER_WITH_OPERATION_MODES = "1.1.0"


def _system_description(*power_ranges: tuple[float, float]) -> FRBCSystemDescription:
    """A storage whose single actuator has one operation mode per power range given."""
    operation_modes = [
        FRBCOperationMode(
            id=get_unique_id(),
            diagnostic_label=f"mode {index}",
            elements=[
                FRBCOperationModeElement(
                    fill_level_range=NumberRange(
                        start_of_range=RANGE_BOTTOM, end_of_range=RANGE_TOP
                    ),
                    fill_rate=NumberRange(start_of_range=0, end_of_range=1),
                    power_ranges=[
                        PowerRange(
                            start_of_range=lower,
                            end_of_range=upper,
                            commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                        )
                    ],
                )
            ],
            abnormal_condition_only=False,
        )
        for index, (lower, upper) in enumerate(power_ranges)
    ]
    actuator = FRBCActuatorDescription(
        id=get_unique_id(),
        supported_commodities=[Commodity.ELECTRICITY],
        operation_modes=operation_modes,
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


def _frbc(
    system_description,
    *,
    server_version: str | None = A_SERVER_WITH_OPERATION_MODES,
    **kwargs,
) -> FRBCSimple:
    """A handler that has had a system description and a storage status, ready to plan."""
    frbc = FRBCSimple(
        power_sensor_id=1,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        **kwargs,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._fm_client._resolve_server_version = AsyncMock(return_value=server_version)
    frbc._fm_client.server_version = server_version
    frbc._logger = logging.getLogger(__name__)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=FILL_LEVEL_AT_START
    )
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        return_value={
            "values": [0.0] * 48,
            "start": "2024-01-01T00:00:00+00:00",
            "duration": "PT12H",
        }
    )
    frbc.send_instruction_batch = AsyncMock(return_value=0)
    return frbc


async def _asked_for(frbc: FRBCSimple, system_description) -> dict:
    """The flex model one schedule was asked for with."""
    await frbc.trigger_schedule(system_description.message_id)
    return frbc._fm_client.trigger_and_get_schedule.await_args.kwargs["flex_model"]


@pytest.mark.asyncio
async def test_an_on_off_actuator_is_asked_for_its_two_power_values():
    """Everything between off and full on is a power the actuator cannot run at."""
    system_description = _system_description((0.0, 0.0), (883.7, 883.7))
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["operation-modes"] == [
        {"consumption-range": ["0 W", "0 W"]},
        {"consumption-range": ["883.7 W", "883.7 W"]},
    ]


@pytest.mark.asyncio
async def test_an_actuator_that_cannot_produce_is_given_no_production_capacity():
    """Left unsaid, a heat pump is planned as if its buffer could be discharged into the grid."""
    system_description = _system_description((0.0, 0.0), (200.0, 1000.0))
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["consumption-capacity"] == "1000.0 W"
    assert flex_model["production-capacity"] == "0.0 W"


@pytest.mark.asyncio
async def test_a_band_that_spans_zero_is_split_at_zero():
    """S2 states one signed range; FlexMeasures asks for the two directions separately."""
    system_description = _system_description((-500.0, 1000.0))
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["consumption-capacity"] == "1000.0 W"
    assert flex_model["production-capacity"] == "500.0 W"
    # One band, so the bands say no more than the capacities do and are left out.
    assert "operation-modes" not in flex_model


@pytest.mark.asyncio
async def test_a_band_that_spans_zero_reaches_both_directions_from_zero():
    """FlexMeasures reads the two ranges of one band as a single band through zero."""
    system_description = _system_description((0.0, 0.0), (-500.0, 1000.0))
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["operation-modes"] == [
        {"consumption-range": ["0 W", "0 W"]},
        {
            "consumption-range": ["0.0 W", "1000.0 W"],
            "production-range": ["0.0 W", "500.0 W"],
        },
    ]


@pytest.mark.asyncio
async def test_a_production_only_band_keeps_its_own_lower_bound():
    """A mode that only feeds back, and not from zero: its band does not start at zero."""
    system_description = _system_description((0.0, 0.0), (-2000.0, -500.0))
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["operation-modes"][1] == {
        "production-range": ["500.0 W", "2000.0 W"]
    }
    assert "consumption-range" not in flex_model["operation-modes"][1]


@pytest.mark.asyncio
async def test_the_same_power_range_twice_is_one_band():
    """Two operation modes differing in fill rate alone leave the scheduler one choice of power."""
    system_description = _system_description((0.0, 0.0), (883.7, 883.7), (883.7, 883.7))
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["operation-modes"] == [
        {"consumption-range": ["0 W", "0 W"]},
        {"consumption-range": ["883.7 W", "883.7 W"]},
    ]


@pytest.mark.asyncio
async def test_one_band_through_zero_is_the_capacity_range_itself():
    """The bands are asked for only where they say more than the capacities already do.

    The capacities already say the actuator can run at any power between them,
    so a single band from zero upwards is exactly that.
    """
    system_description = _system_description((0.0, 1000.0))
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert "operation-modes" not in flex_model
    assert flex_model["consumption-capacity"] == "1000.0 W"


@pytest.mark.asyncio
async def test_one_band_that_cannot_run_at_zero_is_asked_for():
    """An actuator that runs at 200 W or not at all rules out everything in between.

    The capacities cannot say that: they would leave the scheduler free to plan 50 W,
    which the actuator has no operation mode for.
    """
    system_description = _system_description((200.0, 1000.0))
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["operation-modes"] == [
        {"consumption-range": ["200.0 W", "1000.0 W"]}
    ]
    assert flex_model["consumption-capacity"] == "1000.0 W"


@pytest.mark.asyncio
async def test_one_band_that_only_feeds_back_is_asked_for():
    """A mode that only ever produces, and not down to zero, is the same case mirrored."""
    system_description = _system_description((-2000.0, -500.0))
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["operation-modes"] == [
        {"production-range": ["500.0 W", "2000.0 W"]}
    ]
    assert flex_model["production-capacity"] == "2000.0 W"
    assert flex_model["consumption-capacity"] == "0.0 W"


@pytest.mark.asyncio
async def test_a_server_without_the_field_is_not_asked_against_it(caplog):
    """A flex model naming a field the server does not know is refused outright.

    FlexMeasures has the operation-modes field from v1.0.0,
    and a refused trigger leaves the resource manager with no schedule at all,
    which is worse than a schedule planned without the bands.
    """
    system_description = _system_description((0.0, 0.0), (883.7, 883.7))
    frbc = _frbc(system_description, server_version="0.33.2")

    with caplog.at_level(logging.INFO):
        flex_model = await _asked_for(frbc, system_description)

    assert "operation-modes" not in flex_model
    # The capacities are older than that and still go out.
    assert flex_model["consumption-capacity"] == "883.7 W"
    assert flex_model["production-capacity"] == "0.0 W"
    assert "operation-modes flex-model field from v1.0.0" in caplog.text


@pytest.mark.asyncio
async def test_a_development_build_of_the_release_has_the_field():
    """A server reporting 1.0.0.dev26 already exposes the v1.0.0 flex model."""
    system_description = _system_description((0.0, 0.0), (883.7, 883.7))
    frbc = _frbc(system_description, server_version="1.0.0.dev26")

    flex_model = await _asked_for(frbc, system_description)

    assert len(flex_model["operation-modes"]) == 2


@pytest.mark.asyncio
async def test_the_bands_are_in_the_unit_the_handler_states_power_in():
    """A band in another unit than the capacities beside it is read a thousandfold out."""
    system_description = _system_description((0.0, 0.0), (0.8837, 0.8837))
    frbc = _frbc(system_description, power_unit="kW")

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["operation-modes"] == [
        {"consumption-range": ["0 kW", "0 kW"]},
        {"consumption-range": ["0.8837 kW", "0.8837 kW"]},
    ]
    assert flex_model["consumption-capacity"] == "0.8837 kW"


@pytest.mark.asyncio
async def test_a_bound_of_zero_is_stated_as_zero():
    """Flipping the sign of a range that reaches up to zero leaves a negative zero behind."""
    system_description = _system_description((0.0, 0.0), (-2000.0, 0.0))
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["operation-modes"][1] == {
        "production-range": ["0.0 W", "2000.0 W"]
    }
    assert flex_model["production-capacity"] == "2000.0 W"
