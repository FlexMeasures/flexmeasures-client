from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from s2python.common import (
    Commodity,
    CommodityQuantity,
    ControlType,
    Duration,
    EnergyManagementRole,
    Handshake,
    NumberRange,
    PowerForecastValue,
    PowerRange,
    ResourceManagerDetails,
    Role,
    RoleType,
)
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCSystemDescription,
)
from s2python.ppbc import (
    PPBCPowerProfileDefinition,
    PPBCPowerSequence,
    PPBCPowerSequenceContainer,
    PPBCPowerSequenceElement,
)

from flexmeasures_client.s2.utils import get_unique_id


@pytest.fixture(scope="session")
def frbc_system_description():
    ########
    # FRBC #
    ########

    thp_operation_mode_element = FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=0, end_of_range=80),
        fill_rate=NumberRange(start_of_range=0, end_of_range=2),
        power_ranges=[
            PowerRange(
                start_of_range=10,
                end_of_range=1000,
                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
            )
        ],
    )

    thp_operation_mode = FRBCOperationMode(
        id=str(uuid.uuid4()),
        elements=[thp_operation_mode_element],
        abnormal_condition_only=False,
    )

    nes_operation_mode_element = FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=0, end_of_range=100),
        fill_rate=NumberRange(start_of_range=0, end_of_range=1),
        power_ranges=[
            PowerRange(
                start_of_range=10,
                end_of_range=1000,
                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
            )
        ],
    )

    nes_operation_mode = FRBCOperationMode(
        id=str(uuid.uuid4()),
        elements=[nes_operation_mode_element],
        abnormal_condition_only=False,
    )

    actuator = FRBCActuatorDescription(
        id=str(uuid.uuid4()),
        supported_commodities=[Commodity.ELECTRICITY],
        operation_modes=[thp_operation_mode, nes_operation_mode],
        transitions=[],
        timers=[],
    )

    storage = FRBCStorageDescription(
        provides_leakage_behaviour=True,
        provides_fill_level_target_profile=True,
        provides_usage_forecast=True,
        fill_level_range=NumberRange(start_of_range=0, end_of_range=1),
    )

    system_description_message = FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),  # Attach UTC timezone
        actuators=[actuator],
        storage=storage,
    )

    return system_description_message


@pytest.fixture(scope="session")
def ppbc_power_profile_definition():
    forecast1 = PowerForecastValue(
        value_expected=100.0, commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
    )
    forecast2 = PowerForecastValue(
        value_expected=200.0, commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
    )
    forecast3 = PowerForecastValue(
        value_expected=300.0, commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
    )

    element1 = PPBCPowerSequenceElement(
        duration=Duration(1), power_values=[forecast1, forecast2]
    )
    element2 = PPBCPowerSequenceElement(
        duration=Duration(1), power_values=[forecast2, forecast3, forecast1]
    )

    power_sequence1 = PPBCPowerSequence(
        id=uuid.uuid4(),
        elements=[element1, element2],
        is_interruptible=False,
        max_pause_before=Duration(0),
        abnormal_condition_only=False,
    )

    power_sequence2 = PPBCPowerSequence(
        id=uuid.uuid4(),
        elements=[element2, element1],
        is_interruptible=True,
        max_pause_before=Duration(0),
        abnormal_condition_only=True,
    )

    power_sequence3 = PPBCPowerSequence(
        id=uuid.uuid4(),
        elements=[element2],
        is_interruptible=False,
        max_pause_before=Duration(10000),
        abnormal_condition_only=False,
    )

    power_sequence4 = PPBCPowerSequence(
        id=uuid.uuid4(),
        elements=[element1],
        is_interruptible=True,
        max_pause_before=Duration(10000),
        abnormal_condition_only=True,
    )

    power_sequence_container1 = PPBCPowerSequenceContainer(
        id=uuid.uuid4(),
        power_sequences=[
            power_sequence1,
            power_sequence2,
        ],
    )

    power_sequence_container2 = PPBCPowerSequenceContainer(
        id=uuid.uuid4(),
        power_sequences=[
            power_sequence3,
        ],
    )

    power_sequence_container3 = PPBCPowerSequenceContainer(
        id=uuid.uuid4(),
        power_sequences=[
            power_sequence4,
        ],
    )

    power_profile_definition = PPBCPowerProfileDefinition(
        message_id=uuid.uuid4(),
        id=uuid.uuid4(),
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(hours=4),
        power_sequences_containers=[
            power_sequence_container1,
            power_sequence_container2,
            power_sequence_container3,
        ],
    )

    return power_profile_definition


def resource_manager_details_frbc():

    return ResourceManagerDetails(
        message_id=get_unique_id(),
        resource_id=get_unique_id(),
        roles=[Role(role=RoleType.ENERGY_STORAGE, commodity=Commodity.ELECTRICITY)],
        instruction_processing_delay=Duration(1),
        available_control_types=[
            ControlType.FILL_RATE_BASED_CONTROL,
            ControlType.NO_SELECTION,
        ],
        provides_forecast=True,
        provides_power_measurement_types=[
            CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
        ],
    )


@pytest.fixture(scope="session")
def resource_manager_details_ppbc():
    return ResourceManagerDetails(
        message_id=get_unique_id(),
        resource_id=get_unique_id(),
        roles=[Role(role=RoleType.ENERGY_CONSUMER, commodity=Commodity.ELECTRICITY)],
        instruction_processing_delay=Duration(1),
        available_control_types=[
            ControlType.POWER_PROFILE_BASED_CONTROL,
            ControlType.NO_SELECTION,
        ],
        provides_forecast=True,
        provides_power_measurement_types=[
            CommodityQuantity.ELECTRIC_POWER_L1,
            CommodityQuantity.ELECTRIC_POWER_L2,
            CommodityQuantity.ELECTRIC_POWER_L3,
        ],
    )


@pytest.fixture(scope="session")
def rm_handshake():
    return Handshake(
        message_id=get_unique_id(),
        role=EnergyManagementRole.RM,
        supported_protocol_versions=["1.0.0"],
    )
