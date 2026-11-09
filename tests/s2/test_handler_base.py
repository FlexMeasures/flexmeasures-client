"""What every S2 handler does with the messages it is given, whatever its control type."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from s2python.common import ControlType, Handshake, ReceptionStatusValues

from flexmeasures_client.s2 import Handler, register
from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.control_types.FRBC import FRBCTest
from flexmeasures_client.s2.utils import get_reception_status


class HandshakeHandler(Handler):
    """A handler for one message type, which counts what reaches it."""

    def __init__(self):
        super().__init__()
        self.handled = []

    @register(Handshake)
    def handle_handshake(self, message: Handshake):
        self.handled.append(message)
        return get_reception_status(message)


@pytest.mark.asyncio
async def test_an_invalid_message_is_answered_as_invalid_data(rm_handshake):
    handler = HandshakeHandler()
    message = rm_handshake.to_dict()
    message["supported_protocol_versions"] = "not a list"

    answer = await handler.handle_message(message)

    assert answer.status == ReceptionStatusValues.INVALID_DATA
    assert str(answer.subject_message_id) == str(message["message_id"])
    assert answer.diagnostic_label, "the answer says what was invalid"
    assert handler.handled == [], "the handler itself never sees an invalid message"
    assert answer in handler.outgoing_messages.values(), "the answer is kept as sent"


@pytest.mark.asyncio
async def test_a_valid_message_reaches_its_handler(rm_handshake):
    """The positive control for the test above: the same message, unaltered, is handled."""
    handler = HandshakeHandler()

    answer = await handler.handle_message(rm_handshake.to_dict())

    assert answer.status == ReceptionStatusValues.OK
    assert len(handler.handled) == 1


@pytest.mark.asyncio
async def test_a_message_can_be_given_as_json(rm_handshake):
    handler = HandshakeHandler()

    answer = await handler.handle_message(rm_handshake.to_json())

    assert answer.status == ReceptionStatusValues.OK
    assert len(handler.handled) == 1


def test_a_failure_callback_is_stored_with_its_arguments():
    handler = HandshakeHandler()
    called = []

    handler.register_failure_callbacks(
        "some-id", lambda **kwargs: called.append(kwargs), why="test"
    )
    handler.failure_callbacks["some-id"]()

    assert called == [{"why": "test"}]
    assert handler.success_callbacks == {}


def test_a_message_given_as_json_is_recognised_as_supported(rm_handshake):
    handler = HandshakeHandler()

    assert handler.supports_message(rm_handshake.to_json())
    assert not handler.supports_message(
        json.dumps({"message_type": "FRBC.StorageStatus"})
    )


async def _cem_with_details(resource_manager_details, rm_handshake) -> CEM:
    cem = CEM(fm_client=None)
    cem.register_control_type(FRBCTest())
    await cem.handle_message(rm_handshake)
    await cem.handle_message(resource_manager_details)
    while not cem._sending_queue.empty():
        await cem.get_message()
    return cem


@pytest.mark.asyncio
async def test_activating_the_control_type_in_force_sends_nothing(
    resource_manager_details, rm_handshake
):
    cem = await _cem_with_details(resource_manager_details, rm_handshake)
    assert cem.control_type == ControlType.NO_SELECTION

    answer = await cem.activate_control_type(ControlType.NO_SELECTION)

    assert answer is None
    assert cem._sending_queue.empty()


@pytest.mark.asyncio
async def test_a_control_type_the_resource_manager_lacks_is_not_activated(
    resource_manager_details, rm_handshake
):
    cem = await _cem_with_details(resource_manager_details, rm_handshake)

    answer = await cem.activate_control_type(ControlType.POWER_ENVELOPE_BASED_CONTROL)

    assert answer is None
    assert cem._sending_queue.empty()
    assert cem.control_type == ControlType.NO_SELECTION


def test_registering_a_control_type_again_replaces_its_handler(caplog):
    cem = CEM(fm_client=None)
    first, second = FRBCTest(), FRBCTest()
    cem.register_control_type(first)

    cem.register_control_type(second)

    assert cem._control_types_handlers[ControlType.FILL_RATE_BASED_CONTROL] is second
    assert "already registered" in caplog.text


def test_an_empty_schedule_gives_no_instructions(frbc_system_description):
    from flexmeasures_client.s2.control_types.FRBC.utils import (
        fm_schedule_to_instructions,
    )

    no_values = {
        "start": "2026-01-01T00:00:00+00:00",
        "duration": "PT0H",
        "values": [],
        "unit": "W",
    }
    no_frame = pd.DataFrame({"schedule": []}, index=pd.DatetimeIndex([], tz="UTC"))

    assert (
        fm_schedule_to_instructions(
            no_values, frbc_system_description, initial_fill_level=0.0
        )
        == []
    )
    assert (
        fm_schedule_to_instructions(
            no_frame, frbc_system_description, initial_fill_level=0.0
        )
        == []
    )
