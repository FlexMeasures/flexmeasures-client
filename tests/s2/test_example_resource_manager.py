"""The example resource manager answers an instruction, and answers it when it is due.

An instruction carries an execution_time: the moment the resource manager is to be in that mode,
not the moment the instruction arrives.
Answering on arrival reports a mode the device is not in yet,
and a customer energy manager recording that status ends up with a measurement of a power nothing is drawing,
timestamped now.
Two instructions sent together, a schedule's worth apart, were answered within milliseconds of each other that way,
which is how this was found.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from s2python.frbc import FRBCInstruction

from flexmeasures_client.s2.script.websockets_client import (
    answer_instructions,
    apply_at_execution_time,
)
from flexmeasures_client.s2.utils import get_unique_id


class FakeWebSocket:
    """A websocket that remembers what was sent and hands over the messages it was given."""

    def __init__(self, incoming: list[str] | None = None):
        self.sent: list[dict] = []
        self._incoming = incoming or []

    async def send_str(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def __aiter__(self):
        async def messages():
            for data in self._incoming:
                yield FakeMessage(data)

        return messages()

    def of_type(self, message_type: str) -> list[dict]:
        return [m for m in self.sent if m.get("message_type") == message_type]


class FakeMessage:
    def __init__(self, data: str):
        self.data = data

    def json(self) -> dict:
        return json.loads(self.data)


def _instruction(execution_time: datetime, factor: float = 1.0) -> FRBCInstruction:
    return FRBCInstruction(
        message_id=get_unique_id(),
        id=get_unique_id(),
        actuator_id=get_unique_id(),
        operation_mode=get_unique_id(),
        operation_mode_factor=factor,
        execution_time=execution_time,
        abnormal_condition=False,
    )


@pytest.mark.asyncio
async def test_an_instruction_already_due_is_answered_at_once():
    """Its moment has passed, so the transition is reported now."""
    ws = FakeWebSocket()
    instruction = _instruction(datetime.now(timezone.utc) - timedelta(minutes=5))

    await apply_at_execution_time(ws, instruction)

    (status,) = ws.of_type("FRBC.ActuatorStatus")
    assert status["active_operation_mode_id"] == str(instruction.operation_mode)
    assert status["operation_mode_factor"] == instruction.operation_mode_factor
    assert status["transition_timestamp"] is not None


@pytest.mark.asyncio
async def test_an_instruction_due_later_is_not_answered_yet():
    """The whole point: a mode the device will be in is not a mode it is in."""
    ws = FakeWebSocket()
    instruction = _instruction(datetime.now(timezone.utc) + timedelta(seconds=30))

    applying = asyncio.create_task(apply_at_execution_time(ws, instruction))
    await asyncio.sleep(0.05)

    assert ws.of_type("FRBC.ActuatorStatus") == []
    applying.cancel()


@pytest.mark.asyncio
async def test_an_instruction_due_in_a_moment_is_answered_then():
    """And once its moment arrives, the transition is reported."""
    ws = FakeWebSocket()
    instruction = _instruction(datetime.now(timezone.utc) + timedelta(milliseconds=50))

    await apply_at_execution_time(ws, instruction)

    assert len(ws.of_type("FRBC.ActuatorStatus")) == 1


@pytest.mark.asyncio
async def test_every_instruction_is_acknowledged():
    """A customer energy manager waits for a reception status before it goes on."""
    first = _instruction(datetime.now(timezone.utc) + timedelta(hours=1))
    second = _instruction(datetime.now(timezone.utc) + timedelta(hours=2))
    ws = FakeWebSocket([first.model_dump_json(), second.model_dump_json()])

    await answer_instructions(ws)
    await asyncio.sleep(0.05)

    acknowledged = [m["subject_message_id"] for m in ws.of_type("ReceptionStatus")]
    assert acknowledged == [str(first.message_id), str(second.message_id)]
    # Neither is due, so neither has been reported as a transition.
    assert ws.of_type("FRBC.ActuatorStatus") == []


@pytest.mark.asyncio
async def test_two_instructions_sent_together_are_not_answered_together():
    """The collision this came from: both answered on arrival, milliseconds apart.

    The first is due now and the second in an hour,
    so exactly one transition is reported, rather than two contradicting each other.
    """
    now_due = _instruction(datetime.now(timezone.utc) - timedelta(seconds=1))
    later = _instruction(datetime.now(timezone.utc) + timedelta(hours=1))
    ws = FakeWebSocket([now_due.model_dump_json(), later.model_dump_json()])

    await answer_instructions(ws)
    await asyncio.sleep(0.05)

    statuses = ws.of_type("FRBC.ActuatorStatus")
    assert len(statuses) == 1
    assert statuses[0]["active_operation_mode_id"] == str(now_due.operation_mode)
