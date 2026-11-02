"""Tests for how the FRBC handler keeps track of outstanding work:

- a re-sent system description is acknowledged but not acted on,
- only state-changing instructions are sent, and the outstanding batch is tracked,
- the newest request supersedes older ones that are still waiting to run.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from s2python.common import InstructionStatus, InstructionStatusUpdate, RevokeObject
from s2python.frbc import FRBCInstruction, FRBCSystemDescription

from flexmeasures_client.s2.control_types.FRBC import FRBC, FRBCTest
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


class TestTransitionFilter:
    def test_repeats_are_dropped_and_changes_kept(self):
        on, off = get_unique_id(), get_unique_id()
        actuator = get_unique_id()
        modes = [on, on, on, off, off, on, on, on]
        instructions = [
            make_instruction(mode, 1.0, slot, actuator)
            for slot, mode in enumerate(modes)
        ]

        filtered = FRBC.filter_instruction_transitions(instructions)

        assert [str(i.operation_mode) for i in filtered] == [str(on), str(off), str(on)]
        assert (
            filtered[0] is instructions[0]
        ), "the first instruction establishes the state"

    def test_a_changed_factor_is_a_transition(self):
        mode = get_unique_id()
        actuator = get_unique_id()
        instructions = [
            make_instruction(mode, factor, slot, actuator)
            for slot, factor in enumerate([0.5, 0.5, 1.0, 1.0])
        ]

        filtered = FRBC.filter_instruction_transitions(instructions)

        assert [i.operation_mode_factor for i in filtered] == [0.5, 1.0]

    def test_actuators_are_followed_separately(self):
        mode = get_unique_id()
        one, other = get_unique_id(), get_unique_id()
        instructions = [
            make_instruction(mode, 1.0, 0, one),
            make_instruction(mode, 1.0, 0, other),  # another actuator, so a transition
            make_instruction(mode, 1.0, 1, one),  # a repeat for the first actuator
        ]

        filtered = FRBC.filter_instruction_transitions(instructions)

        assert len(filtered) == 2
        assert {str(i.actuator_id) for i in filtered} == {str(one), str(other)}


@pytest.mark.asyncio
async def test_only_transitions_are_sent_and_recorded():
    frbc = RecordingFRBC()
    mode, actuator = get_unique_id(), get_unique_id()
    instructions = [make_instruction(mode, 1.0, slot, actuator) for slot in range(4)]

    sent = await frbc.send_instruction_batch(instructions)

    assert sent == 1, "three of the four instructions ask for nothing new"
    assert len(frbc.sent) == 1
    assert list(frbc._sent_instructions) == [str(instructions[0].id)]


@pytest.mark.asyncio
async def test_a_new_batch_revokes_only_the_instructions_still_pending():
    frbc = RecordingFRBC()
    frbc.send_revocations = True

    mode, actuator = get_unique_id(), get_unique_id()
    first = [
        make_instruction(mode, float(slot % 2), slot, actuator) for slot in range(4)
    ]
    await frbc.send_instruction_batch(first)
    assert len(frbc._sent_instructions) == 4, "alternating factors are all transitions"

    # The resource manager reports one of them as finished, so it cannot be called off.
    finished = first[0]
    await frbc.handle_message(
        InstructionStatusUpdate(
            message_id=get_unique_id(),
            instruction_id=finished.id,
            status_type=InstructionStatus.SUCCEEDED,
            timestamp=datetime(2022, 12, 1, 12, 0, tzinfo=timezone.utc),
        )
    )

    frbc.sent.clear()
    await frbc.send_instruction_batch(
        [make_instruction(get_unique_id(), 1.0, 0, actuator)]
    )

    revocations = [m for m in frbc.sent if isinstance(m, RevokeObject)]
    assert len(revocations) == 3
    assert str(finished.id) not in {str(r.object_id) for r in revocations}
    assert len(frbc._sent_instructions) == 1, "the registry holds only the new batch"


@pytest.mark.asyncio
async def test_a_new_batch_revokes_nothing_unless_revocations_are_on():
    frbc = RecordingFRBC()
    actuator = get_unique_id()

    await frbc.send_instruction_batch(
        [make_instruction(get_unique_id(), 1.0, 0, actuator)]
    )
    frbc.sent.clear()
    await frbc.send_instruction_batch(
        [make_instruction(get_unique_id(), 1.0, 1, actuator)]
    )

    assert not [m for m in frbc.sent if isinstance(m, RevokeObject)]
