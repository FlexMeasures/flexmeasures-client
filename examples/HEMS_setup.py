"""
Complete FlexMeasures HEMS (Home Energy Management System) setup script.
Creates a comprehensive structure with building, PV, battery, weather station assets
and all required sensors with proper flex-context configuration.
"""

import asyncio

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

    print(f"Created building asset with ID: {building_asset['id']}")
    return building_asset, consumption_sensor, energy_costs_sensor


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

    # Create production sensor (15min, kWh)
    pv_production_sensor = await client.add_sensor(
        name="electricity-production",
        event_resolution="PT15M",
        unit="kWh",
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
    )

    # Create state-of-charge sensor (15min, kWh)
    battery_soc_sensor = await client.add_sensor(
        name="state-of-charge",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=battery_asset["id"],
    )

    # Store battery settings in asset attributes
    print("Updating battery asset with settings...")
    battery_settings = {
        "soc_unit": "kWh",
        "soc_at_start": 5.0,  # 50% of 10kWh capacity
        "soc_max": 9.0,  # 90% of 10kWh capacity (setting)
        "soc_min": 1.5,  # 15% of 10kWh capacity (setting)
        "roundtrip_efficiency": 0.85,  # 85% roundtrip efficiency (setting)
        "capacity_kwh": 10.0,  # Total battery capacity
    }

    await client.update_asset(
        asset_id=battery_asset["id"], updates={"attributes": battery_settings}
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


async def configure_sensors_to_show(
    client: FlexMeasuresClient,
    building_asset,
    consumption_sensor,
    pv_production_sensor,
    battery_power_sensor,
    battery_soc_sensor,
):
    """Configure sensors_to_show for building asset graphs."""
    print("Configuring sensors to show...")

    # Configure graph displays as requested
    sensors_to_show = [
        {
            "title": "Energy Flows",
            "sensors": [
                consumption_sensor["id"],
                pv_production_sensor["id"],
                battery_power_sensor["id"],
            ],
        },
        {"title": "Battery SoC", "sensors": [battery_soc_sensor["id"]]},
    ]

    # Update building asset with sensors_to_show
    await client.update_asset(
        asset_id=building_asset["id"], updates={"sensors_to_show": sensors_to_show}
    )

    print("Sensors to show configured successfully")


async def create_asset_with_sensor(client: FlexMeasuresClient):

    account = await client.get_account()
    account_id = account["id"]
    print("Creating building asset with PV and battery sensors")
    price_sensor = await create_public_price_sensor(client)
    building_asset, consumption_sensor, energy_costs_sensor = (
        await create_building_asset(client, account_id, price_sensor["id"])
    )
    print(f"Asset ID: {building_asset['id']}")
    print(f"Sensor ID: {consumption_sensor['id']}")
    print("Creating PV asset with production sensor")
    pv_asset, pv_production_sensor = await create_pv_asset(
        client, account_id, building_asset["id"]
    )
    print(f"Asset ID: {pv_asset['id']}")
    print(f"Sensor ID: {pv_production_sensor['id']}")
    print("Creating battery asset with power and SoC sensors")
    battery_asset, battery_power_sensor, battery_soc_sensor = (
        await create_battery_asset(client, account_id, building_asset["id"])
    )
    print(f"Asset ID: {battery_asset['id']}")
    print(f"Sensor ID: {battery_power_sensor['id']}")
    print(f"Sensor ID: {battery_soc_sensor['id']}")
    print("Creating weather station with irradiation and cloud coverage sensors")
    weather_asset, irradiation_sensor, cloud_coverage_sensor = (
        await create_weather_station(client)
    )
    print(f"Asset ID: {weather_asset['id']}")
    print(f"Sensor ID: {irradiation_sensor['id']}")
    print(f"Sensor ID: {cloud_coverage_sensor['id']}")
    print("Configuring building flex-context ...")
    await configure_building_flex_context(
        client,
        building_asset,
        price_sensor,
        consumption_sensor,
        pv_production_sensor,
        battery_power_sensor,
    )
    print("Configuring sensors to show ...")
    await configure_sensors_to_show(
        client,
        building_asset,
        consumption_sensor,
        pv_production_sensor,
        battery_power_sensor,
        battery_soc_sensor,
    )


async def cleanup_existing_assets(client: FlexMeasuresClient):
    """Clean up existing HEMS assets to avoid naming conflicts."""
    print("Cleaning up existing assets...")

    # Asset names to clean up
    asset_names_to_clean = [
        building_name,
        pv_name,
        battery_name,
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

    print("Starting FlexMeasures HEMS Setup")
    print("=" * 50)

    # NOTE: Account and admin user creation must be done via FlexMeasures CLI first:
    # flexmeasures add account --name "MyCompany"
    # flexmeasures add user --username admin@mycompany.io --account-id 2 --roles admin

    client = FlexMeasuresClient(email=usr, password=pwd, host=host)

    try:
        # Get user account information
        account = await client.get_account()
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
            await create_asset_with_sensor(client)
        else:
            answer = input(f"Asset '{building_name}' already exists. Re-create?")
            if answer.lower() in ["y", "yes"]:
                await client.delete_asset(asset_id=asset["id"])
                await create_asset_with_sensor(client)
            else:
                print("Tutorial setup complete")

    except Exception as e:
        print(f" Error during setup: {e}")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
