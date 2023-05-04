import asyncio

import aiohttp
from aiohttp import web

from flexmeasures_client.s2.cem import CEM

# import json


cem = CEM(sensor_id=1, fm_client=None)


async def producing2(cem):
    i = 0
    while True:
        await cem._sending_queue.put({"hola": 2 * i})
        await asyncio.sleep(1)
        i += 1


async def producing1(cem):
    i = 0
    while True:
        await cem._sending_queue.put({"hola": 2 * i + 1})
        await asyncio.sleep(1)
        i += 1


async def websocket_producer(ws):
    while True:
        message = await cem.get_message()
        await ws.send_json(message)


async def websocket_consumer(ws):
    async for msg in ws:
        print(msg.data)
        if msg.type == aiohttp.WSMsgType.TEXT:
            if msg.data == "close":
                # TODO: save cem state?
                await ws.close()
            else:
                # response = cem.handle_message(json.loads(msg.json()))
                print(msg.data)

                # if response:
                #    await ws.send_json(response)

        elif msg.type == aiohttp.WSMsgType.ERROR:
            print("ws connection closed with exception %s" % ws.exception())
            # TODO: save cem state?

    print("websocket connection closed")


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    await asyncio.gather(
        websocket_consumer(ws), websocket_producer(ws), producing1(cem), producing2(cem)
    )

    return ws


app = web.Application()
app.add_routes([web.get("/ws", websocket_handler)])
web.run_app(app)
