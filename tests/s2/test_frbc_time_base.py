"""The time base under the instructions: every rate per second, as S2 states it.

S2 states a fill rate, a usage rate and a leakage rate each as a change in fill level per second.
The schedule translation keeps a running fill level, to answer each step with the operation mode for the fill level
the storage is then at, and it used to advance that level by a rate per second times a step in hours,
so it moved 3600 times too little, and every step of a twelve-hour schedule was answered at the fill level it started from.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

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
    FRBCActuatorStatus,
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

from flexmeasures_client.client import FlexMeasuresClient, _server_version_at_least
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.control_types.FRBC.utils import (
    compute_next_fill_level,
    fm_schedule_to_instructions,
    usage_rate_at,
)
from flexmeasures_client.s2.control_types.translations import (
    leakage_behaviour_to_storage_efficiency,
)
from flexmeasures_client.s2.utils import get_unique_id

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
QUARTER_HOUR = timedelta(minutes=15)


def _leakage(*elements: tuple[float, float, float]) -> FRBCLeakageBehaviour:
    return FRBCLeakageBehaviour(
        message_id=get_unique_id(),
        valid_from=START,
        elements=[
            FRBCLeakageBehaviourElement(
                fill_level_range=NumberRange(start_of_range=lo, end_of_range=hi),
                leakage_rate=rate,
            )
            for lo, hi, rate in elements
        ],
    )


def _usage(*elements: tuple[timedelta, float]) -> FRBCUsageForecast:
    return FRBCUsageForecast(
        message_id=get_unique_id(),
        start_time=START,
        elements=[
            FRBCUsageForecastElement(
                duration=Duration(root=int(duration / timedelta(milliseconds=1))),
                usage_rate_expected=rate,
            )
            for duration, rate in elements
        ],
    )


# ---------------------------------------------------------------------------
# The integrator's contract
# ---------------------------------------------------------------------------


def test_a_fill_rate_is_integrated_per_second():
    """0.001 per second for a quarter hour is 0.9, which the old integrator made 0.00025."""
    assert compute_next_fill_level(1.0, 0.001, QUARTER_HOUR) == pytest.approx(1.9)


def test_usage_lowers_the_fill_level_per_second():
    assert compute_next_fill_level(
        10.0, 0.0, QUARTER_HOUR, usage_rate=0.0005
    ) == pytest.approx(10.0 - 0.45)


def test_leakage_lowers_the_fill_level_at_the_rate_of_the_element_that_applies():
    """At 50, in the leakier upper element, an hour loses 3.6; the lower element would lose 0.36."""
    leakage = _leakage((50, 100, 0.001), (0, 50, 0.0001))
    assert compute_next_fill_level(
        50.0, 0.0, timedelta(hours=1), leakage_behaviour=leakage
    ) == pytest.approx(46.4)
    assert compute_next_fill_level(
        20.0, 0.0, timedelta(hours=1), leakage_behaviour=leakage
    ) == pytest.approx(19.64)


@pytest.mark.parametrize(
    "moment, expected",
    [
        (START - timedelta(minutes=1), 0.0),
        (START + timedelta(minutes=30), 0.001),
        (START + timedelta(minutes=90), 0.002),
        # Where one element ends, the next begins, and its rate applies.
        (START + timedelta(minutes=60), 0.002),
        (START + timedelta(minutes=150), 0.0),
    ],
)
def test_the_usage_rate_a_forecast_expects_at_a_moment(moment, expected):
    """Elements follow one another for their own durations; outside them nothing is forecast."""
    forecast = _usage((timedelta(hours=1), 0.001), (timedelta(hours=1), 0.002))
    assert usage_rate_at(forecast, pd.Timestamp(moment)) == pytest.approx(expected)


def test_without_a_forecast_nothing_is_used():
    assert usage_rate_at(None, pd.Timestamp(START)) == 0.0


# ---------------------------------------------------------------------------
# The translation
# ---------------------------------------------------------------------------


def _element(lo, hi, power_top, fill_rate_top, power_bottom=0.0, fill_rate_bottom=0.0):
    return FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=lo, end_of_range=hi),
        fill_rate=NumberRange(
            start_of_range=fill_rate_bottom, end_of_range=fill_rate_top
        ),
        power_ranges=[
            PowerRange(
                start_of_range=power_bottom,
                end_of_range=power_top,
                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
            )
        ],
    )


def _system_description(*elements) -> FRBCSystemDescription:
    mode = FRBCOperationMode(
        id=get_unique_id(),
        diagnostic_label="heat",
        elements=list(elements),
        abnormal_condition_only=False,
    )
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from=START,
        actuators=[
            FRBCActuatorDescription(
                id=get_unique_id(),
                supported_commodities=[Commodity.ELECTRICITY],
                operation_modes=[mode],
                transitions=[],
                timers=[],
            )
        ],
        storage=FRBCStorageDescription(
            provides_leakage_behaviour=True,
            provides_fill_level_target_profile=False,
            provides_usage_forecast=True,
            fill_level_range=NumberRange(start_of_range=0, end_of_range=100),
        ),
    )


#: Below 50 a mode draws up to 1000 W, above it up to 3000 W, filling at most 1/150 per second either way.
#: At a constant 500 W it fills 3 per quarter hour below 50, and 1 per quarter hour above it.
LOW = _element(0, 50, power_top=1000.0, fill_rate_top=1 / 150)
HIGH = _element(50, 100, power_top=3000.0, fill_rate_top=1 / 150)


def _schedule(values, step=QUARTER_HOUR) -> dict:
    return {
        "start": START.isoformat(),
        "duration": pd.Timedelta(step * len(values)).isoformat(),
        "values": values,
    }


def _factors(instructions) -> list[tuple[datetime, float]]:
    return [(i.execution_time, i.operation_mode_factor) for i in instructions]


def test_a_constant_power_is_instructed_anew_where_the_fill_level_crosses_into_another_element():
    """500 W is half of what the lower element draws at full tilt, and a sixth of the upper one's.

    From 45, three quarter hours of filling at 3 each take the buffer past 50,
    where the same 500 W needs another factor, which is a new instruction although the power did not change.
    """
    instructions = fm_schedule_to_instructions(
        _schedule([500.0] * 4), _system_description(LOW, HIGH), initial_fill_level=45.0
    )
    assert _factors(instructions) == [
        (START, pytest.approx(0.5)),
        (START + 2 * QUARTER_HOUR, pytest.approx(1 / 6)),
    ]


def test_usage_brings_the_running_fill_level_down_across_a_boundary():
    """Filling 1 per quarter hour above 50 while 4 is used leaves 3 less each step, so from 55 the buffer drops below 50."""
    instructions = fm_schedule_to_instructions(
        _schedule([500.0] * 4),
        _system_description(LOW, HIGH),
        initial_fill_level=55.0,
        usage_forecast=_usage((timedelta(hours=1), 4 / 900)),
    )
    assert _factors(instructions) == [
        (START, pytest.approx(1 / 6)),
        (START + 2 * QUARTER_HOUR, pytest.approx(0.5)),
    ]


def test_leakage_brings_the_running_fill_level_down_across_a_boundary():
    instructions = fm_schedule_to_instructions(
        _schedule([500.0] * 4),
        _system_description(LOW, HIGH),
        initial_fill_level=55.0,
        leakage_behaviour=_leakage((0, 100, 4 / 900)),
    )
    assert _factors(instructions) == [
        (START, pytest.approx(1 / 6)),
        (START + 2 * QUARTER_HOUR, pytest.approx(0.5)),
    ]


def test_a_usage_column_takes_the_place_of_a_forecast():
    """A caller holding its usage per step in the frame, as TUNES does, passes it per second."""
    frame = pd.DataFrame(
        {"schedule": [500.0] * 4, "usage_forecast": [4 / 900] * 4},
        index=pd.date_range(START, periods=4, freq=QUARTER_HOUR),
    )
    instructions = fm_schedule_to_instructions(
        frame, _system_description(LOW, HIGH), initial_fill_level=55.0
    )
    assert _factors(instructions)[1] == (
        START + 2 * QUARTER_HOUR,
        pytest.approx(0.5),
    )


@pytest.mark.parametrize(
    "step", [timedelta(minutes=5), QUARTER_HOUR, timedelta(hours=1)]
)
def test_a_schedule_is_read_at_the_resolution_it_comes_in(step):
    """FlexMeasures answers at the power sensor's resolution, which was taken to be a quarter hour whatever it was.

    Twelve hourly values were laid on forty-eight quarter hours, and pandas refused, so no instruction went out.
    """
    instructions = fm_schedule_to_instructions(
        _schedule([0.0] * 6 + [1000.0] * 6, step=step),
        _system_description(_element(0, 100, power_top=1000.0, fill_rate_top=1e-9)),
        initial_fill_level=10.0,
    )
    assert _factors(instructions) == [
        (START, pytest.approx(0.0)),
        (START + 6 * step, pytest.approx(1.0)),
    ]


def test_the_instruction_asks_for_the_factor_that_draws_the_power():
    """A mode running 800 to 1200 W at one fill rate, asked for 1200 W, is asked for factor 1, which draws 1200 W."""
    instructions = fm_schedule_to_instructions(
        _schedule([1200.0]),
        _system_description(
            _element(
                0,
                100,
                power_top=1200.0,
                power_bottom=800.0,
                fill_rate_top=2e-3,
                fill_rate_bottom=2e-3,
            )
        ),
        initial_fill_level=10.0,
    )
    assert _factors(instructions) == [(START, pytest.approx(1.0))]


# ---------------------------------------------------------------------------
# The storage efficiency
# ---------------------------------------------------------------------------


def test_the_storage_efficiency_compounds_a_leakage_rate_per_second():
    """A 10 kWh buffer losing 1.5 kWh a day keeps 99.844% per quarter hour, about 92.8% over twelve hours."""
    efficiency = leakage_behaviour_to_storage_efficiency(
        _leakage((0, 10, 1.5 / 86400)), resolution=QUARTER_HOUR
    )
    assert efficiency == pytest.approx(100 * (1 - 1.5 / 86400 / 10) ** 900, rel=1e-12)
    assert 100 * (efficiency / 100) ** 48 == pytest.approx(92.774, abs=1e-3)


# ---------------------------------------------------------------------------
# The handler
# ---------------------------------------------------------------------------


def _frbc(system_description: FRBCSystemDescription, **kwargs) -> FRBCSimple:
    frbc = FRBCSimple(
        power_sensor_id=1,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        **kwargs,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._fm_client.server_version_at_least = AsyncMock(
        side_effect=lambda minimum: _server_version_at_least("1.1.0", minimum)
    )
    frbc._logger = logging.getLogger("FRBCSimple")
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=45.0
    )
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        return_value=_schedule([500.0] * 4)
    )
    frbc.send_instruction_batch = AsyncMock(return_value=0)
    frbc._as_the_power_sensor_reads_it = AsyncMock(side_effect=lambda power: power)
    return frbc


@pytest.mark.asyncio
async def test_the_handler_translates_with_the_latest_leakage_and_usage():
    system_description = _system_description(LOW, HIGH)
    frbc = _frbc(system_description, resolution=timedelta(minutes=5))
    older, latest = _leakage((0, 100, 1e-4)), _leakage((0, 100, 2e-4))
    frbc._leakage_behaviour_history[str(older.message_id)] = older
    frbc._leakage_behaviour_history[str(latest.message_id)] = latest
    forecast = _usage((timedelta(hours=1), 1e-3))
    frbc._usage_forecast_history[str(forecast.message_id)] = forecast

    with patch(
        "flexmeasures_client.s2.control_types.FRBC.frbc_simple.fm_schedule_to_instructions",
        return_value=[],
    ) as translate:
        await frbc.trigger_schedule(system_description.message_id)

    kwargs = translate.call_args.kwargs
    assert kwargs["leakage_behaviour"] is latest
    assert kwargs["usage_forecast"] is forecast


@pytest.mark.asyncio
async def test_a_measurement_covers_the_handlers_own_resolution():
    """A power measurement for one step of a five-minute grid is not posted as covering a quarter hour."""
    system_description = _system_description(LOW, HIGH)
    frbc = _frbc(
        system_description, resolution=timedelta(minutes=5), fill_rate_sensor_id=6
    )
    actuator = system_description.actuators[0]
    await frbc.send_actuator_status(
        FRBCActuatorStatus(
            message_id=get_unique_id(),
            actuator_id=actuator.id,
            active_operation_mode_id=actuator.operation_modes[0].id,
            operation_mode_factor=0.5,
        )
    )
    durations = {
        (call.args[:1] or (call.kwargs.get("sensor_id"),))[0]: call.kwargs["duration"]
        for call in frbc._fm_client.post_sensor_data.await_args_list
    }
    assert durations == {1: timedelta(minutes=5), 6: timedelta(minutes=5)}


def test_a_schedule_without_values_asks_for_nothing():
    """Its step would be its duration over no values, so there is none, and no instruction either."""
    assert (
        fm_schedule_to_instructions(
            {"start": START.isoformat(), "duration": "PT0H", "values": []},
            _system_description(LOW, HIGH),
            initial_fill_level=45.0,
        )
        == []
    )
