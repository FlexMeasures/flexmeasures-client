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


# ---------------------------------------------------------------------------
# Following a step as a resource manager does (from the review of #258)
# ---------------------------------------------------------------------------

from flexmeasures_client.s2.control_types.FRBC.utils import (  # noqa: E402
    UsageProfile,
    follow_operation_mode,
)


def _mode(*elements, label="heat") -> FRBCOperationMode:
    return FRBCOperationMode(
        id=get_unique_id(),
        diagnostic_label=label,
        elements=list(elements),
        abnormal_condition_only=False,
    )


def _system_description_of(*modes) -> FRBCSystemDescription:
    description = _system_description(LOW)
    description.actuators[0].operation_modes = list(modes)
    return description


def test_an_on_off_heat_pump_is_told_to_switch_off():
    """On and off each run at one power, so both ask for factor 0: only the mode tells them apart."""
    on = _mode(
        _element(
            0,
            100,
            power_top=883.7,
            power_bottom=883.7,
            fill_rate_top=1e-3,
            fill_rate_bottom=1e-3,
        ),
        label="on",
    )
    off = _mode(_element(0, 100, power_top=0.0, fill_rate_top=0.0), label="off")
    instructions = fm_schedule_to_instructions(
        _schedule([883.7, 883.7, 0.0, 0.0]),
        _system_description_of(on, off),
        initial_fill_level=10.0,
    )
    assert [(i.execution_time, str(i.operation_mode)) for i in instructions] == [
        (START, str(on.id)),
        (START + 2 * QUARTER_HOUR, str(off.id)),
    ]


def test_a_solvers_rounding_is_not_a_new_instruction():
    """At the bottom of a modulating range, 800 W give or take a ten-millionth is one instruction, not one per step."""
    modulating = _mode(
        _element(
            0,
            100,
            power_top=1200.0,
            power_bottom=800.0,
            fill_rate_top=2e-3,
            fill_rate_bottom=1e-3,
        )
    )
    noisy = [800.0 + (1e-7 if i % 2 else -1e-7) for i in range(48)]
    instructions = fm_schedule_to_instructions(
        _schedule(noisy), _system_description_of(modulating), initial_fill_level=10.0
    )
    assert len(instructions) == 1


def test_usage_is_followed_through_the_step_not_sampled_at_its_start():
    """A five-minute draw in each quarter hour takes five minutes' worth, not none and not fifteen."""
    burst = _usage(*[(timedelta(minutes=5), 0.03), (timedelta(minutes=10), 0.0)] * 4)
    idle = _mode(_element(0, 100, power_top=0.0, fill_rate_top=0.0))
    assert follow_operation_mode(
        50.0, idle, 0.0, pd.Timestamp(START), QUARTER_HOUR, usage=UsageProfile(burst)
    ) == pytest.approx(50.0 - 0.03 * 300)


def test_usage_counts_from_where_the_forecast_starts():
    """A forecast from 7 minutes past counts from then: 8 minutes of usage in a step that began on the hour."""
    late = FRBCUsageForecast(
        message_id=get_unique_id(),
        start_time=START + timedelta(minutes=7),
        elements=[
            FRBCUsageForecastElement(
                duration=Duration(root=3600 * 1000), usage_rate_expected=0.01
            )
        ],
    )
    idle = _mode(_element(0, 100, power_top=0.0, fill_rate_top=0.0))
    assert follow_operation_mode(
        50.0, idle, 0.0, pd.Timestamp(START), QUARTER_HOUR, usage=UsageProfile(late)
    ) == pytest.approx(50.0 - 0.01 * 480)


#: Fills 0.02 per second at full tilt below 50, half that above, as a heat pump does when its buffer warms.
STEEP = _element(0, 50, power_top=1000.0, fill_rate_top=0.02)
SHALLOW = _element(50, 100, power_top=2000.0, fill_rate_top=0.01)


def test_a_step_that_crosses_into_another_element_is_followed_across_it():
    """From 46, factor 1 reaches 50 after 200 s, then fills at half the rate: 57, not the 64 of holding the first element."""
    level = follow_operation_mode(
        46.0, _mode(STEEP, SHALLOW), 1.0, pd.Timestamp(START), QUARTER_HOUR
    )
    assert level == pytest.approx(57.0)


def test_leakage_is_followed_across_an_element_boundary_too():
    """Leaking 0.01 per second above 50 and nothing below, a buffer at 54 settles at 50 rather than draining to 45."""
    leakage = _leakage((0, 50, 0.0), (50, 100, 0.01))
    idle = _mode(_element(0, 100, power_top=0.0, fill_rate_top=0.0))
    level = follow_operation_mode(
        54.0, idle, 0.0, pd.Timestamp(START), QUARTER_HOUR, leakage_behaviour=leakage
    )
    assert level == pytest.approx(50.0)


