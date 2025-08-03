"""
A simple script to illustrate using the client to create & browse structure,
and to send data.
"""

import asyncio

from flexmeasures_client import FlexMeasuresClient

usr = "your-email"
pwd = "your-password"

asset_name = "My Asset"
sensor_name = "My Sensor"


async def create_asset_with_sensor(client):
    """
    Create an asset in your account, with one sensor.
    Once we have the sensor, make sure the asset shows it on its graph page.
    """
    asset = await client.add_asset(
        name=asset_name,
        latitude=40,
        longitude=50,
        generic_asset_type_id=2,
        account_id=2,
    )

    sensor = await client.add_sensor(
        name=sensor_name,
        event_resolution="PT1H",
        unit="kW",
        generic_asset_id=asset.get("id"),
    )

    asset = await client.update_asset(
        asset_id=asset["id"],
        updates={
            "flex_context": {"site-consumption-capacity": "100 kW"},  # test this also
            "sensors_to_show": [{"title": "My Graph", "sensors": [sensor["id"]]}],
        },
    )
    return asset, sensor


async def main():
    """
    We want to send data to the sensor.
    Before that, we make sure the asset (and sensor) exists.
    """
    client = FlexMeasuresClient(email=usr, password=pwd)

    asset = None
    sensor = None

    assets = await client.get_assets()
    for sst in assets:
        if sst["name"] == asset_name:
            asset = sst
            break

    if not asset:
        print("Creating asset with sensor ...")
        asset, sensor = await create_asset_with_sensor(client)
    else:
        answer = input(f"Asset '{asset_name}' already exists. Re-create?")
        if answer.lower() in ["y", "yes"]:
            await client.delete_asset(asset_id=asset["id"])
            asset, sensor = await create_asset_with_sensor(client)
        else:  # find sensor
            sensors = await client.get_sensors(asset_id=asset["id"])
            for snsr in sensors:
                if snsr["name"] == sensor_name:
                    sensor = snsr
                    break
    if not sensor:
        raise ValueError("No sensor found")

    print(f"Asset ID: {asset['id']}")
    print(f"Sensor ID: {sensor['id']}")

    await client.post_sensor_data(
        sensor_id=sensor["id"],
        start="2025-07-07T04:00:00+02:00",
        duration="PT4H",
        values=[4.5, 7, 8.3, 1],
        unit="kW",
    )

    await client.close()


asyncio.run(main())
