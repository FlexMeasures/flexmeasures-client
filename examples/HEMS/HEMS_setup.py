"""
Complete FlexMeasures HEMS (Home Energy Management System) setup script.
Creates a comprehensive structure with building, PV, battery, weather station assets
and all required sensors with proper flex-context configuration.
"""

import asyncio
from typing import Callable

from assets_setup import create_community_asset
from const import COMMUNITY_NAME, SITE_NAMES, host, pwd, ssl, usr
from forecasting import generate_forecasts
from reporters import create_reports
from scheduling import just_continue, run_scheduling_simulation
from utils.asset_utils import delete_hems_assets, upload_data_for_first_two_weeks
from utils.workflow_utils import (
    DATA_UPLOAD_PHASE,
    FORECASTING_PHASE,
    PHASE_LABELS,
    REPORTING_PHASE,
    SCHEDULING_PHASE,
    ensure_workflow_state,
    initialize_workflow_state,
    mark_phase_complete,
    phase_is_complete,
    wipe_hems_sensor_data,
)

from flexmeasures_client import FlexMeasuresClient


def print_workflow_summary(state: dict) -> None:
    """Show which phases resume mode will skip and run."""
    completed = set(state["completed-phases"])
    print("\nCompleted phases:")
    for phase, label in PHASE_LABELS.items():
        if phase in completed:
            print(f"- {label}")
    print("Still to run:")
    remaining = [
        label for phase, label in PHASE_LABELS.items() if phase not in completed
    ]
    if remaining:
        for label in remaining:
            print(f"- {label}")
    else:
        print("- Nothing; the tutorial is already complete.")


def prompt_for_existing_setup(account: dict, community_name: str, state: dict) -> str:
    """Ask whether to recreate, wipe data, or resume an existing setup."""
    print(
        f"Asset '{community_name}' already exists in account "
        f"'{account['name']}' (ID: {account['id']})."
    )
    print_workflow_summary(state)
    while True:
        answer = (
            input(
                "\nChoose how to continue:\n"
                "  [y] Recreate assets — delete assets, sensors, IDs, and data.\n"
                "  [w] Wipe data — preserve the asset and sensor structure and IDs, "
                "delete all HEMS time-series data, then restart at data upload.\n"
                "  [n] Resume — preserve everything and continue from the first "
                "unfinished phase.\n"
                "Choice [y/w/N]: "
            )
            .strip()
            .lower()
        )
        if answer in {"y", "yes"}:
            return "recreate"
        if answer in {"w", "wipe"}:
            return "wipe"
        if answer in {"", "n", "no"}:
            if state.get("status") == "wiping":
                print(
                    "A previous data wipe was interrupted. Choose 'w' to resume "
                    "the wipe or 'y' to recreate the assets."
                )
                continue
            if state.get("status") == "untracked":
                print(
                    "This setup predates HEMS phase markers, so its completed "
                    "phases cannot be determined safely. Choose 'w' for fresh "
                    "data with the same IDs, or 'y' to recreate everything."
                )
                continue
            return "resume"
        print("Please choose 'y', 'w', or 'n'.")


def confirm_data_wipe(state: dict) -> bool:
    """Require explicit confirmation before deleting HEMS sensor data."""
    sensor_count = len(state["sensor-ids"])
    answer = input(
        f"This permanently deletes all time-series data from {sensor_count} "
        "HEMS sensors, including uploads, forecasts, schedules, simulated "
        "measurements, and report outputs. Asset and sensor IDs are preserved.\n"
        "Type WIPE to continue: "
    )
    return answer == "WIPE"


