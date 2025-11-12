"""
Complete FlexMeasures HEMS (Home Energy Management System) setup script.
Creates a comprehensive structure with building, PV, battery, weather station assets
and all required sensors with proper flex-context configuration.
"""

import asyncio

from assets_setup import create_community_site_asset
from const import host, pwd, usr, COMMUNITY_NAME, SITE_NAMES
from forecasting import generate_forecasts
from reporters import create_reports
from scheduling import run_scheduling_simulation
from utils.asset_utils import cleanup_existing_assets, upload_data_for_first_two_weeks

from flexmeasures_client import FlexMeasuresClient


async def main(community_name: str, site_names: list[str]):
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
        await cleanup_existing_assets(client=client, account_id=account_id, site_names=site_names)

        asset = None  # Initialize asset variable
        assets = await client.get_assets()
        for sst in assets:
            if sst["name"] in community_name:
                asset = sst
                break

        if not asset:
            print(
                "Creating community Site asset with 2 building assets, each with PV and battery sensors, and weather station"
            )
            await create_community_site_asset(client, account, community_name=community_name, site_names=site_names)
            # todo A1: create 2 sites and register them as children of a community asset (config variables become lists?)
            # todo B1: the community asset should get a site-power-capacity sensor, and a flex-context with the site-power-capacity field referencing that sensor, and a power sensor
        else:
            answer = input(f"Asset '{community_name}' already exists. Re-create?")
            if answer.lower() in ["y", "yes"]:
                await client.delete_asset(asset_id=asset["id"])
                await create_community_site_asset(client, account, community_name=community_name, site_names=site_names)
            else:
                print("Assets already exist, skipping to data upload")

        # Part 2: Upload data for first two weeks
        print("\n" + "=" * 50)
        print("PART 2: UPLOADING DATA")
        # todo A2: upload data for the 2 sites as before
        # todo B3: fill the site-power-capacity sensor with a CSV file with 30 kVA (note that the sum of the sites' max_consumption_capacity / site-power-capacity is 34.5 kW, 40 kVA, respectively)
        await upload_data_for_first_two_weeks(client, community_name=community_name, site_names=site_names)

        # Part 3: Generate PV forecasts for second week
        print("\n" + "=" * 50)
        print("PART 3: GENERATING PV FORECASTS")
        # todo A3: forecast data for the 2 sites as before
        await generate_forecasts(client, site_names=site_names)

        # Part 4: Run scheduling simulation for third week
        print("\n" + "=" * 50)
        print("PART 4: SCHEDULING SIMULATION")
        # todo A4: schedule each site separately
        # todo A5: rerun tutorial and review
        # todo B4: after each scheduling step, run a reporter to save the community's aggregate power schedule to the power sensor (maybe compute this as part of the community scheduler)
        # todo B5: rerun tutorial and review: we now expect to see community capacity breaches
        # todo C1: after the reporter from B4 is finished, determine the expected breaches (use some margin setting to decide between expecting a breach or not expecting a breach; we can refine this later)
        # todo C2: if there are expected breaches, increase the site-peak-consumption-price in the flex-contexts of both sites within the period of the breach (the margin setting and the price delta should be defined in the same policy variable, e.g. `{"30 kW": 1 EUR/MWh", "5 kW": "100 EUR/MWh", "1 kW": "1000 EUR/MWh"}`)
        # todo C3: rerun tutorial and review: we now expect to see a change in community capacity breaches
        # todo C4: experiment with the community policy
        await run_scheduling_simulation(client, community_name=community_name, site_names=site_names)

        # Part 5 : Create reports
        print("\n" + "=" * 50)
        print("PART 5: CREATING REPORTS")
        # todo B2: compute aggregate power flow for the community asset's power sensor
        await create_reports(client, site_names=site_names)
        print("\n" + "=" * 50)
        print("HEMS Tutorial completed successfully!")

    except Exception as e:
        print(f" Error during setup: {e}")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    community_name = COMMUNITY_NAME
    site_names = SITE_NAMES

    asyncio.run(main(community_name=community_name, site_names=site_names))
