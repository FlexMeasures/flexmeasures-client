from __future__ import annotations

from unittest.mock import patch

import pytest
from aioresponses import aioresponses

from flexmeasures_client.client import ContentTypeError, FlexMeasuresClient


@pytest.mark.asyncio
async def test_get_assets() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/",
            status=200,
            payload={
                "flexmeasures_version": "0.31.0",
                "message": "For these API versions a public endpoint is available, listing its service. For example: /api/v3_0. An authentication token can be requested at: /api/requestAuthToken",
                "status": 200,
                "versions": ["v3_0"],
            },
        )
        m.get(
            "http://localhost:5000/api/v3_0/assets?all_accessible=False&include_public=False&sort_by=id&sort_dir=asc",
            status=200,
            payload=[
                {
                    "account_id": 2,
                    "attributes": '{"capacity_in_mw": 0.5, "min_soc_in_mwh": 0.05, "max_soc_in_mwh": 0.45, "sensors_to_show": [3, 2]}',  # noqa: E501
                    "generic_asset_type_id": 5,
                    "id": 3,
                    "latitude": 52.374,
                    "longitude": 4.88969,
                    "name": "toy-battery",
                }
            ],
        )

        assets = await flexmeasures_client.get_assets(parse_json_fields=False)
        assert len(assets) == 1
        assert assets[0]["account_id"] == 2
        # Verify that attributes is still a JSON string
        assert isinstance(assets[0]["attributes"], str)
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_assets_with_json_parsing() -> None:
    """Test get_assets with parse_json_fields=True."""
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/",
            status=200,
            payload={
                "flexmeasures_version": "0.31.0",
                "message": "For these API versions a public endpoint is available, listing its service. For example: /api/v3_0. An authentication token can be requested at: /api/requestAuthToken",
                "status": 200,
                "versions": ["v3_0"],
            },
        )
        m.get(
            "http://localhost:5000/api/v3_0/assets?all_accessible=False&include_public=False&sort_by=id&sort_dir=asc",
            status=200,
            payload=[
                {
                    "account_id": 2,
                    "attributes": (
                        '{"capacity_in_mw": 0.5, "min_soc_in_mwh": 0.05, '
                        '"max_soc_in_mwh": 0.45, "sensors_to_show": [3, 2]}'
                    ),
                    "flex_context": '{"site-power-capacity": "1.5 MW"}',
                    "flex_model": '{"soc-at-start": "0.25 MWh"}',
                    "generic_asset_type_id": 5,
                    "id": 3,
                    "latitude": 52.374,
                    "longitude": 4.88969,
                    "name": "toy-battery",
                }
            ],
        )

        assets = await flexmeasures_client.get_assets(parse_json_fields=True)
        assert len(assets) == 1
        assert assets[0]["account_id"] == 2
        # Verify that JSON fields are parsed into dicts
        assert isinstance(assets[0]["attributes"], dict)
        assert assets[0]["attributes"]["capacity_in_mw"] == 0.5
        assert isinstance(assets[0]["flex_context"], dict)
        assert assets[0]["flex_context"]["site-power-capacity"] == "1.5 MW"
        assert isinstance(assets[0]["flex_model"], dict)
        assert assets[0]["flex_model"]["soc-at-start"] == "0.25 MWh"
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_asset() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/assets/3",
            status=200,
            payload={
                "account_id": 2,
                "attributes": '{"capacity_in_mw": 0.5, "min_soc_in_mwh": 0.05, "max_soc_in_mwh": 0.45, "sensors_to_show": [3, 2]}',  # noqa: E501
                "generic_asset_type_id": 5,
                "id": 3,
                "latitude": 52.374,
                "longitude": 4.88969,
                "name": "toy-battery",
            },
        )

        asset = await flexmeasures_client.get_asset(asset_id=3, parse_json_fields=False)
        assert asset["id"] == 3
        assert asset["account_id"] == 2
        assert asset["name"] == "toy-battery"
        # Verify that attributes is still a JSON string
        assert isinstance(asset["attributes"], str)
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_asset_content_type_error():
    """asset response is a list, not dict."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/assets/3",
            status=200,
            payload=[{"id": 3}],
        )
        with pytest.raises(ContentTypeError):
            await client.get_asset(asset_id=3, parse_json_fields=False)
        await client.close()


@pytest.mark.asyncio
async def test_get_asset_default_parse_json_fields_warning():
    """default parse_json_fields=None emits FutureWarning."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/assets/3",
            status=200,
            payload={
                "id": 3,
                "name": "toy-battery",
                "attributes": '{"key": "val"}',
            },
        )
        with pytest.warns(FutureWarning, match="get_asset"):
            asset = await client.get_asset(asset_id=3)
        assert asset["id"] == 3
        await client.close()


