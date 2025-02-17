from __future__ import annotations

import pytest
from s2python.common import ControlType, ReceptionStatus, ReceptionStatusValues

from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.control_types.FRBC import FRBCTest


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
        cem._sending_queue.qsize() == 1
    )  # check that message is put to the outgoing queue

    # CEM response
    response = await cem.get_message()

    assert (
        response["message_type"] == "HandshakeResponse"
    ), "response message_type should be HandshakeResponse"
    assert (
        response["selected_protocol_version"] == "1.0.0"
    ), "CEM selected protocol version should be supported by the Resource Manager"


@pytest.mark.asyncio
async def test_resource_manager_details(resource_manager_details, rm_handshake):
    cem = CEM(fm_client=None)
    frbc = FRBCTest()

    cem.register_control_type(frbc)

    #############
    # Handshake #
    #############

    await cem.handle_message(rm_handshake)

    assert (
        cem._sending_queue.qsize() == 1
    )  # check that message is put to the outgoing queue

    response = await cem.get_message()

    ##########################
    # ResourceManagerDetails #
    ##########################

    # RM sends ResourceManagerDetails
    await cem.handle_message(resource_manager_details)
    response = await cem.get_message()

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
    frbc_system_description, resource_manager_details, rm_handshake
):
    cem = CEM(fm_client=None)
    frbc = FRBCTest()

    cem.register_control_type(frbc)

    #############
    # Handshake #
    #############

    await cem.handle_message(rm_handshake)
    response = await cem.get_message()

    ##########################
    # ResourceManagerDetails #
    ##########################
    await cem.handle_message(resource_manager_details)
    response = await cem.get_message()

    #########################
    # Activate control type #
    #########################

    # CEM sends a request to change te control type
    await cem.activate_control_type(ControlType.FILL_RATE_BASED_CONTROL)
    message = await cem.get_message()

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
    frbc_system_description, resource_manager_details, rm_handshake
):
    cem = CEM(fm_client=None)
    frbc = FRBCTest()

    cem.register_control_type(frbc)

    #############
    # Handshake #
    #############

    await cem.handle_message(rm_handshake)
    response = await cem.get_message()

    ##########################
    # ResourceManagerDetails #
    ##########################
    await cem.handle_message(resource_manager_details)
    response = await cem.get_message()

    #########################
    # Activate control type #
    #########################

    await cem.activate_control_type(ControlType.FILL_RATE_BASED_CONTROL)
    message = await cem.get_message()

    response = ReceptionStatus(
        subject_message_id=message.get("message_id"), status=ReceptionStatusValues.OK
    )

    await cem.handle_message(response)

    ########
    # FRBC #
    ########

    await cem.handle_message(frbc_system_description)
    response = await cem.get_message()

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
    response = await cem.get_message()
    assert (
        cem._control_type == ControlType.FILL_RATE_BASED_CONTROL
    ), "control type should not change, confirmation still pending"

    await cem.handle_message(
        ReceptionStatus(
            subject_message_id=response.get("message_id"),
            status=ReceptionStatusValues.INVALID_CONTENT,
        )
    )

    assert (
        cem._control_type == ControlType.FILL_RATE_BASED_CONTROL
    ), "control type should not change, confirmation state is not 'OK'"
    assert (
        response.get("message_id")
        not in cem._control_types_handlers[
            ControlType.FILL_RATE_BASED_CONTROL
        ].success_callbacks
    ), "success callback should be deleted"


@pytest.mark.asyncio
async def test_automatic_change_control_type(resource_manager_details, rm_handshake):
    cem = CEM(fm_client=None, default_control_type=ControlType.FILL_RATE_BASED_CONTROL)
    frbc = FRBCTest()

    cem.register_control_type(frbc)

    #############
    # Handshake #
    #############

    await cem.handle_message(rm_handshake)

    assert (
        cem._sending_queue.qsize() == 1
    )  # check that message is put to the outgoing queue

    response = await cem.get_message()

    ##########################
    # ResourceManagerDetails #
    ##########################

    # RM sends ResourceManagerDetails
    await cem.handle_message(resource_manager_details)
    response = await cem.get_message()

    # CEM sends control type on receiving the ResourceManagerDetails
    assert response["message_type"] == "SelectControlType"
    assert response["control_type"] == "FILL_RATE_BASED_CONTROL"

    response = await cem.get_message()

    # CEM response is ReceptionStatus with an OK status
    assert response["message_type"] == "ReceptionStatus"
    assert response["status"] == "OK"
