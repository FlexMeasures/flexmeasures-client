from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

HEMS_DIR = Path(__file__).parents[2] / "examples" / "HEMS"
MODULE_PATH = HEMS_DIR / "utils" / "workflow_utils.py"

sys.path.insert(0, str(HEMS_DIR))
try:
    module_spec = importlib.util.spec_from_file_location(
        "hems_workflow_utils", MODULE_PATH
    )
    assert module_spec is not None
    assert module_spec.loader is not None
    workflow = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(workflow)
finally:
    sys.path.remove(str(HEMS_DIR))


def make_state(**updates):
    state = {
        "workflow-version": workflow.WORKFLOW_VERSION,
        "status": "ready",
        "completed-phases": [workflow.ASSET_SETUP_PHASE],
        "top-level-asset-ids": [10, 20, 30],
        "sensor-ids": [101, 102],
    }
    return state | updates


def test_get_workflow_state_accepts_json_attributes():
    asset = {
        "attributes": (
            '{"unrelated": true, "hems_tutorial": '
            '{"workflow-version": 1, "status": "ready", '
            '"completed-phases": ["asset-setup"], '
            '"top-level-asset-ids": [10, 20, 30], '
            '"sensor-ids": [101, 102]}}'
        )
    }

    state = workflow.get_workflow_state(asset)
    # Reading a marker preserves its recorded version; only an explicit upgrade
    # changes it and invalidates phases. Version 1 must remain readable.
    assert state == make_state(**{"workflow-version": 1})
    assert workflow.state_needs_upgrade(state)


def test_get_workflow_state_rejects_unknown_version():
    asset = {
        "attributes": {
            workflow.WORKFLOW_ATTRIBUTE: make_state(**{"workflow-version": 999})
        }
    }

    assert workflow.get_workflow_state(asset) is None


@pytest.mark.asyncio
async def test_save_workflow_state_preserves_other_attributes():
    client = SimpleNamespace(
        get_asset=AsyncMock(return_value={"attributes": {"unrelated": True}}),
        update_asset=AsyncMock(return_value={}),
    )
    state = make_state()

    await workflow.save_workflow_state(client, 10, state)

    client.update_asset.assert_awaited_once_with(
        asset_id=10,
        updates={
            "attributes": {
                "unrelated": True,
                workflow.WORKFLOW_ATTRIBUTE: state,
            }
        },
        parse_json_fields=True,
    )


@pytest.mark.asyncio
async def test_initialize_workflow_state_records_exact_structure_ids():
    client = SimpleNamespace(
        get_assets=AsyncMock(
            side_effect=[
                [
                    {
                        "id": 20,
                        "name": workflow.price_market_name,
                        "account_id": 2,
                        "parent_asset_id": None,
                    },
                    {
                        "id": 30,
                        "name": workflow.weather_station_name,
                        "account_id": 2,
                        "parent_asset_id": None,
                    },
                ],
                [{"id": 10}, {"id": 11}],
                [{"id": 20}],
                [{"id": 30}],
            ]
        ),
        get_sensors=AsyncMock(
            side_effect=[
                [{"id": 101}],
                [{"id": 102}],
                [{"id": 103}],
                [{"id": 104}],
            ]
        ),
        get_asset=AsyncMock(return_value={"attributes": {}}),
        update_asset=AsyncMock(return_value={}),
    )

    state = await workflow.initialize_workflow_state(
        client,
        community_asset={"id": 10},
        account_id=2,
        site_names=["Building A", "Building B"],
        status="untracked",
    )

    assert state["status"] == "untracked"
    assert state["top-level-asset-ids"] == [10, 20, 30]
    assert state["sensor-ids"] == [101, 102, 103, 104]
    assert state["site-names"] == ["Building A", "Building B"]


@pytest.mark.asyncio
async def test_wipe_hems_sensor_data_marks_state_around_deletions():
    client = SimpleNamespace(
        ensure_minimum_server_version=AsyncMock(),
        delete_sensor_data=AsyncMock(),
        delete_sensor_annotations=AsyncMock(),
    )
    state = make_state(
        **{
            "completed-phases": list(workflow.PHASE_LABELS),
        }
    )
    saved_states = []

    async def save_state(_client, _asset_id, saved_state):
        saved_states.append(saved_state.copy())
        return saved_state

    original_save = workflow.save_workflow_state
    workflow.save_workflow_state = save_state
    try:
        result = await workflow.wipe_hems_sensor_data(client, 10, state)
    finally:
        workflow.save_workflow_state = original_save

    assert saved_states[0]["status"] == "wiping"
    assert saved_states[0]["completed-phases"] == [workflow.ASSET_SETUP_PHASE]
    assert saved_states[-1]["status"] == "ready"
    assert client.delete_sensor_data.await_args_list == [
        call(101, confirm_first=False),
        call(102, confirm_first=False),
    ]
    assert client.delete_sensor_annotations.await_args_list == [
        call(101, confirm_first=False),
        call(102, confirm_first=False),
    ]
    assert result["completed-phases"] == [workflow.ASSET_SETUP_PHASE]