@pytest.mark.asyncio
async def test_get_assets_with_account_id():
    """account_id added to URI."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        client.server_version = "0.31.0"
        m.get(
            "http://localhost:5000/api/v3_0/assets?all_accessible=False&sort_by=id&sort_dir=asc&include_public=False&account_id=1",
            status=200,
            payload=[{"id": 1, "name": "asset1"}],
        )
        assets = await client.get_assets(account_id=1, parse_json_fields=False)
        assert len(assets) == 1
        await client.close()


@pytest.mark.asyncio
async def test_get_assets_root_depth_fields_new_server():
    """root/depth/fields params on server >= 0.31.0."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        client.server_version = "0.31.0"
        m.get(
            "http://localhost:5000/api/v3_0/assets?all_accessible=False&sort_by=id&sort_dir=asc&include_public=False&root=1&depth=2&fields=id|name",
            status=200,
            payload=[{"id": 1, "name": "asset1"}],
        )
        assets = await client.get_assets(
            root=1, depth=2, fields=["id", "name"], parse_json_fields=False
        )
        assert len(assets) == 1
        await client.close()


@pytest.mark.asyncio
async def test_get_assets_root_old_server_warning(caplog):
    """root param on server < 0.31.0 emits warning."""
    import re as _re

    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        client.server_version = "0.30.0"
        m.get(
            _re.compile(r".*assets\?.*"),
            status=200,
            payload=[{"id": 1, "name": "asset1"}],
        )
        with caplog.at_level("WARNING"):
            await client.get_assets(root=1, parse_json_fields=False)
        assert "0.31.0" in caplog.text
        await client.close()


@pytest.mark.asyncio
async def test_get_assets_content_type_error():
    """assets response is a dict, not list."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        client.server_version = "0.31.0"
        m.get(
            "http://localhost:5000/api/v3_0/assets?all_accessible=False&sort_by=id&sort_dir=asc&include_public=False",
            status=200,
            payload={"id": 1},
        )
        with pytest.raises(ContentTypeError):
            await client.get_assets(parse_json_fields=False)
        await client.close()


@pytest.mark.asyncio
async def test_get_assets_default_parse_json_fields_warning():
    """default parse_json_fields=None emits FutureWarning."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        client.server_version = "0.31.0"
        m.get(
            "http://localhost:5000/api/v3_0/assets?all_accessible=False&sort_by=id&sort_dir=asc&include_public=False",
            status=200,
            payload=[{"id": 1, "name": "asset1"}],
        )
        with pytest.warns(FutureWarning, match="get_assets"):
            assets = await client.get_assets()
        assert len(assets) == 1
        await client.close()


@pytest.mark.asyncio
async def test_get_asset_types():
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/assets/types",
            status=200,
            payload=[{"id": 1, "name": "solar", "description": "solar panel(s)"}],
        )
        result = await client.get_asset_types()
        assert len(result) == 1
        assert result[0]["name"] == "solar"
        await client.close()


@pytest.mark.asyncio
async def test_add_asset():
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/assets",
            status=201,
            payload={
                "id": 25,
                "name": "test-asset",
                "account_id": 1,
                "latitude": 52.0,
                "longitude": 4.0,
                "generic_asset_type_id": 5,
            },
        )
        asset = await client.add_asset(
            name="test-asset",
            account_id=1,
            latitude=52.0,
            longitude=4.0,
            generic_asset_type_id=5,
        )
        assert asset["id"] == 25
        assert asset["name"] == "test-asset"
        await client.close()


