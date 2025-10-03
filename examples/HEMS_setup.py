"""
Complete FlexMeasures HEMS (Home Energy Management System) setup script.
Creates a comprehensive structure with building, PV, battery, weather station assets
and all required sensors with proper flex-context configuration.
"""

import asyncio
import json
import subprocess
from datetime import timedelta

import pandas as pd

from flexmeasures_client import FlexMeasuresClient

# Connection details - UPDATE THESE FOR YOUR SETUP
usr = "admin@admin.com"  # Admin user email
pwd = "admin"  # Admin password
host = "127.0.0.1:5000"  # FlexMeasures host

# Asset and sensor names
building_name = "My Home"
pv_name = "Rooftop PV"
battery_name = "Home Battery"
evse1_name = "EV Connector 1"
evse2_name = "EV Connector 2"
weather_station_name = "Local Weather Station"
price_market_name = "Energy Market"

# Location coordinates (Amsterdam as example)
latitude = 52.3676
longitude = 4.9041

# Data configuration
TUTORIAL_START_DATE = "2026-01-01T00:00:00+01:00"
FORECASTING_START = "2026-01-15T00:00:00+01:00"
SCHEDULING_START = "2026-01-15T00:00:00+01:00"
SCHEDULING_END = "2026-01-16T00:00:00+01:00"
SIMULATION_STEP_HOURS = 4
FORECAST_HORIZON_HOURS = 24

# Configuration constants
EV_CONFIG = {
    "default_capacity_kwh": 60.0,
    "default_power_capacity_kw": 11.0,
    "min_soc_percent": 0.20,  # 20% minimum SoC
    "roundtrip_efficiency": 0.85,  # 85% efficiency
    "random_trip_probability": 0.10,  # 10% chance per step
    "random_trip_consumption_range": (0.10, 0.20),  # 10-20% consumption
    "driving_consumption_kwh_per_hour": 7.5,  # 15 kWh/100km at 50 km/h average
}

BATTERY_CONFIG = {
    "capacity_kwh": 10.0,
    "power_capacity_kw": 20.0,
    "min_soc_percent": 0.15,  # 15% minimum
    "max_soc_percent": 0.90,  # 90% maximum
    "soc_at_start_percent": 0.50,  # 50% starting
    "roundtrip_efficiency": 0.85,
}

# EV scheduling patterns - 7-day weekly cycle
# Each entry represents: (needs_charging_overnight, departure_time, return_time, target_soc_percent)
# Index 0 = Monday, 1 = Tuesday, ..., 6 = Sunday
EV_WEEKLY_PATTERNS = [
    (False, None, None, 40),  # Monday - Free day, keep at moderate charge
    (True, "07:00", "13:00", 80),  # Tuesday - Work day, need 80% by 7am
    (True, "08:00", "13:00", 80),  # Wednesday - Work day, need 80% by 8am
    (True, "07:00", "13:00", 80),  # Thursday - Work day, need 80% by 7am
    (False, None, None, 60),  # Friday - Free day, charge to 60% for weekend
    (False, None, None, 40),  # Saturday - Free day
    (False, None, None, 40),  # Sunday - Free day
]


def get_day_pattern(date_time: pd.Timestamp) -> tuple:
    """Get the EV pattern for a specific day of the week."""
    day_of_week = date_time.weekday()  # Monday = 0, Sunday = 6
    day_names = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    pattern = EV_WEEKLY_PATTERNS[day_of_week]

    print(f"  [DAY] {day_names[day_of_week]} ({date_time.strftime('%Y-%m-%d')})")
    print(f"  [PATTERN] {pattern}")

    return pattern


def simulate_random_trip() -> bool:
    """Simulate random shopping trips based on configured probability."""
    import random

    return random.random() < EV_CONFIG["random_trip_probability"]


def calculate_ev_soc_targets_and_constraints(
    current_time: pd.Timestamp,
    capacity_kwh: float = None,
    has_random_trip: bool = False,
) -> dict:
    """
    Calculate dynamic SoC targets and availability constraints for EV charging.

    Returns a dict with:
    - soc_targets: List of target SoC values with datetimes
    - soc_minima: List of minimum SoC constraints during unavailable periods
    - consumption_capacity: Availability windows (0 during unavailable periods)
    """
    if capacity_kwh is None:
        capacity_kwh = EV_CONFIG["default_capacity_kwh"]

    print(
        f"[EV-CALC] Calculating EV constraints for {current_time.strftime('%Y-%m-%d %H:%M')}"
    )
    print(f"  [CAPACITY] Battery capacity: {capacity_kwh} kWh")

    needs_charging, departure_time_str, return_time_str, target_soc_percent = (
        get_day_pattern(current_time)
    )

    target_soc_kwh = (target_soc_percent / 100.0) * capacity_kwh
    min_soc_kwh = EV_CONFIG["min_soc_percent"] * capacity_kwh

    print(f"  [TARGET] Target SoC: {target_soc_percent}% = {target_soc_kwh:.1f} kWh")
    print(
        f"  [MINIMUM] Minimum SoC: {EV_CONFIG['min_soc_percent']*100:.0f}% = {min_soc_kwh:.1f} kWh"
    )

    constraints = {
        "soc_targets": [],
        "soc_minima": [],
        "consumption_capacity": [],
    }

    if needs_charging and departure_time_str and return_time_str:
        # Work day - need to be charged by departure time
        print(
            f"  [WORK-DAY] Departure at {departure_time_str}, return at {return_time_str}"
        )
        departure_hour, departure_minute = map(int, departure_time_str.split(":"))
        return_hour, return_minute = map(int, return_time_str.split(":"))

        # Target: charged to 80% by departure time
        departure_datetime = current_time.replace(
            hour=departure_hour, minute=departure_minute, second=0, microsecond=0
        )

        # If departure is already past today, target tomorrow
        if departure_datetime <= current_time:
            departure_datetime += pd.Timedelta(days=1)
            print(
                f"  [SCHEDULE] Departure time adjusted to next day: {departure_datetime.strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            print(
                f"  [SCHEDULE] Departure time: {departure_datetime.strftime('%Y-%m-%d %H:%M')}"
            )

        constraints["soc_minima"] = [
            {
                "datetime": departure_datetime.isoformat(),
                "value": f"{target_soc_kwh} kWh",
            }
        ]
        print(
            f"  [MINIMUM-SET] SoC minimum set: {target_soc_kwh:.1f} kWh by {departure_datetime.strftime('%H:%M')}"
        )

        # Unavailable period: departure time to return time (same day as departure)
        return_datetime = departure_datetime.replace(
            hour=return_hour, minute=return_minute
        )
        unavailable_duration = return_datetime - departure_datetime

        # Check if we are currently in the unavailable period
        if current_time >= departure_datetime and current_time <= return_datetime:
            print("  [UNAVAILABLE] Currently in unavailable period")
            return_datetime += pd.Timedelta(days=1)
            print(
                f"  [UNAVAILABLE] Period: {departure_datetime.strftime('%H:%M')} - {return_datetime.strftime('%H:%M')} ({unavailable_duration})"
            )
            constraints["unavailable"] = True
        else:
            print(
                f"  [UNAVAILABLE] Period: {departure_datetime.strftime('%H:%M')} - {return_datetime.strftime('%H:%M')} ({unavailable_duration})"
            )

        # Disable charging during unavailable period by setting consumption capacity to 0
        constraints["consumption_capacity"] = [
            {
                "start": departure_datetime.isoformat(),
                "end": return_datetime.isoformat(),
                "value": "0 kW",
            }
        ]

        # Extend minimum SoC constraint during unavailable period
        constraints["soc_minima"].append(
            {
                "start": departure_datetime.isoformat(),
                "end": return_datetime.isoformat(),
                "value": f"{min_soc_kwh} kWh",
            }
        )
        print("  [DISABLED] Charging disabled during unavailable period (0 kW)")
        print(
            f"  [MIN-SOC] Minimum SoC maintained: {min_soc_kwh:.1f} kWh during unavailable period"
        )
    else:
        # Free day - just maintain minimum SoC by end of planning horizon
        print(f"  [FREE-DAY] Flexible charging to {target_soc_percent}%")
        end_of_day = current_time.replace(hour=23, minute=59, second=59, microsecond=0)
        constraints["soc_minima"] = [
            {"datetime": end_of_day.isoformat(), "value": f"{target_soc_kwh} kWh"}
        ]
        print(
            f"  [FLEXIBLE] Minimum: {target_soc_kwh:.1f} kWh by end of day ({end_of_day.strftime('%H:%M')})"
        )
        print("  [AVAILABLE] No availability restrictions - can charge anytime")

    # Handle random trips - reduce SoC randomly to simulate unplanned usage
    if has_random_trip:
        print("  [RANDOM-TRIP] Trip detected!")
        # Random trip consumes configured percentage range of battery
        import random

        min_consumption, max_consumption = EV_CONFIG["random_trip_consumption_range"]
        trip_consumption_percent = random.uniform(min_consumption, max_consumption)
        trip_consumption_kwh = trip_consumption_percent * capacity_kwh

        print(
            f"    [CONSUMPTION] Trip consumption: {trip_consumption_percent*100:.1f}% = {trip_consumption_kwh:.1f} kWh"
        )

        # Adjust minima to account for trip consumption
        for minimum in constraints["soc_minima"]:
            original_minimum_kwh = float(minimum["value"].split()[0])
            # Ensure we charge enough to cover the trip consumption
            adjusted_minimum = min(
                capacity_kwh, original_minimum_kwh + trip_consumption_kwh
            )
            minimum["value"] = f"{adjusted_minimum} kWh"
            print(
                f"    [ADJUSTED] Minimum: {original_minimum_kwh:.1f} kWh -> {adjusted_minimum:.1f} kWh (+{trip_consumption_kwh:.1f} kWh for trip)"
            )

    print("  [SUMMARY] Final constraints:")
    if constraints["soc_minima"]:
        for minima in constraints["soc_minima"]:
            if "datetime" in minima:
                # Point-in-time minimum
                dt = pd.to_datetime(minima["datetime"])
                print(
                    f"    [MINIMUM] {minima['value']} by {dt.strftime('%Y-%m-%d %H:%M')}"
                )
            elif "start" in minima and "end" in minima:
                # Period minimum
                start_dt = pd.to_datetime(minima["start"])
                end_dt = pd.to_datetime(minima["end"])
                print(
                    f"    [MINIMUM] {minima['value']} from {start_dt.strftime('%H:%M')} to {end_dt.strftime('%H:%M')}"
                )
    if constraints["consumption_capacity"]:
        for capacity in constraints["consumption_capacity"]:
            start_dt = pd.to_datetime(capacity["start"])
            end_dt = pd.to_datetime(capacity["end"])
            print(
                f"    [DISABLED] Charging: {start_dt.strftime('%H:%M')} to {end_dt.strftime('%H:%M')} ({capacity['value']})"
            )

    print()

    return constraints


