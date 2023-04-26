import asyncio

import aiohttp
from python_s2_protocol.common.messages import Handshake
from python_s2_protocol.common.schemas import EnergyManagementRole

from flexmeasures_client.s2.utils import get_unique_id


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("http://localhost:8080/ws") as ws:
            message = Handshake(
                message_id=get_unique_id(),
                role=EnergyManagementRole.RM,
                supported_protocol_versions=["0.1.0"],
            )

            await ws.send_json(message.json())

            response = await ws.receive()

            message = Handshake(
                message_id=get_unique_id(),
                role=EnergyManagementRole.RM,
                supported_protocol_versions=["0.1.0"],
            ).json()

            print("REQUEST: ", message)

            print("Sending message...")
            await ws.send_json(message)
            print("Message sent.")

            response = await ws.receive()

            print("RESPONSE: ", response.data)

            await ws.close()


asyncio.run(main())
