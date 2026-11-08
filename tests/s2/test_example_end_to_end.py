"""The shipped example, both halves, end to end: the resource manager it ships, served by the CEM it ships.

Only FlexMeasures is stood in for, and the websocket between the two is held in memory.
The example used to send no instruction at all, for two reasons the halves only show together:
the resource manager sent its storage status before its system description, which planned nothing,
and its description was dated ten months before the prices the server seeds.
"""

from __future__ import annotations

import asyncio
import gc
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiohttp
import pandas as pd
import pytest

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.script import websockets_server as server
from flexmeasures_client.s2.script.websockets_client import run_resource_manager


class _Text:
    type = aiohttp.WSMsgType.TEXT

    def __init__(self, data: str):
        self.data = data

    def json(self) -> dict:
        return json.loads(self.data)


#: Put on an end's inbox, it ends that end's stream of messages, as a websocket closed by its peer does.
_CLOSED = object()


class _End:
    """One end of an in-memory websocket: what it sends arrives at the other end."""

    def __init__(self, inbox: asyncio.Queue, outbox: asyncio.Queue):
        self._inbox, self._outbox = inbox, outbox
        self.received: list[dict] = []

    async def send_str(self, data: str) -> None:
        await self._outbox.put(data)

    async def send_json(self, message) -> None:
        await self._outbox.put(json.dumps(message))

    async def receive(self) -> _Text:
        data = await self._inbox.get()
        self.received.append(json.loads(data))
        return _Text(data)

    def __aiter__(self):
        return self

    async def __anext__(self) -> _Text:
        data = await self._inbox.get()
        if data is _CLOSED:
            raise StopAsyncIteration
        self.received.append(json.loads(data))
        return _Text(data)

    async def close(self) -> None:
        pass

    def of_type(self, message_type: str) -> list[dict]:
        return [m for m in self.received if m.get("message_type") == message_type]


def _connected() -> tuple[_End, _End]:
    to_resource_manager, to_cem = asyncio.Queue(), asyncio.Queue()
    return _End(to_resource_manager, to_cem), _End(to_cem, to_resource_manager)


#: What the stand-in FlexMeasures plans, per quarter hour: idle, then full heat, then minimum heat.
SHAPE = [0.0, 0.0, 1500.0, 1500.0, 400.0, 400.0, 0.0, 0.0]


def _flexmeasures() -> AsyncMock:
    fm = AsyncMock(FlexMeasuresClient)
    fm.server_version_at_least = AsyncMock(return_value=True)
    fm.get_sensor = AsyncMock(
        return_value={"attributes": {"consumption_is_positive": True}}
    )

    def schedule(start, duration, **kwargs) -> dict:
        steps = int(pd.Timedelta(duration) / pd.Timedelta(minutes=15))
        return {
            "start": pd.Timestamp(start).isoformat(),
            "duration": pd.Timedelta(duration).isoformat(),
            "values": [SHAPE[i % len(SHAPE)] for i in range(steps)],
            "scheduler_info": {"scheduler": "StorageScheduler"},
        }

    fm.trigger_and_get_schedule = AsyncMock(side_effect=lambda **kw: schedule(**kw))
    return fm


async def _until(condition, timeout: float = 15.0) -> None:
    async def wait():
        while not condition():
            await asyncio.sleep(0.05)

    await asyncio.wait_for(wait(), timeout)


