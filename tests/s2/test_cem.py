from __future__ import annotations

import asyncio
import pytest
import pytest_asyncio
from s2python.common import ControlType, ReceptionStatus, ReceptionStatusValues
from unittest.mock import AsyncMock, MagicMock

from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.control_types.FRBC import FRBCTest


@pytest_asyncio.fixture
async def cleanup_tasks():
    """Clean up any pending asyncio tasks after each test."""
    yield
    # Give any background tasks a chance to complete or fail
    await asyncio.sleep(0.1)
    # Cancel any remaining tasks
    for task in asyncio.all_tasks():
        if not task.done():
            task.cancel()



@pytest.mark.asyncio
async def test_handshake(rm_handshake):
    cem = CEM(fm_client=None)
    frbc = FRBCTest()

    cem.register_control_type(frbc)

    #############
    # Handshake #
    #############

    # RM sends HandShake
    await cem.handle_message(rm_handshake)

    assert (
        cem._sending_queue.qsize() == 2
    )  # check that two messages are put to the outgoing queue (ReceptionStatus and HandshakeResponse)

    # CEM response
    response, _ = await cem.get_message()  # ReceptionStatus for Handshake
    response, _ = await cem.get_message()  # HandshakeResponse

    assert (
        response["message_type"] == "HandshakeResponse"
    ), "response message_type should be HandshakeResponse"
    assert (
        response["selected_protocol_version"] == "1.0.0"
    ), "CEM selected protocol version should be supported by the Resource Manager"


@pytest.mark.asyncio
async def test_resource_manager_details(resource_manager_details, rm_handshake, cleanup_tasks):
    cem = CEM(fm_client=None)
    frbc = FRBCTest()

    cem.register_control_type(frbc)

    #############
    # Handshake #
    #############

    await cem.handle_message(rm_handshake)

    assert (
        cem._sending_queue.qsize() == 2
    )  # check that message is put to the outgoing queue

    response, _ = await cem.get_message()  # ReceptionStatus for Handshake
    response, _ = await cem.get_message()  # HandshakeResponse

    ##########################
    # ResourceManagerDetails #
    ##########################

    # RM sends ResourceManagerDetails
    await cem.handle_message(resource_manager_details)
    response, _ = await cem.get_message()

    # CEM response is ReceptionStatus with an OK status
    assert response["message_type"] == "ReceptionStatus"
    assert response["status"] == "OK"

    assert (
        cem._resource_manager_details == resource_manager_details
    ), "CEM should store the resource_manager_details"
    assert cem.control_type == ControlType.NO_SELECTION, (
        "CEM control type should switch to ControlType.NO_SELECTION,"
        "independently of the original type"
    )


@pytest.mark.asyncio
async def test_activate_control_type(
    frbc_system_description, resource_manager_details, rm_handshake, cleanup_tasks
):
    cem = CEM(fm_client=None)
    frbc = FRBCTest()

    cem.register_control_type(frbc)

    #############
    # Handshake #
    #############

    await cem.handle_message(rm_handshake)
    response, _ = await cem.get_message()  # ReceptionStatus for Handshake
    response, _ = await cem.get_message()  # HandshakeResponse

    ##########################
    # ResourceManagerDetails #
    ##########################
    await cem.handle_message(resource_manager_details)
    response, _ = await cem.get_message()

    #########################
    # Activate control type #
    #########################

    # CEM sends a request to change te control type
    await cem.activate_control_type(ControlType.FILL_RATE_BASED_CONTROL)
    message, _ = await cem.get_message()

    assert cem.control_type == ControlType.NO_SELECTION, (
        "the control type should still be NO_SELECTION (rather than FRBC),"
        " because the RM has not yet confirmed FRBC activation"
    )

    response = ReceptionStatus(
        subject_message_id=message.get("message_id"), status=ReceptionStatusValues.OK
    )

    await cem.handle_message(response)

    assert (
        cem.control_type == ControlType.FILL_RATE_BASED_CONTROL
    ), "after a positive ResponseStatus, the status changes from NO_SELECTION to FRBC"


