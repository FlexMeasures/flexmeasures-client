"""The time an S2 CEM goes by, and a co-simulation's way of setting it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from s2python.common import ReceptionStatusValues

from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.clock import SimulatedClock, WallClock
from flexmeasures_client.s2.control_types.FRBC import FRBCTest

SIMULATED = datetime(2022, 3, 1, 12, 0, tzinfo=timezone.utc)


def test_a_simulated_clock_stands_still_between_settings():
    clock = SimulatedClock()
    clock.set(SIMULATED)
    assert clock.now() == SIMULATED
    assert clock.now() == SIMULATED


def test_a_simulated_clock_goes_by_the_wall_clock_until_it_is_set():
    clock = SimulatedClock()
    assert not clock.is_set
    assert abs(clock.now() - WallClock().now()) < timedelta(seconds=5)


def test_a_simulated_time_without_a_zone_is_refused():
    """#188 took naive times as Europe/Amsterdam, which a simulation in UTC shifts by an hour or two."""
    with pytest.raises(ValueError, match="time zone"):
        SimulatedClock().set(datetime(2022, 3, 1, 12, 0))


def test_a_handler_registered_with_a_cem_goes_by_its_clock():
    clock = SimulatedClock(SIMULATED)
    cem = CEM(fm_client=None, clock=clock)
    frbc = FRBCTest()
    cem.register_control_type(frbc)
    assert frbc.now() == SIMULATED
    assert cem.now() == SIMULATED
    later = SIMULATED + timedelta(hours=3)
    clock.set(later)
    assert frbc.now() == later  # the same clock, not a copy of its time


def test_now_is_given_in_the_handlers_time_zone():
    cem = CEM(fm_client=None, clock=SimulatedClock(SIMULATED))
    frbc = FRBCTest()
    frbc._timezone = ZoneInfo("Europe/Amsterdam")
    cem.register_control_type(frbc)
    assert frbc.now().tzinfo == ZoneInfo("Europe/Amsterdam")
    assert frbc.now() == SIMULATED


def test_accepting_simulation_metadata_needs_a_simulated_clock():
    with pytest.raises(ValueError, match="SimulatedClock"):
        CEM(fm_client=None, clock=WallClock(), accept_simulation_metadata=True)


def _wrapped(message: dict, dt) -> dict:
    return {"message": message, "metadata": {"dt": dt}}


@pytest.mark.asyncio
async def test_a_wrapped_message_sets_the_clock_and_is_handled(rm_handshake):
    cem = CEM(fm_client=None, accept_simulation_metadata=True)
    await cem.handle_message(
        _wrapped(rm_handshake.model_dump(mode="json"), "2022-03-01T12:00:00+00:00")
    )
    assert cem.now() == SIMULATED
    reception = await cem.get_message()
    assert reception["status"] == ReceptionStatusValues.OK.value


