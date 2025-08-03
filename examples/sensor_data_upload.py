#!/usr/bin/env python3
"""
Sensor Post Data Example

This example demonstrates how to:
1. Find an appropriate sensor for data upload
2. Create a new sensor if none exists
3. Upload data using both JSON and file methods
4. Verify the uploaded data

This is useful for automated data upload scenarios where you need to ensure
a suitable sensor exists before uploading data.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from flexmeasures_client import FlexMeasuresClient

# Enable logging to see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FlexMeasures connection details
usr = "admin@admin.com"  # Admin user email
pwd = "admin"  # Admin password
host = "127.0.0.1:5000"  # FlexMeasures host


async def find_or_create_sensor(client, sensor_name, unit, event_resolution="PT15M"):
    """
    Find a sensor with the given name and unit, or create one if it doesn't exist.

    Args:
        client: FlexMeasuresClient instance
        sensor_name: Name of the sensor to find or create
        unit: Unit of measurement (e.g., "kW", "MW", "kWh")
        event_resolution: Event resolution (default: "PT15M" for 15 minutes)

    Returns:
        dict: Sensor information
    """
    print(f"Looking for sensor: {sensor_name} ({unit})")

    try:
        # Get all sensors
        sensors = await client.get_sensors()

        # Look for existing sensor with matching name and unit
        for sensor in sensors:
            if sensor["name"] == sensor_name and sensor["unit"] == unit:
                print(
                    f"Found existing sensor: ID={sensor['id']}, Name={sensor['name']}, Unit={sensor['unit']}"
                )
                return sensor

        print(
            f"Sensor '{sensor_name}' with unit '{unit}' not found. Creating new sensor..."
        )

        # Get available assets
        assets = await client.get_assets()
        if not assets:
            raise ValueError("No assets found. Cannot create sensor without an asset.")

        # Use the first available asset
        asset = assets[0]
        print(f"Using asset: ID={asset['id']}, Name={asset['name']}")

        # Create new sensor
        new_sensor = await client.add_sensor(
            name=sensor_name,
            event_resolution=event_resolution,
            unit=unit,
            generic_asset_id=asset["id"],
        )

        print(
            f"Created new sensor: ID={new_sensor['id']}, Name={new_sensor['name']}, Unit={new_sensor['unit']}"
        )
        return new_sensor

    except Exception as e:
        print(f"Error finding/creating sensor: {e}")
        raise


async def upload_sensor_data(
    client, sensor_id, sensor_unit, data_type="json", event_resolution="PT15M"
):
    """
    Upload data to a sensor using either JSON or file upload.

    Args:
        client: FlexMeasuresClient instance
        sensor_id: ID of the sensor to upload data to
        sensor_unit: Unit of the sensor
        data_type: "json" or "file"

    Returns:
        bool: True if upload successful, False otherwise
    """
    print(f"\nUploading {data_type.upper()} data to sensor {sensor_id}")

    try:
        if data_type == "json":
            # Upload JSON data
            start_time = datetime.now(timezone.utc).replace(microsecond=0)

            # Adjust data based on sensor resolution
            if event_resolution == "PT1H":
                duration = timedelta(hours=2)
                values = [10.5, 11.2]  # 2 hourly values
            else:
                duration = timedelta(hours=1)
                values = [10.5, 11.2, 12.1, 11.8]  # 4 15-minute values

            await client.post_sensor_data(
                sensor_id=sensor_id,
                start=start_time,
                duration=duration,
                values=values,
                unit=sensor_unit,
            )
            print("JSON data uploaded successfully")

            # Verify the uploaded data
            print("Verifying uploaded data...")
            sensor_data = await client.get_sensor_data(
                sensor_id=sensor_id,
                start=start_time,
                duration=duration,
                unit=sensor_unit,
                resolution=event_resolution,
            )

            print(f"Retrieved data: {sensor_data}")
            print(f"   Expected values: {values}")
            print(f"   Retrieved values: {sensor_data.get('values', [])}")

        elif data_type == "file":
            # Upload file data
            csv_file = "examples/sensor_data.csv"

            # For sensors with 1-hour resolution, we need to adjust the data
            if event_resolution == "PT1H":
                # Create hourly data instead of 15-minute data
                start_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
                duration = timedelta(hours=2)
                values = [10.5, 11.2]  # 2 hourly values instead of 8 15-minute values

                await client.post_sensor_data(
                    sensor_id=sensor_id,
                    start=start_time,
                    duration=duration,
                    values=values,
                    unit=sensor_unit,
                )
                print("Hourly data uploaded successfully")

                # Verify the uploaded data
                print("Verifying uploaded data...")
                sensor_data = await client.get_sensor_data(
                    sensor_id=sensor_id,
                    start=start_time,
                    duration=duration,
                    unit=sensor_unit,
                    resolution="PT1H",
                )

                print(f"Retrieved data: {sensor_data}")
                print(f"   Expected values: {values}")
                print(f"   Retrieved values: {sensor_data.get('values', [])}")
                return True

            await client.post_sensor_data(
                sensor_id=sensor_id,
                file_path=csv_file,
            )
            print("File uploaded successfully")

            # Verify the uploaded data
            print("Verifying uploaded data...")
            start_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
            duration = timedelta(hours=2)

            sensor_data = await client.get_sensor_data(
                sensor_id=sensor_id,
                start=start_time,
                duration=duration,
                unit=sensor_unit,
                resolution="PT15M",
            )

            print(f"Retrieved data: {sensor_data}")
            expected_values = [10.5, 11.2, 12.1, 11.8, 10.9, 11.5, 12.3, 11.7]
            print(f"   Expected values: {expected_values}")
            print(f"   Retrieved values: {sensor_data.get('values', [])}")

        return True

    except Exception as e:
        print(f"{data_type.upper()} upload failed: {e}")
        return False


async def main():
    """Main function demonstrating smart sensor data upload."""
    print("Sensor Post Data Example")
    print("=" * 50)

    client = FlexMeasuresClient(email=usr, password=pwd, host=host)

    try:
        # Test connection
        versions = await client.get_versions()
        print(f"Connected to FlexMeasures server v{versions['server_version']}")

        # Example 1: Find or create a solar sensor
        print("\n" + "=" * 50)
        print("Example 1: Solar Sensor")
        print("=" * 50)

        solar_sensor = await find_or_create_sensor(
            client=client, sensor_name="solar", unit="kW", event_resolution="PT15M"
        )

        # Upload data to solar sensor
        await upload_sensor_data(
            client, solar_sensor["id"], solar_sensor["unit"], "json", "PT15M"
        )
        await upload_sensor_data(
            client, solar_sensor["id"], solar_sensor["unit"], "file", "PT15M"
        )

        # Example 2: Find or create a battery storage sensor
        print("\n" + "=" * 50)
        print("Example 2: Battery Storage Sensor")
        print("=" * 50)

        battery_sensor = await find_or_create_sensor(
            client=client, sensor_name="battery", unit="kWh", event_resolution="PT1H"
        )

        # Upload data to battery sensor
        await upload_sensor_data(
            client, battery_sensor["id"], battery_sensor["unit"], "json", "PT1H"
        )

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