@pytest.mark.asyncio
async def test_add_asset_with_optional_params():
    """Optional parent_asset_id, sensors_to_show, flex_context, flex_model, attributes."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/assets",
            status=201,
            payload={
                "id": 26,
                "name": "test-asset2",
                "account_id": 1,
                "latitude": 52.0,
                "longitude": 4.0,
                "generic_asset_type_id": 5,
            },
        )
        asset = await client.add_asset(
            name="test-asset2",
            account_id=1,
            latitude=52.0,
            longitude=4.0,
            generic_asset_type_id=5,
            parent_asset_id=10,
            sensors_to_show=[1, 2],
            flex_context={"site-power-capacity": "1 MW"},
            flex_model={"soc-at-start": 50},
            attributes={"key": "val"},
        )
        assert asset["id"] == 26
        await client.close()


@pytest.mark.asyncio
async def test_update_assets():
    with aioresponses() as m:
        m.patch(
            "http://localhost:5000/api/v3_0/assets/1",
            status=200,
            payload={"testpayload": "test_payload"},
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
        )
        flexmeasures_client.access_token = "test-token"
        await flexmeasures_client.update_asset(
            asset_id=1, updates={"attributes": {"key": "value"}}
        )

        m.assert_called_once_with(
            "http://localhost:5000/api/v3_0/assets/1",
            method="PATCH",
            json={"attributes": '{"key": "value"}'},
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            params=None,
            ssl=False,
            allow_redirects=False,
        )
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_update_asset_aggregate_power_version_check(caplog):
    """Warning is issued when using 'aggregate-power' with a server < 0.31.0."""
    with aioresponses() as m:
        m.get(
            "http://localhost:5000/api/",
            status=200,
            payload={
                "flexmeasures_version": "0.30.0",
                "message": "",
                "status": 200,
                "versions": ["v3_0"],
            },
        )
        m.patch(
            "http://localhost:5000/api/v3_0/assets/1",
            status=200,
            payload={"testpayload": "test_payload"},
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
        )
        flexmeasures_client.access_token = "test-token"
        with caplog.at_level("WARNING"):
            await flexmeasures_client.update_asset(
                asset_id=1,
                updates={
                    "flex_context": {
                        "site-power-capacity": "1 MW",
                        "aggregate-power": {"sensor": 42},
                    }
                },
            )
        assert "aggregate-power" in caplog.text
        assert "0.31.0" in caplog.text
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_update_asset_aggregate_power_no_warning_on_new_server(caplog):
    """No warning is issued when using 'aggregate-power' with a server >= 0.31.0."""
    with aioresponses() as m:
        m.get(
            "http://localhost:5000/api/",
            status=200,
            payload={
                "flexmeasures_version": "0.31.0",
                "message": "",
                "status": 200,
                "versions": ["v3_0"],
            },
        )
        m.patch(
            "http://localhost:5000/api/v3_0/assets/1",
            status=200,
            payload={"testpayload": "test_payload"},
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
        )
        flexmeasures_client.access_token = "test-token"
        with caplog.at_level("WARNING"):
            await flexmeasures_client.update_asset(
                asset_id=1,
                updates={
                    "flex_context": {
                        "site-power-capacity": "1 MW",
                        "aggregate-power": {"sensor": 42},
                    }
                },
            )
        assert "aggregate-power" not in caplog.text
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_update_asset_flex_model():
    """flex_model serialized to JSON string."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.patch(
            "http://localhost:5000/api/v3_0/assets/1",
            status=200,
            payload={"id": 1, "flex_model": '{"soc-at-start": 50}'},
        )
        result = await client.update_asset(
            asset_id=1, updates={"flex_model": {"soc-at-start": 50}}
        )
        assert result["id"] == 1
        await client.close()


@pytest.mark.asyncio
async def test_update_asset_sensors_to_show():
    """sensors_to_show serialized to JSON string."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.patch(
            "http://localhost:5000/api/v3_0/assets/1",
            status=200,
            payload={"id": 1},
        )
        result = await client.update_asset(
            asset_id=1, updates={"sensors_to_show": [1, 2, 3]}
        )
        assert result["id"] == 1
        await client.close()


@pytest.mark.asyncio
async def test_update_asset_sensors_to_show_as_kpis():
    """sensors_to_show_as_kpis serialized to JSON string."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.patch(
            "http://localhost:5000/api/v3_0/assets/1",
            status=200,
            payload={"id": 1},
        )
        result = await client.update_asset(
            asset_id=1, updates={"sensors_to_show_as_kpis": [1, 2]}
        )
        assert result["id"] == 1
        await client.close()


@pytest.mark.asyncio
async def test_update_asset_invalid_type():
    """Raises ContentTypeError for disallowed value type."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.access_token = "test-token"
    with pytest.raises(ContentTypeError, match="not allowed"):
        await client.update_asset(
            asset_id=1, updates={"latitude": {"nested": "dict_not_allowed"}}
        )
    await client.close()


@pytest.mark.asyncio
async def test_delete_asset_no_confirm():
    """confirm_first=False skips prompt."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.delete(
            "http://localhost:5000/api/v3_0/assets/1",
            status=204,
            payload={},
        )
        await client.delete_asset(asset_id=1, confirm_first=False)
        await client.close()


@pytest.mark.asyncio
async def test_delete_asset_confirm_yes():
    """confirm_first default (True), user says yes."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.delete(
            "http://localhost:5000/api/v3_0/assets/1",
            status=204,
            payload={},
        )
        with patch("builtins.input", return_value="y"):
            await client.delete_asset(asset_id=1)
        await client.close()


@pytest.mark.asyncio
async def test_delete_asset_confirm_no():
    """confirm_first=True, user says no - no DELETE request made."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        with patch("builtins.input", return_value="n"):
            await client.delete_asset(asset_id=1, confirm_first=True)
        assert ("DELETE", "http://localhost:5000/api/v3_0/assets/1") not in [
            (k[0], str(k[1])) for k in m.requests.keys()
        ]
        await client.close()
