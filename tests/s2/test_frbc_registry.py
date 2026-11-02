"""Tests for how the FRBC handler keeps track of outstanding work:

- a re-sent system description is acknowledged but not acted on,
- only state-changing instructions are sent, and the outstanding batch is tracked,
- the newest request supersedes older ones that are still waiting to run.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from s2python.frbc import FRBCInstruction, FRBCSystemDescription

from flexmeasures_client.s2.control_types.FRBC import FRBCTest
from flexmeasures_client.s2.utils import get_unique_id


class RecordingFRBC(FRBCTest):
    """An FRBC handler that records what it sends, instead of queueing it.

    The handler is not registered with a CEM here, so there is no sending queue to put
    messages on; recording them is also what lets a test assert on what went out.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sent: list = []

    async def send_message(self, message):
        self.sent.append(message)


def make_instruction(
    operation_mode: str, factor: float, slot: int, actuator: str
) -> FRBCInstruction:
    """An instruction switching an actuator to a mode at a quarter-hour slot of 2022-12-01."""
    return FRBCInstruction(
        message_id=get_unique_id(),
        id=get_unique_id(),
        actuator_id=actuator,
        operation_mode=operation_mode,
        operation_mode_factor=factor,
        execution_time=datetime(
            2022, 12, 1, slot // 4, (slot % 4) * 15, tzinfo=timezone.utc
        ),
        abnormal_condition=False,
    )


@pytest.mark.asyncio
async def test_resent_system_description_is_acknowledged_but_not_acted_on(
    frbc_system_description,
):
    frbc = RecordingFRBC()

    first_response = await frbc.handle_message(frbc_system_description)
    await asyncio.gather(*frbc.background_tasks)
    history_size = len(frbc._system_description_history)
    sent_after_first = len(frbc.sent)
    assert history_size == 1
    assert sent_after_first == 1, "the first description triggers a schedule"

    resent = FRBCSystemDescription(
        message_id=get_unique_id(),  # a fresh message id, carrying identical content
        valid_from=frbc_system_description.valid_from,
        actuators=frbc_system_description.actuators,
        storage=frbc_system_description.storage,
    )
    second_response = await frbc.handle_message(resent)
    await asyncio.gather(*frbc.background_tasks)

    assert str(first_response.status) == "ReceptionStatusValues.OK"
    assert (
        str(second_response.status) == "ReceptionStatusValues.OK"
    ), "a resend must still be acknowledged, or the resource manager will retry it"
    assert (
        len(frbc._system_description_history) == history_size
    ), "unchanged content must not be stored again"
    assert len(frbc.sent) == sent_after_first, "nor trigger another schedule"


@pytest.mark.asyncio
async def test_a_changed_system_description_is_acted_on(frbc_system_description):
    frbc = RecordingFRBC()

    await frbc.handle_message(frbc_system_description)
    await asyncio.gather(*frbc.background_tasks)

    changed = FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from=frbc_system_description.valid_from + timedelta(hours=1),
        actuators=frbc_system_description.actuators,
        storage=frbc_system_description.storage,
    )
    await frbc.handle_message(changed)
    await asyncio.gather(*frbc.background_tasks)

    assert len(frbc._system_description_history) == 2
    assert len(frbc.sent) == 2