@pytest.mark.asyncio
async def test_the_shipped_resource_manager_is_planned_for_and_instructed():
    resource_manager, cem = _connected()
    fm = _flexmeasures()
    sensors = {name: {"id": i} for i, name in enumerate(server.SITE_SENSORS, start=1)}
    unhandled: list[dict] = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _, context: unhandled.append(context))
    began = datetime.now(timezone.utc)
    # The CEM's clock stands on the boundary the step under way began at, so that is where its schedules start,
    # and the first instruction is due at once rather than at the next boundary, up to a quarter of an hour away.
    on_a_boundary = began.replace(
        minute=began.minute - began.minute % 15, second=0, microsecond=0
    )

    from unittest.mock import patch

    clock = patch.object(
        server.FRBCSimple, "now", lambda self: on_a_boundary.astimezone(self._timezone)
    )
    clock.start()
    tasks = [
        asyncio.create_task(server.serve_resource_manager(cem, fm, sensors)),
        asyncio.create_task(
            run_resource_manager(resource_manager, interval=0.3, forecast_interval=0.3)
        ),
    ]
    try:
        # Two storage statuses past the first, each a new plan, so that replanning is part of what is checked.
        await _until(lambda: fm.trigger_and_get_schedule.await_count >= 3)
        await _until(lambda: len(resource_manager.of_type("FRBC.Instruction")) >= 3)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        clock.stop()
        gc.collect()
        loop.set_exception_handler(None)

    instructions = resource_manager.of_type("FRBC.Instruction")
    distinct = {
        (i["operation_mode"], round(i["operation_mode_factor"], 6))
        for i in instructions
    }
    assert len(distinct) >= 2, instructions

    refused = [
        m for m in resource_manager.of_type("ReceptionStatus") if m["status"] != "OK"
    ]
    assert len(cem.of_type("FRBC.UsageForecast")) >= 2  # sent again, not only once
    assert refused == []

    start = fm.trigger_and_get_schedule.await_args_list[0].kwargs["start"]
    assert start == on_a_boundary
    assert unhandled == []

    # Every declaration reached the sensor the server made for it, to be seen next to the schedule planned against it.
    posted_to = {
        call.args[0] if call.args else call.kwargs.get("sensor_id")
        for call in fm.post_sensor_data.await_args_list
        + fm.post_measurements.await_args_list
    }
    for name in (
        "power",
        "state of charge",
        "fill rate",
        "soc minima",
        "soc maxima",
        "usage forecast",
        "storage efficiency",
        "charging efficiency",
    ):
        assert sensors[name]["id"] in posted_to, name


# ---------------------------------------------------------------------------
# The example's own buffer, as a second opinion on the CEM's arithmetic
# ---------------------------------------------------------------------------

from flexmeasures_client.s2.control_types.FRBC.utils import (  # noqa: E402
    compute_next_fill_level,
    usage_rate_at,
)
from flexmeasures_client.s2.script.websockets_client import (  # noqa: E402
    Buffer,
    heat,
    leakage_behaviour,
    operation_modes,
    per_second,
    usage_forecast,
)

NOON = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)


def _heating_buffer(fill_level: float) -> Buffer:
    modes = operation_modes()
    heating = next(m for m in modes if m.diagnostic_label == "heating")
    return Buffer(
        modes={str(m.id): m for m in modes},
        leakage=leakage_behaviour(NOON),
        fill_level=fill_level,
        mode_id=str(heating.id),
        factor=1.0,
        at=NOON,
    )


def test_the_example_buffer_follows_s2s_definitions():
    """A quarter hour at full heat from a cold 2 kWh: 6 kW of heat in, a tenth of a kWh an hour drawn, and a cold buffer's leakage."""
    buffer = _heating_buffer(2.0)
    buffer.advance(NOON + timedelta(minutes=15))
    expected = 2.0 + (heat(6000) - per_second(0.1) - per_second(0.8 / 24)) * 900
    assert buffer.fill_level == pytest.approx(expected)


def test_the_example_and_the_cem_agree_on_where_the_buffer_goes():
    """Two integrators written separately from S2's definitions, one in the CEM and one in the example's buffer.

    While no boundary between elements is crossed they hold every rate constant over a step, and so must agree.
    """
    buffer = _heating_buffer(2.0)
    element_fill_rate = heat(6000)
    cem_fill_level = 2.0
    for step in range(2):
        moment = pd.Timestamp(NOON + step * timedelta(minutes=15))
        cem_fill_level = compute_next_fill_level(
            cem_fill_level,
            element_fill_rate,
            timedelta(minutes=15),
            leakage_behaviour=leakage_behaviour(NOON),
            usage_rate=usage_rate_at(usage_forecast(NOON), moment),
        )
    buffer.advance(NOON + timedelta(minutes=30))
    assert cem_fill_level < 6.0  # no element boundary crossed
    assert buffer.fill_level == pytest.approx(cem_fill_level, rel=1e-12)


def test_a_warm_buffer_heats_at_the_warm_coefficient():
    """At 7 kWh the buffer is past the cold element, so full heat delivers 4.5 kW, and it leaks as a warm buffer does."""
    buffer = _heating_buffer(7.0)
    buffer.advance(NOON + timedelta(minutes=15))
    expected = 7.0 + (heat(4500) - per_second(0.1) - per_second(1.5 / 24)) * 900
    assert buffer.fill_level == pytest.approx(expected)


