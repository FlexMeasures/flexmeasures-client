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

    tasks = [
        asyncio.create_task(server.serve_resource_manager(cem, fm, sensors)),
        asyncio.create_task(run_resource_manager(resource_manager, interval=0.3)),
    ]
    try:
        # Two storage statuses past the first, each a new plan, so that replanning is part of what is checked.
        await _until(lambda: fm.trigger_and_get_schedule.await_count >= 3)
        await _until(lambda: len(resource_manager.of_type("FRBC.Instruction")) >= 3)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
    assert refused == []

    start = fm.trigger_and_get_schedule.await_args_list[0].kwargs["start"]
    assert began - timedelta(minutes=15) < start <= began + timedelta(seconds=5)
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
    """Both follow a step a minute at a time, so from 5 kWh, through the cold element and into the warm one, they agree."""
    from flexmeasures_client.s2.control_types.FRBC.utils import follow_operation_mode

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
            usage_forecast=usage_forecast(NOON),
        )
    buffer.advance(NOON + timedelta(hours=1))
    assert (
        cem_fill_level > 6.0
    )  # the boundary between the cold and the warm element was crossed
    assert buffer.fill_level == pytest.approx(cem_fill_level, rel=1e-12)
