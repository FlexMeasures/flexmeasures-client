from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

import pytest

HEMS_DIR = Path(__file__).parents[2] / "examples" / "HEMS"
sys.path.insert(0, str(HEMS_DIR))

from HEMS_setup import (  # noqa: E402
    prompt_for_interrupted_wipe,
    prompt_for_untracked_setup,
)
from assets_setup import (  # noqa: E402
    create_community_asset,
    get_or_create_asset,
    get_or_create_sensor,
)
from utils.asset_utils import find_sensor_by_name_and_asset  # noqa: E402
from utils.workflow_utils import (  # noqa: E402
    ASSET_SETUP_PHASE,
    get_site_assets,
    get_workflow_state,
    rename_site_assets,
    wipe_hems_sensor_data,
)


class InMemoryClient:
    """Small API-shaped store for exercising idempotent HEMS asset repair."""

    def __init__(self):
        self.assets = [
            {
                "id": 1,
                "name": "Community Site",
                "account_id": 9,
                "parent_asset_id": None,
            },
            {
                "id": 2,
                "name": "Building A",
                "account_id": 9,
                "parent_asset_id": 1,
            },
        ]
        self.sensors = [
            {
                "id": 100,
                "name": "electricity-consumption",
                "generic_asset_id": 2,
            }
        ]
        self.next_asset_id = 3
        self.next_sensor_id = 101

    async def get_account(self):
        return {"id": 9, "name": "test"}

    async def get_assets(self, **kwargs):
        return [dict(asset) for asset in self.assets]

    async def get_sensors(self, asset_id, **kwargs):
        return [
            dict(sensor)
            for sensor in self.sensors
            if sensor["generic_asset_id"] == asset_id
        ]

    async def add_asset(self, **asset):
        asset = {
            **asset,
            "id": self.next_asset_id,
            "parent_asset_id": asset.get("parent_asset_id"),
        }
        self.next_asset_id += 1
        self.assets.append(asset)
        return dict(asset)

    async def add_sensor(self, **sensor):
        sensor = {**sensor, "id": self.next_sensor_id}
        self.next_sensor_id += 1
        self.sensors.append(sensor)
        return dict(sensor)

    async def update_asset(self, asset_id, updates, **kwargs):
        asset = next(asset for asset in self.assets if asset["id"] == asset_id)
        asset.update(updates)
        return dict(asset)


def test_interrupted_wipe_requires_explicit_continue():
    with patch("builtins.input", return_value="n"):
        assert prompt_for_interrupted_wipe() == "exit"
    with patch("builtins.input", return_value="c"):
        assert prompt_for_interrupted_wipe() == "continue-wipe"


def test_untracked_setup_offers_repair():
    with patch("builtins.input", return_value="c"):
        assert (
            prompt_for_untracked_setup(["Building A"], ["Building A", "Building B"])
            == "repair"
        )


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("k", "keep-names"), ("m", "rename-sites"), ("y", "recreate")],
)
def test_legacy_names_offer_user_choice(answer: str, expected: str):
    with patch("builtins.input", return_value=answer):
        assert (
            prompt_for_untracked_setup(
                ["My Home 1", "My Home 2"], ["Building A", "Building B"]
            )
            == expected
        )


def test_workflow_state_validates_recorded_ids_and_site_names():
    valid_state = {
        "workflow-version": 1,
        "status": "ready",
        "completed-phases": [ASSET_SETUP_PHASE],
        "top-level-asset-ids": [1, 2, 3],
        "sensor-ids": [10, 11],
        "site-names": ["Building A", "Building B"],
    }
    assert (
        get_workflow_state({"attributes": {"hems_tutorial": valid_state}})
        == valid_state
    )
    assert (
        get_workflow_state(
            {
                "attributes": {
                    "hems_tutorial": {**valid_state, "top-level-asset-ids": ["1"]}
                }
            }
        )
        is None
    )


@pytest.mark.asyncio
async def test_get_or_create_asset_reuses_exact_hierarchy_position():
    client = AsyncMock()
    client.get_assets.return_value = [
        {"id": 1, "name": "Building A", "account_id": 9, "parent_asset_id": 4},
        {"id": 2, "name": "Building A", "account_id": 9, "parent_asset_id": 8},
    ]

    asset = await get_or_create_asset(
        client,
        name="Building A",
        account_id=9,
        parent_asset_id=4,
        generic_asset_type_id=6,
    )

    assert asset["id"] == 1
    client.add_asset.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_sensor_only_creates_missing_sensor():
    client = AsyncMock()
    client.get_sensors.return_value = [{"id": 10, "name": "existing"}]
    client.add_sensor.return_value = {"id": 11, "name": "missing"}

    sensor = await get_or_create_sensor(
        client,
        name="missing",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=1,
    )

    assert sensor["id"] == 11
    client.add_sensor.assert_awaited_once()