def test_an_instruction_takes_over_from_where_the_last_one_left_the_buffer():
    """Idle for a quarter hour, losing only what is drawn and what leaks, then a quarter hour at full heat."""
    from s2python.frbc import FRBCInstruction

    from flexmeasures_client.s2.utils import get_unique_id

    buffer = _heating_buffer(4.0)
    buffer.mode_id, buffer.factor = None, 0.0  # idle until told otherwise
    heating = next(m for m in buffer.modes.values() if m.diagnostic_label == "heating")
    buffer.follow(
        FRBCInstruction(
            message_id=get_unique_id(),
            id=get_unique_id(),
            actuator_id=get_unique_id(),
            operation_mode=heating.id,
            operation_mode_factor=1.0,
            execution_time=NOON + timedelta(minutes=15),
            abnormal_condition=False,
        ),
        NOON + timedelta(minutes=15),
    )
    buffer.advance(NOON + timedelta(minutes=30))
    losses = per_second(0.1) + per_second(0.8 / 24)
    expected = 4.0 - losses * 900 + (heat(6000) - losses) * 900
    assert buffer.fill_level == pytest.approx(expected)


@pytest.mark.asyncio
async def test_the_resource_manager_hands_back_its_buffer_when_the_cem_goes_away():
    resource_manager, cem = _connected()
    fm = _flexmeasures()
    sensors = {name: {"id": i} for i, name in enumerate(server.SITE_SENSORS, start=1)}
    serving = asyncio.create_task(server.serve_resource_manager(cem, fm, sensors))
    managing = asyncio.create_task(run_resource_manager(resource_manager, interval=60))
    try:
        await _until(lambda: len(resource_manager.of_type("FRBC.Instruction")) >= 1)
        await resource_manager._inbox.put(_CLOSED)
        buffer = await asyncio.wait_for(managing, timeout=5)
    finally:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
    assert 0 <= buffer.fill_level <= 12


@pytest.mark.asyncio
async def test_the_site_gets_a_sensor_for_every_declaration_and_prices_from_today():
    fm = AsyncMock(FlexMeasuresClient)
    fm.get_account = AsyncMock(return_value={"id": 1})
    fm.get_assets = AsyncMock(
        return_value=[
            {
                "id": 5,
                "name": server.SITE_NAME,
                "sensors": [{"id": 50, "name": "price"}],
            }
        ]
    )
    fm.add_sensor = AsyncMock(
        side_effect=lambda **kwargs: {
            "id": 100 + len(fm.add_sensor.await_args_list),
            **kwargs,
        }
    )

    sensors = await server.configure_site(server.SITE_NAME, fm)

    assert set(sensors) == set(server.SITE_SENSORS)
    assert sensors["price"]["id"] == 50  # kept, not made again
    made = {call.kwargs["name"]: call.kwargs for call in fm.add_sensor.await_args_list}
    assert "price" not in made
    assert made["power"]["attributes"] == {"consumption_is_positive": True}
    assert made["fill rate"]["unit"] == "kWh/s"
    assert made["soc minima"]["unit"] == "kWh"
    prices = fm.post_sensor_data.await_args.kwargs
    assert prices["sensor_id"] == 50
    assert prices["start"].date() == datetime.now(prices["start"].tzinfo).date()


def test_the_example_and_the_cem_agree_across_an_element_boundary():
    """The CEM follows a step exactly, the example's buffer a minute at a time: two discretisations of S2's definitions.

    Through the cold element and into the warm one they can differ only within the minute of the crossing,
    by at most a minute of the difference between the two elements' fill rates.
    """
    from flexmeasures_client.s2.control_types.FRBC.utils import (
        UsageProfile,
        follow_operation_mode,
    )

    buffer = _heating_buffer(5.0)
    heating = buffer.modes[buffer.mode_id]
    cem_fill_level = 5.0
    for step in range(4):
        cem_fill_level = follow_operation_mode(
            cem_fill_level,
            heating,
            1.0,
            pd.Timestamp(NOON + step * timedelta(minutes=15)),
            timedelta(minutes=15),
            leakage_behaviour=leakage_behaviour(NOON),
            usage=UsageProfile(usage_forecast(NOON)),
        )
    buffer.advance(NOON + timedelta(hours=1))
    assert (
        cem_fill_level > 6.0
    )  # the boundary between the cold and the warm element was crossed
    a_minute_of_the_difference = (heat(6000) - heat(4500)) * 60
    assert buffer.fill_level == pytest.approx(
        cem_fill_level, abs=a_minute_of_the_difference
    )


# ---------------------------------------------------------------------------
# What the review of #259 found unpinned
# ---------------------------------------------------------------------------

from flexmeasures_client.s2.script.websockets_client import (  # noqa: E402
    COLD_BELOW,
    PHYSICAL_CAPACITY,
    answer_instructions,
    expected_usage,
    fill_level_target_profile,
    refresh_forecasts,
    report_fill_level,
    system_description,
)


