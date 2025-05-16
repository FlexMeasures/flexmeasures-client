from __future__ import annotations

from datetime import datetime, timezone

import pytest
from s2python.common import (
    Commodity,
    CommodityQuantity,
    ControlType,
    Duration,
    EnergyManagementRole,
    Handshake,
    NumberRange,
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
        id=get_unique_id(),
        elements=[thp_operation_mode_element],
        abnormal_condition_only=False,
        diagnostic_label="THP",
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
        id=get_unique_id(),
        elements=[nes_operation_mode_element],
        abnormal_condition_only=False,
        diagnostic_label="NES",
    )

    idle_operation_mode_element = FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=0, end_of_range=100),
        fill_rate=NumberRange(start_of_range=0, end_of_range=0),
        power_ranges=[
            PowerRange(
                start_of_range=0,
                end_of_range=0,
                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
            )
        ],
    )

    idle_operation_mode = FRBCOperationMode(
        id=get_unique_id(),
        elements=[idle_operation_mode_element],
        abnormal_condition_only=False,
        diagnostic_label="idle",
    )

    actuator = FRBCActuatorDescription(
        id=get_unique_id(),
        supported_commodities=[Commodity.ELECTRICITY],
        operation_modes=[thp_operation_mode, nes_operation_mode, idle_operation_mode],
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
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        actuators=[actuator],
        storage=storage,
    )

    return system_description_message


@pytest.fixture(scope="session")
def resource_manager_details():
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
def rm_handshake():
    return Handshake(
        message_id=get_unique_id(),
        role=EnergyManagementRole.RM,
        supported_protocol_versions=["1.0.0"],
    )


@pytest.fixture
def system_with_transitions():
    POWER_3_PHASE = CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC

    # ---- Helper to define operation mode elements ----
    def make_elem(fill_start, fill_end, rate_max, power_max, efficiency, power_min=10):
        elem = FRBCOperationModeElement(
            fill_level_range=NumberRange(
                start_of_range=fill_start,
                end_of_range=fill_end,
            ),
            fill_rate=NumberRange(
                start_of_range=0.0,
                end_of_range=rate_max,
            ),
            power_ranges=[
                PowerRange(
                    start_of_range=power_min,
                    end_of_range=power_max,
                    commodity_quantity=POWER_3_PHASE,
                )
            ],
            running_costs=NumberRange(start_of_range=1.0, end_of_range=1.0),
        )
        # Attach COP as a custom test-only attribute
        elem._efficiency = efficiency
        return elem

    # ---- Modes ----
    tarnoc_mode = FRBCOperationMode(
        id=get_unique_id(),
        elements=[make_elem(0.0, 0.9, 2.0, 1000, efficiency=0.9)],
        abnormal_condition_only=False,
        diagnostic_label="TARNOC",
    )

    newton_mode = FRBCOperationMode(
        id=get_unique_id(),
        elements=[make_elem(0.9, 1.0, 1.0, 600, efficiency=0.6)],
        abnormal_condition_only=False,
        diagnostic_label="NEWTON",
    )

    idle_mode = FRBCOperationMode(
        id=get_unique_id(),
        elements=[make_elem(0.0, 1.0, 0.0, 0.0, efficiency=0.0, power_min=0)],
        abnormal_condition_only=False,
        diagnostic_label="IDLE",
    )

    # ---- Actuator ----
    actuator = FRBCActuatorDescription(
        id=get_unique_id(),
        operation_modes=[tarnoc_mode, newton_mode, idle_mode],
        transitions=[],  # No transitions
        supported_commodities=[Commodity.ELECTRICITY],
        timers=[],
    )

    # ---- Storage ----
    storage = FRBCStorageDescription(
        provides_leakage_behaviour=True,
        provides_fill_level_target_profile=True,
        provides_usage_forecast=True,
        fill_level_range=NumberRange(start_of_range=0.0, end_of_range=1.0),
    )

    # ---- System ----
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        actuators=[actuator],
        storage=storage,
    )
