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
TUTORIAL_START_DATE = "2025-01-01T00:00:00+00:00"
SECOND_WEEK_START = "2025-01-08T00:00:00+00:00"
FIRST_TWO_WEEKS_END = "2025-01-14T23:59:59+00:00"
THIRD_WEEK_START = "2025-01-15T00:00:00+00:00"
THIRD_WEEK_END = "2025-01-22T00:00:00+00:00"
SIMULATION_STEP_HOURS = 4
FORECAST_HORIZON_HOURS = 24

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
    return EV_WEEKLY_PATTERNS[day_of_week]


def simulate_random_trip() -> bool:
    """Simulate random shopping trips with 10% probability per simulation step."""
    import random
    return random.random() < 0.10  # 10% chance of random trip


def calculate_ev_soc_targets_and_constraints(
    current_time: pd.Timestamp, capacity_kwh: float = 60.0, has_random_trip: bool = False
) -> dict:
    """
    Calculate dynamic SoC targets and availability constraints for EV charging.
    
    Returns a dict with:
    - soc_targets: List of target SoC values with datetimes
    - soc_minima: List of minimum SoC constraints during unavailable periods  
    - consumption_capacity: Availability windows (0 during unavailable periods)
    """
    needs_charging, departure_time_str, return_time_str, target_soc_percent = get_day_pattern(current_time)
    
    target_soc_kwh = (target_soc_percent / 100.0) * capacity_kwh
    min_soc_kwh = 0.20 * capacity_kwh  # Always maintain 20% minimum
    
    constraints = {
        "soc_targets": [],
        "soc_minima": [],
        "consumption_capacity": [],
    }
    
    if needs_charging and departure_time_str and return_time_str:
        # Work day - need to be charged by departure time
        departure_hour, departure_minute = map(int, departure_time_str.split(":"))
        return_hour, return_minute = map(int, return_time_str.split(":"))
        
        # Target: charged to 80% by departure time
        departure_datetime = current_time.replace(
            hour=departure_hour, minute=departure_minute, second=0, microsecond=0
        )
        
        # If departure is already past today, target tomorrow
        if departure_datetime <= current_time:
            departure_datetime += pd.Timedelta(days=1)
            
        constraints["soc_targets"] = [
            {"datetime": departure_datetime.isoformat(), "value": f"{target_soc_kwh} kWh"}
        ]
        
        # Unavailable period: departure time to return time  
        return_datetime = departure_datetime.replace(hour=return_hour, minute=return_minute)
        
        # Disable charging during unavailable period by setting consumption capacity to 0
        constraints["consumption_capacity"] = [
            {
                "start": departure_datetime.isoformat(),
                "end": return_datetime.isoformat(), 
                "value": "0 kW"
            }
        ]
        
        # Maintain minimum SoC during unavailable period
        constraints["soc_minima"] = [
            {
                "start": departure_datetime.isoformat(),
                "end": return_datetime.isoformat(),
                "value": f"{min_soc_kwh} kWh"
            }
        ]
    else:
        # Free day - just maintain target SoC by end of planning horizon
        end_of_day = current_time.replace(hour=23, minute=59, second=59, microsecond=0)
        constraints["soc_targets"] = [
            {"datetime": end_of_day.isoformat(), "value": f"{target_soc_kwh} kWh"}
        ]
    
    # Handle random trips - reduce SoC randomly to simulate unplanned usage
    if has_random_trip:
        # Random trip consumes about 10-20% battery
        import random
        trip_consumption_percent = random.uniform(0.10, 0.20)
        trip_consumption_kwh = trip_consumption_percent * capacity_kwh
        
        # Adjust targets to account for trip consumption
        for target in constraints["soc_targets"]:
            current_target_kwh = float(target["value"].split()[0])
            # Ensure we charge enough to cover the trip consumption
            adjusted_target = min(capacity_kwh, current_target_kwh + trip_consumption_kwh)
            target["value"] = f"{adjusted_target} kWh"
    
    return constraints


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
    )

    # Create cloud coverage sensor (1H, %)
    cloud_coverage_sensor = await client.add_sensor(
        name="cloud-coverage",
        event_resolution="PT1H",
        unit="%",
        generic_asset_id=weather_asset["id"],
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
    )

    # Create energy costs KPI sensor (1D resolution, EUR/kWh)
    energy_costs_sensor = await client.add_sensor(
        name="energy-costs-kpi",
        event_resolution="P1D",
        unit="EUR/kWh",
        generic_asset_id=building_asset["id"],
    )

    # Create aggregate power sensor for the building
    aggregate_sensor = await client.add_sensor(
        name="electricity-aggregate",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=building_asset["id"],
    )

    # Create max production capacity sensor for the building
    max_production_sensor = await client.add_sensor(
        name="max-production-capacity",
        event_resolution="PT1H",
        unit="kW",
        generic_asset_id=building_asset["id"],
    )

    # Create max consumption capacity sensor for the building
    max_consumption_sensor = await client.add_sensor(
        name="max-consumption-capacity",
        event_resolution="PT1H",
        unit="kW",
        generic_asset_id=building_asset["id"],
    )

    # Create self-consumption sensor for the building
    self_consumption_sensor = await client.add_sensor(
        name="self-consumption",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=building_asset["id"],
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
        attributes=dict(consumption_is_positive=True),
    )

    # Create state-of-charge sensor (0min, kWh)
    battery_soc_sensor = await client.add_sensor(
        name="state-of-charge",
        event_resolution="PT0M",
        unit="kWh",
        generic_asset_id=battery_asset["id"],
    )

    # Store battery settings in flex_model attribute (attributes["flex_model"])
    print("Updating battery asset with flex_model settings...")
    battery_settings = {
        "soc_unit": "kWh",
        "soc_at_start": 5.0,  # 50% of 10kWh capacity
        "soc_max": 9.0,  # 90% of 10kWh capacity (setting)
        "soc_min": 1.5,  # 15% of 10kWh capacity (setting)
        "roundtrip_efficiency": 0.85,  # 85% roundtrip efficiency (setting)
        "capacity_kwh": 10.0,  # Total battery capacity
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
        attributes=dict(consumption_is_positive=True),
    )

    # Create state-of-charge sensor (15min, kWh)
    evse_soc_sensor = await client.add_sensor(
        name="state-of-charge",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
    )

    # Create soc-min sensor (15min, kWh)
    evse_soc_min_sensor = await client.add_sensor(
        name="soc-min",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
    )

    # Create soc-max sensor (15min, kWh)
    evse_soc_max_sensor = await client.add_sensor(
        name="soc-max",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
    )

    # Store EVSE settings in flex_model attribute
    print(f"Updating {evse_name} asset with flex_model settings...")
    evse_settings = {
        "soc_unit": "kWh",
        "soc_at_start": 12.0,  # Start at 20% of 60kWh capacity
        "soc_max": 60.0,  # 100% of 60kWh capacity (max physical limit)
        "soc_min": 12.0,  # 20% of 60kWh capacity (typical EV minimum)
        "roundtrip_efficiency": 0.85,  # 85% roundtrip efficiency as requested
        "capacity_kwh": 60.0,  # Typical EV battery capacity
        "power_capacity_kw": 11.0,  # Typical home charging power
    }

    # Store in attributes["flex_model"]
    await client.update_asset(
        asset_id=evse_asset["id"],
        updates={"attributes": {"flex_model": evse_settings}},
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
):
    """Configure building asset with comprehensive flex-context."""
    print("Configuring building flex-context...")

    # Create flex context with all required settings
    flex_context = {
        # Price sensor reference (new format)
        "consumption-price": {"sensor": price_sensor["id"]},
        # Consumption capacity limit (not typically needed for private homes, but including as requested)
        "site-consumption-capacity": "50 kW",  # Relaxed constraint for residential
        # Relax constraints for residential use
        "relax-constraints": True,
        # Pre-configure breach prices with correct units
        # Energy price units (match electricity-price sensor): EUR/kWh
        "soc-minima-breach-price": "1000 EUR/kWh",
        "soc-maxima-breach-price": "1000 EUR/kWh",
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
    aggregate_sensor,
    self_consumption_sensor,
    max_production_sensor,
    max_consumption_sensor,
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
            ],
        },
        {
            "title": "Solar self-consumption",
            "sensors": [self_consumption_sensor["id"], pv_production_sensor["id"]],
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

    # Update building asset with sensors_to_show
    await client.update_asset(
        asset_id=building_asset["id"], updates={"sensors_to_show": sensors_to_show}
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
    evse1_asset, evse1_power_sensor, evse1_soc_sensor, evse1_soc_min_sensor, evse1_soc_max_sensor = (
        await create_evse_asset(client, account_id, building_asset["id"], evse1_name)
    )
    print(f"EVSE 1 asset ID: {evse1_asset['id']}")
    print(f"EVSE 1 power sensor ID: {evse1_power_sensor['id']}")
    print(f"EVSE 1 SoC sensor ID: {evse1_soc_sensor['id']}")
    
    evse2_asset, evse2_power_sensor, evse2_soc_sensor, evse2_soc_min_sensor, evse2_soc_max_sensor = (
        await create_evse_asset(client, account_id, building_asset["id"], evse2_name)
    )
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
        client,
        building_asset,
        price_sensor,
        consumption_sensor,
        pv_production_sensor,
        battery_power_sensor,
    )
    print("Configuring building dashboard ...")
    await configure_building_dashboard(
        client,
        building_asset,
        consumption_sensor,
        pv_production_sensor,
        battery_power_sensor,
        battery_soc_sensor,
        aggregate_sensor,
        self_consumption_sensor,
        max_production_sensor,
        max_consumption_sensor,
    )


async def cleanup_existing_assets(client: FlexMeasuresClient):
    """Clean up existing HEMS assets to avoid naming conflicts."""
    print("Cleaning up existing assets...")

    # Asset names to clean up
    asset_names_to_clean = [
        building_name,  # Deleting this asset also deletes child assets (battery, PV, EVSEs)
        weather_station_name,
        price_market_name,
        evse1_name,  # Clean up EVSE assets (though they'll be deleted with building)
        evse2_name,
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
):
    """Upload CSV file directly to a sensor using file upload."""
    try:
        await client.post_sensor_data(
            sensor_id=sensor_id,
            file_path=file_path,
            belief_time_measured_instantly=True,  # Set belief_time immediately after event ends
        )
        print(f"Uploaded {file_path} to sensor {sensor_id}")
        return True
    except Exception as e:
        print(f"Failed to upload {file_path} to sensor {sensor_id}: {e}")
        return False


async def find_sensors_by_asset(
    client: FlexMeasuresClient, sensor_mappings: list[tuple[str, str]]
):
    """Find multiple sensors by name and asset name."""
    sensors = {}
    for sensor_name, asset_name in sensor_mappings:
        sensor = await find_sensor_by_name_and_asset(client, sensor_name, asset_name)
        if sensor:
            sensors[sensor_name] = sensor
        else:
            print(f"Could not find sensor '{sensor_name}' in asset '{asset_name}'")
            return False
    return sensors


async def upload_data_for_first_two_weeks(client: FlexMeasuresClient):
    """Upload historical data for the first two weeks."""
    print("Uploading data for first two weeks...")

    # Find all required sensors
    sensor_mappings = [
        ("electricity-price", price_market_name),
        ("electricity-consumption", building_name),
        ("max-consumption-capacity", building_name),
        ("max-production-capacity", building_name),
        ("irradiation", weather_station_name),
        ("electricity-production", pv_name),
    ]

    sensors = await find_sensors_by_asset(client, sensor_mappings)

    # Upload data files directly
    data_files = [
        ("HEMS data/price_data.csv", "electricity-price"),
        ("HEMS data/building_data.csv", "electricity-consumption"),
        ("HEMS data/irradiation_data.csv", "irradiation"),
        ("HEMS data/PV_production_data.csv", "electricity-production"),
        ("HEMS data/max_consumption_capacity.csv", "max-consumption-capacity"),
        ("HEMS data/max_production_capacity.csv", "max-production-capacity"),
    ]

    for file_path, sensor_key in data_files:
        if sensor_key not in sensors:
            print(f"Skipping {file_path} - sensor not found")
            continue

        print(f"Processing {file_path}...")

        # Upload CSV file directly
        success = await upload_csv_file_to_sensor(
            client=client,
            sensor_id=sensors[sensor_key]["id"],
            file_path=file_path,
        )

        if success:
            print(f"Successfully uploaded {sensor_key} data")
        else:
            print(f"Failed to upload {sensor_key} data")

    return True


async def generate_pv_forecasts(client: FlexMeasuresClient):
    """Generate PV forecasts using FlexMeasures CLI for the second week."""
    print("Generating PV forecasts...")

    # Check if flexmeasures CLI is available
    check_cmd = ["which", "flexmeasures"]
    check_result = subprocess.run(check_cmd, capture_output=True, text=True)

    if check_result.returncode != 0:
        print("FlexMeasures CLI not found. Skipping forecast generation.")
        return False

    # Find sensors
    pv_sensor = await find_sensor_by_name_and_asset(
        client, "electricity-production", pv_name
    )
    irradiation_sensor = await find_sensor_by_name_and_asset(
        client, "irradiation", weather_station_name
    )

    if not pv_sensor or not irradiation_sensor:
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
        str(pv_sensor["id"]),
        "--past-regressors",  # TODO: to be changed to --regressors when the sensor has irradiance forecasts
        str(irradiation_sensor["id"]),
        "--train-start",
        TUTORIAL_START_DATE,
        "--from-date",
        SECOND_WEEK_START,
        "--to-date",
        THIRD_WEEK_END,
        "--max-forecast-horizon",
        f"PT{FORECAST_HORIZON_HOURS}H",
        "--forecast-frequency",
        f"PT{SIMULATION_STEP_HOURS}H",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode == 0:
        print("PV forecasts generated successfully")
        return True
    else:
        print(f"PV forecast generation failed: {result.stderr}")
        return False


async def run_scheduling_simulation(client: FlexMeasuresClient):
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
    
    evse1_capacity = evse1_flex_model.get("capacity_kwh", 60.0)
    evse2_capacity = evse2_flex_model.get("capacity_kwh", 60.0)

    # Initialize simulation
    current_time = pd.to_datetime(THIRD_WEEK_START)
    end_time = pd.to_datetime(THIRD_WEEK_END)
    current_battery_soc = battery_soc_at_start  # Starting SoC from battery settings
    current_evse1_soc = evse1_flex_model.get("soc_at_start", 12.0)  # Start at 20%
    current_evse2_soc = evse2_flex_model.get("soc_at_start", 12.0)  # Start at 20%

    step_num = 1

    while current_time < end_time:
        print(f"Simulation step {step_num}: {current_time}")

        # Create schedule for the building with battery and EVs
        try:
            schedule_start = current_time
            schedule_duration = timedelta(hours=FORECAST_HORIZON_HOURS)

            # Create flex model for battery
            battery_flex_model = client.create_storage_flex_model(
                soc_unit=battery_soc_unit,
                soc_at_start=current_battery_soc,
                soc_max=battery_soc_max,
                soc_min=battery_soc_min,
                roundtrip_efficiency=battery_roundtrip_efficiency,
            )
            battery_flex_model["power-capacity"] = "20kW"
            # Have FlexMeasures save the SoC schedule to the SoC sensor
            battery_flex_model["state-of-charge"] = {"sensor": sensors["battery-soc"]["id"]}

            # Calculate dynamic EV constraints for current day
            current_time_ts = pd.Timestamp(current_time)
            
            # Simulate random trips for each EVSE
            evse1_has_trip = simulate_random_trip()
            evse2_has_trip = simulate_random_trip()
            
            if evse1_has_trip:
                print(f"  EVSE 1: Random trip simulated for step {step_num}")
            if evse2_has_trip:
                print(f"  EVSE 2: Random trip simulated for step {step_num}")
            
            evse1_constraints = calculate_ev_soc_targets_and_constraints(current_time_ts, evse1_capacity, evse1_has_trip)
            evse2_constraints = calculate_ev_soc_targets_and_constraints(current_time_ts, evse2_capacity, evse2_has_trip)
            
            # Create flex models for EVSE 1
            evse1_scheduler_flex_model = client.create_storage_flex_model(
                soc_unit="kWh",
                soc_at_start=current_evse1_soc,
                soc_max=evse1_capacity,  # Physical max capacity
                soc_min=evse1_capacity * 0.20,  # 20% minimum
                roundtrip_efficiency=evse1_flex_model.get("roundtrip_efficiency", 0.85),
            )
            evse1_scheduler_flex_model["power-capacity"] = "11kW"  # Typical home charging
            evse1_scheduler_flex_model["state-of-charge"] = {"sensor": sensors["evse1-soc"]["id"]}
            
            # Add dynamic constraints for EVSE 1
            if evse1_constraints["soc_targets"]:
                evse1_scheduler_flex_model["soc-targets"] = evse1_constraints["soc_targets"]
            if evse1_constraints["soc_minima"]:
                evse1_scheduler_flex_model["soc-minima"] = evse1_constraints["soc_minima"]
            if evse1_constraints["consumption_capacity"]:
                evse1_scheduler_flex_model["consumption-capacity"] = evse1_constraints["consumption_capacity"]

            # Create flex models for EVSE 2 (similar pattern, could be different car)
            evse2_scheduler_flex_model = client.create_storage_flex_model(
                soc_unit="kWh",
                soc_at_start=current_evse2_soc,
                soc_max=evse2_capacity,  # Physical max capacity  
                soc_min=evse2_capacity * 0.20,  # 20% minimum
                roundtrip_efficiency=evse2_flex_model.get("roundtrip_efficiency", 0.85),
            )
            evse2_scheduler_flex_model["power-capacity"] = "11kW"  # Typical home charging
            evse2_scheduler_flex_model["state-of-charge"] = {"sensor": sensors["evse2-soc"]["id"]}
            
            # For simplicity, EVSE 2 follows same pattern as EVSE 1 (could be differentiated)
            if evse2_constraints["soc_targets"]:
                evse2_scheduler_flex_model["soc-targets"] = evse2_constraints["soc_targets"]
            if evse2_constraints["soc_minima"]:
                evse2_scheduler_flex_model["soc-minima"] = evse2_constraints["soc_minima"]
            if evse2_constraints["consumption_capacity"]:
                evse2_scheduler_flex_model["consumption-capacity"] = evse2_constraints["consumption_capacity"]

            # Create flex context for all devices
            flex_context = {
                "inflexible-device-sensors": [
                    sensors["building-consumption"]["id"],
                    sensors["pv-production"]["id"],
                ],
            }

            # Schedule multiple flexible devices simultaneously
            schedule_result = await client.trigger_and_get_schedule(
                start=schedule_start,
                duration=schedule_duration,
                flex_model=[
                    {"sensor": sensors["battery-power"]["id"], **battery_flex_model},
                    {"sensor": sensors["evse1-power"]["id"], **evse1_scheduler_flex_model},
                    {"sensor": sensors["evse2-power"]["id"], **evse2_scheduler_flex_model},
                ],
                flex_context=flex_context,
                asset_id=building_asset["id"],
            )

            print("Multi-device schedule created successfully")

        except Exception as e:
            error_msg = str(e)
            print(f"Scheduling failed: {error_msg}")

            # Continue simulation with zero power for all devices
            schedule_result = [
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["battery-power"]["id"],
                },
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["evse1-power"]["id"],
                },
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["evse2-power"]["id"],
                },
            ]

        # Extract scheduled power for all devices for the next 4 hours
        step_end_time = current_time + timedelta(hours=SIMULATION_STEP_HOURS)

        # Initialize power schedules
        battery_scheduled_power = [0.0] * SIMULATION_STEP_HOURS
        evse1_scheduled_power = [0.0] * SIMULATION_STEP_HOURS
        evse2_scheduled_power = [0.0] * SIMULATION_STEP_HOURS

        if isinstance(schedule_result, list) and len(schedule_result) >= 3:
            # Extract schedules for each device
            for schedule in schedule_result:
                sensor_id = schedule["sensor"]
                power_values = schedule["values"][:SIMULATION_STEP_HOURS]
                
                if sensor_id == sensors["battery-power"]["id"]:
                    battery_scheduled_power = power_values
                elif sensor_id == sensors["evse1-power"]["id"]:
                    evse1_scheduled_power = power_values  
                elif sensor_id == sensors["evse2-power"]["id"]:
                    evse2_scheduled_power = power_values

        # Upload measurements for the simulation step
        try:
            # Upload battery power
            battery_power_duration = timedelta(hours=SIMULATION_STEP_HOURS)
            await client.post_sensor_data(
                sensor_id=sensors["battery-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
                values=battery_scheduled_power,
                unit="kW",
            )

            # Upload EVSE 1 power
            await client.post_sensor_data(
                sensor_id=sensors["evse1-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
                values=evse1_scheduled_power,
                unit="kW",
            )

            # Upload EVSE 2 power
            await client.post_sensor_data(
                sensor_id=sensors["evse2-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
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
                    values=pv_data_step["event_value"].tolist(),
                    unit="kWh",
                )

            # Calculate and update battery SoC
            battery_average_power = (
                sum(battery_scheduled_power) / len(battery_scheduled_power) if battery_scheduled_power else 0
            )
            battery_energy_change = battery_average_power * SIMULATION_STEP_HOURS
            new_battery_soc = max(
                battery_soc_min,
                min(battery_soc_max, current_battery_soc + battery_energy_change * battery_roundtrip_efficiency),
            )

            # Calculate and update EVSE 1 SoC
            evse1_average_power = (
                sum(evse1_scheduled_power) / len(evse1_scheduled_power) if evse1_scheduled_power else 0
            )
            evse1_energy_change = evse1_average_power * SIMULATION_STEP_HOURS
            evse1_efficiency = evse1_flex_model.get("roundtrip_efficiency", 0.85)
            new_evse1_soc = max(
                evse1_capacity * 0.20,  # 20% minimum
                min(evse1_capacity, current_evse1_soc + evse1_energy_change * evse1_efficiency),
            )

            # Calculate and update EVSE 2 SoC
            evse2_average_power = (
                sum(evse2_scheduled_power) / len(evse2_scheduled_power) if evse2_scheduled_power else 0
            )
            evse2_energy_change = evse2_average_power * SIMULATION_STEP_HOURS
            evse2_efficiency = evse2_flex_model.get("roundtrip_efficiency", 0.85)
            new_evse2_soc = max(
                evse2_capacity * 0.20,  # 20% minimum
                min(evse2_capacity, current_evse2_soc + evse2_energy_change * evse2_efficiency),
            )

            # Upload battery SoC measurements
            battery_soc_values = []
            for i in range(SIMULATION_STEP_HOURS):
                battery_soc_values.append(
                    current_battery_soc
                    + (new_battery_soc - current_battery_soc) * (i + 1) / SIMULATION_STEP_HOURS
                )

            await client.post_sensor_data(
                sensor_id=sensors["battery-soc"]["id"],
                start=current_time,
                duration=timedelta(hours=SIMULATION_STEP_HOURS),
                values=battery_soc_values,
                unit="kWh",
            )

            # Upload EVSE 1 SoC measurements
            evse1_soc_values = []
            for i in range(SIMULATION_STEP_HOURS):
                evse1_soc_values.append(
                    current_evse1_soc
                    + (new_evse1_soc - current_evse1_soc) * (i + 1) / SIMULATION_STEP_HOURS
                )

            await client.post_sensor_data(
                sensor_id=sensors["evse1-soc"]["id"],
                start=current_time,
                duration=timedelta(hours=SIMULATION_STEP_HOURS),
                values=evse1_soc_values,
                unit="kWh",
            )

            # Upload EVSE 2 SoC measurements
            evse2_soc_values = []
            for i in range(SIMULATION_STEP_HOURS):
                evse2_soc_values.append(
                    current_evse2_soc
                    + (new_evse2_soc - current_evse2_soc) * (i + 1) / SIMULATION_STEP_HOURS
                )

            await client.post_sensor_data(
                sensor_id=sensors["evse2-soc"]["id"],
                start=current_time,
                duration=timedelta(hours=SIMULATION_STEP_HOURS),
                values=evse2_soc_values,
                unit="kWh",
            )

            print(f"Updated Battery SoC: {current_battery_soc:.2f} -> {new_battery_soc:.2f} kWh")
            print(f"Updated EVSE 1 SoC: {current_evse1_soc:.2f} -> {new_evse1_soc:.2f} kWh")
            print(f"Updated EVSE 2 SoC: {current_evse2_soc:.2f} -> {new_evse2_soc:.2f} kWh")
            
            # Update current SoC values for next iteration
            current_battery_soc = new_battery_soc
            current_evse1_soc = new_evse1_soc
            current_evse2_soc = new_evse2_soc

        except Exception as e:
            print(f"Failed to upload measurements: {e}")

        # Move to next simulation step
        current_time = step_end_time
        step_num += 1

        # Add small delay between steps
        await asyncio.sleep(1)

    print("Scheduling simulation completed")
    return True


def fill_reporter_params(
    input_sensors: list[dict],
    output_sensor: str,
    start: str,
    end: str,
    reporter_type: str,
):
    """Fill reporter parameters and save to JSON file."""
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
        "output": (
            [{"sensor": output_sensor}]
            if reporter_type == "aggregate"
            else [{"name": "self-consumption", "sensor": output_sensor}]
        ),
        "start": start,
        "end": end,
    }

    # overwrite the file (creates it if not exists)
    with open(f"{reporter_type}_reporter_param.json", "w") as f:
        json.dump(params, f, indent=4)


def run_reporter_cmd(reporter_map: dict, start: str, end: str) -> bool:
    """Run subprocess command for reporter and print result."""

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

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3000)
    if result.returncode == 0:
        print(f"{reporter_map['name']} reporters generated successfully")
        return True
    else:
        print(f"{reporter_map['name']} reporter generation failed: {result.stderr}")
        return False


async def create_reporters(client: FlexMeasuresClient):
    """Generate Reporters using FlexMeasures CLI."""
    print("Generating Reporters...")

    # Check if flexmeasures CLI is available
    check_cmd = ["which", "flexmeasures"]
    check_result = subprocess.run(check_cmd, capture_output=True, text=True)

    if check_result.returncode != 0:
        print("FlexMeasures CLI not found. Skipping reporter generation.")
        return False

    # Find all required sensors
    sensor_mappings = [
        ("electricity-production", pv_name),
        ("electricity-consumption", building_name),
        ("electricity-power", battery_name),
        ("electricity-aggregate", building_name),
        ("self-consumption", building_name),
    ]
    sensors = await find_sensors_by_asset(client, sensor_mappings)

    # Prepare parameters for the aggregate reporter
    fill_reporter_params(
        input_sensors=[
            {"pv": sensors["electricity-production"]["id"]},
            {"consumption": sensors["electricity-consumption"]["id"]},
            {"battery": sensors["electricity-power"]["id"]},
        ],
        output_sensor=sensors["electricity-aggregate"]["id"],
        start=THIRD_WEEK_START,
        end=THIRD_WEEK_END,
        reporter_type="aggregate",
    )

    # Prepare parameters for self-consumption reporter
    fill_reporter_params(
        input_sensors=[
            {"production": sensors["electricity-production"]["id"]},
            {"aggregate-power": sensors["electricity-aggregate"]["id"]},
        ],
        output_sensor=sensors["self-consumption"]["id"],
        start=THIRD_WEEK_START,
        end=THIRD_WEEK_END,
        reporter_type="self_consumption",
    )

    # Run AggregateReporter command
    aggregate_result = run_reporter_cmd(
        reporter_map={"name": "aggregate", "reporter": "AggregateReporter"},
        start=THIRD_WEEK_START,
        end=THIRD_WEEK_END,
    )

    # Run SelfConsumptionReporter command
    self_consumption_result = run_reporter_cmd(
        reporter_map={"name": "self_consumption", "reporter": "PandasReporter"},
        start=THIRD_WEEK_START,
        end=THIRD_WEEK_END,
    )

    return self_consumption_result and aggregate_result


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
        await cleanup_existing_assets(client)

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
        await generate_pv_forecasts(client)

        # Part 4: Run scheduling simulation for third week
        print("\n" + "=" * 50)
        print("PART 4: SCHEDULING SIMULATION")
        await run_scheduling_simulation(client)

        # Part 5 : Create reporters
        print("\n" + "=" * 50)
        print("PART 5: CREATING REPORTERS")
        await create_reporters(client)
        print("\n" + "=" * 50)
        print("HEMS Tutorial completed successfully!")

    except Exception as e:
        print(f" Error during setup: {e}")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
