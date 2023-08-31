import asyncio
from datetime import datetime

import aiohttp
import pytz

from flexmeasures_client.s2.python_s2_protocol.common.messages import (
    Handshake,
    ReceptionStatus,
    ReceptionStatusValues,
    ResourceManagerDetails,
)
from flexmeasures_client.s2.python_s2_protocol.common.schemas import (
    Commodity,
    CommodityQuantity,
    ControlType,
    Duration,
    EnergyManagementRole,
    NumberRange,
    PowerRange,
    Role,
    RoleType,
)
from flexmeasures_client.s2.python_s2_protocol.FRBC.messages import (
    FRBCStorageStatus,
    FRBCSystemDescription,
)
from flexmeasures_client.s2.python_s2_protocol.FRBC.schemas import (
    FRBCActuatorDescription,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
)
from flexmeasures_client.s2.utils import get_unique_id


async def main_s2():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("http://localhost:8080/ws") as ws:
            message = Handshake(
                message_id=get_unique_id(),
                role=EnergyManagementRole.RM,
                supported_protocol_versions=["0.1.0"],
            )

            print("SENDING: HANDSHAKE")

            await ws.send_json(message.json())

            response = await ws.receive()

            print("RECEIVING: ", response.json())

            # send resource manager details

            resource_manager_details_message = ResourceManagerDetails(
                message_id=get_unique_id(),
                resource_id=get_unique_id(),
                roles=[
                    Role(role=RoleType.ENERGY_STORAGE, commodity=Commodity.ELECTRICITY)
                ],
                instruction_processing_delay=Duration(__root__=1.0),
                available_control_types=[
                    ControlType.FILL_RATE_BASED_CONTROL,
                    ControlType.NO_SELECTION,
                ],
                provides_forecast=True,
                provides_power_measurement_types=[
                    CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
                ],
            ).json()

            print("SENDING: ResourceManagerDetails")

            await ws.send_json(resource_manager_details_message)

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
            await ws.send_json(message.json())

            electric_power = CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC

            # send storage status
            storage_status = FRBCStorageStatus(
                message_id=get_unique_id(), present_fill_level=0.4
            )

            print("SENDING: FRBC.StorageStatus")

            await ws.send_json(storage_status.json())

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

            valid_from = pytz.timezone("Europe/Amsterdam").localize(
                datetime(2023, 5, 14)
            )

            system_description_message = FRBCSystemDescription(
                message_id=get_unique_id(),
                valid_from=valid_from,
                actuators=[actuator],
                storage=storage,
            ).json()

            print("SENDING: FRBC.SystemDescription")

            await ws.send_json(system_description_message)

            msg = await ws.receive()
            print("RECEIVED: ", msg.json())

            for i in range(6):
                msg = await ws.receive()
                print("RECEIVED: ", msg.json())

            await ws.close()


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


asyncio.run(main_s2())
