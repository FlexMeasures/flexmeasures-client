import asyncio
import json

import aiohttp
from aiohttp import web
from s2python.common import ControlType

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple


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

    site_name = "My CEM"
    fm_client = FlexMeasuresClient(
        "toy-password", "toy-user@flexmeasures.io", host="server:5000"
    )

    price_sensor, power_sensor, soc_sensor, rm_discharge_sensor = await configure_site(
        site_name, fm_client
    )

    cem = CEM(sensor_id=power_sensor["id"], fm_client=fm_client)
    frbc = FRBCSimple(
        power_sensor_id=power_sensor["id"],
        price_sensor_id=price_sensor["id"],
        soc_sensor_id=soc_sensor["id"],
        rm_discharge_sensor_id=rm_discharge_sensor["id"],
    )
    cem.register_control_type(frbc)

    # create "parallel" tasks for the message producer and consumer
    await asyncio.gather(
        websocket_consumer(ws, cem),
        websocket_producer(ws, cem),
        rm_details_watchdog(ws, cem),
    )

    return ws


async def configure_site(
    site_name: str, fm_client: FlexMeasuresClient
) -> tuple[dict, dict, dict, dict]:
    account = await fm_client.get_account()
    assets = await fm_client.get_assets(parse_json_fields=True)

    site_asset = None
    for asset in assets:
        if asset["name"] == site_name:
            site_asset = asset
            break

    site_asset_specs = dict(
        latitude=0,
        longitude=0,
        generic_asset_type_id=6,  # Building asset type
        flex_model={
            "power-capacity": f"{3 * 25 * 230} VA",
        },
    )

    if not site_asset:
        site_asset = await fm_client.add_asset(
            name=site_name, account_id=account["id"], **site_asset_specs
        )
    # Update site asset with the latest specs
    await fm_client.update_asset(site_asset["id"], site_asset_specs)

    sensors = site_asset.get("sensors", [])
    price_sensor = None
    power_sensor = None
    soc_sensor = None
    rm_discharge_sensor = None
    for sensor in sensors:
        if sensor["name"] == "price":
            price_sensor = sensor
        elif sensor["name"] == "power":
            power_sensor = sensor
        elif sensor["name"] == "state of charge":
            soc_sensor = sensor
        elif sensor["name"] == "RM discharge":
            rm_discharge_sensor = sensor

    if price_sensor is None:
        price_sensor = await fm_client.add_sensor(
            name="price",
            event_resolution="PT15M",
            unit="EUR/kWh",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )
    await fm_client.post_sensor_data(
        sensor_id=price_sensor["id"],
        start="2026-01-15T00:00+01",  # 2026-01-01T00:00+01
        duration="P3D",  # P1M
        values=[0.3],
        unit="EUR/kWh",
    )
    if power_sensor is None:
        power_sensor = await fm_client.add_sensor(
            name="power",
            event_resolution="PT15M",
            unit="kW",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )
    if soc_sensor is None:
        soc_sensor = await fm_client.add_sensor(
            name="state of charge",
            event_resolution="PT0M",
            unit="kWh",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )
    if rm_discharge_sensor is None:
        rm_discharge_sensor = await fm_client.add_sensor(
            name="RM discharge",
            event_resolution="PT15M",
            unit="dimensionless",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )
    return price_sensor, power_sensor, soc_sensor, rm_discharge_sensor


app = web.Application()
app.add_routes([web.get("/ws", websocket_handler)])
web.run_app(app)