@pytest.mark.asyncio
async def test_a_wrapped_message_is_refused_by_a_cem_that_does_not_accept_it(
    rm_handshake,
):
    """The wrapper is not S2, and a live CEM does not let a resource manager set its time."""
    cem = CEM(fm_client=None)
    before = cem.now()
    message = rm_handshake.model_dump(mode="json")
    await cem.handle_message(_wrapped(message, "2022-03-01T12:00:00+00:00"))
    reception = await cem.get_message()
    assert reception["status"] == ReceptionStatusValues.INVALID_MESSAGE.value
    assert reception["subject_message_id"] == message["message_id"]
    assert cem._sending_queue.empty()  # the handshake itself was not answered
    assert cem.now() - before < timedelta(seconds=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("dt", ["2022-03-01T12:00:00", "not a time"])
async def test_a_wrapped_message_with_an_unusable_time_is_refused(rm_handshake, dt):
    cem = CEM(fm_client=None, accept_simulation_metadata=True)
    message = rm_handshake.model_dump(mode="json")
    await cem.handle_message(_wrapped(message, dt))
    reception = await cem.get_message()
    assert reception["status"] == ReceptionStatusValues.INVALID_MESSAGE.value
    assert not cem._clock.is_set


@pytest.mark.asyncio
async def test_a_wrapper_around_no_message_is_refused_without_an_answer():
    """Nothing to answer: a reception status needs the id of the message it is about."""
    cem = CEM(fm_client=None)
    await cem.handle_message({"message": "garbage", "metadata": {}})
    assert cem._sending_queue.empty()


def test_a_cem_timer_runs_on_simulated_time():
    # On a period boundary, which a co-simulation's times are: the ceiling of the moment is the moment itself.
    clock = SimulatedClock(SIMULATED)
    cem = CEM(fm_client=None, clock=clock)
    assert cem._is_timer_due("power")
    assert not cem._is_timer_due("power")  # a clock standing still on a boundary
    clock.set(SIMULATED + timedelta(hours=1))
    assert cem._is_timer_due("power")


def test_a_timer_handed_in_without_a_zone_is_read_as_the_hosts_local_time():
    """As it was compared before, with a zoneless datetime.now(), which is the host's local time."""
    local = (SIMULATED + timedelta(minutes=30)).astimezone().replace(tzinfo=None)
    cem = CEM(fm_client=None, clock=SimulatedClock(SIMULATED), timers={"power": local})
    assert not cem._is_timer_due("power")
    cem._clock.set(SIMULATED + timedelta(minutes=31))
    assert cem._is_timer_due("power")


def test_the_example_server_accepts_simulation_metadata_only_when_told_to(monkeypatch):
    import importlib

    from flexmeasures_client.s2.script import websockets_server

    monkeypatch.setenv("S2_ACCEPT_SIMULATION_METADATA", "1")
    assert importlib.reload(websockets_server).ACCEPT_SIMULATION_METADATA is True
    monkeypatch.delenv("S2_ACCEPT_SIMULATION_METADATA")
    assert importlib.reload(websockets_server).ACCEPT_SIMULATION_METADATA is False


def test_a_tunes_timer_runs_on_the_clock_its_cem_gives_it():
    from flexmeasures_client.s2.control_types.FRBC.frbc_tunes import (
        FillRateBasedControlTUNES,
    )

    start = SIMULATED
    clock = SimulatedClock(start)
    cem = CEM(fm_client=None, clock=clock)
    tunes = FillRateBasedControlTUNES(
        asset_id=1,
        soc_minima_sensor_id=2,
        soc_maxima_sensor_id=3,
        rm_discharge_sensor_id=4,
        fill_level_sensor_id=7,
        thp_fill_rate_sensor_id=8,
        thp_efficiency_sensor_id=9,
        nes_fill_rate_sensor_id=10,
        nes_efficiency_sensor_id=11,
        usage_forecast_sensor_id=12,
        fill_rate_sensor_id=13,
        active_actuator_id_sensor_id=14,
        leakage_behaviour_sensor_id=15,
        production_price_sensor=16,
        consumption_price_sensor=17,
        state_of_charge_sensor_id=18,
    )
    cem.register_control_type(tunes)
    assert tunes._is_timer_due("usage_forecast")
    assert not tunes._is_timer_due(
        "usage_forecast"
    )  # a clock standing still on a boundary
    clock.set(start + timedelta(hours=1))
    assert tunes._is_timer_due("usage_forecast")


@pytest.mark.asyncio
@pytest.mark.parametrize("inner", ["garbage", {"no": "type"}])
async def test_a_wrapper_around_no_message_leaves_the_clock_alone(inner):
    """With the flag on, too: the clock is set only for a message that will be handled."""
    cem = CEM(fm_client=None, accept_simulation_metadata=True)
    await cem.handle_message(
        {"message": inner, "metadata": {"dt": "2022-03-01T12:00:00+00:00"}}
    )
    assert not cem._clock.is_set
    assert cem._sending_queue.empty()


def test_a_tunes_timer_handed_in_without_a_zone_is_read_as_the_hosts_local_time():
    from flexmeasures_client.s2.control_types.FRBC.frbc_tunes import (
        FillRateBasedControlTUNES,
    )

    start = SIMULATED
    clock = SimulatedClock(start)
    cem = CEM(fm_client=None, clock=clock)
    tunes = FillRateBasedControlTUNES(
        asset_id=1,
        soc_minima_sensor_id=2,
        soc_maxima_sensor_id=3,
        rm_discharge_sensor_id=4,
        fill_level_sensor_id=7,
        thp_fill_rate_sensor_id=8,
        thp_efficiency_sensor_id=9,
        nes_fill_rate_sensor_id=10,
        nes_efficiency_sensor_id=11,
        usage_forecast_sensor_id=12,
        fill_rate_sensor_id=13,
        active_actuator_id_sensor_id=14,
        leakage_behaviour_sensor_id=15,
        production_price_sensor=16,
        consumption_price_sensor=17,
        state_of_charge_sensor_id=18,
    )
    cem.register_control_type(tunes)
    tunes._timers["usage_forecast"] = (
        (start + timedelta(minutes=30)).astimezone().replace(tzinfo=None)
    )
    assert not tunes._is_timer_due("usage_forecast")


@pytest.mark.asyncio
async def test_a_state_of_charge_is_known_as_of_the_simulated_time():
    """Without a prior, FlexMeasures stamped a simulated reading with the moment the request arrived, years later."""
    from unittest.mock import AsyncMock

    from s2python.frbc import FRBCStorageStatus

    from flexmeasures_client.client import FlexMeasuresClient
    from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
    from flexmeasures_client.s2.utils import get_unique_id

    fm = AsyncMock(FlexMeasuresClient)
    fm.get_sensor = AsyncMock(return_value={"event_resolution": "PT0M"})
    frbc = FRBCSimple(
        power_sensor_id=1,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
    )
    cem = CEM(fm_client=fm, clock=SimulatedClock(SIMULATED))
    cem.register_control_type(frbc)
    await frbc.send_storage_status(
        FRBCStorageStatus(message_id=get_unique_id(), present_fill_level=5.0)
    )
    posted = fm.post_measurements.await_args.kwargs
    assert posted["start"] == SIMULATED
    assert posted["prior"] == SIMULATED
