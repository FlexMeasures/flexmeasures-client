import json

import aiohttp
from aiohttp import web

from flexmeasures_client.s2.cem import CEM


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    cem = CEM()

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            if msg.data == "close":
                # TODO: save cem state?
                await ws.close()
            else:
                response = cem.handle_message(json.loads(msg.json()))

                if response:
                    await ws.send_json(response)

        elif msg.type == aiohttp.WSMsgType.ERROR:
            print("ws connection closed with exception %s" % ws.exception())
            # TODO: save cem state?

    print("websocket connection closed")

    return ws


app = web.Application()
app.add_routes([web.get("/ws", websocket_handler)])
web.run_app(app)