class _Collect:
    """A websocket that collects what is sent and hands over what it is given, then ends."""

    def __init__(self, incoming: list[str] | None = None):
        self.sent: list[dict] = []
        self._incoming = list(incoming or [])

    async def send_str(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def __aiter__(self):
        async def messages():
            for data in self._incoming:
                yield _Text(data)
                await asyncio.sleep(0)

        return messages()


def test_the_forecast_the_example_sends_is_the_usage_it_simulates():
    """Hour by hour, through a whole day, so that what is planned for is what is drawn."""
    for start_hour in (0, 13):
        start = datetime(2024, 1, 1, start_hour, tzinfo=timezone.utc)
        forecast = usage_forecast(start)
        for h, element in enumerate(forecast.elements):
            assert element.usage_rate_expected == pytest.approx(
                per_second(expected_usage((start_hour + h) % 24))
            )


def test_the_comfort_profile_preheats_before_the_morning_showers():
    profile = fill_level_target_profile(datetime(2024, 1, 1, tzinfo=timezone.utc))
    lows = [e.fill_level_range.start_of_range for e in profile.elements]
    assert lows[6] == 6.0 and expected_usage(7) > expected_usage(6)
    assert set(lows[:6] + lows[7:]) == {3.0}


def test_at_the_boundary_the_buffer_heats_at_the_warm_coefficient():
    buffer = _heating_buffer(COLD_BELOW)
    fill, _, _ = buffer.rates(NOON)
    assert fill == pytest.approx(heat(4500))


def test_the_buffer_never_holds_more_than_it_physically_can():
    buffer = _heating_buffer(PHYSICAL_CAPACITY - 0.1)
    buffer.advance(NOON + timedelta(hours=2))
    assert buffer.fill_level == PHYSICAL_CAPACITY


def test_the_example_declares_what_it_sends_and_how_it_can_switch():
    modes = operation_modes()
    description = system_description(modes, NOON)
    storage = description.storage
    assert storage.provides_leakage_behaviour and storage.provides_usage_forecast
    assert storage.provides_fill_level_target_profile
    switches = {(str(t.from_), str(t.to)) for t in description.actuators[0].transitions}
    ids = [str(m.id) for m in modes]
    assert switches == {(ids[0], ids[1]), (ids[1], ids[0])}


@pytest.mark.asyncio
async def test_the_reported_fill_level_is_where_the_buffer_has_got_to():
    buffer = _heating_buffer(5.0)
    buffer.at = datetime.now(timezone.utc) - timedelta(minutes=30)
    ws = _Collect()
    reporting = asyncio.create_task(report_fill_level(ws, buffer, interval=0.01))
    await _until(lambda: ws.sent)
    reporting.cancel()
    assert (
        ws.sent[0]["present_fill_level"] > 5.5
    )  # half an hour of heating, not where it started


@pytest.mark.asyncio
async def test_a_revoked_instruction_is_not_followed():
    """A newer plan replaced it; following it anyway left the heat pump in whatever the oldest plan said last."""
    from s2python.common import RevokableObjects, RevokeObject
    from s2python.frbc import FRBCInstruction

    from flexmeasures_client.s2.utils import get_unique_id

    due_soon = FRBCInstruction(
        message_id=get_unique_id(),
        id=get_unique_id(),
        actuator_id=get_unique_id(),
        operation_mode=get_unique_id(),
        operation_mode_factor=1.0,
        execution_time=datetime.now(timezone.utc) + timedelta(milliseconds=200),
        abnormal_condition=False,
    )
    revoke = RevokeObject(
        message_id=get_unique_id(),
        object_type=RevokableObjects.FRBC_Instruction,
        object_id=due_soon.id,
    )
    ws = _Collect([due_soon.model_dump_json(), revoke.model_dump_json()])
    await answer_instructions(ws)
    await asyncio.sleep(0.4)
    assert not [m for m in ws.sent if m["message_type"] == "FRBC.ActuatorStatus"]
    acknowledged = {
        m["subject_message_id"]
        for m in ws.sent
        if m["message_type"] == "ReceptionStatus"
    }
    assert str(revoke.message_id) in acknowledged


@pytest.mark.asyncio
async def test_the_forecasts_are_sent_again_from_now_on():
    ws = _Collect()
    refreshing = asyncio.create_task(
        refresh_forecasts(ws, leakage_behaviour(NOON), interval=0.01)
    )
    await _until(lambda: len(ws.sent) >= 3)
    refreshing.cancel()
    assert [m["message_type"] for m in ws.sent[:3]] == [
        "FRBC.LeakageBehaviour",
        "FRBC.UsageForecast",
        "FRBC.FillLevelTargetProfile",
    ]
    assert pd.Timestamp(ws.sent[0]["valid_from"]) > NOON


@pytest.mark.asyncio
async def test_the_server_plans_from_now_in_the_site_time_zone_and_revokes_what_it_replaces():
    from unittest.mock import patch

    made = []
    real = server.FRBCSimple

    def capture(**kwargs):
        frbc = real(**kwargs)
        made.append((kwargs, frbc))
        return frbc

    resource_manager, cem = _connected()
    sensors = {name: {"id": i} for i, name in enumerate(server.SITE_SENSORS, start=1)}
    with patch.object(server, "FRBCSimple", side_effect=capture):
        serving = asyncio.create_task(
            server.serve_resource_manager(cem, _flexmeasures(), sensors)
        )
        await _until(lambda: made)
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
    kwargs, frbc = made[0]
    assert kwargs["timezone"] == server.SITE_TIMEZONE
    assert kwargs["valid_from_shift"] == timedelta(0)
    assert frbc.send_revocations is True


def test_the_site_sensors_are_what_the_cem_records_in():
    assert (
        server.SITE_SENSORS["state of charge"].unit,
        server.SITE_SENSORS["state of charge"].resolution,
    ) == ("kWh", "PT0M")
    assert server.SITE_SENSORS["storage efficiency"].unit == "%"
    assert server.SITE_SENSORS["usage forecast"].unit == "kWh/s"


@pytest.mark.asyncio
async def test_the_demo_prices_are_seeded_again_while_the_server_runs():
    from unittest.mock import patch

    from aiohttp import web

    app = web.Application()
    fm = _flexmeasures()
    app[server.FM_CLIENT] = fm
    app[server.SENSORS] = {"price": {"id": 10}}
    with patch.object(server, "SEED_PRICES_EVERY", timedelta(milliseconds=10)):
        seeding = server.keep_prices_seeded(app)
        await seeding.__anext__()
        await _until(lambda: fm.post_sensor_data.await_count >= 2)
        with pytest.raises(StopAsyncIteration):
            await seeding.__anext__()
    assert fm.post_sensor_data.await_args.kwargs["sensor_id"] == 10


def test_the_loss_is_a_share_of_the_fill_level_a_schedule_starts_from():
    """1.5 kWh a day from a 5 kWh buffer is a larger share of it than of a 12 kWh one."""
    from flexmeasures_client.s2.control_types.translations import (
        leakage_behaviour_to_storage_efficiency,
    )

    leakage = leakage_behaviour(NOON)
    at_five = leakage_behaviour_to_storage_efficiency(
        leakage, resolution=timedelta(minutes=15), fill_level=7.0, share_of=5.0
    )
    rate = per_second(1.5 / 24)  # the warm element, which applies at 7
    assert at_five == pytest.approx(100 * (1 - rate / 5.0) ** 900, rel=1e-12)


@pytest.mark.asyncio
async def test_a_failed_reseed_is_tried_again_at_the_next_interval():
    from unittest.mock import patch

    from aiohttp import web

    app = web.Application()
    fm = _flexmeasures()
    fm.post_sensor_data.side_effect = [ConnectionError("down"), None, None]
    app[server.FM_CLIENT] = fm
    app[server.SENSORS] = {"price": {"id": 10}}
    with patch.object(server, "SEED_PRICES_EVERY", timedelta(milliseconds=10)):
        seeding = server.keep_prices_seeded(app)
        await seeding.__anext__()
        await _until(lambda: fm.post_sensor_data.await_count >= 2)
        with pytest.raises(StopAsyncIteration):
            await seeding.__anext__()


def _instruction(mode, due_in: timedelta):
    from s2python.frbc import FRBCInstruction

    from flexmeasures_client.s2.utils import get_unique_id

    return FRBCInstruction(
        message_id=get_unique_id(),
        id=get_unique_id(),
        actuator_id=get_unique_id(),
        operation_mode=mode,
        operation_mode_factor=1.0,
        execution_time=datetime.now(timezone.utc) + due_in,
        abnormal_condition=False,
    )


@pytest.mark.asyncio
async def test_the_mode_reported_as_left_is_the_one_last_carried_out():
    """A revoked instruction was never carried out, so the next transition does not start from its mode."""
    from s2python.common import RevokableObjects, RevokeObject

    from flexmeasures_client.s2.utils import get_unique_id

    a, b = get_unique_id(), get_unique_id()
    first = _instruction(a, timedelta(0))
    revoked = _instruction(b, timedelta(seconds=30))
    later = _instruction(a, timedelta(milliseconds=200))
    revoke = RevokeObject(
        message_id=get_unique_id(),
        object_type=RevokableObjects.FRBC_Instruction,
        object_id=revoked.id,
    )
    ws = _Collect(
        [
            first.model_dump_json(),
            revoked.model_dump_json(),
            revoke.model_dump_json(),
            later.model_dump_json(),
        ]
    )
    await answer_instructions(ws)
    await _until(
        lambda: len([m for m in ws.sent if m["message_type"] == "FRBC.ActuatorStatus"])
        >= 2
    )
    statuses = [m for m in ws.sent if m["message_type"] == "FRBC.ActuatorStatus"]
    assert statuses[1]["previous_operation_mode_id"] == str(a)


@pytest.mark.asyncio
async def test_the_example_tells_the_cem_where_each_instruction_stands():
    """Accepted on arrival, succeeded once carried out, revoked once revoked: the CEM revokes only what is not yet carried out."""
    from s2python.common import RevokableObjects, RevokeObject

    from flexmeasures_client.s2.utils import get_unique_id

    done = _instruction(get_unique_id(), timedelta(0))
    revoked = _instruction(get_unique_id(), timedelta(seconds=30))
    revoke = RevokeObject(
        message_id=get_unique_id(),
        object_type=RevokableObjects.FRBC_Instruction,
        object_id=revoked.id,
    )
    ws = _Collect(
        [done.model_dump_json(), revoked.model_dump_json(), revoke.model_dump_json()]
    )
    await answer_instructions(ws)
    await _until(
        lambda: any(
            m.get("status_type") == "SUCCEEDED"
            for m in ws.sent
            if m["message_type"] == "InstructionStatusUpdate"
        )
    )
    updates = {
        (m["instruction_id"], m["status_type"])
        for m in ws.sent
        if m["message_type"] == "InstructionStatusUpdate"
    }
    assert updates == {
        (str(done.id), "ACCEPTED"),
        (str(done.id), "SUCCEEDED"),
        (str(revoked.id), "ACCEPTED"),
        (str(revoked.id), "REVOKED"),
    }


@pytest.mark.asyncio
async def test_a_failed_ingestion_of_the_reseeded_prices_does_not_end_the_reseeding():
    from unittest.mock import patch

    from aiohttp import web

    from flexmeasures_client.exceptions import JobFailedError

    app = web.Application()
    fm = _flexmeasures()
    fm.post_sensor_data.side_effect = [JobFailedError("ingestion failed"), None, None]
    app[server.FM_CLIENT] = fm
    app[server.SENSORS] = {"price": {"id": 10}}
    with patch.object(server, "SEED_PRICES_EVERY", timedelta(milliseconds=10)):
        seeding = server.keep_prices_seeded(app)
        await seeding.__anext__()
        await _until(lambda: fm.post_sensor_data.await_count >= 2)
        with pytest.raises(StopAsyncIteration):
            await seeding.__anext__()


@pytest.mark.asyncio
async def test_a_revocation_arriving_once_an_instruction_is_due_is_too_late_to_stop_it():
    """It is carried out and reported as succeeded, not reported as revoked while the heat pump follows it."""
    from s2python.common import RevokableObjects, RevokeObject

    from flexmeasures_client.s2.script.websockets_client import apply_at_execution_time
    from flexmeasures_client.s2.utils import get_unique_id

    instruction = _instruction(get_unique_id(), timedelta(0))
    became_due = []
    ws = _Collect()
    await apply_at_execution_time(
        ws, instruction, {"mode": None}, on_due=lambda: became_due.append(True)
    )
    assert became_due == [True]

    revoke = RevokeObject(
        message_id=get_unique_id(),
        object_type=RevokableObjects.FRBC_Instruction,
        object_id=instruction.id,
    )
    ws = _Collect([instruction.model_dump_json(), revoke.model_dump_json()])
    await answer_instructions(ws)
    await _until(lambda: any(m.get("status_type") == "SUCCEEDED" for m in ws.sent))
    assert not [m for m in ws.sent if m.get("status_type") == "REVOKED"]


@pytest.mark.asyncio
async def test_the_site_graphs_show_the_buffer_with_its_band_and_what_fills_and_draws_it():
    """The graphs page showed only price and power, which says nothing about whether the plan kept the buffer warm."""
    fm = AsyncMock(FlexMeasuresClient)
    fm.get_account = AsyncMock(return_value={"id": 1})
    fm.get_assets = AsyncMock(
        return_value=[
            {
                "id": 5,
                "name": server.SITE_NAME,
                "sensors": [],
                "sensors_to_show": [{"title": "Edited in the UI", "sensors": [1]}],
            }
        ]
    )
    fm.add_sensor = AsyncMock(
        side_effect=lambda **kwargs: {
            "id": 100 + len(fm.add_sensor.await_args_list),
            **kwargs,
        }
    )
    sensors = await server.configure_site(server.SITE_NAME, fm)
    shown = [
        call.args[1]["sensors_to_show"]
        for call in fm.update_asset.await_args_list
        if "sensors_to_show" in call.args[1]
    ]
    assert len(shown) == 1
    plotted = {graph["title"]: set(graph["sensors"]) for graph in shown[0]}
    by_id = {sensor["id"]: name for name, sensor in sensors.items()}
    named = {title: {by_id[i] for i in ids} for title, ids in plotted.items()}
    assert {"state of charge", "soc minima", "soc maxima"} in named.values()
    assert {"fill rate", "usage forecast"} in named.values()
    assert {"power", "measured-power"} in named.values()
    assert {"price", "production price"} in named.values()


def test_the_site_graphs_are_in_a_form_every_flexmeasures_reads():
    """A title and a list of sensors per graph; the form with plots is refused before FlexMeasures v0.32."""
    from flexmeasures_client.s2.site import frbc_site_graphs

    graphs = frbc_site_graphs(
        {name: {"id": i} for i, name in enumerate(server.SITE_SENSORS, start=1)}
    )
    for graph in graphs:
        assert set(graph) == {"title", "sensors"}
        assert all(isinstance(i, int) for i in graph["sensors"])


@pytest.mark.asyncio
async def test_a_session_ends_with_its_connection():
    """Its producer, its watchdog and its planning stop, so a resource manager that reconnects is served by one session."""
    from unittest.mock import patch

    made = []
    real = server.CEM

    def capture(**kwargs):
        cem = real(**kwargs)
        made.append(cem)
        return cem

    resource_manager, cem_end = _connected()
    sensors = {name: {"id": i} for i, name in enumerate(server.SITE_SENSORS, start=1)}
    with patch.object(server, "CEM", side_effect=capture):
        serving = asyncio.create_task(
            server.serve_resource_manager(cem_end, _flexmeasures(), sensors)
        )
        await _until(lambda: made)
        await cem_end._inbox.put(_CLOSED)
        await asyncio.wait_for(serving, timeout=5)
    assert made[0].is_closed()
    leftover = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task()
        and getattr(t.get_coro(), "__qualname__", "")
        in ("websocket_producer", "rm_details_watchdog")
    ]
    assert leftover == []