def test_a_gap_in_a_usage_column_is_no_usage_rather_than_a_buffer_full_to_the_top():
    index = pd.date_range(START, periods=4, freq=QUARTER_HOUR)
    with_gap = pd.DataFrame(
        {"schedule": [500.0] * 4, "usage_forecast": [float("nan")] * 4}, index=index
    )
    without = pd.DataFrame(
        {"schedule": [500.0] * 4, "usage_forecast": [0.0] * 4}, index=index
    )
    description = _system_description(LOW, HIGH)
    assert _factors(
        fm_schedule_to_instructions(with_gap, description, initial_fill_level=45.0)
    ) == _factors(
        fm_schedule_to_instructions(without, description, initial_fill_level=45.0)
    )


def test_a_frame_is_read_at_its_own_step():
    """An hourly frame: an hour at 500 W fills 12, so the buffer is past 50 by the second row."""
    frame = pd.DataFrame(
        {"schedule": [500.0] * 3},
        index=pd.date_range(START, periods=3, freq=timedelta(hours=1)),
    )
    instructions = fm_schedule_to_instructions(
        frame, _system_description(LOW, HIGH), initial_fill_level=45.0
    )
    assert _factors(instructions) == [
        (START, pytest.approx(0.5)),
        (START + timedelta(hours=1), pytest.approx(1 / 6)),
    ]


def test_a_schedule_duration_between_two_whole_steps_is_refused():
    """FlexMeasures answers with whole steps only, so such a schedule would be read at the wrong step."""
    with pytest.raises(ValueError, match="whole number of resolution steps"):
        FRBCSimple(
            power_sensor_id=1,
            price_sensor_id=2,
            soc_sensor_id=3,
            rm_discharge_sensor_id=4,
            schedule_duration=timedelta(hours=2, minutes=30),
            resolution=timedelta(hours=1),
        )


@pytest.mark.asyncio
async def test_the_handler_translates_with_the_latest_usage_forecast():
    system_description = _system_description(LOW, HIGH)
    frbc = _frbc(system_description)
    older, latest = _usage((timedelta(hours=1), 1e-4)), _usage(
        (timedelta(hours=1), 2e-4)
    )
    frbc._usage_forecast_history[str(older.message_id)] = older
    frbc._usage_forecast_history[str(latest.message_id)] = latest
    with patch(
        "flexmeasures_client.s2.control_types.FRBC.frbc_simple.fm_schedule_to_instructions",
        return_value=[],
    ) as translate:
        await frbc.trigger_schedule(system_description.message_id)
    assert translate.call_args.kwargs["usage_forecast"] is latest


# ---------------------------------------------------------------------------
# Followed exactly, between the moments anything changes (second round of review)
# ---------------------------------------------------------------------------

IDLE = _mode(_element(0, 100, power_top=0.0, fill_rate_top=0.0))


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(seconds=30)])
def test_a_draw_shorter_than_a_minute_counts_exactly_once(offset):
    """Half-minute draws every two minutes, starting on the minute or off it: a quarter hour holds 7 or 8 of them, exactly."""
    elements = []
    if offset:
        elements.append((offset, 0.0))
    while sum((d for d, _ in elements), timedelta(0)) < QUARTER_HOUR:
        elements += [(timedelta(seconds=30), 0.1), (timedelta(seconds=90), 0.0)]
    pulses = _usage(*elements)
    drawn_for = sum(
        (
            min(QUARTER_HOUR, start + d) - start
            for start, (d, rate) in zip(
                [
                    sum((e[0] for e in elements[:k]), timedelta(0))
                    for k in range(len(elements))
                ],
                elements,
            )
            if rate and start < QUARTER_HOUR
        ),
        timedelta(0),
    )
    level = follow_operation_mode(
        50.0, IDLE, 0.0, pd.Timestamp(START), QUARTER_HOUR, usage=UsageProfile(pulses)
    )
    assert level == pytest.approx(50.0 - 0.1 * drawn_for.total_seconds())


def test_a_level_held_at_a_boundary_stays_there_rather_than_flipping_across():
    """Filling below 50 and not above, while leaking everywhere: the level settles at 50, and one instruction serves 12 hours."""
    below = _element(0, 50, power_top=1000.0, fill_rate_top=0.02)
    above = _element(50, 100, power_top=1000.0, power_bottom=200.0, fill_rate_top=0.0)
    leaking = _leakage((0, 100, 0.01))
    instructions = fm_schedule_to_instructions(
        _schedule([600.0] * 48),
        _system_description_of(_mode(below, above)),
        initial_fill_level=40.0,
        leakage_behaviour=leaking,
    )
    assert len(instructions) <= 2, _factors(instructions)


