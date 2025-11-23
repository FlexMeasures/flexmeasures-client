"""
Complete FlexMeasures HEMS (Home Energy Management System) setup script.
Creates a comprehensive structure with building, PV, battery, weather station assets
and all required sensors with proper flex-context configuration.
"""

import asyncio

from assets_setup import create_building_assets_and_sensors
from const import (
    building_name,
    heating_name,
    host,
    pv_name,
    pwd,
    usr,
    weather_station_name,
)
from forecasting import generate_forecasts
from reporters import create_reports
from scheduling import run_scheduling_simulation
from utils.asset_utils import cleanup_existing_assets, upload_data_for_first_two_weeks

from flexmeasures_client import FlexMeasuresClient


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
    # flexmeasures add user --username admin@admin.com --account-id 2 --roles admin

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
        await generate_forecasts(
            client,
            asset_name=pv_name,
            sensor_name="electricity-production",
            regressors=[("irradiation", weather_station_name)],
        )
        await generate_forecasts(
            client, asset_name=building_name, sensor_name="electricity-consumption"
        )
        await generate_forecasts(
            client, asset_name=heating_name, sensor_name="soc-usage"
        )

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