@pytest.mark.asyncio
@pytest.mark.parametrize("accept", [False, True])
async def test_the_server_hands_its_simulation_flag_to_each_cem(accept):
    from unittest.mock import patch

    made = []
    real = server.CEM

    def capture(**kwargs):
        made.append(kwargs)
        return real(**kwargs)

    resource_manager, cem_end = _connected()
    sensors = {name: {"id": i} for i, name in enumerate(server.SITE_SENSORS, start=1)}
    with (
        patch.object(server, "CEM", side_effect=capture),
        patch.object(server, "ACCEPT_SIMULATION_METADATA", accept),
    ):
        serving = asyncio.create_task(
            server.serve_resource_manager(cem_end, _flexmeasures(), sensors)
        )
        await _until(lambda: made)
        await cem_end._inbox.put(_CLOSED)
        await asyncio.wait_for(serving, timeout=5)
    assert made[0]["accept_simulation_metadata"] is accept


@pytest.mark.asyncio
async def test_with_a_site_per_resource_manager_the_server_configures_none_at_startup():
    from unittest.mock import patch

    from aiohttp import web

    app = web.Application()
    with (
        patch.object(server, "SITE_PER_RESOURCE_MANAGER", True),
        patch.object(server, "configure_site", AsyncMock()) as configure,
    ):
        await server.configure_site_once(app)
        configure.assert_not_awaited()
        assert isinstance(app[server.CONFIGURATOR], server.FRBCSiteConfigurator)
        assert (
            app[server.CONFIGURATOR]
            ._sensors["power"]
            .attributes["is_strictly_non_positive"]
        )
        assert app[server.CONFIGURATOR]._handler_options["relax_site_capacity"] is True
    # So that seeding, were it to happen, would reach FlexMeasures.
    app[server.SENSORS] = {"price": {"id": 1}}
    with (
        patch.object(server, "SITE_PER_RESOURCE_MANAGER", True),
        patch.object(server, "SEED_PRICES_EVERY", timedelta(milliseconds=10)),
        patch.object(server, "seed_prices", AsyncMock()) as seed,
    ):
        seeding = server.keep_prices_seeded(app)
        await seeding.__anext__()  # nothing to seed: the prices are the simulation's to post
        await asyncio.sleep(0.05)
        with pytest.raises(StopAsyncIteration):
            await seeding.__anext__()
        seed.assert_not_awaited()
    await app[server.FM_CLIENT].close()