def create_device_flex_model(
    client: FlexMeasuresClient,
    device_type: str,
    current_soc: float,
    capacity_kwh: float,
    power_capacity_kw: float,
    min_soc_percent: float,
    roundtrip_efficiency: float,
    soc_sensor_id: int,
    constraints: dict = None,
    max_soc_percent: float = 1.0,  # Allow override for operational vs physical capacity
) -> dict:
    """Create a standardized flex model for storage devices."""

    if device_type == "evse":
        # EVSEs should be unidirectional (charging only) - no V2G capability
        # Use power-capacity for now and restrict with production-capacity
        flex_model = client.create_storage_flex_model(
            soc_unit="kWh",
            soc_at_start=current_soc,
            soc_max=capacity_kwh
            * max_soc_percent,  # Allow operational max to be different from physical capacity
            soc_min=capacity_kwh * min_soc_percent,
            roundtrip_efficiency=roundtrip_efficiency,
        )
        # Override to make it unidirectional (charging only)
        flex_model["power-capacity"] = f"{power_capacity_kw}kW"  # Total power capacity
        flex_model["production-capacity"] = "0kW"  # No V2G capability
        flex_model["state-of-charge"] = {"sensor": soc_sensor_id}
    else:
        # Batteries are bidirectional (can charge and discharge)
        # For batteries, we need to handle operational max vs physical capacity properly
        flex_model = client.create_storage_flex_model(
            soc_unit="kWh",
            soc_at_start=current_soc,
            soc_max=capacity_kwh
            * max_soc_percent,  # Use operational max (e.g., 90% of physical capacity)
            soc_min=capacity_kwh * min_soc_percent,
            roundtrip_efficiency=roundtrip_efficiency,
        )
        flex_model["power-capacity"] = f"{power_capacity_kw}kW"
        flex_model["state-of-charge"] = {"sensor": soc_sensor_id}

    # Add dynamic constraints if provided
    if constraints:
        if constraints.get("soc_minima"):
            # todo: here we remove the last soc_minima constraint and set it up as a soc_usage component instead
            # this is a workaround; we should define the SoC drop during the trip as a soc_usage component straightaway
            soc_usage = constraints["soc_minima"].pop(-1)
            flex_model["soc-minima"] = constraints["soc_minima"]
            soc_usage["value"] = f'{EV_CONFIG["driving_consumption_kwh_per_hour"]} kW'
            # add soc_usage as a component (soc-usage supports a list of usage components)
            flex_model["soc-usage"] = [[soc_usage]]
        if constraints.get("consumption_capacity"):
            flex_model["consumption-capacity"] = constraints["consumption_capacity"]

    return flex_model


async def create_public_price_sensor(client: FlexMeasuresClient):
    """Create a public price sensor (1h, EUR/kWh).

    Returns the price sensor for use in flex-context.
    """
    print("Creating public price sensor...")
    # Get the client account id
    account = await client.get_account()
    account_id = account["id"]
    print(f"Account ID: {account_id}")
    # Create public market asset (no account_id for public assets)
    # Generic asset type 8 is typically used for market/price assets
    price_market_asset = await client.add_asset(
        name=price_market_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=8,  # Transmission zone  A grid regulated & balanced as a whole, usually a national grid.
        account_id=account_id,
    )

    # Create price sensor with 1-hour resolution
    price_sensor = await client.add_sensor(
        name="electricity-price",
        event_resolution="PT1H",
        unit="EUR/kWh",
        generic_asset_id=price_market_asset["id"],
        timezone="Europe/Amsterdam",
    )

    print(f"Created public price sensor with ID: {price_sensor['id']}")
    return price_sensor


