import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flexmeasures_client.client import FlexMeasuresClient

log_level = os.getenv("LOGGING_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=log_level,
    format="[CEM][%(asctime)s] %(levelname)s:  %(name)s | %(message)s",
)
LOGGER = logging.getLogger(__name__)


async def configure_site(
    site_name: str, fm_client: FlexMeasuresClient
) -> tuple[dict, dict, dict, dict, dict, dict, dict, dict, dict, dict]:
    account = await fm_client.get_account()
    assets = await fm_client.get_assets(parse_json_fields=True)

    site_asset: dict | None = None
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
    production_price_sensor = None
    power_sensor = None
    soc_sensor = None
    rm_discharge_sensor = None
    soc_minima_sensor = None
    soc_maxima_sensor = None
    usage_forecast_sensor = None
    leakage_behaviour_sensor = None
    charging_efficiency_sensor = None
    for sensor in sensors:
        if sensor["name"] == "price":
            price_sensor = sensor
        if sensor["name"] == "production price":
            production_price_sensor = sensor
        elif sensor["name"] == "power":
            power_sensor = sensor
        elif sensor["name"] == "state of charge":
            soc_sensor = sensor
        elif sensor["name"] == "RM discharge":
            rm_discharge_sensor = sensor
        elif sensor["name"] == "soc-minima":
            soc_minima_sensor = sensor
        elif sensor["name"] == "soc-maxima":
            soc_maxima_sensor = sensor
        elif sensor["name"] == "usage-forecast":
            usage_forecast_sensor = sensor
        elif sensor["name"] == "leakage-behaviour":
            leakage_behaviour_sensor = sensor
        elif sensor["name"] == "charging-efficiency":
            charging_efficiency_sensor = sensor

    if price_sensor is None:
        price_sensor = await fm_client.add_sensor(
            name="price",
            event_resolution="PT15M",
            unit="EUR/kWh",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )
    if production_price_sensor is None:
        production_price_sensor = await fm_client.add_sensor(
            name="production price",
            event_resolution="PT15M",
            unit="EUR/kWh",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )

    # Continue immediately without awaiting
    LOGGER.debug("Posting 3 days of prices in a background task..")
    start_of_today = (
        datetime.now(ZoneInfo("Europe/Amsterdam"))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )
    asyncio.create_task(
        fm_client.post_sensor_data(
            sensor_id=price_sensor["id"],
            start=start_of_today,
            prior="2026-01-01T00:00+01",  # 2026-01-01T00:00+01
            duration="P3D",  # P1M
            values=[0.3],
            unit="EUR/kWh",
        )
    )
    asyncio.create_task(
        fm_client.post_sensor_data(
            sensor_id=production_price_sensor["id"],
            start=start_of_today,
            prior="2026-01-01T00:00+01",  # 2026-01-01T00:00+01
            duration="P3D",  # P1M
            values=[0.2],
            unit="EUR/kWh",
        )
    )
    if power_sensor is None:
        power_sensor = await fm_client.add_sensor(
            name="power",
            event_resolution="PT15M",
            unit="kW",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
            attributes={"consumption_is_positive": True},
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
    if soc_minima_sensor is None:
        soc_minima_sensor = await fm_client.add_sensor(
            name="soc-minima",
            event_resolution="PT15M",
            unit="kWh",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )
    if soc_maxima_sensor is None:
        soc_maxima_sensor = await fm_client.add_sensor(
            name="soc-maxima",
            event_resolution="PT15M",
            unit="kWh",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )
    if usage_forecast_sensor is None:
        usage_forecast_sensor = await fm_client.add_sensor(
            name="usage-forecast",
            event_resolution="PT15M",
            unit="kW",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )
    if leakage_behaviour_sensor is None:
        leakage_behaviour_sensor = await fm_client.add_sensor(
            name="leakage-behaviour",
            event_resolution="PT15M",
            unit="%",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )
    if charging_efficiency_sensor is None:
        charging_efficiency_sensor = await fm_client.add_sensor(
            name="charging-efficiency",
            event_resolution="PT15M",
            unit="%",
            generic_asset_id=site_asset["id"],
            timezone="Europe/Amsterdam",
        )
    sensors_to_show = [
        {
            "title": "State of charge",
            "sensors": [
                soc_minima_sensor["id"],
                soc_maxima_sensor["id"],
                soc_sensor["id"],
            ],
        },
        {
            "title": "Prices",
            "sensors": [price_sensor["id"], production_price_sensor["id"]],
        },
        {
            "title": "Power",
            "sensors": [power_sensor["id"]],
        },
    ]
    await fm_client.update_asset(
        asset_id=site_asset["id"],
        updates=dict(sensors_to_show=sensors_to_show),
    )
    return (
        price_sensor,
        production_price_sensor,
        power_sensor,
        soc_sensor,
        rm_discharge_sensor,
        soc_minima_sensor,
        soc_maxima_sensor,
        usage_forecast_sensor,
        leakage_behaviour_sensor,
        charging_efficiency_sensor,
    )