@pytest.mark.asyncio
async def test_a_resource_manager_with_a_site_of_its_own_is_served_once_it_is_configured():
    from unittest.mock import patch

    made = []
    real = server.CEM

    def capture(**kwargs):
        cem = real(**kwargs)
        made.append(cem)
        return cem

    async def configurator(details):
        raise AssertionError("not called in this test")

    resource_manager, cem_end = _connected()
    with patch.object(server, "CEM", side_effect=capture):
        serving = asyncio.create_task(
            server.serve_resource_manager(
                cem_end, _flexmeasures(), configurator=configurator
            )
        )
        await _until(lambda: made)
        await cem_end._inbox.put(_CLOSED)
        await asyncio.wait_for(serving, timeout=5)
    assert made[0]._site_configurator is configurator
    assert (
        made[0]._control_types_handlers == {}
    )  # no handler until its site is configured


@pytest.mark.asyncio
async def test_on_the_fixed_site_measured_power_is_recorded_on_its_sensor():
    from unittest.mock import patch

    made = []
    real = server.CEM

    def capture(**kwargs):
        made.append(kwargs)
        return real(**kwargs)

    resource_manager, cem_end = _connected()
    sensors = {name: {"id": i} for i, name in enumerate(server.SITE_SENSORS, start=1)}
    with patch.object(server, "CEM", side_effect=capture):
        serving = asyncio.create_task(
            server.serve_resource_manager(cem_end, _flexmeasures(), sensors)
        )
        await _until(lambda: made)
        await cem_end._inbox.put(_CLOSED)
        await asyncio.wait_for(serving, timeout=5)
    assert made[0]["power_sensor_id"] == {
        "ELECTRIC.POWER.L1": sensors["measured-power"]["id"]
    }


