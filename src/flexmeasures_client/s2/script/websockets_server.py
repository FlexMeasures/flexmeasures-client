import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web
from s2python.common import ControlType

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.exceptions import (
    ContentTypeError,
    JobFailedError,
    WrongAPIVersionError,
)
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
# Enough days of demo prices to cover what the CEM plans for, which is twelve hours from now,
# with the seeding repeated every SEED_PRICES_EVERY so that a demo left running never runs out.
SEED_PRICE_DAYS = 3
SEED_PRICES_EVERY = timedelta(hours=12)

#: Each sensor the site needs: its unit, its resolution, and any attributes.
#: The units are the ones the CEM records in: fill levels in kWh, rates of fill level per second, as S2 states them.
SITE_SENSORS: dict[str, tuple[str, str, dict]] = {
    "price": ("EUR/kWh", "PT15M", {}),
    # S2 states a power as positive when it is consumed,
    # and this sensor holds both an actuator's power and the schedule planned for it.
    # Left unsaid, FlexMeasures reads a power sensor the other way round,
    # and flips a schedule's sign on the way in, which puts the two in opposite directions.
    "power": ("kW", "PT15M", {"consumption_is_positive": True}),
    "state of charge": ("kWh", "PT0M", {}),
    "RM discharge": ("dimensionless", "PT15M", {}),
    "fill rate": ("kWh/s", "PT15M", {}),
    "soc minima": ("kWh", "PT15M", {}),
    "soc maxima": ("kWh", "PT15M", {}),
    "usage forecast": ("kWh/s", "PT15M", {}),
    "storage efficiency": ("%", "PT15M", {}),
    "charging efficiency": ("dimensionless", "PT15M", {}),
}

# Keys under which the configured site is kept on the aiohttp application.
FM_CLIENT = web.AppKey("fm_client", FlexMeasuresClient)
SENSORS = web.AppKey("sensors", dict)


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

    # From here on, a schedule is asked for on each storage status,
    # and on a system description that arrives after one.


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
    await serve_resource_manager(ws, request.app[FM_CLIENT], request.app[SENSORS])
    return ws


async def serve_resource_manager(
    ws, fm_client: FlexMeasuresClient, sensors: dict[str, dict]
) -> None:
    """Act as the CEM for one resource manager connected over ws, until it disconnects."""
    cem = CEM(
        sensor_id=sensors["power"]["id"],
        fm_client=fm_client,
        logger=LOGGER,
    )
    frbc = FRBCSimple(
        power_sensor_id=sensors["power"]["id"],
        price_sensor_id=sensors["price"]["id"],
        soc_sensor_id=sensors["state of charge"]["id"],
        rm_discharge_sensor_id=sensors["RM discharge"]["id"],
        # Where the resource manager's declarations are recorded, so that each can be seen next to the schedule planned against it.
        fill_rate_sensor_id=sensors["fill rate"]["id"],
        soc_minima_sensor_id=sensors["soc minima"]["id"],
        soc_maxima_sensor_id=sensors["soc maxima"]["id"],
        usage_forecast_sensor_id=sensors["usage forecast"]["id"],
        leakage_behaviour_sensor_id=sensors["storage efficiency"]["id"],
        charging_efficiency_sensor_id=sensors["charging efficiency"]["id"],
        timezone=SITE_TIMEZONE,
        # Plan from now, rather than from a day after the description, so that a demo shows its instructions being followed.
        valid_from_shift=timedelta(0),
    )
    # A newer plan revokes what is left of the one before, so the resource manager does not follow both.
    frbc.send_revocations = True
    cem.register_control_type(frbc)

    # The session lasts as long as the resource manager's messages do.
    # When they end, the CEM is closed, which stops what its handlers still have in flight,
    # and the producer and the watchdog stop with it: left running, they outlived the connection,
    # and a resource manager that reconnected was served by two sessions planning for it at once.
    consumer = asyncio.create_task(websocket_consumer(ws, cem))
    side_tasks = [
        asyncio.create_task(websocket_producer(ws, cem)),
        asyncio.create_task(rm_details_watchdog(ws, cem)),
    ]
    try:
        await consumer
    finally:
        await cem.close()
        for task in side_tasks:
            task.cancel()
        await asyncio.gather(*side_tasks, return_exceptions=True)


