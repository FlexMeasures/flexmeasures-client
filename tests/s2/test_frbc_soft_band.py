"""What a resource manager declared about its storage is what a schedule is asked against.

A resource manager says where it wants its storage kept, what it expects to draw from it and
what it leaks; recording all of that changed nothing about the schedule that came back, because
the flex model named none of it. These tests pin what the flex model now carries, and that the
declared range is asked for as a band a breach of which is priced, rather than as a bound that
makes the states a storage really reaches infeasible.
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

SOC_MINIMA_SENSOR_ID = 10
SOC_MAXIMA_SENSOR_ID = 11
USAGE_FORECAST_SENSOR_ID = 12
LEAKAGE_SENSOR_ID = 13

# The fill-level range the storage below declares.
RANGE_BOTTOM = 20.0
RANGE_TOP = 80.0

# Where the storage says it is when a schedule is asked for: inside its declared range.
FILL_LEVEL_AT_START = 30.0


@pytest.fixture
def system_description() -> FRBCSystemDescription:
    """A storage declaring a fill-level range of [20, 80], and one trivial operation mode."""
    operation_mode = FRBCOperationMode(
        id=get_unique_id(),
        diagnostic_label="charge",
        elements=[
            FRBCOperationModeElement(
                fill_level_range=NumberRange(
                    start_of_range=RANGE_BOTTOM, end_of_range=RANGE_TOP
                ),
                fill_rate=NumberRange(start_of_range=0, end_of_range=1),
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=1000,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                    )
                ],
            )
        ],
        abnormal_condition_only=False,
    )
    actuator = FRBCActuatorDescription(
        id=get_unique_id(),
        supported_commodities=[Commodity.ELECTRICITY],
        operation_modes=[operation_mode],
        transitions=[],
        timers=[],
    )
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from="2024-01-01T00:00:00+00:00",
        actuators=[actuator],
        storage=FRBCStorageDescription(
            provides_leakage_behaviour=True,
            provides_fill_level_target_profile=True,
            provides_usage_forecast=True,
            fill_level_range=NumberRange(
                start_of_range=RANGE_BOTTOM, end_of_range=RANGE_TOP
            ),
        ),
    )


def _frbc(system_description, *, with_sensors: bool = True, **kwargs) -> FRBCSimple:
    """A handler that has had a system description and a storage status, ready to plan."""
    sensors = (
        dict(
            soc_minima_sensor_id=SOC_MINIMA_SENSOR_ID,
            soc_maxima_sensor_id=SOC_MAXIMA_SENSOR_ID,
            usage_forecast_sensor_id=USAGE_FORECAST_SENSOR_ID,
            leakage_behaviour_sensor_id=LEAKAGE_SENSOR_ID,
        )
        if with_sensors
        else {}
    )
    frbc = FRBCSimple(
        power_sensor_id=1,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        **sensors,
        **kwargs,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._logger = logging.getLogger(__name__)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    # A schedule is only asked for once a storage status has said where to start from.
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=FILL_LEVEL_AT_START
    )
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        return_value={
            "values": [0.0] * 48,  # twelve hours on quarter-hour steps
            "start": "2024-01-01T00:00:00+00:00",
            "duration": "PT12H",
        }
    )
    frbc.send_instruction_batch = AsyncMock(return_value=0)
    return frbc


async def _asked_for(frbc: FRBCSimple, system_description) -> tuple[dict, dict]:
    """The flex model and flex context one schedule was asked for with."""
    await frbc.trigger_schedule(system_description.message_id)
    kwargs = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs
    return kwargs["flex_model"], kwargs["flex_context"]


@pytest.mark.asyncio
async def test_the_declared_band_is_what_a_schedule_is_asked_against(
    system_description,
):
    """Recording the band changed nothing until the flex model named the sensors holding it."""
    frbc = _frbc(system_description)

    flex_model, flex_context = await _asked_for(frbc, system_description)

    assert flex_model["soc-minima"] == {"sensor": SOC_MINIMA_SENSOR_ID}
    assert flex_model["soc-maxima"] == {"sensor": SOC_MAXIMA_SENSOR_ID}
    # A minimum or maximum FlexMeasures may not breach is a hard bound by another name, and
    # the whole point of the band is that it is soft.
    assert flex_context["relax-soc-constraints"] is True


@pytest.mark.asyncio
async def test_the_usage_and_the_leakage_a_resource_manager_declares_are_planned_against(
    system_description,
):
    """A storage that is drawn from and leaks is not planned for by its prices alone."""
    frbc = _frbc(system_description)

    flex_model, _ = await _asked_for(frbc, system_description)

    assert flex_model["soc-usage"] == [{"sensor": USAGE_FORECAST_SENSOR_ID}]
    assert flex_model["storage-efficiency"] == {"sensor": LEAKAGE_SENSOR_ID}


@pytest.mark.asyncio
async def test_the_hard_bounds_are_safety_rails_rather_than_the_declared_range(
    system_description,
):
    """The declared range as a hard bound makes reachable states infeasible.

    A storage left above its range by a previous day, or free-floating past the top of it, is
    where a resource manager says its storage is; a bound refusing that is a job that fails
    before its first step, and an infeasible job is answered by the fallback scheduler, which
    ignores the band altogether.
    """
    frbc = _frbc(system_description)

    flex_model, _ = await _asked_for(frbc, system_description)

    assert flex_model["soc-min"] == 0.0
    assert flex_model["soc-max"] == pytest.approx(RANGE_TOP * 1.5)
    # Which is to say: not the range itself, which the minima and maxima above now carry.
    assert flex_model["soc-min"] != RANGE_BOTTOM
    assert flex_model["soc-max"] != RANGE_TOP


@pytest.mark.asyncio
async def test_the_rails_widen_to_take_in_a_start_state_beyond_them(
    system_description,
):
    """A start state outside the bounds is infeasible before the first step, so they give way."""
    frbc = _frbc(system_description)
    beyond_the_rails = RANGE_TOP * 1.5 + 100.0
    frbc._storage_status_history.clear()
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=beyond_the_rails
    )

    flex_model, _ = await _asked_for(frbc, system_description)

    assert flex_model["soc-max"] == pytest.approx(beyond_the_rails)
    assert flex_model["soc-min"] <= flex_model["soc-at-start"] <= flex_model["soc-max"]


@pytest.mark.asyncio
async def test_the_rails_are_in_the_same_scale_as_everything_else(system_description):
    """A rail in another scale than the level it holds is read a thousandfold out."""
    scale = 1000.0
    frbc = _frbc(system_description, fill_level_scale=scale)

    flex_model, _ = await _asked_for(frbc, system_description)

    assert flex_model["soc-max"] == pytest.approx(RANGE_TOP * scale * 1.5)
    assert flex_model["soc-at-start"] == pytest.approx(FILL_LEVEL_AT_START * scale)


@pytest.mark.asyncio
async def test_how_far_the_rails_sit_from_the_range_is_the_deployments_to_choose(
    system_description,
):
    """A deployment whose storage cannot overshoot its range can close the gap entirely."""
    frbc = _frbc(system_description, safety_rail_factor=1.0)

    flex_model, _ = await _asked_for(frbc, system_description)

    assert flex_model["soc-max"] == pytest.approx(RANGE_TOP)


@pytest.mark.asyncio
async def test_one_side_of_a_band_is_not_a_band(system_description):
    """One side alone is an open-ended push, not what the resource manager declared.

    A band needs both sides, which is also the rule the recording half goes by: a fill-level
    target profile is recorded only where both sensors are there to hold it.
    """
    frbc = _frbc(
        system_description,
        with_sensors=False,
        soc_minima_sensor_id=SOC_MINIMA_SENSOR_ID,
    )

    flex_model, flex_context = await _asked_for(frbc, system_description)

    assert "soc-minima" not in flex_model
    assert "soc-maxima" not in flex_model
    # And nothing is relaxed either, since there is no band for a breach price to apply to.
    assert "relax-soc-constraints" not in flex_context


def test_a_rail_inside_the_declared_range_is_refused():
    """FlexMeasures clips the band into the hard bounds, so a rail below one narrows the band.

    Silently: the resource manager would be answered against a band it never declared, so a
    factor under one is refused where it is configured rather than where it has an effect.
    """
    with pytest.raises(ValueError, match="safety_rail_factor has to be at least 1"):
        FRBCSimple(
            power_sensor_id=1,
            price_sensor_id=2,
            soc_sensor_id=3,
            rm_discharge_sensor_id=4,
            safety_rail_factor=0.9,
        )


@pytest.mark.asyncio
async def test_nothing_is_asked_against_a_sensor_that_was_never_configured(
    system_description,
):
    """An entry naming a sensor that holds nothing has the scheduler plan against nothing."""
    frbc = _frbc(system_description, with_sensors=False)

    flex_model, flex_context = await _asked_for(frbc, system_description)

    for absent in ("soc-minima", "soc-maxima", "soc-usage", "storage-efficiency"):
        assert absent not in flex_model
    assert "relax-soc-constraints" not in flex_context
    # The rails, the start state and the unit are not a resource manager's to declare, so they
    # are asked for whatever it configured.
    assert flex_model["soc-min"] == 0.0
    assert flex_model["soc-max"] == pytest.approx(RANGE_TOP * 1.5)
    assert flex_model["soc-at-start"] == pytest.approx(FILL_LEVEL_AT_START)
