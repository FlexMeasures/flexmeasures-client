"""
Complete FlexMeasures HEMS (Home Energy Management System) setup script.
Creates a comprehensive structure with building, PV, battery, weather station assets
and all required sensors with proper flex-context configuration.
"""

import json
import asyncio
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
    Create a building asset with its associated sensors and linked assets (PV, battery, and weather station),
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
        building_name,  # Deleting this asset also deletes child assets (battery, PV)
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


async def find_sensors_by_asset(client: FlexMeasuresClient, sensor_mappings: list[tuple[str, str]]):
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
            sensor_id=sensors[sensor_key]['id'],
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
    """Run step-by-step scheduling simulation for the third week."""
    print("Running scheduling simulation for third week...")

    # Find required assets and sensors
    assets = await client.get_assets()

    # Find building and battery assets
    assets_by_name = {a["name"]: a for a in assets}
    building_asset = assets_by_name.get(building_name)
    battery_asset = assets_by_name.get(battery_name)

    if not building_asset:
        print("Could not find building asset for scheduling")
        return False

    if not battery_asset:
        print("Could not find battery asset for scheduling")
        return False

    # Find sensors
    sensor_mappings = [
        ("electricity-consumption", building_name),
        ("electricity-production", pv_name),
        ("electricity-power", battery_name),
        ("state-of-charge", battery_name),
        ("electricity-price", price_market_name),
    ]

    sensors = await find_sensors_by_asset(client, sensor_mappings)

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
    soc_unit = battery_flex_model.get("soc_unit")
    soc_at_start = battery_flex_model.get("soc_at_start")
    soc_max = battery_flex_model.get("soc_max")
    soc_min = battery_flex_model.get("soc_min")
    roundtrip_efficiency = battery_flex_model.get("roundtrip_efficiency")

    # Initialize simulation
    current_time = pd.to_datetime(THIRD_WEEK_START)
    end_time = pd.to_datetime(THIRD_WEEK_END)
    current_soc = soc_at_start  # Starting SoC from battery settings

    step_num = 1

    while current_time < end_time:
        print(f"Simulation step {step_num}: {current_time}")

        # Create schedule for the building
        try:
            schedule_start = current_time
            schedule_duration = timedelta(hours=FORECAST_HORIZON_HOURS)

            # Create flex model for battery
            flex_model = client.create_storage_flex_model(
                soc_unit=soc_unit,
                soc_at_start=current_soc,
                soc_max=soc_max,
                soc_min=soc_min,
                roundtrip_efficiency=roundtrip_efficiency,
            )
            flex_model["power-capacity"] = "20kW"
            # Have FlexMeasures save the SoC schedule to the SoC sensor
            flex_model["state-of-charge"] = {"sensor": sensors["state-of-charge"]["id"]}

            # Create flex context
            flex_context = {
                "inflexible-device-sensors": [
                    sensors["electricity-consumption"]["id"],
                    sensors["electricity-production"]["id"],
                ],
            }

            schedule_result = await client.trigger_and_get_schedule(
                start=schedule_start,
                duration=schedule_duration,
                flex_model=[
                    {"sensor": sensors["electricity-power"]["id"], **flex_model}
                ],
                flex_context=flex_context,
                asset_id=building_asset["id"],
            )

            print("Schedule created successfully")

        except Exception as e:
            error_msg = str(e)
            print(f"Scheduling failed: {error_msg}")

            # Continue simulation with zero battery power
            schedule_result = [
                {
                    "values": [0.0] * SIMULATION_STEP_HOURS,
                    "sensor": sensors["electricity-power"]["id"],
                }
            ]

        # Extract scheduled battery power for the next 4 hours
        step_end_time = current_time + timedelta(hours=SIMULATION_STEP_HOURS)

        if isinstance(schedule_result, list) and len(schedule_result) > 0:
            battery_schedule = schedule_result[0]
            scheduled_power = battery_schedule["values"][:SIMULATION_STEP_HOURS]
        else:
            scheduled_power = [0.0] * SIMULATION_STEP_HOURS

        # Upload measurements for the simulation step
        try:
            # Upload battery power
            battery_power_duration = timedelta(hours=SIMULATION_STEP_HOURS)
            await client.post_sensor_data(
                sensor_id=sensors["electricity-power"]["id"],
                start=current_time,
                duration=battery_power_duration,
                values=scheduled_power,
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
                    sensor_id=sensors["electricity-consumption"]["id"],
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
                    sensor_id=sensors["electricity-production"]["id"],
                    start=pv_data_step["event_start"].iloc[0],
                    duration=timedelta(hours=len(pv_data_step)),
                    values=pv_data_step["event_value"].tolist(),
                    unit="kWh",
                )

            # Calculate and update battery SoC
            average_power = (
                sum(scheduled_power) / len(scheduled_power) if scheduled_power else 0
            )
            energy_change = average_power * SIMULATION_STEP_HOURS
            new_soc = max(soc_min, min(soc_max, current_soc + energy_change * roundtrip_efficiency))

            # Upload SoC measurements
            soc_values = []
            for i in range(SIMULATION_STEP_HOURS):
                soc_values.append(
                    current_soc
                    + (new_soc - current_soc) * (i + 1) / SIMULATION_STEP_HOURS
                )

            await client.post_sensor_data(
                sensor_id=sensors["state-of-charge"]["id"],
                start=current_time,
                duration=timedelta(hours=SIMULATION_STEP_HOURS),
                values=soc_values,
                unit="kWh",
            )

            print(f"Updated SoC: {current_soc:.2f} -> {new_soc:.2f} kWh")
            current_soc = new_soc

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


async def create_reporters(
    client: FlexMeasuresClient
):
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
        ("self-consumption", building_name)
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
        reporter_type="aggregate"
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
        reporter_type="self_consumption"
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
