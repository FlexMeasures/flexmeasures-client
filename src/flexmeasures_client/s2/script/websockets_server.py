import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web
from s2python.common import ControlType

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple

log_level = os.getenv("LOGGING_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=log_level,
    format="[CEM][%(asctime)s] %(levelname)s:  %(name)s | %(message)s",
)
LOGGER = logging.getLogger(__name__)

SITE_NAME = "My CEM"
SITE_TIMEZONE = "Europe/Amsterdam"
# Enough days of demo prices to cover what the CEM plans for: a schedule starts a day after the
# system description that asked for it and runs for twelve hours, so two days would just do.
SEED_PRICE_DAYS = 3

# Keys under which the configured site is kept on the aiohttp application.
FM_CLIENT = web.AppKey("fm_client", FlexMeasuresClient)
SENSORS = web.AppKey("sensors", tuple)


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
        cem._logger.debug("waiting for the activation of the control type...")
        await asyncio.sleep(1)

    cem._logger.debug(f"CONTROL TYPE: {cem._control_type}")

    # after this, schedule will be triggered on reception of a new system description


async def websocket_producer(ws, cem: CEM):
    cem._logger.debug("start websocket message producer")
    cem._logger.debug(f"IS CLOSED? {cem.is_closed()}")
    while not cem.is_closed():
        message = await cem.get_message()
        cem._logger.debug("sending message")
        try:
            await ws.send_json(message)
        except aiohttp.ClientConnectionResetError:
            break
    cem._logger.debug("cem closed")


async def websocket_consumer(ws, cem: CEM):
    async for msg in ws:
        cem._logger.info(f"RECEIVED: {msg}")
        if msg.type == aiohttp.WSMsgType.TEXT:
            if msg.data == "close":
                # TODO: save cem state?
                cem._logger.debug("close...")
                await cem.close()
                await ws.close()
            else:
                try:
                    await cem.handle_message(json.loads(msg.data))
                except (json.JSONDecodeError, KeyError):
                    cem._logger.exception(f"handle_message failed for {msg.data!r}")

        elif msg.type == aiohttp.WSMsgType.ERROR:
            cem._logger.debug("close...")
            await cem.close()
            cem._logger.error(f"ws connection closed with exception {ws.exception()}")
            # TODO: save cem state?

    cem._logger.debug("websocket connection closed")


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # The site is configured once, before this server accepts connections, and not here.
    # Configuring it here meant the handshake was answered only after a round of FlexMeasures
    # calls, which takes about ten seconds against a local server, while s2-python's
    # reception-status timeout is five: a resource manager gave up before the CEM had read its
    # first message.
    fm_client = request.app[FM_CLIENT]
    price_sensor, power_sensor, soc_sensor, rm_discharge_sensor = request.app[SENSORS]

    cem = CEM(
        sensor_id=power_sensor["id"],
        fm_client=fm_client,
        logger=LOGGER,
    )
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
    """Create or update the site asset and its sensors, and seed it with demo prices."""
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
    await fm_client.update_asset(
        site_asset["id"], site_asset_specs, parse_json_fields=True
    )

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
            timezone=SITE_TIMEZONE,
        )
    # Demo prices, so that a freshly configured site has something to schedule against.
    # One day's hourly shape, repeated over SEED_PRICE_DAYS from this morning, because the CEM
    # plans a day and a half ahead of the moment a resource manager sends its system description:
    # a fixed date here goes stale the day after it is written, and the scheduler then fails the
    # job with UnknownPricesException rather than returning a schedule.
    one_day = [
        0.10, 0.11, 0.12, 0.15, 0.18, 0.17, 0.11, 0.09,
        0.10, 0.09, 0.09, 0.10, 0.08, 0.05, 0.04, 0.04,
        0.06, 0.08, 0.12, 0.13, 0.14, 0.13, 0.10, 0.07,
    ]  # fmt: skip
    today = datetime.now(ZoneInfo(SITE_TIMEZONE)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    await fm_client.post_sensor_data(
        sensor_id=price_sensor["id"],
        start=today,
        duration=timedelta(days=SEED_PRICE_DAYS),
        values=one_day * SEED_PRICE_DAYS,
        unit="EUR/kWh",
    )
    if power_sensor is None:
        power_sensor = await fm_client.add_sensor(
            name="power",
            event_resolution="PT15M",
            unit="kW",
            generic_asset_id=site_asset["id"],
            timezone=SITE_TIMEZONE,
            # S2 states a power as positive when it is consumed,
            # and this sensor holds both an actuator's power and the schedule planned for it.
            # Left unsaid, FlexMeasures reads a power sensor the other way round,
            # and flips a schedule's sign on the way in, which puts the two in opposite directions.
            attributes={"consumption_is_positive": True},
        )
    if soc_sensor is None:
        soc_sensor = await fm_client.add_sensor(
            name="state of charge",
            event_resolution="PT0M",
            unit="kWh",
            generic_asset_id=site_asset["id"],
            timezone=SITE_TIMEZONE,
        )
    if rm_discharge_sensor is None:
        rm_discharge_sensor = await fm_client.add_sensor(
            name="RM discharge",
            event_resolution="PT15M",
            unit="dimensionless",
            generic_asset_id=site_asset["id"],
            timezone=SITE_TIMEZONE,
        )
    return price_sensor, power_sensor, soc_sensor, rm_discharge_sensor


async def configure_site_once(app: web.Application) -> None:
    """Set the site up before the first resource manager connects.

    Everything here talks to FlexMeasures, and none of it depends on which resource manager
    connects, so it happens once at startup rather than inside the websocket handler, where it
    delayed the handshake past the timeout a resource manager waits for it.
    """
    base_url = os.getenv("FLEXMEASURES_BASE_URL", "http://localhost:5000")
    parsed = urlparse(base_url)
    fm_client = FlexMeasuresClient(
        password=os.getenv("FLEXMEASURES_PASSWORD", "toy-password"),
        email=os.getenv("FLEXMEASURES_USER", "toy-user@flexmeasures.io"),
        host=parsed.netloc,
        ssl=parsed.scheme == "https",
    )
    app[FM_CLIENT] = fm_client
    app[SENSORS] = await configure_site(SITE_NAME, fm_client)
    LOGGER.info("Site '%s' is configured; ready for resource managers.", SITE_NAME)


async def close_fm_client(app: web.Application) -> None:
    await app[FM_CLIENT].close()


app = web.Application()
app.on_startup.append(configure_site_once)
app.on_cleanup.append(close_fm_client)
app.add_routes([web.get("/ws", websocket_handler)])

if __name__ == "__main__":
    web.run_app(app)
