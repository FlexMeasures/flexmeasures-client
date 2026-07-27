"""Tests for the FRBC outstanding-work registry (instruction-storm fix):

- transition filtering of instruction batches,
- latest-request-wins supersession of storage-status handler work,
- content-hash dedupe of re-sent system descriptions,
- the sent-instruction registry, status updates and opt-in revocation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import pytest
from s2python.common import (
    InstructionStatus,
    InstructionStatusUpdate,
    ReceptionStatusValues,
)
from s2python.frbc import FRBCInstruction, FRBCStorageStatus, FRBCSystemDescription

from flexmeasures_client.s2.control_types.FRBC import FRBC, FRBCTest
from flexmeasures_client.s2.utils import get_unique_id


def make_instruction(mode: str, factor: float, slot: int, actuator: str) -> FRBCInstruction:
    return FRBCInstruction(
        message_id=get_unique_id(),
        id=get_unique_id(),
        actuator_id=actuator,
        operation_mode=mode,
        operation_mode_factor=factor,
        execution_time=datetime(2022, 12, 1, slot // 4, (slot % 4) * 15, tzinfo=timezone.utc),
        abnormal_condition=False,
    )


class TestTransitionFilter:
    def test_repeats_are_dropped_and_changes_kept(self):
        on, off = get_unique_id(), get_unique_id()
        actuator = get_unique_id()
        modes = [on, on, on, off, off, on, on, on]
        instructions = [
            make_instruction(m, 1.0, i, actuator) for i, m in enumerate(modes)
        ]
        filtered = FRBC.filter_instruction_transitions(instructions)
        assert [str(i.operation_mode) for i in filtered] == [str(on), str(off), str(on)]
        # The first instruction always survives (it establishes the state).
        assert filtered[0] is instructions[0]

    def test_factor_change_is_a_transition(self):
        mode = get_unique_id()
        actuator = get_unique_id()
        instructions = [
            make_instruction(mode, f, i, actuator)
            for i, f in enumerate([0.5, 0.5, 1.0, 1.0])
        ]
        filtered = FRBC.filter_instruction_transitions(instructions)
        assert [i.operation_mode_factor for i in filtered] == [0.5, 1.0]

    def test_actuators_are_filtered_independently(self):
        mode = get_unique_id()
        a1, a2 = get_unique_id(), get_unique_id()
        instructions = [
            make_instruction(mode, 1.0, 0, a1),
            make_instruction(mode, 1.0, 0, a2),  # other actuator: kept
            make_instruction(mode, 1.0, 1, a1),  # repeat for a1: dropped
        ]
        filtered = FRBC.filter_instruction_transitions(instructions)
        assert len(filtered) == 2
        assert {str(i.actuator_id) for i in filtered} == {str(a1), str(a2)}


class _RecordingFRBC(FRBCTest):
    """FRBCTest with a recording send_storage_status chain that mimics the
    Simple handler's long round trip (SoC post + schedule wait)."""

    def __init__(self, delay_s: float = 0.05, **kwargs):
        super().__init__(**kwargs)
        self._logger = logging.getLogger("test")
        self.completed_generations: list[int] = []
        self.delay_s = delay_s
        self.sent = []

    async def send_message(self, message):  # capture instead of queueing
        self.sent.append(message)

    async def send_storage_status(self, status: FRBCStorageStatus):
        generation = self._supersession_counters.get("storage_status", 0)
        await asyncio.sleep(self.delay_s)  # the FM round trip
        batch = [
            make_instruction(get_unique_id(), 1.0, i, get_unique_id())
            for i in range(3)
        ]
        sent = await self.send_instruction_batch(
            batch, supersession_key="storage_status", generation=generation
        )
        if sent:
            self.completed_generations.append(generation)


def make_status(fill_level: float = 0.5) -> FRBCStorageStatus:
    return FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=fill_level
    )


@pytest.mark.asyncio
async def test_only_the_latest_storage_status_produces_a_batch():
    """Three rapid statuses (a backlog, or an RM retry storm re-sending under
    fresh message ids): only the newest request's batch may reach the RM."""
    frbc = _RecordingFRBC()

    for _ in range(3):
        await frbc.handle_message(make_status())
    await asyncio.gather(*frbc.background_tasks)

    assert frbc.completed_generations == [3], (
        "only generation 3 (the newest request) may complete a batch"
    )
    # Registry holds exactly the surviving batch.
    assert len(frbc._sent_instructions) == 3


@pytest.mark.asyncio
async def test_resent_system_description_is_acked_but_not_reprocessed(
    frbc_system_description,
):
    frbc = _RecordingFRBC()

    response_1 = await frbc.handle_message(frbc_system_description)
    await asyncio.gather(*frbc.background_tasks)
    history_size = len(frbc._system_description_history)

    resent = FRBCSystemDescription(
        message_id=get_unique_id(),  # fresh id, identical content
        valid_from=frbc_system_description.valid_from,
        actuators=frbc_system_description.actuators,
        storage=frbc_system_description.storage,
    )
    response_2 = await frbc.handle_message(resent)
    await asyncio.gather(*frbc.background_tasks)

    assert str(response_1.status) == str(ReceptionStatusValues.OK)
    assert str(response_2.status) == str(ReceptionStatusValues.OK), (
        "the RM's resend must still be acknowledged"
    )
    assert len(frbc._system_description_history) == history_size, (
        "unchanged content must not be stored or reprocessed"
    )


@pytest.mark.asyncio
async def test_batch_replacement_revokes_only_pending_instructions():
    frbc = _RecordingFRBC()
    frbc.send_revocations = True

    mode, actuator = get_unique_id(), get_unique_id()
    first = [make_instruction(mode, float(i % 2), i, actuator) for i in range(4)]
    await frbc.send_instruction_batch(first)
    first_sent = list(frbc._sent_instructions.values())
    assert len(first_sent) == 4  # alternating factors: all are transitions

    # The RM reports one instruction finished; it must not be revoked.
    finished = first_sent[0]
    await frbc.handle_message(
        InstructionStatusUpdate(
            message_id=get_unique_id(),
            instruction_id=finished.message_id,
            status_type=InstructionStatus.SUCCEEDED,
            timestamp=datetime(2022, 12, 1, 12, 0, tzinfo=timezone.utc),
        )
    )

    frbc.sent.clear()
    second = [make_instruction(get_unique_id(), 1.0, 0, actuator)]
    await frbc.send_instruction_batch(second)

    revokes = [m for m in frbc.sent if m.__class__.__name__ == "RevokeObject"]
    assert len(revokes) == 3, "only NEW/ACCEPTED instructions are revoked"
    revoked_ids = {str(r.object_id) for r in revokes}
    assert str(finished.id) not in revoked_ids
    # Registry now holds only the new batch.
    assert len(frbc._sent_instructions) == 1


@pytest.mark.asyncio
async def test_stale_batch_is_dropped_even_after_the_schedule_returned():
    """The supersession re-check at queueing time: a batch computed for an
    older request is dropped when a newer request arrived meanwhile."""
    frbc = _RecordingFRBC()

    generation = frbc._bump_generation("storage_status")
    frbc._bump_generation("storage_status")  # a newer request arrives

    batch = [make_instruction(get_unique_id(), 1.0, 0, get_unique_id())]
    sent = await frbc.send_instruction_batch(
        batch, supersession_key="storage_status", generation=generation
    )
    assert sent == 0
    assert frbc.sent == []
    assert frbc._sent_instructions == {}
