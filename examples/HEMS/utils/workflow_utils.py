from __future__ import annotations

import json

from const import price_market_name, weather_station_name

from flexmeasures_client import FlexMeasuresClient

WORKFLOW_ATTRIBUTE = "hems_tutorial"
WORKFLOW_VERSION = 1

ASSET_SETUP_PHASE = "asset-setup"
DATA_UPLOAD_PHASE = "historical-data-upload"
FORECASTING_PHASE = "forecasting"
SCHEDULING_PHASE = "scheduling"
REPORTING_PHASE = "reporting"

PHASE_LABELS = {
    ASSET_SETUP_PHASE: "Asset setup",
    DATA_UPLOAD_PHASE: "Historical data upload",
    FORECASTING_PHASE: "Forecast generation",
    SCHEDULING_PHASE: "Scheduling simulation",
    REPORTING_PHASE: "Report generation",
}


def get_workflow_state(community_asset: dict) -> dict | None:
    """Read a valid HEMS workflow marker from a community asset."""
    attributes = community_asset.get("attributes", {})
    if isinstance(attributes, str):
        try:
            attributes = json.loads(attributes)
        except json.JSONDecodeError:
            return None
    if not isinstance(attributes, dict):
        return None

    state = attributes.get(WORKFLOW_ATTRIBUTE)
    if not isinstance(state, dict) or state.get("workflow-version") != WORKFLOW_VERSION:
        return None
    if not isinstance(state.get("completed-phases"), list):
        return None
    if not isinstance(state.get("sensor-ids"), list):
        return None
    if not all(isinstance(sensor_id, int) for sensor_id in state["sensor-ids"]):
        return None
    if not isinstance(state.get("top-level-asset-ids"), list):
        return None
    if not all(isinstance(asset_id, int) for asset_id in state["top-level-asset-ids"]):
        return None
    if "site-names" in state and not (
        isinstance(state["site-names"], list)
        and all(isinstance(name, str) for name in state["site-names"])
    ):
        return None
    return state


async def get_site_assets(
    client: FlexMeasuresClient,
    community_asset_id: int,
    account_id: int,
) -> list[dict]:
    """Return the community's direct child sites in stable creation order."""
    assets = await client.get_assets(
        account_id=account_id,
        fields=["id", "name", "account_id", "parent_asset_id"],
        parse_json_fields=False,
    )
    return sorted(
        (
            asset
            for asset in assets
            if asset.get("account_id") == account_id
            and asset.get("parent_asset_id") == community_asset_id
        ),
        key=lambda asset: asset["id"],
    )


async def rename_site_assets(
    client: FlexMeasuresClient,
    site_assets: list[dict],
    site_names: list[str],
) -> None:
    """Rename existing sites in stable order while preserving their IDs."""
    if len(site_assets) > len(site_names):
        raise ValueError(
            f"Cannot map {len(site_assets)} existing sites to "
            f"{len(site_names)} configured site names."
        )
    for site_asset, site_name in zip(site_assets, site_names):
        if site_asset["name"] != site_name:
            await client.update_asset(
                asset_id=site_asset["id"],
                updates={"name": site_name},
                parse_json_fields=False,
            )


async def save_workflow_state(
    client: FlexMeasuresClient,
    community_asset_id: int,
    state: dict,
) -> dict:
    """Save workflow state without replacing unrelated asset attributes."""
    community_asset = await client.get_asset(
        asset_id=community_asset_id, parse_json_fields=True
    )
    attributes = community_asset.get("attributes", {})
    if not isinstance(attributes, dict):
        attributes = {}
    attributes[WORKFLOW_ATTRIBUTE] = state
    await client.update_asset(
        asset_id=community_asset_id,
        updates={"attributes": attributes},
        parse_json_fields=False,
    )
    return state


async def collect_hems_structure_ids(
    client: FlexMeasuresClient,
    community_asset: dict,
    account_id: int,
) -> tuple[list[int], list[int]]:
    """Collect the asset and sensor IDs that belong to this HEMS tutorial."""
    all_assets = await client.get_assets(
        fields=["id", "name", "account_id", "parent_asset_id"],
        parse_json_fields=False,
    )
    top_level_asset_ids = [community_asset["id"]]
    for asset_name in (price_market_name, weather_station_name):
        matching_assets = [
            asset
            for asset in all_assets
            if asset.get("name") == asset_name
            and asset.get("account_id") == account_id
            and asset.get("parent_asset_id") is None
        ]
        if len(matching_assets) != 1:
            raise LookupError(
                f"Expected one top-level HEMS asset named '{asset_name}' in "
                f"account {account_id}, found {len(matching_assets)}."
            )
        top_level_asset_ids.append(matching_assets[0]["id"])

    hems_asset_ids: set[int] = set()
    for root_id in top_level_asset_ids:
        hems_asset_ids.add(root_id)
        descendants = await client.get_assets(
            root=root_id,
            fields=["id"],
            parse_json_fields=False,
        )
        hems_asset_ids.update(asset["id"] for asset in descendants)

    sensor_ids: set[int] = set()
    for asset_id in sorted(hems_asset_ids):
        sensors = await client.get_sensors(
            asset_id=asset_id,
            parse_json_fields=False,
        )
        sensor_ids.update(sensor["id"] for sensor in sensors)

    return sorted(top_level_asset_ids), sorted(sensor_ids)


async def initialize_workflow_state(
    client: FlexMeasuresClient,
    community_asset: dict,
    account_id: int,
    site_names: list[str],
    status: str = "ready",
) -> dict:
    """Create the workflow marker after the complete asset structure exists."""
    top_level_asset_ids, sensor_ids = await collect_hems_structure_ids(
        client=client,
        community_asset=community_asset,
        account_id=account_id,
    )
    state = {
        "workflow-version": WORKFLOW_VERSION,
        "status": status,
        "completed-phases": [ASSET_SETUP_PHASE],
        "top-level-asset-ids": top_level_asset_ids,
        "sensor-ids": sensor_ids,
        "site-names": list(site_names),
    }
    return await save_workflow_state(client, community_asset["id"], state)


def phase_is_complete(state: dict, phase: str) -> bool:
    return phase in state["completed-phases"]


async def mark_phase_complete(
    client: FlexMeasuresClient,
    community_asset_id: int,
    state: dict,
    phase: str,
) -> dict:
    """Mark one successfully finished phase as complete."""
    completed_phases = list(state["completed-phases"])
    if phase not in completed_phases:
        completed_phases.append(phase)
    state = {**state, "status": "ready", "completed-phases": completed_phases}
    return await save_workflow_state(client, community_asset_id, state)


async def wipe_hems_sensor_data(
    client: FlexMeasuresClient,
    community_asset_id: int,
    state: dict,
) -> dict:
    """Delete HEMS time-series data and reset all data phase markers."""
    state = {
        **state,
        "status": "wiping",
        "completed-phases": [ASSET_SETUP_PHASE],
    }
    await save_workflow_state(client, community_asset_id, state)

    sensor_ids = state["sensor-ids"]
    print(f"Deleting time-series data from {len(sensor_ids)} HEMS sensors...")
    for sensor_id in sensor_ids:
        await client.delete_sensor_data(sensor_id, confirm_first=False)

    state = {**state, "status": "ready"}
    return await save_workflow_state(client, community_asset_id, state)
