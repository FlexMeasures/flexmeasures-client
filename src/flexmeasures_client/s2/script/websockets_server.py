import asyncio
import json

import aiohttp
from aiohttp import web

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.python_s2_protocol.common.schemas import ControlType


async def rm_details_watchdog(ws, cem: CEM):
    """This function will define a service in Home Assistant, or could
     be a HTTP endpoint to trigger schedules.

    :param ws: websocket object
    :param cem: Customer Energy Manager petitions handler
    """

    # wait to get resource manager details
    while cem._control_type is None:
        await asyncio.sleep(1)

    await cem.activate_control_type(control_type=ControlType.FILL_RATE_BASED_CONTROL)

    # check/wait that the control type is set properly
    while cem._control_type != ControlType.FILL_RATE_BASED_CONTROL:
        print("waiting for the activation of the control type...")
        await asyncio.sleep(1)

    print("CONTROL TYPE: ", cem._control_type)

    # after this, schedule will be triggered on reception of a new system description


async def websocket_producer(ws, cem: CEM):
    print("start websocket message producer")
    print("IS CLOSED? ", cem.is_closed())
    while not cem.is_closed():
        message = await cem.get_message()
        print("sending message")
        await ws.send_json(message)
    print("cem closed")


async def websocket_consumer(ws, cem: CEM):
    async for msg in ws:
        print("RECEIVED: ", json.loads(msg.json()))
        if msg.type == aiohttp.WSMsgType.TEXT:
            if msg.data == "close":
                # TODO: save cem state?
                print("close...")
                cem.close()
                await ws.close()
            else:
                await cem.handle_message(json.loads(msg.json()))

        elif msg.type == aiohttp.WSMsgType.ERROR:
            print("close...")
            cem.close()
            print("ws connection closed with exception %s" % ws.exception())
            # TODO: save cem state?

    print("websocket connection closed")


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    fm_client = FlexMeasuresClient("toy-password", "toy-user@flexmeasures.io")

    cem = CEM(sensor_id=1, fm_client=fm_client)
    frbc = FRBCSimple(power_sensor_id=1, price_sensor_id=2)
    cem.register_control_type(frbc)

    # create "parallel" tasks for the message producer and consumer
    await asyncio.gather(
        websocket_consumer(ws, cem),
        websocket_producer(ws, cem),
        rm_details_watchdog(ws, cem),
    )

    return ws


app = web.Application()
app.add_routes([web.get("/ws", websocket_handler)])
web.run_app(app)