def test_an_element_narrower_than_a_minutes_travel_is_not_skipped():
    """Filling at 1 per second below 50 and not at all above: from 20, the level stops at 50, not 30 past it."""
    stop = _mode(
        _element(0, 50, power_top=1000.0, fill_rate_top=1.0),
        _element(50, 100, power_top=1000.0, fill_rate_top=0.0),
    )
    assert follow_operation_mode(
        20.0, stop, 1.0, pd.Timestamp(START), QUARTER_HOUR
    ) == pytest.approx(50.0)


def test_a_step_that_is_not_a_whole_number_of_minutes_is_followed_to_its_end():
    level = follow_operation_mode(
        10.0, _mode(STEEP, SHALLOW), 1.0, pd.Timestamp(START), timedelta(seconds=90)
    )
    assert level == pytest.approx(10.0 + 0.02 * 90)


def test_a_level_draining_to_the_lower_bound_is_held_there():
    drained = follow_operation_mode(
        5.0,
        IDLE,
        0.0,
        pd.Timestamp(START),
        QUARTER_HOUR,
        usage=UsageProfile(rate=0.01),
        bounds=(0.0, 100.0),
    )
    assert drained == 0.0


def test_a_small_but_real_change_of_factor_is_instructed():
    """The tolerance absorbs conversion noise, not a change the schedule asks for: 600 W then 605 W is two instructions."""
    modulating = _mode(_element(0, 100, power_top=1000.0, fill_rate_top=1e-6))
    instructions = fm_schedule_to_instructions(
        _schedule([600.0, 605.0]),
        _system_description_of(modulating),
        initial_fill_level=10.0,
    )
    assert [round(f, 3) for _, f in _factors(instructions)] == [0.6, 0.605]


def test_a_usage_column_takes_precedence_over_a_forecast():
    """A caller that gives both means its column: 4 per quarter hour drawn, not the forecast's nothing."""
    frame = pd.DataFrame(
        {"schedule": [500.0] * 4, "usage_forecast": [4 / 900] * 4},
        index=pd.date_range(START, periods=4, freq=QUARTER_HOUR),
    )
    with_both = fm_schedule_to_instructions(
        frame,
        _system_description(LOW, HIGH),
        initial_fill_level=55.0,
        usage_forecast=_usage((timedelta(hours=1), 0.0)),
    )
    assert _factors(with_both)[1] == (START + 2 * QUARTER_HOUR, pytest.approx(0.5))


@pytest.mark.parametrize(
    "resolution, duration",
    [(timedelta(0), timedelta(hours=12)), (QUARTER_HOUR, timedelta(0))],
)
def test_a_handler_needs_a_positive_resolution_and_duration(resolution, duration):
    with pytest.raises(ValueError, match="have to be positive"):
        FRBCSimple(
            power_sensor_id=1,
            price_sensor_id=2,
            soc_sensor_id=3,
            rm_discharge_sensor_id=4,
            resolution=resolution,
            schedule_duration=duration,
        )


def test_a_long_schedule_against_a_detailed_forecast_is_translated_quickly():
    """48 hours, at quarter hours, against the largest forecast S2 allows: well under the 1.7 s that stepping each minute took."""
    import time

    detailed = _usage(*[(timedelta(minutes=10), 1e-4 * (i % 3)) for i in range(288)])
    started = time.perf_counter()
    fm_schedule_to_instructions(
        _schedule([500.0, 0.0] * 96),
        _system_description(LOW, HIGH),
        initial_fill_level=45.0,
        usage_forecast=detailed,
        leakage_behaviour=_leakage((0, 50, 1e-4), (50, 100, 2e-4)),
    )
    assert time.perf_counter() - started < 0.5


def test_a_level_held_at_its_lower_bound_rises_from_there_once_filling_wins():
    """Drained to 0 in the first 5 minutes, held there while the draw lasts, then filling from 0, not from below it."""
    draw_then_none = _usage((timedelta(minutes=10), 0.02), (timedelta(minutes=5), 0.0))
    filling = _mode(_element(0, 100, power_top=1000.0, fill_rate_top=0.01))
    level = follow_operation_mode(
        3.0,
        filling,
        1.0,
        pd.Timestamp(START),
        QUARTER_HOUR,
        usage=UsageProfile(draw_then_none),
        bounds=(0.0, 100.0),
    )
    assert level == pytest.approx(0.01 * 300)
