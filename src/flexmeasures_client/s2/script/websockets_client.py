import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import aiohttp
from s2python.common import (
    Commodity,
    CommodityQuantity,
    ControlType,
    Duration,
    EnergyManagementRole,
    Handshake,
    NumberRange,
    PowerRange,
    ReceptionStatus,
    ReceptionStatusValues,
    ResourceManagerDetails,
    Role,
    RoleType,
)
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCActuatorStatus,
    FRBCInstruction,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCStorageStatus,
    FRBCSystemDescription,
)

from flexmeasures_client.s2.utils import get_unique_id


async def apply_at_execution_time(
    ws, instruction: FRBCInstruction, previous_operation_mode_id=None
) -> None:
    """Enter the operation mode an instruction asks for, at the moment it asks for it.

    An instruction carries an execution_time,
    which is when the resource manager is to be in that mode, not when the instruction happens to arrive.
    A resource manager that answers on arrival reports a mode it is not in yet,
    and a customer energy manager recording that status has a measurement of a power the device is not drawing,
    timestamped now.
    """

    delay = (instruction.execution_time - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        print(f"WAITING: {delay:.0f}s until this instruction is due")
        await asyncio.sleep(delay)

    status = FRBCActuatorStatus(
        message_id=get_unique_id(),
        actuator_id=instruction.actuator_id,
        active_operation_mode_id=instruction.operation_mode,
        operation_mode_factor=instruction.operation_mode_factor,
        previous_operation_mode_id=previous_operation_mode_id,
        # The moment the transition happened, so that what is recorded about it is placed there,
        # rather than wherever the message is read.
        transition_timestamp=datetime.now(timezone.utc),
    )
    print("SENDING: FRBC.ActuatorStatus")
    await ws.send_str(status.model_dump_json())


async def answer_instructions(ws) -> None:
    """Acknowledge every instruction, and report the transition each one asks for when it is due."""

    active_operation_mode_id = None
    async for msg in ws:
        payload = msg.json()
        print("RECEIVED: ", payload)
        if payload.get("message_type") != "FRBC.Instruction":
            continue
        instruction = FRBCInstruction.model_validate(payload)
        await ws.send_str(
            ReceptionStatus(
                subject_message_id=instruction.message_id,
                status=ReceptionStatusValues.OK,
            ).model_dump_json()
        )
        # In a task of its own, so that an instruction due later does not hold up the ones behind it.
        asyncio.create_task(
            apply_at_execution_time(ws, instruction, active_operation_mode_id)
        )
        active_operation_mode_id = instruction.operation_mode


async def main_s2():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("http://localhost:8080/ws") as ws:
            message = Handshake(
                message_id=get_unique_id(),
                role=EnergyManagementRole.RM,
                supported_protocol_versions=["0.1.0"],
            )

            print("SENDING: HANDSHAKE")

            await ws.send_str(message.model_dump_json())

            response = await ws.receive()

            print("RECEIVING: ", response.json())

            # send resource manager details

            resource_manager_details_message = ResourceManagerDetails(
                message_id=get_unique_id(),
                resource_id=get_unique_id(),
                roles=[
                    Role(role=RoleType.ENERGY_STORAGE, commodity=Commodity.ELECTRICITY)
                ],
                instruction_processing_delay=Duration(1),
                available_control_types=[
                    ControlType.FILL_RATE_BASED_CONTROL,
                    ControlType.NO_SELECTION,
                ],
                provides_forecast=True,
                provides_power_measurement_types=[
                    CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
                ],
            ).model_dump_json()

            print("SENDING: ResourceManagerDetails")

            await ws.send_str(resource_manager_details_message)

            response = await ws.receive()

            print("RECEIVING: ", response.json())

            response = await ws.receive()

            print("RECEIVING: ", response.json())

            # Let the server activate the control type
            control_type = await ws.receive()

            control_type = control_type.json()

            print("RECEIVING: ", control_type)

            message = ReceptionStatus(
                subject_message_id=control_type["message_id"],
                status=ReceptionStatusValues.OK,
            )

            print("SENDING: ReceptionStatus")
            await ws.send_str(message.model_dump_json())

            electric_power = CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC

            # send storage status
            storage_status = FRBCStorageStatus(
                message_id=get_unique_id(), present_fill_level=0.4
            )

            print("SENDING: FRBC.StorageStatus")

            await ws.send_str(storage_status.model_dump_json())

            response = await ws.receive()

            print("RECEIVING: ", response.json())

            # send system description
            operation_mode_element = FRBCOperationModeElement(
                fill_level_range=NumberRange(start_of_range=0.0, end_of_range=0.5),
                fill_rate=NumberRange(start_of_range=-0.5, end_of_range=0.5),
                power_ranges=[
                    PowerRange(
                        start_of_range=-0.5,
                        end_of_range=0.5,
                        commodity_quantity=electric_power,
                    )
                ],
            )

            operation_mode = FRBCOperationMode(
                id=get_unique_id(),
                elements=[operation_mode_element],
                abnormal_condition_only=False,
                diagnostic_label="full operating range of a perfectly efficiency battery",
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
                fill_level_range=NumberRange(start_of_range=0.05, end_of_range=0.45),
            )

            valid_from = datetime(2026, 1, 15, 20, tzinfo=ZoneInfo("Europe/Amsterdam"))

            system_description_message = FRBCSystemDescription(
                message_id=get_unique_id(),
                valid_from=valid_from,
                actuators=[actuator],
                storage=storage,
            ).model_dump_json()

            print("SENDING: FRBC.SystemDescription")

            await ws.send_str(system_description_message)

            await answer_instructions(ws)


async def main_websocket():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("http://localhost:8080/ws") as ws:
            try:
                async for msg in ws:
                    print("RESPONSE: ", msg.data)
                    await asyncio.sleep(1)
                    await ws.send_json(msg.data)
            except KeyboardInterrupt:
                await ws.close()


if __name__ == "__main__":
    asyncio.run(main_s2())