@pytest.mark.asyncio
async def test_messages_route_to_control_type_handler(
    frbc_system_description, resource_manager_details, rm_handshake, cleanup_tasks
):
    cem = CEM(fm_client=None)
    frbc = FRBCTest()

    cem.register_control_type(frbc)
    cem._control.handler_ready[ControlType.FILL_RATE_BASED_CONTROL] = True

    #############
    # Handshake #
    #############

    await cem.handle_message(rm_handshake)
    response, _ = await cem.get_message()  # ReceptionStatus for Handshake
    response, _ = await cem.get_message()  # HandshakeResponse

    ##########################
    # ResourceManagerDetails #
    ##########################
    await cem.handle_message(resource_manager_details)
    response, _ = await cem.get_message()

    #########################
    # Activate control type #
    #########################

    await cem.activate_control_type(ControlType.FILL_RATE_BASED_CONTROL)
    message, _ = await cem.get_message()

    response = ReceptionStatus(
        subject_message_id=message.get("message_id"), status=ReceptionStatusValues.OK
    )

    await cem.handle_message(response)

    ########
    # FRBC #
    ########

    await cem.handle_message(frbc_system_description)
    response, _ = await cem.get_message()

    # checking that FRBC handler is being called
    assert (
        cem._control_types_handlers[
            ControlType.FILL_RATE_BASED_CONTROL
        ]._system_description_history[str(frbc_system_description.message_id)]
        == frbc_system_description
    ), (
        "the FRBC.SystemDescription message should be stored"
        "in the frbc.system_description_history variable"
    )

    # change of control type is not performed in case that the RM answers
    # with a negative response
    await cem.activate_control_type(ControlType.NO_SELECTION)
    response, _ = await cem.get_message()
    assert (
        cem._control.control_type == ControlType.FILL_RATE_BASED_CONTROL
    ), "control type should not change, confirmation still pending"

    await cem.handle_message(
        ReceptionStatus(
            subject_message_id=response.get("message_id"),
            status=ReceptionStatusValues.INVALID_CONTENT,
        )
    )

    assert (
        cem._control.control_type == ControlType.FILL_RATE_BASED_CONTROL
    ), "control type should not change, confirmation state is not 'OK'"
    assert (
        response.get("message_id")
        not in cem._control_types_handlers[
            ControlType.FILL_RATE_BASED_CONTROL
        ].success_callbacks
    ), "success callback should be deleted"


@pytest.mark.asyncio
async def test_automatic_change_control_type(resource_manager_details, rm_handshake, cleanup_tasks):
    cem = CEM(fm_client=None, default_control_type=ControlType.FILL_RATE_BASED_CONTROL)
    frbc = FRBCTest()

    cem.register_control_type(frbc)

    #############
    # Handshake #
    #############

    await cem.handle_message(rm_handshake)

    assert (
        cem._sending_queue.qsize() == 2
    )  # check that message is put to the outgoing queue

    response, _ = await cem.get_message()
    response, _ = await cem.get_message()  # HandshakeResponse

    ##########################
    # ResourceManagerDetails #
    ##########################

    # RM sends ResourceManagerDetails
    await cem.handle_message(resource_manager_details)
    response, _ = await cem.get_message()

    # CEM sends control type on receiving the ResourceManagerDetails
    assert response["message_type"] == "SelectControlType"
    assert response["control_type"] == "FILL_RATE_BASED_CONTROL"

    response, _ = await cem.get_message()

    # CEM response is ReceptionStatus with an OK status
    assert response["message_type"] == "ReceptionStatus"
    assert response["status"] == "OK"


@pytest.mark.asyncio
async def test_handle_message_during_handler_registration_race():
    cem = CEM(
        fm_client=MagicMock(),
        logger=MagicMock(),
    )

    # --- Fake FRBC handler that we will "register late"
    frbc_handler = AsyncMock()
    frbc_handler._control_type = ControlType.FILL_RATE_BASED_CONTROL
    frbc_handler.supports_message.return_value = True
    frbc_handler.handle_message.return_value = {"ok": True}

    # Simulate slow map_resource_to_asset
    registration_started = asyncio.Event()
    registration_continue = asyncio.Event()

    async def slow_map_resource_to_asset(message):
        registration_started.set()
        await registration_continue.wait()

        cem.register_control_type(frbc_handler)
        cem._control.handler_ready[ControlType.FILL_RATE_BASED_CONTROL] = True

    cem.map_resource_to_asset = slow_map_resource_to_asset

    # Set control type BEFORE handler exists
    cem.update_control_type(ControlType.FILL_RATE_BASED_CONTROL)

    # Start async registration
    task = asyncio.create_task(
        cem.map_resource_to_asset(MagicMock(resource_id="x", name="test"))
    )

    # Wait until registration has started but not finished
    await registration_started.wait()

    # --- THIS is the race moment
    msg = {"message_type": "TestMessage", "message_id": "550e8400-e29b-41d4-a716-446655440000"}

    # Should NOT crash even though handler isn't registered yet
    await cem.handle_message(msg)

    # A response should be queued (ReceptionStatus with TEMPORARY_ERROR since handler not ready)
    response, _ = await cem.get_message()
    assert response.get("message_type") == "ReceptionStatus"
    assert response.get("status") == "TEMPORARY_ERROR"

    # Now finish registration
    registration_continue.set()
    await task

    # Now handler should have been called the second time
    await cem.handle_message(msg)

    assert frbc_handler.handle_message.called
