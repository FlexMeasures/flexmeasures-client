"""How the example server passes messages between a websocket and its CEM."""

from __future__ import annotations

from types import SimpleNamespace

import aiohttp
import pytest

from flexmeasures_client.s2.script import websockets_server as server


class FakeCEM:
    """Just enough of a CEM: a queue of messages to send, and a record of what it handled."""

    def __init__(self, to_send=(), handle_raises=None):
        self._to_send = list(to_send)
        self.handled = []
        self.closed = False
        self._handle_raises = handle_raises
        self._logger = server.LOGGER

    def is_closed(self):
        return self.closed or not self._to_send

    async def get_message(self):
        return self._to_send.pop(0)

    async def handle_message(self, message):
        if self._handle_raises:
            raise self._handle_raises
        self.handled.append(message)

    async def close(self):
        self.closed = True


class FakeWebSocket:
    def __init__(self, incoming=(), send_fails_after=None):
        self._incoming = list(incoming)
        self.sent = []
        self.closed = False
        self._send_fails_after = send_fails_after

    async def send_json(self, message):
        if (
            self._send_fails_after is not None
            and len(self.sent) >= self._send_fails_after
        ):
            raise aiohttp.ClientConnectionResetError("gone")
        self.sent.append(message)

    async def close(self):
        self.closed = True

    def exception(self):
        return RuntimeError("broken pipe")

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)


def text(data: str):
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=data)


@pytest.mark.asyncio
async def test_the_producer_sends_what_the_cem_queues():
    cem = FakeCEM(to_send=[{"n": 1}, {"n": 2}])
    ws = FakeWebSocket()

    await server.websocket_producer(ws, cem)

    assert ws.sent == [{"n": 1}, {"n": 2}]


@pytest.mark.asyncio
async def test_the_producer_stops_when_the_connection_is_reset():
    cem = FakeCEM(to_send=[{"n": 1}, {"n": 2}, {"n": 3}])
    ws = FakeWebSocket(send_fails_after=1)

    await server.websocket_producer(ws, cem)

    assert ws.sent == [{"n": 1}]
    assert cem._to_send == [
        {"n": 3}
    ], "nothing more is taken from the CEM once the connection is gone"


@pytest.mark.asyncio
async def test_the_consumer_hands_each_message_to_the_cem():
    cem = FakeCEM()
    ws = FakeWebSocket(incoming=[text('{"message_type": "Handshake"}')])

    await server.websocket_consumer(ws, cem)

    assert cem.handled == [{"message_type": "Handshake"}]


@pytest.mark.asyncio
async def test_the_consumer_logs_a_message_that_is_not_json_and_carries_on(caplog):
    cem = FakeCEM()
    ws = FakeWebSocket(
        incoming=[text("not json"), text('{"message_type": "Handshake"}')]
    )

    await server.websocket_consumer(ws, cem)

    assert cem.handled == [{"message_type": "Handshake"}]
    assert "handle_message failed for 'not json'" in caplog.text


@pytest.mark.asyncio
async def test_close_closes_the_cem_and_the_connection():
    cem = FakeCEM()
    ws = FakeWebSocket(incoming=[text("close")])

    await server.websocket_consumer(ws, cem)

    assert cem.closed and ws.closed
    assert cem.handled == []


@pytest.mark.asyncio
async def test_a_connection_error_closes_the_cem(caplog):
    cem = FakeCEM()
    ws = FakeWebSocket(
        incoming=[SimpleNamespace(type=aiohttp.WSMsgType.ERROR, data=None)]
    )

    await server.websocket_consumer(ws, cem)

    assert cem.closed
    assert "ws connection closed with exception broken pipe" in caplog.text
