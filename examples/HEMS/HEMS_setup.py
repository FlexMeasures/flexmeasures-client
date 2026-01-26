"""
Complete FlexMeasures HEMS (Home Energy Management System) setup script.
Creates a comprehensive structure with building, PV, battery, weather station assets
and all required sensors with proper flex-context configuration.
"""

import asyncio
from typing import Callable

from assets_setup import create_community_asset
from const import COMMUNITY_NAME, SITE_NAMES, host, pwd, usr
from forecasting import generate_forecasts
from reporters import create_reports
from scheduling import just_continue, run_scheduling_simulation
from utils.asset_utils import upload_data_for_first_two_weeks

from flexmeasures_client import FlexMeasuresClient


async def main(
    community_name: str, site_names: list[str], callback: Callable = just_continue
):
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

        asset = None  # Initialize asset variable
        assets = await client.get_assets(parse_json_fields=True)
        for sst in assets:
            if sst["name"] in community_name:
                asset = sst
                break

        if not asset:
            print(
                "Creating community Site asset with 2 building assets, each with PV and battery sensors, and weather station"
            )
            await create_community_asset(
                client, account, community_name=community_name, site_names=site_names
            )
        else:
            answer = input(f"Asset '{community_name}' already exists. Re-create?")
            if answer.lower() in ["y", "yes"]:
                await client.delete_asset(asset_id=asset["id"])
                await create_community_asset(
                    client,
                    account,
                    community_name=community_name,
                    site_names=site_names,
                )
            else:
                print("Assets already exist, skipping to data upload")

        # Part 2: Upload data for first two weeks
        print("\n" + "=" * 50)
        print("PART 2: UPLOADING DATA")
        await upload_data_for_first_two_weeks(
            client, community_name=community_name, site_names=site_names
        )

        # Part 3: Generate PV forecasts for second week
        print("\n" + "=" * 50)
        print("PART 3: GENERATING PV FORECASTS")
        await generate_forecasts(client, site_names=site_names)

        # Part 4: Run scheduling simulation for third week
        print("\n" + "=" * 50)
        print("PART 4: SCHEDULING SIMULATION")
        await run_scheduling_simulation(
            client,
            community_name=community_name,
            site_names=site_names,
            callback=callback,
        )

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