async def create_weather_station(client: FlexMeasuresClient):
    """Create a public weather station with irradiation and cloud coverage sensors."""
    print("Creating weather station...")
    # Get the client account id
    account = await client.get_account()
    account_id = account["id"]
    print(f"Account ID: {account_id}")
    # Create public weather station asset
    # Generic asset type 7 (process) used for weather stations since no dedicated type exists
    weather_asset = await client.add_asset(
        name=weather_station_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=7,  # Process asset type (for weather station)
        account_id=account_id,  # Public account ID
    )

    # Create irradiation sensor (1H, W/m²)
    irradiation_sensor = await client.add_sensor(
        name="irradiation",
        event_resolution="PT1H",
        unit="W/m²",
        generic_asset_id=weather_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create cloud coverage sensor (1H, %)
    cloud_coverage_sensor = await client.add_sensor(
        name="cloud-coverage",
        event_resolution="PT1H",
        unit="%",
        generic_asset_id=weather_asset["id"],
        timezone="Europe/Amsterdam",
    )

    print(f"Created weather station with ID: {weather_asset['id']}")
    return weather_asset, irradiation_sensor, cloud_coverage_sensor


async def create_building_asset(
    client: FlexMeasuresClient, account_id: int, price_sensor_id: int
):
    """Create building asset with consumption and energy costs KPI sensors."""
    print("Creating building asset...")

    # Create building asset (generic_asset_type_id=6 for building)
    building_asset = await client.add_asset(
        name=building_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=6,  # Building asset type
        account_id=account_id,
    )

    # Create general consumption sensor (15min resolution, kW)
    consumption_sensor = await client.add_sensor(
        name="electricity-consumption",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create energy costs KPI sensor (1D resolution, EUR)
    energy_costs_sensor = await client.add_sensor(
        name="energy-costs-kpi",
        event_resolution="P1D",
        unit="EUR",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create aggregate power sensor for the building
    aggregate_sensor = await client.add_sensor(
        name="electricity-aggregate",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create max production capacity sensor for the building
    max_production_sensor = await client.add_sensor(
        name="max-production-capacity",
        event_resolution="PT1H",
        unit="kW",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create max consumption capacity sensor for the building
    max_consumption_sensor = await client.add_sensor(
        name="max-consumption-capacity",
        event_resolution="PT1H",
        unit="kW",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create self-consumption sensor for the building
    self_consumption_sensor = await client.add_sensor(
        name="self-consumption",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create total energy costs sensor for the building
    total_energy_costs_sensor = await client.add_sensor(
        name="total-energy-costs",
        event_resolution="PT15M",
        unit="EUR",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create daily total energy costs sensor for the building
    daily_total_energy_costs_sensor = await client.add_sensor(
        name="daily-total-energy-costs",
        event_resolution="P1D",
        unit="EUR",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create daily share of self-consumption sensor for the building
    daily_share_of_self_consumption_sensor = await client.add_sensor(
        name="daily-share-of-self-consumption",
        event_resolution="P1D",
        unit="%",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
    )

    print(f"Created building asset with ID: {building_asset['id']}")
    return (
        building_asset,
        consumption_sensor,
        energy_costs_sensor,
        aggregate_sensor,
        self_consumption_sensor,
        max_production_sensor,
        max_consumption_sensor,
        total_energy_costs_sensor,
        daily_total_energy_costs_sensor,
        daily_share_of_self_consumption_sensor,
    )


async def create_pv_asset(
    client: FlexMeasuresClient, account_id: int, building_asset_id: int
):
    """Create PV asset as child of building with production sensor."""
    print("Creating PV asset...")

    # Create PV asset (generic_asset_type_id=1 for solar/PV)
    pv_asset = await client.add_asset(
        name=pv_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=1,  # Solar/PV asset type
        account_id=account_id,
        parent_asset_id=building_asset_id,  # Child of building
    )

    # Create production sensor (15min, kW)
    pv_production_sensor = await client.add_sensor(
        name="electricity-production",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=pv_asset["id"],
        timezone="Europe/Amsterdam",
    )

    print(f"Created PV asset with ID: {pv_asset['id']}")
    return pv_asset, pv_production_sensor


async def create_battery_asset(
    client: FlexMeasuresClient, account_id: int, building_asset_id: int
):
    """Create battery asset as child of building with power and SoC sensors + settings."""
    print("Creating battery asset...")

    # Create battery asset (generic_asset_type_id=5 for battery)
    battery_asset = await client.add_asset(
        name=battery_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=5,  # Battery asset type
        account_id=account_id,
        parent_asset_id=building_asset_id,  # Child of building
    )

    # Create power sensor (15min, kW)
    battery_power_sensor = await client.add_sensor(
        name="electricity-power",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=battery_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create state-of-charge sensor (0min, kWh)
    battery_soc_sensor = await client.add_sensor(
        name="state-of-charge",
        event_resolution="PT0M",
        unit="kWh",
        generic_asset_id=battery_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Store battery settings in flex_model attribute (attributes["flex_model"])
    print("Updating battery asset with flex_model settings...")
    capacity = BATTERY_CONFIG["capacity_kwh"]
    battery_settings = {
        "soc_unit": "kWh",
        "soc_at_start": capacity * BATTERY_CONFIG["soc_at_start_percent"],
        "soc_max": capacity * BATTERY_CONFIG["max_soc_percent"],
        "soc_min": capacity * BATTERY_CONFIG["min_soc_percent"],
        "roundtrip_efficiency": BATTERY_CONFIG["roundtrip_efficiency"],
        "capacity_kwh": capacity,
        "power_capacity_kw": BATTERY_CONFIG["power_capacity_kw"],
    }

    # Store in attributes["flex_model"] for now, will be easy to adapt to new flex_model attribute
    await client.update_asset(
        asset_id=battery_asset["id"],
        updates={"attributes": {"flex_model": battery_settings}},
    )

    print(f"Created battery asset with ID: {battery_asset['id']}")
    return battery_asset, battery_power_sensor, battery_soc_sensor


async def create_evse_asset(
    client: FlexMeasuresClient, account_id: int, building_asset_id: int, evse_name: str
):
    """Create EVSE asset as child of building with power and SoC sensors + settings."""
    print(f"Creating EVSE asset: {evse_name}...")

    # Create EVSE asset - using generic type 4 for one-way EVSE based on the codebase search
    # Note: We'll use a basic asset type since one-way_evse might not be available by default
    evse_asset = await client.add_asset(
        name=evse_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=4,  # Using a generic type, could be EVSE specific if available
        account_id=account_id,
        parent_asset_id=building_asset_id,  # Child of building
    )

    # Create power sensor (15min, kW)
    evse_power_sensor = await client.add_sensor(
        name="electricity-power",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create state-of-charge sensor (instantaneous, kWh)
    evse_soc_sensor = await client.add_sensor(
        name="state-of-charge",
        event_resolution="PT0M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create soc-min sensor (15min, kWh)
    evse_soc_min_sensor = await client.add_sensor(
        name="soc-min",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create soc-max sensor (15min, kWh)
    evse_soc_max_sensor = await client.add_sensor(
        name="soc-max",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Store EVSE settings in flex_model attribute
    print(f"Updating {evse_name} asset with flex_model settings...")
    capacity = EV_CONFIG["default_capacity_kwh"]
    evse_settings = {
        "soc_unit": "kWh",
        "soc_at_start": capacity * EV_CONFIG["min_soc_percent"],  # Start at minimum SoC
        "soc_max": capacity,  # 100% of capacity (max physical limit)
        "soc_min": capacity * EV_CONFIG["min_soc_percent"],  # Minimum SoC
        "roundtrip_efficiency": EV_CONFIG["roundtrip_efficiency"],
        "capacity_kwh": capacity,
        "power_capacity_kw": EV_CONFIG["default_power_capacity_kw"],
    }

    # Configure graph displays as requested
    sensors_to_show = [
        {
            "title": "State of charge",
            "sensors": [
                evse_soc_sensor["id"],
                evse_soc_min_sensor["id"],
                evse_soc_max_sensor["id"],
            ],
        },
        {
            "title": "Power",
            "sensors": [
                evse_power_sensor["id"],
            ],
        },
    ]

    # Store in attributes["flex_model"]
    await client.update_asset(
        asset_id=evse_asset["id"],
        updates={"attributes": {"flex_model": evse_settings, "sensors_to_show": sensors_to_show}},
    )

    print(f"Created EVSE asset {evse_name} with ID: {evse_asset['id']}")
    return (
        evse_asset,
        evse_power_sensor,
        evse_soc_sensor,
        evse_soc_min_sensor,
        evse_soc_max_sensor,
    )


async def configure_building_flex_context(
    client: FlexMeasuresClient,
    building_asset,
    price_sensor,
    consumption_sensor,
    pv_production_sensor,
    battery_power_sensor,
    max_consumption_sensor,
    max_production_sensor,
):
    """Configure building asset with comprehensive flex-context."""
    print("Configuring building flex-context...")

    # Create flex context with all required settings
    flex_context = {
        # Price sensor reference (new format)
        "consumption-price": {"sensor": price_sensor["id"]},
        # Consumption capacity limit (not typically needed for private homes, but including as requested)
        # Calculated using a smaller connection category: 3 x 25 A at 230 V
        "site-consumption-capacity": {
            "sensor": max_consumption_sensor["id"]
        },  # Relaxed constraint for residential
        "site-production-capacity": {
            "sensor": max_production_sensor["id"]
        },  # Relaxed constraint for residential
        "site-power-capacity": "20 kVA",
        # Enable soft constraints for SoC minima (this makes soc-minima soft constraints instead of hard)
        "relax-soc-constraints": True,
        # Configure breach prices for soft constraints
        # Energy price units (match electricity-price sensor): EUR/kWh
        # Moderate penalty for not meeting soc-minima (allows some flexibility)
        "soc-minima-breach-price": "100 EUR/kWh",  # Lower penalty for soft constraint
        "soc-maxima-breach-price": "1000 EUR/kWh",  # Higher penalty for safety limits
        # Capacity price units (for power capacity constraints): EUR/MW
        "site-consumption-breach-price": "1000 EUR/MW",
        "site-production-breach-price": "1000 EUR/MW",
        "consumption-breach-price": "1000 EUR/MW",
        "production-breach-price": "1000 EUR/MW",
        # Add inflexible devices as requested
        "inflexible-device-sensors": [
            consumption_sensor["id"],  # General consumption
            pv_production_sensor["id"],  # PV production
            battery_power_sensor["id"],  # Battery power
        ],
    }

    # Update building asset with flex-context
    await client.update_asset(
        asset_id=building_asset["id"], updates={"flex_context": flex_context}
    )

    print("Building flex-context configured successfully")


async def configure_building_dashboard(
    client: FlexMeasuresClient,
    building_asset,
    consumption_sensor,
    pv_production_sensor,
    battery_power_sensor,
    battery_soc_sensor,
    evse1_power_sensor,
    evse2_power_sensor,
    aggregate_sensor,
    self_consumption_sensor,
    max_production_sensor,
    max_consumption_sensor,
    price_sensor,
    total_energy_costs_sensor,
    daily_total_energy_costs_sensor,
    daily_share_of_self_consumption_sensor,
):
    """Configure sensors_to_show for building asset graphs."""
    print("Configuring sensors to show...")

    # Configure graph displays as requested
    sensors_to_show = [
        {
            "title": "Power flow by type",
            "sensors": [
                consumption_sensor["id"],
                pv_production_sensor["id"],
                battery_power_sensor["id"],
                evse1_power_sensor["id"],
                # evse2_power_sensor["id"],  # Just showing one now to avoid cluttering the chart
            ],
        },
        {
            "title": "Solar self-consumption",
            "sensors": [self_consumption_sensor["id"], pv_production_sensor["id"]],
        },
        {
            "title": "Prices",
            "sensors": [
                price_sensor["id"],
            ],
        },
        {
            "title": "Energy costs",
            "sensors": [
                total_energy_costs_sensor["id"],
            ],
        },
        {"title": "Battery Soc", "sensors": [battery_soc_sensor["id"]]},
        {
            "title": "Site capacity",
            "sensors": [
                aggregate_sensor["id"],
                max_consumption_sensor["id"],
                max_production_sensor["id"],
            ],
        },
    ]

    sensors_to_show_as_kpis = [
        {
            "title": "Daily costs",
            "sensor": daily_total_energy_costs_sensor["id"],
            "function": "sum",
        },
        {
            "title": "Self-consumption",
            "sensor": daily_share_of_self_consumption_sensor["id"],
            "function": "mean",
        },
    ]

    # Update building asset with sensors_to_show
    await client.update_asset(
        asset_id=building_asset["id"],
        updates={
            "sensors_to_show": sensors_to_show,
            "sensors_to_show_as_kpis": sensors_to_show_as_kpis,
        },
    )

    print("Sensors to show configured successfully")


async def create_building_assets_and_sensors(client: FlexMeasuresClient, account: dict):
    """
    Create a building asset with its associated sensors and linked assets (PV, battery, EVSEs, and weather station),
    then configure the building's flex context and dashboard.
    """
    account_id = account["id"]
    print("Creating price market asset and associated price sensor")
    price_sensor = await create_public_price_sensor(client)
    print("Creating building asset with PV and battery sensors")
    (
        building_asset,
        consumption_sensor,
        energy_costs_sensor,
        aggregate_sensor,
        self_consumption_sensor,
        max_production_sensor,
        max_consumption_sensor,
        total_energy_costs_sensor,
        daily_total_energy_costs_sensor,
        daily_share_of_self_consumption_sensor,
    ) = await create_building_asset(client, account_id, price_sensor["id"])
    print(f"Building asset ID: {building_asset['id']}")
    print(f"Consumption sensor ID: {consumption_sensor['id']}")
    print(f"Energy costs sensor ID: {energy_costs_sensor['id']}")
    print(f"Aggregate sensor ID: {aggregate_sensor['id']}")
    print(f"Max production sensor ID: {max_production_sensor['id']}")
    print(f"Max consumption sensor ID: {max_consumption_sensor['id']}")
    print(f"Self-consumption sensor ID: {self_consumption_sensor['id']}")
    print("Creating PV asset with production sensor")
    pv_asset, pv_production_sensor = await create_pv_asset(
        client, account_id, building_asset["id"]
    )
    print(f"PV asset ID: {pv_asset['id']}")
    print(f"PV production sensor ID: {pv_production_sensor['id']}")
    print("Creating battery asset with power and SoC sensors")
    battery_asset, battery_power_sensor, battery_soc_sensor = (
        await create_battery_asset(client, account_id, building_asset["id"])
    )
    print(f"Battery asset ID: {battery_asset['id']}")
    print(f"Battery power sensor ID: {battery_power_sensor['id']}")
    print(f"Battery SoC sensor ID: {battery_soc_sensor['id']}")

    # Create EVSE assets (2 connectors for one charge point)
    print("Creating EVSE assets with power and SoC sensors")
    (
        evse1_asset,
        evse1_power_sensor,
        evse1_soc_sensor,
        evse1_soc_min_sensor,
        evse1_soc_max_sensor,
    ) = await create_evse_asset(client, account_id, building_asset["id"], evse1_name)
    print(f"EVSE 1 asset ID: {evse1_asset['id']}")
    print(f"EVSE 1 power sensor ID: {evse1_power_sensor['id']}")
    print(f"EVSE 1 SoC sensor ID: {evse1_soc_sensor['id']}")

    (
        evse2_asset,
        evse2_power_sensor,
        evse2_soc_sensor,
        evse2_soc_min_sensor,
        evse2_soc_max_sensor,
    ) = await create_evse_asset(client, account_id, building_asset["id"], evse2_name)
    print(f"EVSE 2 asset ID: {evse2_asset['id']}")
    print(f"EVSE 2 power sensor ID: {evse2_power_sensor['id']}")
    print(f"EVSE 2 SoC sensor ID: {evse2_soc_sensor['id']}")

    print("Creating weather station with irradiation and cloud coverage sensors")
    weather_asset, irradiation_sensor, cloud_coverage_sensor = (
        await create_weather_station(client)
    )
    print(f"Weather station asset ID: {weather_asset['id']}")
    print(f"Irradiation sensor ID: {irradiation_sensor['id']}")
    print(f"Cloud coverage sensor ID: {cloud_coverage_sensor['id']}")
    print("Configuring building flex-context ...")
    await configure_building_flex_context(
        client=client,
        building_asset=building_asset,
        price_sensor=price_sensor,
        consumption_sensor=consumption_sensor,
        pv_production_sensor=pv_production_sensor,
        battery_power_sensor=battery_power_sensor,
        max_consumption_sensor=max_consumption_sensor,
        max_production_sensor=max_production_sensor,
    )
    print("Configuring building dashboard ...")
    await configure_building_dashboard(
        client=client,
        building_asset=building_asset,
        consumption_sensor=consumption_sensor,
        pv_production_sensor=pv_production_sensor,
        battery_power_sensor=battery_power_sensor,
        battery_soc_sensor=battery_soc_sensor,
        evse1_power_sensor=evse1_power_sensor,
        evse2_power_sensor=evse2_power_sensor,
        aggregate_sensor=aggregate_sensor,
        self_consumption_sensor=self_consumption_sensor,
        max_production_sensor=max_production_sensor,
        max_consumption_sensor=max_consumption_sensor,
        price_sensor=price_sensor,
        total_energy_costs_sensor=total_energy_costs_sensor,
        daily_total_energy_costs_sensor=daily_total_energy_costs_sensor,
        daily_share_of_self_consumption_sensor=daily_share_of_self_consumption_sensor,
    )


async def cleanup_existing_assets(client: FlexMeasuresClient, account_id: int):
    """Clean up existing HEMS assets to avoid naming conflicts."""
    print("Cleaning up existing assets...")

    # Asset names to clean up
    asset_names_to_clean = [
        building_name,  # Deleting this asset also deletes child assets (battery, PV, EVSEs)
        weather_station_name,
        price_market_name,
    ]

    try:
        # Get all existing assets
        assets = await client.get_assets()

        # Find and delete assets that match our names
        deleted_count = 0
        for asset in assets:
            if asset["name"] in asset_names_to_clean:
                print(f"Deleting existing asset: {asset['name']} (ID: {asset['id']})")
                try:
                    if asset.get("account_id") != account_id:
                        print(
                            f"Warning: Asset {asset['name']} (ID: {asset['id']}) does not belong to the current account."
                        )
                        raise
                    await client.delete_asset(asset_id=asset["id"], confirm_first=False)
                    deleted_count += 1
                except Exception as delete_error:
                    # Check if it's a 404 error (asset not found)
                    if "404" in str(delete_error) or "NOT FOUND" in str(delete_error):
                        print(
                            f"Asset {asset['name']} (ID: {asset['id']}) no longer exists, skipping..."
                        )
                    else:
                        print(
                            f"Warning: Could not delete asset {asset['name']}: {delete_error}"
                        )
                    # Continue with other assets

        if deleted_count > 0:
            print(f"Cleaned up {deleted_count} existing assets")
        else:
            print("No existing assets to clean up")

        # Wait a moment for deletions to complete
        await asyncio.sleep(1)

    except Exception as e:
        print(f"Warning: Error during cleanup: {e}")
        print("Continuing with setup...")


async def find_sensor_by_name_and_asset(
    client: FlexMeasuresClient, sensor_name: str, asset_name: str
):
    """Find a sensor by name within a specific asset."""
    assets = await client.get_assets()
    target_asset = None
    for asset in assets:
        if asset["name"] == asset_name:
            target_asset = asset
            break

    if not target_asset:
        print(f"Asset '{asset_name}' not found")
        return None

    sensors = await client.get_sensors(asset_id=target_asset["id"])
    for sensor in sensors:
        if sensor["name"] == sensor_name:
            return sensor

    print(f"Sensor '{sensor_name}' not found in asset '{asset_name}'")
    return None


def load_and_align_csv_data(
    file_path: str, target_start_date: str, resolution_minutes: int = 60
):
    """Load CSV data and align it to the target start date."""
    df = pd.read_csv(file_path)
    df["event_start"] = pd.to_datetime(df["event_start"])
    df = df.sort_values("event_start")

    # Create new date range starting from target date
    target_start = pd.to_datetime(target_start_date)
    freq = f"{resolution_minutes}min"
    new_dates = pd.date_range(start=target_start, periods=len(df), freq=freq)

    # Create aligned dataframe
    aligned_df = df.copy()
    aligned_df["event_start"] = new_dates

    print(f"Aligned {len(df)} records from {file_path}")
    return aligned_df


async def upload_csv_file_to_sensor(
    client: FlexMeasuresClient,
    sensor_id: int,
    file_path: str,
    belief_time_measured_instantly: bool,
):
    """Upload CSV file directly to a sensor using file upload."""
    try:
        await client.post_sensor_data(
            sensor_id=sensor_id,
            file_path=file_path,
            belief_time_measured_instantly=belief_time_measured_instantly,  # Set belief_time immediately after event ends
        )
        print(f"Uploaded {file_path} to sensor {sensor_id}")
        return True
    except Exception as e:
        print(f"Failed to upload {file_path} to sensor {sensor_id}: {e}")
        return False


async def find_sensors_by_asset(
    client: FlexMeasuresClient, sensor_mappings: list[tuple[str, str, str]]
):
    """Find multiple sensors by name and asset name."""
    sensors = {}
    for key, sensor_name, asset_name in sensor_mappings:
        sensor = await find_sensor_by_name_and_asset(client, sensor_name, asset_name)
        if sensor:
            sensors[key] = sensor
        else:
            print(f"Could not find sensor '{sensor_name}' in asset '{asset_name}'")
            return False
    return sensors


async def upload_data_for_first_two_weeks(client: FlexMeasuresClient):
    """Upload historical data for the first two weeks."""
    print("Uploading data for first two weeks...")

    # Find all required sensors
    sensor_mappings = [
        ("electricity-price", "electricity-price", price_market_name),
        ("electricity-consumption", "electricity-consumption", building_name),
        ("max-consumption-capacity", "max-consumption-capacity", building_name),
        ("max-production-capacity", "max-production-capacity", building_name),
        ("irradiation", "irradiation", weather_station_name),
        ("electricity-production", "electricity-production", pv_name),
    ]

    sensors = await find_sensors_by_asset(client, sensor_mappings)

    # Upload data files directly
    data_files = [
        ("HEMS data/price_data.csv", "electricity-price", False),
        ("HEMS data/building_data.csv", "electricity-consumption", True),
        ("HEMS data/irradiation_data.csv", "irradiation", True),
        ("HEMS data/PV_production_data.csv", "electricity-production", True),
        ("HEMS data/max_consumption_capacity.csv", "max-consumption-capacity", False),
        ("HEMS data/max_production_capacity.csv", "max-production-capacity", False),
    ]

    for file_path, sensor_key, belief_time_measured_instantly in data_files:
        if sensor_key not in sensors:
            print(f"Skipping {file_path} - sensor not found")
            continue

        print(f"Processing {file_path}...")

        # Upload CSV file directly
        success = await upload_csv_file_to_sensor(
            client=client,
            sensor_id=sensors[sensor_key]["id"],
            file_path=file_path,
            belief_time_measured_instantly=belief_time_measured_instantly,
        )

        if success:
            print(f"Successfully uploaded {sensor_key} data")
        else:
            print(f"Failed to upload {sensor_key} data")

    return True


async def generate_forecasts(client: FlexMeasuresClient, sensor_name: str, asset_name: str, regressors: list[tuple[str, str]] | None = None):
    """Generate forecasts using FlexMeasures CLI for the second week."""
    print(f"Generating {sensor_name} forecasts for {asset_name}...")

    # Check if flexmeasures CLI is available
    check_cmd = ["which", "flexmeasures"]
    check_result = subprocess.run(check_cmd, capture_output=True, text=True)

    if check_result.returncode != 0:
        print("FlexMeasures CLI not found. Skipping forecast generation.")
        return False

    # Find sensors
    target_sensor = await find_sensor_by_name_and_asset(
        client, sensor_name, asset_name
    )
    regressor_sensors = []
    if regressors is not None:
        for regressor in regressors:
            regressor_sensor = await find_sensor_by_name_and_asset(
                client, regressor[0], regressor[1]
            )
            regressor_sensors.append(regressor_sensor)

    if not target_sensor:
        print("Could not find required sensors for forecasting")
        return False

    # Run CLI command
    # NOTE: This uses the CLI because there is no public API yet.
    #       An API endpoint is coming soon, so this can later be done via the client.
    #       Requires FlexMeasures PR #1546.
    cmd = [
        "flexmeasures",
        "add",
        "forecasts",
        "--sensor",
        str(target_sensor["id"]),
        "--train-start",
        TUTORIAL_START_DATE,
        "--from-date",
        FORECASTING_START,
        "--to-date",
        SCHEDULING_END,
        "--max-forecast-horizon",
        f"PT{FORECAST_HORIZON_HOURS}H",
        "--forecast-frequency",
        f"PT{SIMULATION_STEP_HOURS}H",
        "--ensure-positive"
    ]

    if regressor_sensors:
        cmd.extend(
            [
                "--past-regressors",
                ",".join([str(sensor["id"]) for sensor in regressor_sensors]),
            ]
        )  # TODO: to be changed to --regressors when the sensor has irradiance forecasts

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode == 0:
        print(f"{sensor_name} forecasts for {asset_name} generated successfully")
        return True
    else:
        print(f"{sensor_name} forecasts for {asset_name} failed: {result.stderr}")
        return False


async def run_scheduling_simulation(client: FlexMeasuresClient, simulate_live_corrections: bool = True):
    """Run step-by-step scheduling simulation for the third week with EV charging."""
    print("Running scheduling simulation for third week with EV charging...")

    # Find required assets and sensors
    assets = await client.get_assets()

    # Find building, battery, and EVSE assets
    assets_by_name = {a["name"]: a for a in assets}
    building_asset = assets_by_name.get(building_name)
    battery_asset = assets_by_name.get(battery_name)
    evse1_asset = assets_by_name.get(evse1_name)
    evse2_asset = assets_by_name.get(evse2_name)

    if not building_asset:
        print("Could not find building asset for scheduling")
        return False

    if not battery_asset:
        print("Could not find battery asset for scheduling")
        return False

    if not evse1_asset or not evse2_asset:
        print("Could not find EVSE assets for scheduling")
        return False

    # Find sensors (including EVSE sensors) - using unique keys for duplicate sensor names
    sensor_mappings = [
        ("building-consumption", building_name, "electricity-consumption"),
        ("pv-production", pv_name, "electricity-production"),
        ("battery-power", battery_name, "electricity-power"),
        ("battery-soc", battery_name, "state-of-charge"),
        ("evse1-power", evse1_name, "electricity-power"),
        ("evse1-soc", evse1_name, "state-of-charge"),
        ("evse2-power", evse2_name, "electricity-power"),
        ("evse2-soc", evse2_name, "state-of-charge"),
        ("electricity-price", price_market_name, "electricity-price"),
        ("electricity-aggregate", building_name, "electricity-aggregate"),
    ]

    sensors = {}
    for sensor_key, asset_name, sensor_name in sensor_mappings:
        sensor = await find_sensor_by_name_and_asset(client, sensor_name, asset_name)
        if sensor:
            sensors[sensor_key] = sensor
        else:
            print(f"Could not find sensor '{sensor_name}' in asset '{asset_name}'")
            return False

    # Load complete datasets for simulation
    building_df = load_and_align_csv_data(
        "HEMS data/building_data.csv", TUTORIAL_START_DATE, 15
    )
    pv_df = load_and_align_csv_data(
        "HEMS data/PV_production_data.csv", TUTORIAL_START_DATE, 60
    )

    # Get battery soc settings
    battery_flex_model = json.loads(battery_asset["attributes"]).get("flex_model")
    if not battery_flex_model:
        print("Battery asset missing flex_model settings")
        return False
    battery_soc_unit = battery_flex_model.get("soc_unit")
    battery_soc_at_start = battery_flex_model.get("soc_at_start")
    battery_soc_max = battery_flex_model.get("soc_max")
    battery_soc_min = battery_flex_model.get("soc_min")
    battery_roundtrip_efficiency = battery_flex_model.get("roundtrip_efficiency")

    # Get EVSE settings
    evse1_flex_model = json.loads(evse1_asset["attributes"]).get("flex_model")
    evse2_flex_model = json.loads(evse2_asset["attributes"]).get("flex_model")
    if not evse1_flex_model or not evse2_flex_model:
        print("EVSE assets missing flex_model settings")
        return False

    evse1_capacity = evse1_flex_model.get(
        "capacity_kwh", EV_CONFIG["default_capacity_kwh"]
    )
    evse2_capacity = evse2_flex_model.get(
        "capacity_kwh", EV_CONFIG["default_capacity_kwh"]
    )

    # Initialize simulation
    current_time = pd.to_datetime(SCHEDULING_START)
    end_time = pd.to_datetime(SCHEDULING_END)
    step_num = 1
    battery_next_current_soc = None
    evse1_next_current_soc = None
    evse2_next_current_soc = None

    while current_time < end_time:
        print(f"Simulation step {step_num}: {current_time}")

        # Create schedule for the building with battery and EVs
        try:
            schedule_start = current_time
            schedule_duration = timedelta(hours=FORECAST_HORIZON_HOURS)

            # Create flex model for battery
            battery_power_capacity = battery_flex_model.get(
                "power_capacity_kw", BATTERY_CONFIG["power_capacity_kw"]
            )
            battery_capacity_kwh = battery_flex_model.get(
                "capacity_kwh", BATTERY_CONFIG["capacity_kwh"]
            )  # Use actual physical capacity
            if battery_next_current_soc is None:
                battery_current_soc = (
                    battery_soc_at_start  # Use initial SoC for first step
                )
            else:
                battery_current_soc = battery_next_current_soc
            battery_scheduler_flex_model = create_device_flex_model(
                client=client,
                device_type="battery",
                current_soc=battery_current_soc,
                capacity_kwh=battery_capacity_kwh,  # Use actual physical capacity, not the operational max
                power_capacity_kw=battery_power_capacity,
                min_soc_percent=battery_soc_min
                / battery_capacity_kwh,  # Calculate percentage against physical capacity
                roundtrip_efficiency=battery_roundtrip_efficiency,
                soc_sensor_id=sensors["battery-soc"]["id"],
                max_soc_percent=BATTERY_CONFIG[
                    "max_soc_percent"
                ],  # Use configured operational max (90%)
            )

            # Calculate dynamic EV constraints for current day
            current_time_ts = pd.Timestamp(current_time)

            print("\n[EVSE-SCHEDULING] === EVSE SCHEDULING CALCULATIONS ===")
            print(
                f"Simulation step {step_num} at {current_time.strftime('%Y-%m-%d %H:%M')}"
            )

            # Simulate random trips for each EVSE
            evse1_has_trip = simulate_random_trip()
            evse2_has_trip = simulate_random_trip()

            print("\n[RANDOM-TRIPS] Trip simulation results:")
            print(f"  EVSE 1: {'Trip scheduled' if evse1_has_trip else 'No trip'}")
            print(f"  EVSE 2: {'Trip scheduled' if evse2_has_trip else 'No trip'}")

            print("\n[EVSE-1] Constraints Calculation:")
            evse1_constraints = calculate_ev_soc_targets_and_constraints(
                current_time_ts, evse1_capacity, evse1_has_trip
            )

            print("[EVSE-2] Constraints Calculation:")
            evse2_constraints = calculate_ev_soc_targets_and_constraints(
                current_time_ts, evse2_capacity, evse2_has_trip
            )
            if not evse1_constraints.get("unavailable"):

                # Create flex models for EVSE 1
                evse1_power_capacity = evse1_flex_model.get(
                    "power_capacity_kw", EV_CONFIG["default_power_capacity_kw"]
                )
                evse1_efficiency = evse1_flex_model.get(
                    "roundtrip_efficiency", EV_CONFIG["roundtrip_efficiency"]
                )
                if evse1_next_current_soc is None:
                    # Use initial SoC for first step
                    evse1_current_soc = evse1_flex_model.get("soc_at_start", 12.0)
                else:
                    evse1_current_soc = evse1_next_current_soc
                evse1_scheduler_flex_model = create_device_flex_model(
                    client=client,
                    device_type="evse",
                    current_soc=evse1_current_soc,
                    capacity_kwh=evse1_capacity,
                    power_capacity_kw=evse1_power_capacity,
                    min_soc_percent=EV_CONFIG["min_soc_percent"],
                    roundtrip_efficiency=evse1_efficiency,
                    soc_sensor_id=sensors["evse1-soc"]["id"],
                    constraints=evse1_constraints,
                )
                

            if not evse2_constraints.get("unavailable"):
                # Create flex models for EVSE 2 (similar pattern, could be different car)
                evse2_power_capacity = evse2_flex_model.get(
                    "power_capacity_kw", EV_CONFIG["default_power_capacity_kw"]
                )
                evse2_efficiency = evse2_flex_model.get(
                    "roundtrip_efficiency", EV_CONFIG["roundtrip_efficiency"]
                )
                if evse2_next_current_soc is None:
                    # Use initial SoC for first step
                    evse2_current_soc = evse2_flex_model.get("soc_at_start", 12.0)
                else:
                    evse2_current_soc = evse2_next_current_soc
                evse2_scheduler_flex_model = create_device_flex_model(
                    client=client,
                    device_type="evse",
                    current_soc=evse2_current_soc,
                    capacity_kwh=evse2_capacity,
                    power_capacity_kw=evse2_power_capacity,
                    min_soc_percent=EV_CONFIG["min_soc_percent"],
                    roundtrip_efficiency=evse2_efficiency,
                    soc_sensor_id=sensors["evse2-soc"]["id"],
                    constraints=evse2_constraints,
                )

            # Create flex context for all devices
            flex_context = {
                "consumption-price": {"sensor": sensors["electricity-price"]["id"]},
                # Enable soft constraints for EV charging flexibility
                "relax-soc-constraints": True,
                # Configure breach prices for soft constraints (EV charging optimization)
                "soc-minima-breach-price": "50 EUR/kWh",  # Moderate penalty - allows flexibility vs price optimization
                "soc-maxima-breach-price": "1000 EUR/kWh",  # High penalty for safety limits
                "inflexible-device-sensors": [
                    sensors["building-consumption"]["id"],
                    sensors["pv-production"]["id"],
                ],
                "aggregate-power": {"sensor": sensors["electricity-aggregate"]["id"]}
            }

            # Start with the battery flex model
            final_flex_models = [
                {
                    "sensor": sensors["battery-power"]["id"],
                    **battery_scheduler_flex_model,
                },
            ]

            # Conditionally add EVSE flex models if they are not on a trip
            if not evse1_constraints.get("unavailable"):
                final_flex_models.append(
                    {"sensor": sensors["evse1-power"]["id"], **evse1_scheduler_flex_model}
                )
            else:
                print("EVSE 1 is on a trip, skipping scheduling.")

            if not evse2_constraints.get("unavailable"):
                final_flex_models.append(
                    {"sensor": sensors["evse2-power"]["id"], **evse2_scheduler_flex_model}
                )
            else:
                print("EVSE 2 is on a trip, skipping scheduling.")


            print("[FLEX-MODEL-DEBUG] === FLEX MODELS SENT TO SCHEDULER ===")
            for i, model in enumerate(final_flex_models):
                device_name = ["Battery", "EVSE-1", "EVSE-2"][i]
                print(f"[FLEX-MODEL] {device_name}: {model}")
            print()

            # Trigger scheduling and get job UUID to retrieve both power and SoC schedules
            job_uuid = await client.trigger_schedule(
                start=schedule_start,
                duration=schedule_duration,
                flex_model=final_flex_models,
                flex_context=flex_context,
                asset_id=building_asset["id"],
                prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS) if simulate_live_corrections else current_time,
            )

            print(f"Multi-device scheduling job triggered with UUID: {job_uuid}")

            # Get power schedules for each device
            schedule_result = []
            for flex_model in final_flex_models:
                sensor_id = flex_model["sensor"]
                power_schedule = await client.get_schedule(
                    sensor_id=sensor_id,
                    schedule_id=job_uuid,
                    duration=schedule_duration,
                )
                power_schedule["sensor"] = sensor_id
                schedule_result.append(power_schedule)

            # Get SoC schedules computed by FlexMeasures using the same job UUID
            # This retrieves the SoC values that FlexMeasures computed based on the flex-model constraints
            try:
                battery_soc_schedule = await client.get_schedule(
                    sensor_id=sensors["battery-soc"]["id"],
                    schedule_id=job_uuid,
                    duration=schedule_duration,
                )
            except Exception as e:
                print(f"Warning: Could not retrieve battery SoC schedule: {e}")
                battery_soc_schedule = {"values": [], "duration": "PT0H"}

            try:
                evse1_soc_schedule = await client.get_schedule(
                    sensor_id=sensors["evse1-soc"]["id"],
                    schedule_id=job_uuid,
                    duration=schedule_duration,
                )
            except Exception as e:
                print(f"Warning: Could not retrieve EVSE1 SoC schedule: {e}")
                evse1_soc_schedule = {"values": [], "duration": "PT0H"}

            try:
                evse2_soc_schedule = await client.get_schedule(
                    sensor_id=sensors["evse2-soc"]["id"],
                    schedule_id=job_uuid,
                    duration=schedule_duration,
                )
            except Exception as e:
                print(f"Warning: Could not retrieve EVSE2 SoC schedule: {e}")
                evse2_soc_schedule = {"values": [], "duration": "PT0H"}

            print("Multi-device power and SoC schedules retrieved successfully")

        except Exception as e:
            error_msg = str(e)
            print(f"Scheduling failed: {error_msg}")

            # Continue simulation with zero power for all devices
            schedule_result = [
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["battery-power"]["id"],
                    "duration": f"PT{SIMULATION_STEP_HOURS}H",
                },
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["evse1-power"]["id"],
                    "duration": f"PT{SIMULATION_STEP_HOURS}H",
                },
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["evse2-power"]["id"],
                    "duration": f"PT{SIMULATION_STEP_HOURS}H",
                },
            ]

            # Set empty SoC schedules for error case
            battery_soc_schedule = {"values": [], "duration": "PT0H"}
            evse1_soc_schedule = {"values": [], "duration": "PT0H"}
            evse2_soc_schedule = {"values": [], "duration": "PT0H"}

        # Extract scheduled power for all devices for the next 4 hours
        step_end_time = current_time + timedelta(hours=SIMULATION_STEP_HOURS)

        # Initialize power schedules
        battery_scheduled_power = [0.0] * SIMULATION_STEP_HOURS
        evse1_scheduled_power = [0.0] * SIMULATION_STEP_HOURS
        evse2_scheduled_power = [0.0] * SIMULATION_STEP_HOURS

        if isinstance(schedule_result, list) and len(schedule_result) >= 3:
            # Extract schedules for each device
            print("[SCHEDULE-DEBUG] === SCHEDULE RESULTS ===")
            for i, schedule in enumerate(schedule_result):
                sensor_id = schedule["sensor"]
                resolution_in_hours = (
                    pd.Timedelta(schedule["duration"])
                    // pd.Timedelta(hours=1)
                    / len(schedule["values"])
                )
                power_values = schedule["values"][
                    : int(SIMULATION_STEP_HOURS / resolution_in_hours)
                ]

                # Find which sensor this is for logging
                sensor_name = "Unknown"
                if sensor_id == sensors["battery-power"]["id"]:
                    battery_scheduled_power = power_values
                    sensor_name = "Battery"
                elif sensor_id == sensors["evse1-power"]["id"]:
                    evse1_scheduled_power = power_values
                    sensor_name = "EVSE-1"
                elif sensor_id == sensors["evse2-power"]["id"]:
                    evse2_scheduled_power = power_values
                    sensor_name = "EVSE-2"

                print(
                    f"[SCHEDULE] {sensor_name} (sensor {sensor_id}): {power_values} kW"
                )

            print(f"[SCHEDULE-DEBUG] Current time: {current_time}")
            print(f"[SCHEDULE-DEBUG] Step duration: {SIMULATION_STEP_HOURS} hours")
            print()

        # Upload measurements for the simulation step
        try:
            # Upload battery power
            battery_power_duration = timedelta(hours=SIMULATION_STEP_HOURS)
            await client.post_sensor_data(
                sensor_id=sensors["battery-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
                prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                values=battery_scheduled_power,
                unit="kW",
            )
        

            # Upload EVSE 1 power
            await client.post_sensor_data(
                sensor_id=sensors["evse1-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
                prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                values=evse1_scheduled_power,
                unit="kW",
            )

            # Upload EVSE 2 power
            await client.post_sensor_data(
                sensor_id=sensors["evse2-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
                prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                values=evse2_scheduled_power,
                unit="kW",
            )

            # Upload building consumption for this period
            building_data_step = building_df[
                (building_df["event_start"] >= current_time)
                & (building_df["event_start"] < step_end_time)
            ]

            if not building_data_step.empty:
                step_duration = pd.Timedelta(
                    (
                        building_data_step["event_start"].iloc[-1]
                        - building_data_step["event_start"].iloc[0]
                    )
                    + pd.Timedelta(minutes=15)
                )
                await client.post_sensor_data(
                    sensor_id=sensors["building-consumption"]["id"],
                    start=building_data_step["event_start"].iloc[0],
                    duration=step_duration,
                    prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                    values=building_data_step["event_value"].tolist(),
                    unit="kW",
                )

            # Upload PV production for this period
            pv_data_step = pv_df[
                (pv_df["event_start"] >= current_time)
                & (pv_df["event_start"] < step_end_time)
            ]

            if not pv_data_step.empty:
                await client.post_sensor_data(
                    sensor_id=sensors["pv-production"]["id"],
                    start=pv_data_step["event_start"].iloc[0],
                    duration=timedelta(hours=len(pv_data_step)),
                    prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                    values=pv_data_step["event_value"].tolist(),
                    unit="kWh",
                )

            # Upload FlexMeasures-computed SoC schedules to sensor data
            # Extract SoC values for the current simulation step
            battery_resolution_in_hours = (
                pd.Timedelta(battery_soc_schedule["duration"])
                // pd.Timedelta(hours=1)
                / (len(battery_soc_schedule["values"]) - 1)
            )
            evse1_resolution_in_hours = (
                pd.Timedelta(evse1_soc_schedule["duration"])
                // pd.Timedelta(hours=1)
                / (len(evse1_soc_schedule["values"]) - 1)
            )
            evse2_resolution_in_hours = (
                pd.Timedelta(evse2_soc_schedule["duration"])
                // pd.Timedelta(hours=1)
                / (len(evse2_soc_schedule["values"]) - 1)
            )
            battery_soc_values = (
                battery_soc_schedule["values"][
                    : int(SIMULATION_STEP_HOURS / battery_resolution_in_hours)
                ]
                if battery_soc_schedule.get("values")
                else []
            )
            battery_next_current_soc = battery_soc_schedule["values"][
                int(SIMULATION_STEP_HOURS / battery_resolution_in_hours)
            ]
            evse1_next_current_soc = evse1_soc_schedule["values"][
                int(SIMULATION_STEP_HOURS / evse1_resolution_in_hours)
            ]
            evse2_next_current_soc = evse2_soc_schedule["values"][
                int(SIMULATION_STEP_HOURS / evse2_resolution_in_hours)
            ]
            evse1_soc_values = (
                evse1_soc_schedule["values"][
                    : int(SIMULATION_STEP_HOURS / evse1_resolution_in_hours)
                ]
                if evse1_soc_schedule.get("values")
                else []
            )
            evse2_soc_values = (
                evse2_soc_schedule["values"][
                    : int(SIMULATION_STEP_HOURS / evse2_resolution_in_hours)
                ]
                if evse2_soc_schedule.get("values")
                else []
            )

            # Upload battery SoC measurements (FlexMeasures computed)
            if battery_soc_values:
                await client.post_sensor_data(
                    sensor_id=sensors["battery-soc"]["id"],
                    start=current_time,
                    duration=pd.Timedelta(hours=SIMULATION_STEP_HOURS).isoformat(),
                    prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                    values=battery_soc_values,
                    unit="kWh",
                )
                print(
                    f"[BATTERY-SOC] Uploaded {len(battery_soc_values)} FlexMeasures-computed SoC values"
                )

            # Upload EVSE 1 SoC measurements (FlexMeasures computed)
            if evse1_soc_values:
                await client.post_sensor_data(
                    sensor_id=sensors["evse1-soc"]["id"],
                    start=current_time,
                    duration=pd.Timedelta(hours=SIMULATION_STEP_HOURS).isoformat(),
                    prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                    values=evse1_soc_values,
                    unit="kWh",
                )
                print(
                    f"[EVSE1-SOC] Uploaded {len(evse1_soc_values)} FlexMeasures-computed SoC values"
                )

            # Upload EVSE 2 SoC measurements (FlexMeasures computed)
            if evse2_soc_values:
                await client.post_sensor_data(
                    sensor_id=sensors["evse2-soc"]["id"],
                    start=current_time,
                    duration=pd.Timedelta(hours=SIMULATION_STEP_HOURS).isoformat(),
                    prior=current_time + timedelta(hours=SIMULATION_STEP_HOURS),
                    values=evse2_soc_values,
                    unit="kWh",
                )
                print(
                    f"[EVSE2-SOC] Uploaded {len(evse2_soc_values)} FlexMeasures-computed SoC values"
                )

            # Display FlexMeasures-computed SoC and power data
            print("\n[FLEXMEASURES-RESULTS] === FLEX-MODEL COMPUTED SCHEDULES ===")

            # Log power and SoC schedules for this step
            battery_average_power = (
                sum(battery_scheduled_power) / len(battery_scheduled_power)
                if battery_scheduled_power
                else 0
            )
            evse1_average_power = (
                sum(evse1_scheduled_power) / len(evse1_scheduled_power)
                if evse1_scheduled_power
                else 0
            )
            evse2_average_power = (
                sum(evse2_scheduled_power) / len(evse2_scheduled_power)
                if evse2_scheduled_power
                else 0
            )

            # Show SoC progression if available
            if battery_soc_values:
                battery_soc_start = battery_soc_values[0]
                battery_soc_end = battery_soc_values[-1]
                battery_soc_change = battery_soc_end - battery_soc_start
                print(
                    f"[BATTERY] Power: {battery_average_power:.2f} kW | SoC: {battery_soc_start:.1f} → {battery_soc_end:.1f} kWh ({battery_soc_change:+.1f} kWh)"
                )
            else:
                print(
                    f"[BATTERY] Power: {battery_average_power:.2f} kW | SoC: Not available"
                )

            if evse1_soc_values:
                evse1_soc_start = evse1_soc_values[0]
                evse1_soc_end = evse1_soc_values[-1]
                evse1_soc_change = evse1_soc_end - evse1_soc_start
                evse1_capacity = evse1_flex_model.get(
                    "capacity_kwh", EV_CONFIG["default_capacity_kwh"]
                )
                evse1_percent_end = (evse1_soc_end / evse1_capacity) * 100
                print(
                    f"[EVSE-1] Power: {evse1_average_power:.2f} kW | SoC: {evse1_soc_start:.1f} → {evse1_soc_end:.1f} kWh ({evse1_soc_change:+.1f} kWh, {evse1_percent_end:.1f}%)"
                )
            else:
                print(
                    f"[EVSE-1] Power: {evse1_average_power:.2f} kW | SoC: Not available"
                )

            if evse2_soc_values:
                evse2_soc_start = evse2_soc_values[0]
                evse2_soc_end = evse2_soc_values[-1]
                evse2_soc_change = evse2_soc_end - evse2_soc_start
                evse2_capacity = evse2_flex_model.get(
                    "capacity_kwh", EV_CONFIG["default_capacity_kwh"]
                )
                evse2_percent_end = (evse2_soc_end / evse2_capacity) * 100
                print(
                    f"[EVSE-2] Power: {evse2_average_power:.2f} kW | SoC: {evse2_soc_start:.1f} → {evse2_soc_end:.1f} kWh ({evse2_soc_change:+.1f} kWh, {evse2_percent_end:.1f}%)"
                )
            else:
                print(
                    f"[EVSE-2] Power: {evse2_average_power:.2f} kW | SoC: Not available"
                )

        except Exception as e:
            print(f"Failed to upload measurements: {e}")

        # Move to next simulation step
        current_time = step_end_time
        step_num += 1

        print(
            f"\n[STEP-COMPLETE] Step {step_num-1} completed. Next step starts at {current_time.strftime('%Y-%m-%d %H:%M')}"
        )
        print("=" * 80)

        # Add small delay between steps
        await asyncio.sleep(1)

    print("Scheduling simulation completed")
    return True


def fill_reporter_params(
    input_sensors: list[dict],
    output_sensors: list[dict] | dict,
    start: str,
    end: str,
    reporter_type: str,
):
    """Fill reporter parameters and save to JSON file."""

    if reporter_type == "aggregate":
        # For the aggregate reporter, output_sensors is a single sensor ID
        output = [{"sensor": output_sensors['id']}]
    else:
        output = [{"name": s['name'], "sensor": s['id']} for s in output_sensors]

    params = {
        "input": [
            {
                "name": name,
                "sensor": sensor,
                "exclude_source_types": ["scheduler", "forecaster"],
            }
            for sensor_dict in input_sensors
            for name, sensor in sensor_dict.items()
        ],
        "output": output,
        "start": start,
        "end": end,
        "belief_horizon": "PT0H",  # Live reporting; reports on measurements straight away (no lag)
        "check_output_resolution": False,
    }

    # overwrite the file (creates it if not exists)
    with open(f"{reporter_type}_reporter_param.json", "w") as f:
        json.dump(params, f, indent=4)


def run_report_cmd(reporter_map: dict, start: str, end: str) -> bool:
    """Run subprocess command for report and print result."""

    cmd = [
        "flexmeasures",
        "add",
        "report",
        "--reporter",
        reporter_map["reporter"],
        "--config",
        f"{reporter_map['name']}_reporter_config.json",
        "--parameters",
        f"{reporter_map['name']}_reporter_param.json",
        "--start",
        start,
        "--end",
        end,
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3000)
    if result.returncode == 0:
        print(f"{reporter_map['name']} reporters generated successfully")
        return True
    else:
        print(f"{reporter_map['name']} reporter generation failed: {result.stderr}")
        return False


async def create_reports(client: FlexMeasuresClient):
    """Generate reports using FlexMeasures CLI."""
    print("Generating reports...")

    # Check if flexmeasures CLI is available
    check_cmd = ["which", "flexmeasures"]
    check_result = subprocess.run(check_cmd, capture_output=True, text=True)

    if check_result.returncode != 0:
        print("FlexMeasures CLI not found. Skipping report generation.")
        return False

    # Find all required sensors
    sensor_mappings = [
        ("electricity-production", "electricity-production", pv_name),
        ("electricity-consumption", "electricity-consumption", building_name),
        ("electricity-power", "electricity-power", battery_name),
        ("evse1-power", "electricity-power", evse1_name),
        ("evse2-power", "electricity-power", evse2_name),
        ("electricity-aggregate", "electricity-aggregate", building_name),
        ("self-consumption", "self-consumption", building_name),
        ("electricity-price", "electricity-price", price_market_name),
        ("total-energy-costs", "total-energy-costs", building_name),
        ("daily-total-energy-costs", "daily-total-energy-costs", building_name),
        ("daily-share-of-self-consumption", "daily-share-of-self-consumption", building_name),
    ]
    sensors = await find_sensors_by_asset(client, sensor_mappings)

    # Prepare parameters for the aggregate reporter
    fill_reporter_params(
        input_sensors=[
            {"pv": sensors["electricity-production"]["id"]},
            {"consumption": sensors["electricity-consumption"]["id"]},
            {"battery-power": sensors["electricity-power"]["id"]},
            {"evse1-power": sensors["evse1-power"]["id"]},
            {"evse2-power": sensors["evse2-power"]["id"]},
        ],
        output_sensors=sensors["electricity-aggregate"],
        start=SCHEDULING_START,
        end=SCHEDULING_END,
        reporter_type="aggregate",
    )

    # Prepare parameters for self-consumption reporter
    fill_reporter_params(
        input_sensors=[
            {"production": sensors["electricity-production"]["id"]},
            {"building-consumption": sensors["electricity-consumption"]["id"]},
            {"evse1-consumption": sensors["evse1-power"]["id"]},
            {"evse2-consumption": sensors["evse2-power"]["id"]},
            {"battery-power": sensors["electricity-power"]["id"]},
        ],
        output_sensors=[sensors["self-consumption"], sensors["daily-share-of-self-consumption"]],
        start=SCHEDULING_START,
        end=SCHEDULING_END,
        reporter_type="self-consumption",
    )

    # Prepare parameters for the total energy costs reporter
    fill_reporter_params(
        input_sensors=[
            {"aggregate-power": sensors["electricity-aggregate"]["id"]},
            {"consumption-production-price": sensors["electricity-price"]["id"]},
        ],
        output_sensors=[
            sensors["total-energy-costs"],
            sensors["daily-total-energy-costs"],
        ],
        start=SCHEDULING_START,
        end=SCHEDULING_END,
        reporter_type="total-energy-costs",
    )

    # Run AggregatorReporter
    aggregate_result = run_report_cmd(
        reporter_map={"name": "aggregate", "reporter": "AggregatorReporter"},
        start=SCHEDULING_START,
        end=SCHEDULING_END,
    )

    # Run SelfConsumptionReporter
    self_consumption_result = run_report_cmd(
        reporter_map={"name": "self-consumption", "reporter": "PandasReporter"},
        start=SCHEDULING_START,
        end=SCHEDULING_END,
    )

    # Run TotalEnergyCostsReporter
    total_energy_costs_result = run_report_cmd(
        reporter_map={"name": "total-energy-costs", "reporter": "PandasReporter"},
        start=SCHEDULING_START,
        end=SCHEDULING_END,
    )

    return self_consumption_result and aggregate_result and total_energy_costs_result


async def main():
    """
    Complete HEMS setup using FlexMeasures client.

    Creates a comprehensive home energy management structure including:
    - Public price sensor for electricity costs
    - Building asset with consumption and energy cost KPI sensors
    - PV asset (child of building) with production sensor
    - Battery asset (child of building) with power and SoC sensors + settings
    - Weather station with irradiation and cloud coverage sensors
    - Comprehensive flex-context configuration
    - Graph configuration for building asset
    """

    print("Starting FlexMeasures HEMS")
    print("=" * 50)

    # NOTE: Account and admin user creation must be done via FlexMeasures CLI first:
    # flexmeasures add account --name "MyCompany"
    # flexmeasures add user --username admin@mycompany.io --account-id 2 --roles admin

    client = FlexMeasuresClient(email=usr, password=pwd, host=host)

    try:
        # Get user account information
        account = await client.get_account()
        if not account:
            raise Exception("No account found. Please create an account first.")

        account_id = account["id"]
        print(f" Connected to account: {account['name']} (ID: {account_id})")

        # Clean up existing assets first
        await cleanup_existing_assets(client=client, account_id=account_id)

        asset = None  # Initialize asset variable
        assets = await client.get_assets()
        for sst in assets:
            if sst["name"] == building_name:
                asset = sst
                break

        if not asset:
            print(
                "Creating building asset, with PV and battery sensors, and weather station"
            )
            await create_building_assets_and_sensors(client, account)
        else:
            answer = input(f"Asset '{building_name}' already exists. Re-create?")
            if answer.lower() in ["y", "yes"]:
                await client.delete_asset(asset_id=asset["id"])
                await create_building_assets_and_sensors(client, account)
            else:
                print("Assets already exist, skipping to data upload")

        # Part 2: Upload data for first two weeks
        print("\n" + "=" * 50)
        print("PART 2: UPLOADING DATA")
        await upload_data_for_first_two_weeks(client)

        # Part 3: Generate PV forecasts for second week
        print("\n" + "=" * 50)
        print("PART 3: GENERATING PV FORECASTS")
        await generate_forecasts(client, asset_name=pv_name, sensor_name="electricity-production", regressors=[("irradiation", weather_station_name)])
        await generate_forecasts(client, asset_name=building_name, sensor_name="electricity-consumption")

        # Part 4: Run scheduling simulation for third week
        print("\n" + "=" * 50)
        print("PART 4: SCHEDULING SIMULATION")
        await run_scheduling_simulation(client)

        # Part 5 : Create reports
        print("\n" + "=" * 50)
        print("PART 5: CREATING REPORTS")
        await create_reports(client)
        print("\n" + "=" * 50)
        print("HEMS Tutorial completed successfully!")

    except Exception as e:
        print(f" Error during setup: {e}")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
