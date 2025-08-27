#!/usr/bin/env python3
"""
Simple Sensor Data Upload Example

This example demonstrates how to:
1. Find an appropriate sensor for data upload
2. Create a new sensor if none exists
3. Upload data using the new post_sensor_data method

This is a simple demonstration of the new functionality.
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
    """
    print(f"Looking for sensor: {sensor_name} ({unit})")

    try:
        # Get all sensors
        sensors = await client.get_sensors()

        # Look for existing sensor with matching name and unit
        for sensor in sensors:
            if sensor["name"] == sensor_name and sensor["unit"] == unit:
                print(f"Found existing sensor: ID={sensor['id']}")
                return sensor

        print(f"Creating new sensor: {sensor_name}")
        # Get available assets
        assets = await client.get_assets()
        if not assets:
            raise ValueError("No assets found. Cannot create sensor without an asset.")

        # Use the first available asset
        asset = assets[0]

        # Create new sensor
        new_sensor = await client.add_sensor(
            name=sensor_name,
            event_resolution=event_resolution,
            unit=unit,
            generic_asset_id=asset["id"],
        )

        print(f"Created sensor: ID={new_sensor['id']}")
        return new_sensor

    except Exception as e:
        print(f"Error: {e}")
        raise


async def upload_json_data(client, sensor_id, sensor_unit, event_resolution="PT15M"):
    """
    Upload JSON data to a sensor.
    """
    try:
        start_time = datetime.now(timezone.utc).replace(microsecond=0)
        duration = timedelta(hours=1)
        values = [10.5, 11.2, 12.1, 11.8]  # 4 values for 1 hour
        print(f"Uploading {len(values)} values")

        await client.post_sensor_data(
            sensor_id=sensor_id,
            start=start_time,
            duration=duration,
            values=values,
            unit=sensor_unit,
        )
        print(f"Uploaded {len(values)} values")
        return True

    except Exception as e:
        print(f"Upload failed: {e}")
        return False


async def upload_file_data(client, sensor_id, file_path):
    """
    Upload file data to a sensor.
    """
    try:
        await client.post_sensor_data(
            sensor_id=sensor_id,
            file_path=file_path,
        )
        print(f"File uploaded: {file_path}")
        return True

    except Exception as e:
        print(f"File upload failed: {e}")
        return False


async def main():
    """Main function demonstrating sensor data upload."""
    print("Sensor Data Upload Example")

    client = FlexMeasuresClient(email=usr, password=pwd, host=host)

    try:
        # Test connection
        versions = await client.get_versions()
        print(f"Connected to FlexMeasures v{versions['server_version']}")

        solar_sensor = await find_or_create_sensor(
            client=client, sensor_name="solar", unit="kW", event_resolution="PT15M"
        )
        await upload_json_data(
            client, solar_sensor["id"], solar_sensor["unit"], "PT15M"
        )
        await upload_file_data(client, solar_sensor["id"], "examples/sensor_data.csv")
        await upload_file_data(client, solar_sensor["id"], "examples/sensor_data.xlsx")

    except Exception as e:
        print(f"Error: {e}")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
