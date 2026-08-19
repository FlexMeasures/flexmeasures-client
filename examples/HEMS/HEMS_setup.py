"""
Complete FlexMeasures HEMS (Home Energy Management System) setup script.
Creates a comprehensive structure with building, PV, battery, weather station assets
and all required sensors with proper flex-context configuration.
"""

import asyncio
from typing import Callable

from assets_setup import create_community_asset
from const import COMMUNITY_NAME, PV_MODE, SITE_NAMES, host, pwd, ssl, usr
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
    get_site_assets,
    get_workflow_state,
    initialize_workflow_state,
    mark_phase_complete,
    phase_is_complete,
    rename_site_assets,
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
                "  [y] Recreate assets — delete assets, sensors, IDs, and data "
                "(requires typing RECREATE).\n"
                "  [w] Wipe data — preserve the asset and sensor structure and IDs, "
                "delete all HEMS time-series data, then restart at data upload "
                "(requires typing WIPE).\n"
                "  [n] Resume — preserve everything and continue from the first "
                "unfinished phase.\n"
                "  [q] Exit without making changes.\n"
                "Choice [y/w/N/q]: "
            )
            .strip()
            .lower()
        )
        if answer in {"y", "yes"}:
            return "recreate"
        if answer in {"w", "wipe"}:
            return "wipe"
        if answer in {"q", "quit", "exit"}:
            return "exit"
        if answer in {"", "n", "no"}:
            return "resume"
        print("Please choose 'y', 'w', 'n', or 'q'.")


def prompt_for_interrupted_wipe() -> str:
    """Require an explicit recovery choice after a partial data wipe."""
    print(
        "A previous data wipe was interrupted. Some sensor data may already "
        "be deleted, so normal resume is not safe."
    )
    while True:
        answer = (
            input(
                "\nChoose how to recover:\n"
                "  [c] Continue the interrupted wipe and preserve all IDs.\n"
                "  [y] Recreate assets, sensors, IDs, and data "
                "(requires typing RECREATE).\n"
                "  [q] Exit without deleting anything else.\n"
                "Choice [c/y/Q]: "
            )
            .strip()
            .lower()
        )
        if answer in {"c", "continue"}:
            return "continue-wipe"
        if answer in {"y", "yes", "recreate"}:
            return "recreate"
        if answer in {"", "q", "quit", "exit", "n", "no"}:
            return "exit"
        print("Please choose 'c', 'y', or 'q'.")


def prompt_for_untracked_setup(
    existing_site_names: list[str], configured_site_names: list[str]
) -> str:
    """Choose how to recover a setup whose asset phase was not recorded."""
    legacy_names = any(
        name not in configured_site_names for name in existing_site_names
    )
    if legacy_names:
        print(
            "This setup uses different site names than the current tutorial:\n"
            f"- Existing: {existing_site_names}\n"
            f"- Configured: {configured_site_names}"
        )
        while True:
            answer = (
                input(
                    "\nChoose how to recover the asset structure:\n"
                    "  [k] Keep the existing site names and complete missing items.\n"
                    "  [m] Rename existing sites to the configured names and "
                    "complete missing items.\n"
                    "  [y] Recreate the complete setup with new IDs "
                    "(requires typing RECREATE).\n"
                    "  [q] Exit without making changes.\n"
                    "Choice [k/m/y/Q]: "
                )
                .strip()
                .lower()
            )
            if answer in {"k", "keep"}:
                return "keep-names"
            if answer in {"m", "migrate", "rename"}:
                return "rename-sites"
            if answer in {"y", "yes", "recreate"}:
                return "recreate"
            if answer in {"", "q", "quit", "exit"}:
                return "exit"
            print("Please choose 'k', 'm', 'y', or 'q'.")

    while True:
        answer = (
            input(
                "The existing setup has no completed asset-setup marker and may "
                "be incomplete.\n"
                "  [c] Complete missing assets and sensors, preserving existing IDs.\n"
                "  [y] Recreate the complete setup with new IDs "
                "(requires typing RECREATE).\n"
                "  [q] Exit without making changes.\n"
                "Choice [c/y/Q]: "
            )
            .strip()
            .lower()
        )
        if answer in {"c", "continue", "complete", "repair"}:
            return "repair"
        if answer in {"y", "yes", "recreate"}:
            return "recreate"
        if answer in {"", "q", "quit", "exit"}:
            return "exit"
        print("Please choose 'c', 'y', or 'q'.")