@pytest.mark.asyncio
async def test_every_cem_the_server_makes_records_with_the_servers_recorder():
    """It outlives a connection, so what a reconnecting resource manager sends again is not recorded twice."""
    from unittest.mock import patch

    from aiohttp import web

    from flexmeasures_client.s2.measurements import MeasurementRecorder

    app = web.Application()
    with patch.object(server, "SITE_PER_RESOURCE_MANAGER", True):
        await server.configure_site_once(app)
    assert isinstance(app[server.RECORDER], MeasurementRecorder)
    await app[server.FM_CLIENT].close()

    made = []
    real = server.CEM

    def capture(**kwargs):
        made.append(kwargs)
        return real(**kwargs)

    recorder = MeasurementRecorder(_flexmeasures())
    resource_manager, cem_end = _connected()
    sensors = {name: {"id": i} for i, name in enumerate(server.SITE_SENSORS, start=1)}
    with patch.object(server, "CEM", side_effect=capture):
        serving = asyncio.create_task(
            server.serve_resource_manager(
                cem_end, _flexmeasures(), sensors, recorder=recorder
            )
        )
        await _until(lambda: made)
        await cem_end._inbox.put(_CLOSED)
        await asyncio.wait_for(serving, timeout=5)
    assert made[0]["measurement_recorder"] is recorder