async def configure_site(
    site_name: str, fm_client: FlexMeasuresClient
) -> dict[str, dict]:
    """Create or update the site asset and its sensors, and seed it with demo prices."""
    account = await fm_client.get_account()
    if account is None:
        raise ValueError(
            "The demo user belongs to no organisation, so there is nowhere to create the site."
        )
    assets = await fm_client.get_assets(parse_json_fields=True)

    site_asset = None
    for asset in assets:
        if asset["name"] == site_name:
            site_asset = asset
            break

    site_asset_specs: dict[str, Any] = dict(
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

    existing = {sensor["name"]: sensor for sensor in site_asset.get("sensors", [])}
    sensors: dict[str, dict] = {}
    for name, (unit, resolution, attributes) in SITE_SENSORS.items():
        sensors[name] = existing.get(name) or await fm_client.add_sensor(
            name=name,
            event_resolution=resolution,
            unit=unit,
            generic_asset_id=site_asset["id"],
            timezone=SITE_TIMEZONE,
            **({"attributes": attributes} if attributes else {}),
        )

    # The site's graphs page shows what the plan is about, not only its price and power:
    # the buffer with the comfort band around it, what is drawn from it, and what the heat pump puts in.
    await fm_client.update_asset(
        site_asset["id"],
        {"sensors_to_show": site_graphs(sensors)},
        parse_json_fields=True,
    )

    await seed_prices(fm_client, sensors["price"]["id"])
    return sensors


def site_graphs(sensors: dict[str, dict]) -> list[dict]:
    """The site's graphs, top to bottom, as FlexMeasures takes an asset's sensors_to_show.

    In the form with a title and a list of sensors per graph, which every FlexMeasures version reads,
    rather than the newer one with plots, which needs v0.32 or later and draws the same graphs.
    The server sets them on every start, so changes made to them in the UI do not outlive a restart.
    """

    def ids(*names: str) -> list[int]:
        return [sensors[name]["id"] for name in names]

    return [
        {"title": "Price", "sensors": ids("price")},
        {"title": "Heat pump: planned and measured power", "sensors": ids("power")},
        {
            "title": "Buffer: fill level and comfort band",
            "sensors": ids("state of charge", "soc minima", "soc maxima"),
        },
        {
            "title": "Buffer: filled by the heat pump, drawn by hot-water use",
            "sensors": ids("fill rate", "usage forecast"),
        },
    ]


async def seed_prices(fm_client: FlexMeasuresClient, price_sensor_id: int) -> None:
    """Demo prices, so that a site has something to schedule against.

    One day's hourly shape, repeated over SEED_PRICE_DAYS from this morning, because the CEM
    plans ahead of the moment a resource manager reports its storage:
    a fixed date here goes stale the day after it is written, and the scheduler then fails the
    job with UnknownPricesException rather than returning a schedule.
    """
    one_day = [
        0.10, 0.11, 0.12, 0.15, 0.18, 0.17, 0.11, 0.09,
        0.10, 0.09, 0.09, 0.10, 0.08, 0.05, 0.04, 0.04,
        0.06, 0.08, 0.12, 0.13, 0.14, 0.13, 0.10, 0.07,
    ]  # fmt: skip
    today = datetime.now(ZoneInfo(SITE_TIMEZONE)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    await fm_client.post_sensor_data(
        sensor_id=price_sensor_id,
        start=today,
        duration=timedelta(days=SEED_PRICE_DAYS),
        values=one_day * SEED_PRICE_DAYS,
        unit="EUR/kWh",
    )


async def keep_prices_seeded(app: web.Application):
    """Seed the prices again every SEED_PRICES_EVERY for as long as the server runs, so a demo never runs out of them."""

    async def reseed():
        while True:
            await asyncio.sleep(SEED_PRICES_EVERY.total_seconds())
            try:
                await seed_prices(app[FM_CLIENT], app[SENSORS]["price"]["id"])
            except (
                ConnectionError,
                ValueError,
                ContentTypeError,
                JobFailedError,
                WrongAPIVersionError,
            ):
                LOGGER.exception(
                    "Could not seed the demo prices again; trying at the next interval."
                )

    task = asyncio.create_task(reseed())
    yield
    task.cancel()


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
app.cleanup_ctx.append(keep_prices_seeded)
app.on_cleanup.append(close_fm_client)
app.add_routes([web.get("/ws", websocket_handler)])

if __name__ == "__main__":
    web.run_app(app)
