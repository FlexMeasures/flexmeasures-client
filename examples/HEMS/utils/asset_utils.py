import asyncio

import pandas as pd
from const import (
    building_name,
    heating_name,
    price_market_name,
    pv_name,
    weather_station_name,
)

from flexmeasures_client import FlexMeasuresClient


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
        ("soc-usage", "soc-usage", heating_name),
    ]

    sensors = await find_sensors_by_asset(client, sensor_mappings)

    # Upload data files directly
    data_files = [
        ("data/price_data.csv", "electricity-price", False),
        ("data/building_data.csv", "electricity-consumption", True),
        ("data/irradiation_data.csv", "irradiation", True),
        ("data/PV_production_data.csv", "electricity-production", True),
        ("data/max_consumption_capacity.csv", "max-consumption-capacity", False),
        ("data/max_production_capacity.csv", "max-production-capacity", False),
        ("data/heating_soc_usage_data.csv", "soc-usage", True),
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