@pytest.mark.asyncio
async def test_repair_completes_structure_without_replacing_existing_ids():
    client = InMemoryClient()
    existing_community = client.assets[0]

    community = await create_community_asset(
        client,
        account={"id": 9},
        community_name="Community Site",
        site_names=["Building A", "Building B"],
        community_asset=existing_community,
    )
    first_asset_count = len(client.assets)
    first_sensor_count = len(client.sensors)

    await create_community_asset(
        client,
        account={"id": 9},
        community_name="Community Site",
        site_names=["Building A", "Building B"],
        community_asset=community,
    )

    assert community["id"] == 1
    assert (
        next(asset for asset in client.assets if asset["name"] == "Building A")["id"]
        == 2
    )
    assert (
        next(
            sensor
            for sensor in client.sensors
            if sensor["name"] == "electricity-consumption"
            and sensor["generic_asset_id"] == 2
        )["id"]
        == 100
    )
    assert len(client.assets) == first_asset_count
    assert len(client.sensors) == first_sensor_count


@pytest.mark.asyncio
async def test_sensor_lookup_stays_inside_community_by_default():
    client = AsyncMock()
    client.get_asset.return_value = {
        "id": 1,
        "name": "Community Site",
        "account_id": 9,
    }
    client.get_assets.return_value = [{"id": 2, "name": "Building A"}]
    client.get_sensors.return_value = [{"id": 20, "name": "power"}]

    sensor = await find_sensor_by_name_and_asset(
        client,
        sensor_name="power",
        asset_name="Building A",
        top_level_asset_id=1,
    )

    assert sensor["id"] == 20
    client.get_assets.assert_awaited_once_with(root=1, parse_json_fields=True)


@pytest.mark.asyncio
async def test_explicit_top_level_sensor_lookup_stays_in_community_account():
    client = AsyncMock()
    client.get_asset.return_value = {
        "id": 1,
        "name": "Community Site",
        "account_id": 9,
    }
    client.get_assets.side_effect = [
        [{"id": 2, "name": "Building A", "account_id": 9}],
        [{"id": 3, "name": "Price Market", "account_id": 9}],
    ]
    client.get_sensors.return_value = [{"id": 30, "name": "electricity-price"}]

    sensor = await find_sensor_by_name_and_asset(
        client,
        sensor_name="electricity-price",
        asset_name="Price Market",
        top_level_asset_id=1,
        allow_top_level_asset=True,
    )

    assert sensor["id"] == 30
    assert client.get_assets.await_args_list == [
        call(root=1, parse_json_fields=True),
        call(account_id=9, depth=0, parse_json_fields=True),
    ]


@pytest.mark.asyncio
async def test_get_site_assets_only_returns_direct_children():
    client = AsyncMock()
    client.get_assets.return_value = [
        {"id": 3, "name": "Battery", "account_id": 9, "parent_asset_id": 2},
        {"id": 2, "name": "Building A", "account_id": 9, "parent_asset_id": 1},
        {"id": 4, "name": "Building B", "account_id": 9, "parent_asset_id": 1},
    ]

    sites = await get_site_assets(client, community_asset_id=1, account_id=9)

    assert [site["id"] for site in sites] == [2, 4]


@pytest.mark.asyncio
async def test_rename_site_assets_preserves_ids():
    client = AsyncMock()
    sites = [{"id": 20, "name": "My Home 1"}, {"id": 21, "name": "My Home 2"}]

    await rename_site_assets(client, sites, ["Building A", "Building B"])

    assert client.update_asset.await_args_list == [
        call(asset_id=20, updates={"name": "Building A"}, parse_json_fields=False),
        call(asset_id=21, updates={"name": "Building B"}, parse_json_fields=False),
    ]


@pytest.mark.asyncio
async def test_wipe_can_be_repeated_and_finishes_ready():
    client = AsyncMock()
    client.get_asset.return_value = {"id": 1, "attributes": {}}
    state = {
        "workflow-version": 1,
        "status": "wiping",
        "completed-phases": [ASSET_SETUP_PHASE],
        "top-level-asset-ids": [1, 2, 3],
        "sensor-ids": [10, 11],
        "site-names": ["Building A", "Building B"],
    }

    result = await wipe_hems_sensor_data(client, 1, state)

    assert result["status"] == "ready"
    assert client.delete_sensor_data.await_args_list == [
        call(10, confirm_first=False),
        call(11, confirm_first=False),
    ]
    assert client.update_asset.await_count == 2
