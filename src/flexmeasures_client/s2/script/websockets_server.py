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
from flexmeasures_client.s2.measurements import MeasurementRecorder
from flexmeasures_client.s2.site import (
    FRBC_SITE_SENSORS,
    FRBCSiteConfigurator,
    ensure_site,
)

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

#: Whether resource managers may send messages wrapped with a simulated time, as a co-simulation does.
ACCEPT_SIMULATION_METADATA = os.getenv("S2_ACCEPT_SIMULATION_METADATA", "").lower() in (
    "1",
    "true",
    "yes",
)

#: Each sensor the site needs, which is the library's table of what an FRBC site records.
SITE_SENSORS = FRBC_SITE_SENSORS

#: What every site asset is made with: a building, whose connection takes three phases of 25 A at 230 V.
SITE_ASSET_SPECS: dict[str, Any] = dict(
    generic_asset_type_id=6,
    flex_model={"power-capacity": f"{3 * 25 * 230} VA"},
)

#: Whether each resource manager gets a site of its own, named after it, as in a co-simulation of a community,
#: rather than all of them sharing the one site this server sets up at startup.
SITE_PER_RESOURCE_MANAGER = os.getenv("S2_SITE_PER_RESOURCE_MANAGER", "").lower() in (
    "1",
    "true",
    "yes",
)

# Keys under which the configured site is kept on the aiohttp application.
FM_CLIENT = web.AppKey("fm_client", FlexMeasuresClient)
SENSORS = web.AppKey("sensors", dict)
CONFIGURATOR = web.AppKey("configurator", FRBCSiteConfigurator)
RECORDER = web.AppKey("recorder", MeasurementRecorder)


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
    if SITE_PER_RESOURCE_MANAGER:
        await serve_resource_manager(
            ws,
            request.app[FM_CLIENT],
            configurator=request.app[CONFIGURATOR],
            recorder=request.app[RECORDER],
        )
    else:
        await serve_resource_manager(
            ws,
            request.app[FM_CLIENT],
            request.app[SENSORS],
            recorder=request.app[RECORDER],
        )
    return ws


async def serve_resource_manager(
    ws,
    fm_client: FlexMeasuresClient,
    sensors: dict[str, dict] | None = None,
    configurator: FRBCSiteConfigurator | None = None,
    recorder: MeasurementRecorder | None = None,
) -> None:
    """Act as the CEM for one resource manager connected over ws, until it disconnects.

    On the site whose sensors are given, or, with a configurator, on a site of the resource manager's own,
    which is set up once its details arrive.
    """
    cem = CEM(
        fm_client=fm_client,
        logger=LOGGER,
        site_configurator=configurator,
        # The server's, which outlives a connection, so a resource manager that reconnects is not recorded twice.
        measurement_recorder=recorder,
        # Where what the resource manager measures is recorded, on the fixed site; a configured site wires its own.
        power_sensor_id=(
            {"ELECTRIC.POWER.L1": sensors["measured-power"]["id"]}
            if sensors is not None and "measured-power" in sensors
            else None
        ),
        # A co-simulation's resource manager sends its messages wrapped with the simulated time, which this sets the clock to.
        accept_simulation_metadata=ACCEPT_SIMULATION_METADATA,
    )
    if sensors is not None:
        cem.register_control_type(fixed_site_handler(sensors))
    await serve(ws, cem)


def fixed_site_handler(sensors: dict[str, dict]) -> FRBCSimple:
    """The handler for a resource manager on the one site this server sets up at startup."""
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
    return frbc


async def serve(ws, cem: CEM) -> None:
    """Serve one resource manager with this CEM, for as long as its connection lasts."""
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
    """Create or update the site asset, its sensors and its graphs, and seed it with demo prices."""
    _, sensors = await ensure_site(
        fm_client,
        site_name,
        asset_specs=SITE_ASSET_SPECS,
        sensors=SITE_SENSORS,
        timezone=SITE_TIMEZONE,
        # The demo's own graphs, on every start, so changes made to them in the UI do not outlive a restart.
        reset_graphs=True,
    )
    await seed_prices(fm_client, sensors["price"]["id"])
    return sensors


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
    """Seed the prices again every SEED_PRICES_EVERY for as long as the server runs, so a demo never runs out of them.

    Not where each resource manager has a site of its own, whose prices are whoever runs them to post.
    """
    if SITE_PER_RESOURCE_MANAGER:
        yield
        return

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
    app[RECORDER] = MeasurementRecorder(fm_client, logger=LOGGER)
    if SITE_PER_RESOURCE_MANAGER:
        # One configurator for the server's lifetime, so a resource manager that reconnects finds its site again by id.
        app[CONFIGURATOR] = FRBCSiteConfigurator(
            fm_client,
            asset_type_id=SITE_ASSET_SPECS["generic_asset_type_id"],
            asset_specs={"flex_model": SITE_ASSET_SPECS["flex_model"]},
            timezone=SITE_TIMEZONE,
            # The co-simulation's resource managers are heat pumps, which cannot feed power back:
            # FlexMeasures then keeps their production at zero even where constraints are relaxed.
            power_sensor_attributes={"is_strictly_non_positive": True},
            # A community's capacity is a target set by its members' steering, breached at a price, as #188 asked for.
            handler_options={"relax_site_capacity": True},
            logger=LOGGER,
        )
        LOGGER.info("Each resource manager gets a site of its own; ready for them.")
        return
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
