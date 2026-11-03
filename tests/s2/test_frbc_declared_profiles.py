"""What a resource manager declares about its storage is recorded, instead of crashing the CEM.

A standard FRBC resource manager announces three things after its system description: the fill
levels it wants to be kept between, the usage it expects, and what its storage leaks. The base
handler raises NotImplementedError for all three, and the CEM dispatches each as a task nobody
awaits, so a connection produced three "Task exception was never retrieved" tracebacks and
recorded none of it.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from s2python.common import (
    Commodity,
    CommodityQuantity,
    Duration,
    NumberRange,
    PowerRange,
)
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCFillLevelTargetProfile,
    FRBCFillLevelTargetProfileElement,
    FRBCLeakageBehaviour,
    FRBCLeakageBehaviourElement,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCStorageStatus,
    FRBCSystemDescription,
    FRBCUsageForecast,
    FRBCUsageForecastElement,
)

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.control_types.FRBC.utils import (
    clip_fill_level_target_profile,
)
from flexmeasures_client.s2.utils import get_unique_id

SOC_MINIMA_SENSOR_ID = 10
SOC_MAXIMA_SENSOR_ID = 11
USAGE_FORECAST_SENSOR_ID = 12
LEAKAGE_SENSOR_ID = 13

# The declared range the target profile is held inside.
RANGE_BOTTOM = 20.0
RANGE_TOP = 80.0


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


def _frbc(system_description, *, with_sensors: bool) -> FRBCSimple:
    """A handler with or without the sensors these declarations are recorded on."""
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
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._logger = logging.getLogger(__name__)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    return frbc


def _target_profile(bottom: float, top: float) -> FRBCFillLevelTargetProfile:
    """One hour of target band, as a resource manager sends it: durations in milliseconds."""
    return FRBCFillLevelTargetProfile(
        message_id=get_unique_id(),
        start_time="2024-01-01T12:00:00+00:00",
        elements=[
            FRBCFillLevelTargetProfileElement(
                duration=Duration.from_timedelta(timedelta(hours=1)),
                fill_level_range=NumberRange(start_of_range=bottom, end_of_range=top),
            )
        ],
    )


def _posted(frbc: FRBCSimple, sensor_id: int) -> dict:
    for call in frbc._fm_client.post_sensor_data.await_args_list:
        if (
            call.kwargs.get("sensor_id", call.args[0] if call.args else None)
            == sensor_id
        ):
            return call.kwargs
    raise AssertionError(f"nothing was posted to sensor {sensor_id}")


def _posted_measurement(frbc: FRBCSimple, sensor_id: int) -> dict:
    """A state of charge still goes out through the deprecated post_measurements alias."""
    for call in frbc._fm_client.post_measurements.await_args_list:
        if (
            call.kwargs.get("sensor_id", call.args[0] if call.args else None)
            == sensor_id
        ):
            return call.kwargs
    raise AssertionError(f"nothing was posted to sensor {sensor_id}")


def _posted_sensor_ids(frbc: FRBCSimple) -> list[int]:
    return [
        call.kwargs.get("sensor_id", call.args[0] if call.args else None)
        for call in frbc._fm_client.post_sensor_data.await_args_list
    ]


# --- The clipping rule on its own ---


def test_clipping_holds_a_profile_inside_the_declared_range():
    """A band reaching past both ends of the range comes back inside it."""
    minima = pd.Series([0.0, 10.0, 50.0])
    maxima = pd.Series([95.0, 100.0, 60.0])

    clipped_minima, clipped_maxima, number_crossed = clip_fill_level_target_profile(
        minima, maxima, RANGE_BOTTOM, RANGE_TOP
    )

    assert clipped_minima.tolist() == [RANGE_BOTTOM, RANGE_BOTTOM, 50.0]
    assert clipped_maxima.tolist() == [RANGE_TOP, RANGE_TOP, 60.0]
    assert number_crossed == 0


def test_clipping_collapses_a_band_that_falls_outside_the_range():
    """A band wholly outside the range collapses onto the nearest edge of it, not past it.

    [0, 10] is entirely below [20, 80], so the minimum clips up to 20 while the maximum stays
    at 10, which crosses. Their midpoint is 15, which is itself outside the range, so it is
    held at the bottom of it. The mirror case collapses onto the top.
    """
    minima = pd.Series([0.0, 90.0, 50.0])
    maxima = pd.Series([10.0, 95.0, 60.0])

    clipped_minima, clipped_maxima, number_crossed = clip_fill_level_target_profile(
        minima, maxima, RANGE_BOTTOM, RANGE_TOP
    )

    assert number_crossed == 2
    assert (clipped_minima <= clipped_maxima).all()
    # Wholly below, so onto the bottom; wholly above, so onto the top.
    assert clipped_minima[0] == clipped_maxima[0] == RANGE_BOTTOM
    assert clipped_minima[1] == clipped_maxima[1] == RANGE_TOP
    # Nothing lands outside the range, which is what the whole function is for.
    assert (clipped_minima >= RANGE_BOTTOM).all() and (
        clipped_maxima <= RANGE_TOP
    ).all()
    # The step that was already inside the range is untouched.
    assert clipped_minima[2] == 50.0 and clipped_maxima[2] == 60.0


# --- The three handlers, with the sensors configured ---


@pytest.mark.asyncio
async def test_fill_level_target_profile_is_recorded_clipped(system_description):
    frbc = _frbc(system_description, with_sensors=True)

    # Asks for [0, 95], which reaches past both ends of the declared [20, 80].
    await frbc.send_fill_level_target_profile(_target_profile(0.0, 95.0))

    minima = _posted(frbc, SOC_MINIMA_SENSOR_ID)
    maxima = _posted(frbc, SOC_MAXIMA_SENSOR_ID)
    assert set(minima["values"]) == {RANGE_BOTTOM}
    assert set(maxima["values"]) == {RANGE_TOP}
    assert minima["unit"] == frbc.energy_unit == "kWh"
    # One hour on quarter-hour steps.
    assert len(minima["values"]) == 4
    assert minima["duration"] == pd.Timedelta("1h")


@pytest.mark.asyncio
async def test_usage_forecast_is_recorded_from_a_resolution_boundary(
    system_description,
):
    frbc = _frbc(system_description, with_sensors=True)
    usage_forecast = FRBCUsageForecast(
        message_id=get_unique_id(),
        # Deliberately off a boundary, as a resource manager's own clock would be.
        start_time="2024-01-01T12:07:23+00:00",
        elements=[
            FRBCUsageForecastElement(
                duration=Duration.from_timedelta(timedelta(hours=1)),
                usage_rate_expected=0.5,
            )
        ],
    )

    await frbc.send_usage_forecast(usage_forecast)

    posted = _posted(frbc, USAGE_FORECAST_SENSOR_ID)
    assert posted["start"] == frbc.align_to_resolution(usage_forecast.start_time)
    assert posted["start"].minute % 15 == 0 and posted["start"].second == 0
    assert posted["unit"] == frbc.power_unit


@pytest.mark.asyncio
async def test_leakage_behaviour_is_recorded_as_a_storage_efficiency(
    system_description,
):
    frbc = _frbc(system_description, with_sensors=True)
    leakage = FRBCLeakageBehaviour(
        message_id=get_unique_id(),
        valid_from="2024-01-01T12:07:23+00:00",
        elements=[
            FRBCLeakageBehaviourElement(
                fill_level_range=NumberRange(
                    start_of_range=RANGE_BOTTOM, end_of_range=RANGE_TOP
                ),
                leakage_rate=0.01,
            )
        ],
    )

    await frbc.send_leakage_behaviour(leakage)

    posted = _posted(frbc, LEAKAGE_SENSOR_ID)
    assert posted["unit"] == "%"
    assert len(posted["values"]) == 1
    assert posted["start"].minute % 15 == 0 and posted["start"].second == 0


# --- Without the sensors: nothing recorded, and still no exception ---


@pytest.mark.asyncio
async def test_the_three_declarations_do_not_raise_without_their_sensors(
    system_description,
):
    """The regression. Each of these raised NotImplementedError from the base handler before."""
    frbc = _frbc(system_description, with_sensors=False)

    await frbc.send_fill_level_target_profile(_target_profile(30.0, 60.0))
    await frbc.send_usage_forecast(
        FRBCUsageForecast(
            message_id=get_unique_id(),
            start_time="2024-01-01T12:00:00+00:00",
            elements=[
                FRBCUsageForecastElement(
                    duration=Duration.from_timedelta(timedelta(hours=1)),
                    usage_rate_expected=0.5,
                )
            ],
        )
    )
    await frbc.send_leakage_behaviour(
        FRBCLeakageBehaviour(
            message_id=get_unique_id(),
            valid_from="2024-01-01T12:00:00+00:00",
            elements=[
                FRBCLeakageBehaviourElement(
                    fill_level_range=NumberRange(
                        start_of_range=RANGE_BOTTOM, end_of_range=RANGE_TOP
                    ),
                    leakage_rate=0.01,
                )
            ],
        )
    )

    assert _posted_sensor_ids(frbc) == []


@pytest.mark.asyncio
async def test_a_target_profile_needs_both_bounds_to_be_recorded(system_description):
    """A band is two series, so one sensor without the other records neither."""
    frbc = _frbc(system_description, with_sensors=True)
    frbc._soc_maxima_sensor_id = None

    await frbc.send_fill_level_target_profile(_target_profile(30.0, 60.0))

    assert _posted_sensor_ids(frbc) == []


# --- One unit for a fill level and the bounds around it ---


@pytest.mark.asyncio
async def test_a_state_of_charge_and_its_bounds_share_one_unit(system_description):
    """A band in one unit around a level in another would be read a thousandfold out.

    The example server creates its state-of-charge sensor in kWh, while this handler used to
    post that level declaring MWh, so FlexMeasures converted a fill level of 10 into 10,000.
    """
    frbc = _frbc(system_description, with_sensors=True)
    frbc._soc_sensor_id = 3

    await frbc.send_storage_status(
        FRBCStorageStatus(message_id=get_unique_id(), present_fill_level=10.0)
    )
    await frbc.send_fill_level_target_profile(_target_profile(30.0, 60.0))

    level_unit = _posted_measurement(frbc, 3)["unit"]
    minima_unit = _posted(frbc, SOC_MINIMA_SENSOR_ID)["unit"]
    maxima_unit = _posted(frbc, SOC_MAXIMA_SENSOR_ID)["unit"]
    assert level_unit == minima_unit == maxima_unit == "kWh"


@pytest.mark.asyncio
async def test_the_energy_unit_is_the_deployment_s_to_choose(system_description):
    """A deployment at another scale says so once, and everything follows it."""
    frbc = FRBCSimple(
        power_sensor_id=1,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        soc_minima_sensor_id=SOC_MINIMA_SENSOR_ID,
        soc_maxima_sensor_id=SOC_MAXIMA_SENSOR_ID,
        energy_unit="MWh",
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._logger = logging.getLogger(__name__)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }

    await frbc.send_storage_status(
        FRBCStorageStatus(message_id=get_unique_id(), present_fill_level=10.0)
    )
    await frbc.send_fill_level_target_profile(_target_profile(30.0, 60.0))

    assert _posted_measurement(frbc, 3)["unit"] == "MWh"
    assert _posted(frbc, SOC_MINIMA_SENSOR_ID)["unit"] == "MWh"


@pytest.mark.asyncio
async def test_a_schedule_is_asked_for_in_the_unit_the_fill_level_is_recorded_in(
    system_description,
):
    """soc-unit has to follow energy_unit, or the scheduler plans against bounds a thousand times its measurements."""
    frbc = _frbc(system_description, with_sensors=True)
    # A schedule is only asked for once a storage status has said where to start from.
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=30.0
    )
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        return_value={
            "values": [0.0] * 48,  # twelve hours on quarter-hour steps
            "start": "2024-01-01T00:00:00+00:00",
            "duration": "PT12H",
        }
    )
    frbc.send_instruction_batch = AsyncMock(return_value=0)

    await frbc.trigger_schedule(system_description.message_id)

    flex_model = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs[
        "flex_model"
    ]
    assert flex_model["soc-unit"] == frbc.energy_unit == "kWh"


@pytest.mark.asyncio
async def test_a_target_profile_is_posted_from_the_boundary_it_was_translated_onto(
    system_description,
):
    """The translation floors the start, so posting at the raw start_time would shift every value."""
    frbc = _frbc(system_description, with_sensors=True)
    profile = _target_profile(30.0, 60.0)
    # Move it off a boundary, as a resource manager's own clock would be.
    profile.start_time = pd.Timestamp("2024-01-01T12:07:23+00:00").to_pydatetime()

    await frbc.send_fill_level_target_profile(profile)

    posted_start = _posted(frbc, SOC_MINIMA_SENSOR_ID)["start"]
    assert posted_start == frbc.align_to_resolution(profile.start_time)
    assert posted_start.minute % 15 == 0 and posted_start.second == 0


@pytest.mark.asyncio
async def test_every_fill_level_follows_the_same_scale(system_description):
    """A level, the band around it and the scheduler's bounds must all be in one scale."""
    scale = 1000.0
    frbc = FRBCSimple(
        power_sensor_id=1,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        soc_minima_sensor_id=SOC_MINIMA_SENSOR_ID,
        soc_maxima_sensor_id=SOC_MAXIMA_SENSOR_ID,
        fill_level_scale=scale,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._logger = logging.getLogger(__name__)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=30.0
    )
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        return_value={
            "values": [0.0] * 48,
            "start": "2024-01-01T00:00:00+00:00",
            "duration": "PT12H",
        }
    )
    frbc.send_instruction_batch = AsyncMock(return_value=0)

    await frbc.send_storage_status(
        FRBCStorageStatus(message_id=get_unique_id(), present_fill_level=30.0)
    )
    await frbc.send_fill_level_target_profile(_target_profile(30.0, 60.0))
    await frbc.trigger_schedule(system_description.message_id)

    assert _posted_measurement(frbc, 3)["values"] == [30.0 * scale]
    assert min(_posted(frbc, SOC_MINIMA_SENSOR_ID)["values"]) == 30.0 * scale
    flex_model = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs[
        "flex_model"
    ]
    assert flex_model["soc-min"] == RANGE_BOTTOM * scale
    assert flex_model["soc-max"] == RANGE_TOP * scale
