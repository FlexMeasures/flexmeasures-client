from datetime import datetime

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
from flexmeasures_client.s2.frbc import FRBC
from flexmeasures_client.s2.utils import get_unique_id


def test_cem():  # TODO: move into different test functions
    cem = CEM()
    frbc = FRBC()

    cem.register_control_type(ControlType.FILL_RATE_BASED_CONTROL, frbc)

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

    response = cem.handle_message(handshake_message)

    assert response["message_type"] == "HandshakeResponse"
    assert response["selected_protocol_version"] == "0.1.0"

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
        available_control_types=[ControlType.FILL_RATE_BASED_CONTROL],
        provides_forecast=True,
        provides_power_measurement_types=[
            CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
        ],
    ).dict()

    response = cem.handle_message(resource_manager_details_message)

    assert response["message_type"] == "ReceptionStatus"
    assert response["status"] == "OK"
    assert cem.resource_manager_details == resource_manager_details_message
    assert cem.control_type == ControlType.NO_SELECTION

    print(response)

    """
    ========================
    Activate control type
    ========================
    """

    message = cem.activate_control_type(ControlType.FILL_RATE_BASED_CONTROL)

    print(message)
    assert cem.control_type == ControlType.NO_SELECTION

    response = ReceptionStatus(
        subject_message_id=message.message_id.__root__, status=ReceptionStatusValues.OK
    ).dict()

    cem.handle_message(response)

    assert cem.control_type == ControlType.FILL_RATE_BASED_CONTROL

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

    response = cem.handle_message(system_description_message)

    # checking that FRBC handler is being called
    assert (
        cem.control_types_handlers[
            ControlType.FILL_RATE_BASED_CONTROL
        ].system_description_list[-1]
        == system_description_message
    )
    print(response)

    # Let's check check what it does when we change state

    response = cem.activate_control_type()