async def main(
    community_name: str, site_names: list[str], callback: Callable = just_continue
):
    """
    Complete HEMS setup using FlexMeasures client.

    Creates a comprehensive home energy management structure including:
    - Price sensor for electricity costs
    - Building asset with consumption and energy cost KPI sensors
    - PV asset (child of building) with production sensor
    - Battery asset (child of building) with power and SoC sensors + settings
    - Weather station with irradiation and cloud coverage sensors
    - Comprehensive flex-context configuration
    - Graph configuration for building asset
    """

    print("Starting FlexMeasures HEMS")
    print("=" * 50)

    # NOTE: Create the account and account-admin user via FlexMeasures CLI first:
    # flexmeasures add account --name "MyCompany"
    # flexmeasures add user --username hems-admin --email hems-admin@example.com \
    #     --account 2 --roles account-admin

    client = FlexMeasuresClient(email=usr, password=pwd, host=host, ssl=ssl)

    try:
        await client.ensure_minimum_server_version(
            "0.31.0",
            "The HEMS example requires a FlexMeasures server of v0.31.0 or above.",
        )

        # Get user account information
        account = await client.get_account()
        if not account:
            raise Exception("No account found. Please create an account first.")

        account_id = account["id"]
        print(f" Connected to account: {account['name']} (ID: {account_id})")

        asset = None
        assets = await client.get_assets(parse_json_fields=True)
        for sst in assets:
            if sst["name"] == community_name and sst.get("account_id") == account_id:
                asset = sst
                break

        if not asset:
            print(
                "Creating community Site asset with 2 building assets, each with PV and battery sensors, and weather station"
            )
            asset = await create_community_asset(
                client, account, community_name=community_name, site_names=site_names
            )
            state = await initialize_workflow_state(client, asset, account_id)
        else:
            state = await ensure_workflow_state(client, asset, account_id)
            action = prompt_for_existing_setup(account, community_name, state)
            if action == "recreate":
                await delete_hems_assets(
                    client=client,
                    account_id=account["id"],
                    community_name=community_name,
                    confirm_first=False,
                )
                asset = await create_community_asset(
                    client,
                    account,
                    community_name=community_name,
                    site_names=site_names,
                )
                state = await initialize_workflow_state(client, asset, account_id)
            elif action == "wipe":
                if not confirm_data_wipe(state):
                    print("Data wipe cancelled. No sensor data was deleted.")
                    return
                state = await wipe_hems_sensor_data(client, asset["id"], state)
            else:
                print("Resuming the existing HEMS setup.")

        # Part 2: Upload data for first two weeks
        print("\n" + "=" * 50)
        if phase_is_complete(state, DATA_UPLOAD_PHASE):
            print("PART 2: UPLOADING DATA (already complete; skipping)")
        else:
            print("PART 2: UPLOADING DATA")
            await upload_data_for_first_two_weeks(
                client, community_name=community_name, site_names=site_names
            )
            state = await mark_phase_complete(
                client, asset["id"], state, DATA_UPLOAD_PHASE
            )

        # Part 3: Generate PV forecasts for second week
        print("\n" + "=" * 50)
        if phase_is_complete(state, FORECASTING_PHASE):
            print("PART 3: GENERATING PV FORECASTS (already complete; skipping)")
        else:
            print("PART 3: GENERATING PV FORECASTS")
            await generate_forecasts(
                client, community_name=community_name, site_names=site_names
            )
            state = await mark_phase_complete(
                client, asset["id"], state, FORECASTING_PHASE
            )

        # Part 4: Run scheduling simulation for third week
        print("\n" + "=" * 50)
        if phase_is_complete(state, SCHEDULING_PHASE):
            print("PART 4: SCHEDULING SIMULATION (already complete; skipping)")
        else:
            print("PART 4: SCHEDULING SIMULATION")
            scheduling_succeeded = await run_scheduling_simulation(
                client,
                community_name=community_name,
                site_names=site_names,
                callback=callback,
            )
            if not scheduling_succeeded:
                raise RuntimeError("Scheduling simulation did not complete.")
            state = await mark_phase_complete(
                client, asset["id"], state, SCHEDULING_PHASE
            )

        # Part 5 : Create reports
        print("\n" + "=" * 50)
        if phase_is_complete(state, REPORTING_PHASE):
            print("PART 5: CREATING REPORTS (already complete; skipping)")
        else:
            print("PART 5: CREATING REPORTS")
            # todo B2: compute aggregate power flow for the community asset's power sensor
            reports_succeeded = await create_reports(
                client, community_name=community_name, site_names=site_names
            )
            if not reports_succeeded:
                raise RuntimeError("Report generation did not complete.")
            state = await mark_phase_complete(
                client, asset["id"], state, REPORTING_PHASE
            )
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
