from datetime import datetime

import pytest
from python_s2_protocol.common.messages import (
    EnergyManagementRole,
    Handshake,
    ReceptionStatus,
    ReceptionStatusValues,
    ResourceManagerDetails,
)
from python_s2_protocol.common.schemas import (
    Commodity,
    CommodityQuantity,
    ControlType,
    Duration,
    NumberRange,
    PowerRange,
    Role,
    RoleType,
)
from python_s2_protocol.FRBC.messages import FRBCSystemDescription
from python_s2_protocol.FRBC.schemas import (
    FRBCActuatorDescription,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
)

from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.control_types.frbc import FRBC
from flexmeasures_client.s2.utils import get_unique_id


@pytest.mark.asyncio
async def test_cem():  # TODO: move into different test functions
    cem = CEM(sensor_id=1, fm_client=None)
    frbc = FRBC()

    cem.register_control_type(frbc)

    """
    =========
    Handshake
    =========
    """
    handshake_message = Handshake(
        message_id=get_unique_id(),
        role=EnergyManagementRole.RM,
        supported_protocol_versions=["0.1.0"],
    ).dict()

    await cem.handle_message(handshake_message)

    assert (
        cem._sending_queue.qsize() == 1
    )  # check that message is put to the outgoing queue

    response = await cem.get_message()

    assert (
        response["message_type"] == "HandshakeResponse"
    ), "response message_type should be HandshakeResponse"
    assert (
        response["selected_protocol_version"] == "0.1.0"
    ), "CEM selected protocol version should be supported by the Resource Manager"

    print(cem)

    """
    =========
    ResourceManagerDetails
    =========
    """
    resource_manager_details_message = ResourceManagerDetails(
        message_id=get_unique_id(),
        resource_id=get_unique_id(),
        roles=[Role(role=RoleType.ENERGY_STORAGE, commodity=Commodity.ELECTRICITY)],
        instruction_processing_delay=Duration(__root__=1.0),
        available_control_types=[
            ControlType.FILL_RATE_BASED_CONTROL,
            ControlType.NO_SELECTION,
        ],
        provides_forecast=True,
        provides_power_measurement_types=[
            CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
        ],
    ).dict()

    await cem.handle_message(resource_manager_details_message)
    response = await cem.get_message()

    assert response["message_type"] == "ReceptionStatus"
    assert response["status"] == "OK"
    assert (
        cem._resource_manager_details == resource_manager_details_message
    ), "CEM should store the resource_manager_details"
    assert cem.control_type == ControlType.NO_SELECTION, (
        "CEM control type should switch to ControlType.NO_SELECTION,"
        "independently of the original type"
    )

    print(response)

    """
    ========================
    Activate control type
    ========================
    """

    await cem.activate_control_type(ControlType.FILL_RATE_BASED_CONTROL)
    message = await cem.get_message()

    print(message)
    assert cem.control_type == ControlType.NO_SELECTION, (
        "the control type should still be NO_SELECTION (rather than FRBC),"
        " because the RM has not yet confirmed FRBC activation"
    )

    response = ReceptionStatus(
        subject_message_id=message.message_id.__root__, status=ReceptionStatusValues.OK
    ).dict()

    await cem.handle_message(response)

    assert (
        cem.control_type == ControlType.FILL_RATE_BASED_CONTROL
    ), "after a positive ResponseStatus, the status changes from NO_SELECTION to FRBC"

    """
    ====
    FRBC
    ====
    """
    operation_mode_element = FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=0, end_of_range=1),
        fill_rate=NumberRange(start_of_range=0, end_of_range=1),
        power_ranges=[
            PowerRange(
                start_of_range=10,
                end_of_range=1000,
                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
            )
        ],
    )

    operation_mode = FRBCOperationMode(
        id=get_unique_id(),
        elements=[operation_mode_element],
        abnormal_condition_only=False,
    )

    actuator = FRBCActuatorDescription(
        id=get_unique_id(),
        supported_commodities=[Commodity.ELECTRICITY],
        operation_modes=[operation_mode],
        transitions=[],
        timers=[],
    )

    storage = FRBCStorageDescription(
        provides_leakage_behaviour=False,
        provides_fill_level_target_profile=False,
        provides_usage_forecast=False,
        fill_level_range=NumberRange(start_of_range=0, end_of_range=1),
    )

    system_description_message = FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from=datetime.now(),
        actuators=[actuator],
        storage=storage,
    ).dict()

    await cem.handle_message(system_description_message)
    response = await cem.get_message()

    # checking that FRBC handler is being called
    assert (
        cem.control_types_handlers[
            ControlType.FILL_RATE_BASED_CONTROL
        ]._system_description_list[-1]
        == system_description_message
    ), (
        "the FRBC.SystemDescription message should be stored"
        "in the frbc.system_description_list variable"
    )

    print(response)

    # change of control type is not performed in case that the RM answers
    # with a negative response
    await cem.activate_control_type(ControlType.NO_SELECTION)
    response = await cem.get_message()
    assert (
        cem.control_type == ControlType.FILL_RATE_BASED_CONTROL
    ), "control type should not change, confirmation still pending"

    await cem.handle_message(
        ReceptionStatus(
            subject_message_id=response.message_id.__root__,
            status=ReceptionStatusValues.INVALID_CONTENT,
        ).dict()
    )

    assert (
        cem.control_type == ControlType.FILL_RATE_BASED_CONTROL
    ), "control type should not change, confirmation state is not 'OK'"
    assert (
        response.message_id.__root__
        not in cem.control_types_handlers[
            ControlType.FILL_RATE_BASED_CONTROL
        ].success_callbacks
    ), "success callback should be deleted"