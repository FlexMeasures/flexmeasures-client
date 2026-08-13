import os
from pathlib import Path

import pandas as pd
from const import heating_name, price_market_name, pv_name, weather_station_name

from flexmeasures_client import FlexMeasuresClient

BASE_DIR = Path(__file__).parent.parent


async def find_sensor_by_name_and_asset(
    client: FlexMeasuresClient,
    sensor_name: str,
    asset_name: str,
    top_level_asset_id: int | None = None,
):
    """Find a sensor by name within a specific asset."""
    assets = await client.get_assets(
        root=top_level_asset_id
    )  # first list those that are part of the community
    assets += await client.get_assets(
        parse_json_fields=True
    )  # then list all accessible assets
    target_asset = None
    for asset in assets:
        if asset["name"] == asset_name:
            target_asset = asset
            break

    if not target_asset:
        raise LookupError(f"Asset '{asset_name}' not found")

    sensors = await client.get_sensors(asset_id=target_asset["id"])
    for sensor in sensors:
        if sensor["name"] == sensor_name:
            return sensor

    raise LookupError(f"Sensor '{sensor_name}' not found in asset '{asset_name}'")


async def upload_csv_file_to_sensor(
    client: FlexMeasuresClient,
    sensor_id: int,
    file_path: str,
    belief_time_measured_instantly: bool,
):
    """Upload CSV file directly to a sensor using file upload."""
    try:
        full_path = os.path.join(BASE_DIR, file_path)
        await client.post_sensor_data(
            sensor_id=sensor_id,
            file_path=full_path,
            belief_time_measured_instantly=belief_time_measured_instantly,  # Set belief_time immediately after event ends
        )
        print(f"Uploaded {file_path} to sensor {sensor_id}")
        return True
    except Exception as e:
        print(f"Failed to upload {file_path} to sensor {sensor_id}: {e}")
        return False


async def find_top_level_asset_id(
    client: FlexMeasuresClient,
    name: str,
) -> int:
    top_level_assets = await client.get_assets(
        depth=0, fields=["id", "name"], parse_json_fields=True
    )
    for asset in top_level_assets:
        if asset["name"] == name:
            return asset["id"]


async def find_sensors_by_asset(
    client: FlexMeasuresClient,
    sensor_mappings: list[tuple[str, str, str]],
    top_level_asset_name: str | None = None,
):
    """Find multiple sensors by name and asset name, optionally under a given top level asset."""
    top_level_asset_id = None
    if top_level_asset_name is not None:
        top_level_asset_id = await find_top_level_asset_id(client, top_level_asset_name)

    sensors = {}
    for key, sensor_name, asset_name in sensor_mappings:
        sensor = await find_sensor_by_name_and_asset(
            client, sensor_name, asset_name, top_level_asset_id
        )
        if sensor:
            sensors[key] = sensor
        else:
            raise LookupError(
                f"Could not find sensor '{sensor_name}' in asset '{asset_name}'"
            )
    return sensors


async def upload_data_for_first_two_weeks(
    client: FlexMeasuresClient, community_name: str, site_names: list[str]
):
    """Upload historical data for the first two weeks."""
    print("Uploading data for first two weeks...")

    for i, site_name in enumerate(site_names, start=1):
        # Find all required sensors
        sensor_mappings = [
            # (key, sensor name, asset name)
            ("site-power-capacity", "site-power-capacity", community_name),
            ("electricity-price", "electricity-price", price_market_name),
            ("electricity-consumption", "electricity-consumption", site_name),
            ("max-consumption-capacity", "max-consumption-capacity", site_name),
            ("max-production-capacity", "max-production-capacity", site_name),
            ("irradiation", "irradiation", weather_station_name),
            ("electricity-production", "electricity-production", pv_name + f" {i}"),
            ("soc-usage", "soc-usage", heating_name + f" {i}"),
        ]

        sensors = await find_sensors_by_asset(client, sensor_mappings, community_name)

        # Upload data files directly
        data_files = [
            ("data/site_power_capacity.csv", "site-power-capacity", False),
            ("data/price_data.csv", "electricity-price", False),
            ("data/building_data.csv", "electricity-consumption", True),
            ("data/irradiation_data.csv", "irradiation", True),
            ("data/PV_production_data.csv", "electricity-production", True),
            ("data/max_consumption_capacity.csv", "max-consumption-capacity", False),
            ("data/max_production_capacity.csv", "max-production-capacity", False),
            ("data/heating_soc_usage_data.csv", "soc-usage", True),
        ]
        if i > 1:
            data_files = data_files[
                2:
            ]  # Remove site power capacity and price datafiles to not fill them more than once
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


async def delete_hems_assets(
    client: FlexMeasuresClient,
    account_id: int,
    community_name: str,
    confirm_first: bool = True,
) -> int:
    """Delete the top-level assets belonging to this HEMS example.

    Deleting the community asset also deletes all child assets, sensors, and data.
    The price market and weather station are separate top-level assets, so they
    are deleted explicitly.
    """
    asset_names_to_delete = {
        community_name,
        weather_station_name,
        price_market_name,
    }
    top_level_assets = await client.get_assets(
        depth=0,
        fields=["id", "name", "account_id"],
        parse_json_fields=False,
    )
    assets_to_delete = [
        asset
        for asset in top_level_assets
        if asset["name"] in asset_names_to_delete
        and asset.get("account_id") == account_id
    ]

    if not assets_to_delete:
        print("No HEMS assets found in the current account.")
        return 0

    print("The following top-level HEMS assets will be deleted:")
    for asset in assets_to_delete:
        print(f"- {asset['name']} (ID: {asset['id']})")
    print("Their child assets, sensors, and time-series data will also be deleted.")

    if confirm_first:
        answer = input("Permanently delete these assets and all their data? [yN] ")
        if answer.lower() not in ["y", "yes"]:
            print("Aborting ...")
            return 0

    for asset in assets_to_delete:
        await client.delete_asset(asset_id=asset["id"], confirm_first=False)

    print(f"Deleted {len(assets_to_delete)} top-level HEMS assets.")
    return len(assets_to_delete)


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


def get_first_asset_by_name(
    assets: list[dict], name: str, account_id: int | None = 0
) -> dict | None:
    """
    :param assets:      List of dictionaries describing assets, each with at least a "name".
    :param name:        The asset name to find the first occurrence for.
    :param account_id:  Optionally, filter by account_id (a positive integer, or None for a public account).
                        To use this filter, each dictionary in `assets` should contain the "account_id", too.
                        NB the 0 default is used to signal the argument is missing (real IDs are strictly positive).
    """
    for asset in assets:
        if asset["name"] == name:
            if account_id != 0 and asset["account_id"] != account_id:
                continue
            return asset