def confirm_recreation(account: dict, community_name: str) -> bool:
    """Require explicit confirmation before replacing the HEMS structure."""
    answer = input(
        f"This permanently deletes the HEMS setup '{community_name}' from account "
        f"'{account['name']}' (ID: {account['id']}), including its assets, sensors, "
        "IDs, and all time-series data. The replacement assets and sensors will "
        "receive new IDs. The HEMS energy market and weather station in this "
        "account will also be replaced.\n"
        "Type RECREATE to continue: "
    )
    return answer == "RECREATE"


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

    if PV_MODE not in {"inflexible", "curtailable"}:
        raise ValueError(
            f"Unsupported PV_MODE {PV_MODE!r}; choose 'inflexible' or 'curtailable'."
        )

    # NOTE: Create the account and account-admin user via FlexMeasures CLI first:
    # flexmeasures add account --name "MyCompany"
    # flexmeasures add user --username hems-admin --email hems-admin@example.com \
    #     --account 2 --roles account-admin

    client = FlexMeasuresClient(email=usr, password=pwd, host=host, ssl=ssl)

    try:
        print(
            f"Checking server is up and on supported version ... connecting to {host} (ssl: {ssl})"
        )
        # The sign-explicit ``inflexible-consumption`` and
        # ``inflexible-production`` flex-context fields were introduced for
        # FlexMeasures 1.0. Accept its development releases for testing, too.
        await client.ensure_minimum_server_version(
            "1.0.0.dev0",
            "The HEMS example requires a FlexMeasures server from the v1.0 "
            "series or above.",
        )

        # Get user account information
        print(f"Logging in as {usr} ...")
        account = await client.get_account()
        if not account:
            raise Exception("No account found. Please create an account first.")

        account_id = account["id"]
        print(f"Connected to account: {account['name']} (ID: {account_id})")

        active_site_names = list(site_names)
        top_level_assets = await client.get_assets(
            account_id=account_id,
            depth=0,
            fields=["id", "name", "account_id", "parent_asset_id", "attributes"],
            parse_json_fields=True,
        )
        matching_communities = [
            candidate
            for candidate in top_level_assets
            if candidate.get("name") == community_name
            and candidate.get("account_id") == account_id
        ]
        if len(matching_communities) > 1:
            raise LookupError(
                f"Expected at most one top-level asset named '{community_name}', "
                f"found {len(matching_communities)}."
            )
        asset = matching_communities[0] if matching_communities else None

        if not asset:
            print(
                "Creating community Site asset with 2 building assets, each with PV and battery sensors, and weather station"
            )
            asset = await create_community_asset(
                client,
                account,
                community_name=community_name,
                site_names=active_site_names,
            )
            state = await initialize_workflow_state(
                client, asset, account_id, active_site_names
            )
        else:
            existing_site_assets = await get_site_assets(
                client, asset["id"], account_id
            )
            existing_site_names = [site["name"] for site in existing_site_assets]
            state = get_workflow_state(asset)

            if state is None or state.get("status") == "untracked":
                action = prompt_for_untracked_setup(existing_site_names, site_names)
                if action == "exit":
                    print("Exiting without making changes.")
                    return
                if action == "recreate":
                    if not confirm_recreation(account, community_name):
                        print("Recreation cancelled. No assets or data were deleted.")
                        return
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
                        site_names=active_site_names,
                    )
                else:
                    if action == "keep-names":
                        active_site_names = existing_site_names
                    elif action == "rename-sites":
                        await rename_site_assets(
                            client, existing_site_assets, active_site_names
                        )
                    asset = await create_community_asset(
                        client,
                        account,
                        community_name=community_name,
                        site_names=active_site_names,
                        community_asset=asset,
                    )
                state = await initialize_workflow_state(
                    client, asset, account_id, active_site_names
                )
            else:
                active_site_names = list(
                    state.get("site-names") or existing_site_names or site_names
                )
                if "site-names" not in state:
                    state = {**state, "site-names": active_site_names}

                if state.get("status") == "wiping":
                    action = prompt_for_interrupted_wipe()
                else:
                    action = prompt_for_existing_setup(account, community_name, state)

                if action == "exit":
                    print("Exiting without making changes.")
                    return
                if action == "recreate":
                    if not confirm_recreation(account, community_name):
                        print("Recreation cancelled. No assets or data were deleted.")
                        return
                    active_site_names = list(site_names)
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
                        site_names=active_site_names,
                    )
                    state = await initialize_workflow_state(
                        client, asset, account_id, active_site_names
                    )
                elif action in {"wipe", "continue-wipe"}:
                    if not confirm_data_wipe(state):
                        print("Data wipe cancelled. No additional data was deleted.")
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
                client, community_name=community_name, site_names=active_site_names
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
                client, community_name=community_name, site_names=active_site_names
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
                site_names=active_site_names,
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
                client, community_name=community_name, site_names=active_site_names
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