@pytest.mark.asyncio
@pytest.mark.parametrize("per_resource_manager", [False, True])
async def test_a_connection_is_served_with_the_servers_recorder(per_resource_manager):
    from unittest.mock import patch

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from flexmeasures_client.s2.measurements import MeasurementRecorder

    app = web.Application()
    app.add_routes([web.get("/ws", server.websocket_handler)])
    app[server.FM_CLIENT] = _flexmeasures()
    app[server.RECORDER] = MeasurementRecorder(app[server.FM_CLIENT])
    app[server.SENSORS] = {"power": {"id": 1}}
    app[server.CONFIGURATOR] = server.FRBCSiteConfigurator(
        app[server.FM_CLIENT], asset_type_id=6
    )
    served = AsyncMock()
    with (
        patch.object(server, "serve_resource_manager", served),
        patch.object(server, "SITE_PER_RESOURCE_MANAGER", per_resource_manager),
    ):
        async with TestClient(TestServer(app)) as client:
            async with client.ws_connect("/ws"):
                await _until(lambda: served.await_count)
    assert served.await_args.kwargs["recorder"] is app[server.RECORDER]
    if per_resource_manager:
        assert served.await_args.kwargs["configurator"] is app[server.CONFIGURATOR]
    else:
        assert served.await_args.args[2] is app[server.SENSORS]
