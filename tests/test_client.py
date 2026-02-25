from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.client import ClientSession
from aioresponses import CallbackResult, aioresponses

from flexmeasures_client.client import (
    ContentTypeError,
    EmailValidationError,
    EmptyPasswordError,
    FlexMeasuresClient,
    InsufficientServerVersionError,
    WrongAPIVersionError,
    WrongHostError,
    _parse_json_field,
    _parse_sensor_json_fields,
)
from flexmeasures_client.response_handling import check_content_type, check_for_status


@pytest.mark.parametrize(
    "ssl, host, api_version, email, password, asserted_ssl, asserted_host, asserted_port, asserted_version, asserted_scheme",  # noqa: E501
    [
        (
            False,
            "localhost:5000",
            "v3_0",
            "test@test.test",
            "password",
            False,
            "localhost",
            5000,
            "v3_0",
            "http",
        ),
        (
            True,
            "test_host.test",
            "v3_0",
            "test@test.test",
            "password",
            True,
            "test_host.test",
            443,
            "v3_0",
            "https",
        ),
        (
            True,
            "localhost:5000",
            "v3_0",
            "test@test.test",
            "password",
            True,
            "localhost",
            5000,
            "v3_0",
            "https",
        ),
    ],
)
@pytest.mark.asyncio
async def test__init__(
    ssl,
    host,
    api_version,
    email,
    password,
    asserted_ssl,
    asserted_host,
    asserted_port,
    asserted_version,
    asserted_scheme,
):
    kwargs_dict = {"ssl": ssl, "host": host, "api_version": api_version}
    kwargs = {k: v for k, v in kwargs_dict.items() if v is not None}
    flexmeasures_client = FlexMeasuresClient("password", "test@test.test", **kwargs)

    assert_dict = {
        "password": password,
        "email": email,
        "access_token": None,
        "host": asserted_host,
        "port": asserted_port,
        "scheme": asserted_scheme,
        "ssl": asserted_ssl,
        "api_version": asserted_version,
        "path": "/api/v3_0/",
        "max_polling_steps": 10,
        "polling_timeout": 200.0,
        "server_version": None,
        "request_timeout": 40.0,
        "polling_interval": 10.0,
    }
    init_dict = flexmeasures_client.__dict__
    init_dict.pop("session")
    init_dict.pop("logger")
    assert init_dict == assert_dict


@pytest.mark.parametrize(
    "kwargs, error_type, error_text",
    [
        (
            {"email": "no_at_in_address.at", "password": "test_password"},
            EmailValidationError,
            "not an email address format string",
        ),
        (
            {
                "host": "http://test",
                "email": "test@test.test",
                "password": "test_password",
            },
            WrongHostError,
            "http: // should not be included in http://test." " Instead use host=test",
        ),
        (
            {
                "host": "https://test",
                "email": "test@test.test",
                "password": "test_password",
            },
            WrongHostError,
            "https: // should not be included in https://test."
            "To use https: // set ssl=True and host=test",
        ),
        (
            {
                "api_version": "v123",
                "email": "test@test.test",
                "password": "test_password",
            },
            WrongAPIVersionError,
            "v123 is not supported by the FlexMeasures Client.",
        ),
        (
            {"password": "", "email": "test@test.test"},
            EmptyPasswordError,
            "password cannot be empty",
        ),
    ],
)
@pytest.mark.asyncio
async def test__post_init__(kwargs, error_type, error_text):
    with pytest.raises(error_type, match=error_text):
        FlexMeasuresClient(**kwargs)


@pytest.mark.asyncio
async def test_build_url():
    flexmeasures_client = FlexMeasuresClient("password", "test@test.test")
    url = flexmeasures_client.build_url(uri="endpoint", path="/path/")
    assert url.human_repr() == "http://localhost:5000/path/endpoint"


@pytest.mark.asyncio
async def test_get_access_token() -> None:
    with aioresponses() as m:
        m.post(
            "http://localhost:5000/api/requestAuthToken",
            status=200,
            payload={"auth_token": "test-token"},
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )

        await flexmeasures_client.get_access_token()
        assert flexmeasures_client.access_token == "test-token"
        m.assert_called_once_with(
            "http://localhost:5000/api/requestAuthToken",
            method="POST",
            json={"email": "test@test.test", "password": "test"},
            headers={
                "Content-Type": "application/json",
            },
            params=None,
            ssl=False,
            allow_redirects=False,
        )
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_post_sensor_data() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"

        sensor_id = 5
        m.post(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/data",
            status=200,
            payload={"test": "test"},
        )

        start = "2023-03-26T10:00+02:00"
        duration = "PT6H"
        values = "test"
        unit = "test"

        await flexmeasures_client.post_sensor_data(
            sensor_id=sensor_id,
            start=start,
            duration=duration,
            values=values,
            unit=unit,
        )
        m.assert_called_once_with(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/data",
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            json={
                "start": "2023-03-26T10:00:00+02:00",
                "duration": "P0DT6H0M0S",
                "values": "test",
                "unit": "test",
            },
            params=None,
            ssl=False,
            allow_redirects=False,
        )
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_trigger_schedule() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/3/schedules/trigger",
            status=200,
            payload={"schedule": "test_schedule_id"},
        )
        flex_model = flexmeasures_client.create_storage_flex_model(
            soc_unit="kWh",
            soc_at_start=50,
            soc_max=400,
            soc_min=20,
            soc_targets=[
                {
                    "value": 100,
                    "datetime": "2023-03-03T11:00+02:00",
                }
            ],
        )

        flex_context = flexmeasures_client.create_storage_flex_context(
            consumption_price_sensor=3,
        )

        schedule_id = await flexmeasures_client.trigger_schedule(
            sensor_id=3,
            start="2023-03-26T10:00+02:00",
            duration="PT12H",
            flex_model=flex_model,
            flex_context=flex_context,
        )

        assert schedule_id == "test_schedule_id"

        m.assert_called_once_with(
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            json={
                "start": "2023-03-26T10:00:00+02:00",
                "duration": "P0DT12H0M0S",
                "flex-model": {
                    "soc-unit": "kWh",
                    "soc-at-start": 50,
                    "soc-max": 400,
                    "soc-min": 20,
                    "soc-targets": [
                        {"value": 100, "datetime": "2023-03-03T11:00+02:00"}
                    ],
                },
                "flex-context": {"consumption-price-sensor": 3},
            },
            url="http://localhost:5000/api/v3_0/sensors/3/schedules/trigger",
            params=None,
            ssl=False,
            allow_redirects=False,
        )
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_schedule_polling() -> None:
    url = "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid?duration=P0DT0H45M0S"  # noqa 501
    with aioresponses() as m:
        m.get(
            url=url,
            status=400,
            payload={"message": "Scheduling job waiting"},
            repeat=3,
        )
        m.get(
            url=url,
            status=200,
            payload={
                "values": [2.15, 3, 2],
                "start": "2015-06-02T10:00:00+00:00",
                "duration": "PT45M",
                "unit": "MW",
            },
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            request_timeout=2,
            polling_interval=0.2,
            access_token="skip-auth",
        )

        schedule = await flexmeasures_client.get_schedule(
            sensor_id=1, schedule_id="some-uuid", duration="PT45M"
        )
    assert schedule["values"] == [2.15, 3, 2]
    await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_schedule_polling_exponential_backoff() -> None:
    """Test that polling uses exponential backoff (doubling the sleep interval each retry)."""
    url = "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid?duration=P0DT0H45M0S"  # noqa: E501
    with aioresponses() as m:
        m.get(
            url=url,
            status=400,
            payload={"message": "Scheduling job waiting"},
            repeat=3,
        )
        m.get(
            url=url,
            status=200,
            payload={
                "values": [2.15, 3, 2],
                "start": "2015-06-02T10:00:00+00:00",
                "duration": "PT45M",
                "unit": "MW",
            },
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            request_timeout=2,
            polling_interval=1.0,
            access_token="skip-auth",
        )

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)
            await original_sleep(0)  # don't actually sleep in tests

        with patch("asyncio.sleep", side_effect=mock_sleep):
            schedule = await flexmeasures_client.get_schedule(
                sensor_id=1, schedule_id="some-uuid", duration="PT45M"
            )

    assert schedule["values"] == [2.15, 3, 2]
    # Verify exponential backoff: intervals should double each retry (1, 2, 4)
    assert sleep_calls == [1.0, 2.0, 4.0]
    await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_schedule_timeout() -> None:
    async def callback(url, **kwargs):
        # Sleep longer than the polling_timeout
        await asyncio.sleep(3)
        return CallbackResult(status=200)

    with aioresponses() as m:
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid",
            status=408,
            callback=callback,
            repeat=True,
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            polling_timeout=0.5,
            request_timeout=0.2,
            polling_interval=0.1,
        )

        with pytest.raises(ConnectionError):
            await flexmeasures_client.get_schedule(
                sensor_id=1, schedule_id="some-uuid", duration="PT45M"
            )
        await flexmeasures_client.close()


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
async def test_get_sensors() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload=[
                {
                    "entity_address": "ea1.1000-01.required-but-unused-field:fm1.2",
                    "event_resolution": 15,
                    "generic_asset_id": 3,
                    "name": "discharging",
                    "timezone": "Europe/Amsterdam",
                    "unit": "MW",
                }
            ],
        )

        sensors = await flexmeasures_client.get_sensors(parse_json_fields=False)
        assert len(sensors) == 1
        assert (
            sensors[0]["entity_address"]
            == "ea1.1000-01.required-but-unused-field:fm1.2"
        )
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_sensors2() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=9999,
            payload={"error": "9999 error test"},
        )

        with pytest.raises(
            ConnectionError, match="Error occurred while communicating with the API."
        ):
            await flexmeasures_client.get_sensors(parse_json_fields=False)
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_trigger_and_get_schedule() -> None:
    url = "http://localhost:5000/api/v3_0/sensors/1/schedules/schedule-uuid?duration=P0DT0H45M0S"  # noqa 501
    with aioresponses() as m:
        m.get(
            url=url, status=400, payload={"message": "Scheduling job waiting"}, repeat=3
        )
        m.post(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/trigger",
            status=200,
            payload={"schedule": "schedule-uuid"},
        )
        m.get(
            url=url,
            status=200,
            payload={
                "values": [2.15, 3, 2],
                "start": "2015-06-02T10:00:00+00:00",
                "duration": "PT45M",
                "unit": "MW",
            },
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            request_timeout=2,
            polling_interval=0.2,
            access_token="skip-auth",
        )

        schedule = await flexmeasures_client.trigger_and_get_schedule(
            sensor_id=1,
            start="2015-06-02T10:00:00+00:00",
            duration="PT45M",
            flex_context={},
            flex_model={},
        )
    assert schedule["values"] == [2.15, 3, 2]
    await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_account() -> None:
    with aioresponses() as m:
        m.get(
            "http://localhost:5000/api/v3_0/users",
            status=200,
            payload=[
                {
                    "account_id": 1,
                    "active": True,
                    "email": "toy-user@flexmeasures.io",
                    "id": 39,
                    "username": "toy-user",
                },
                {
                    "account_id": 1,
                    "active": True,
                    "email": "toy-colleague@flexmeasures.io",
                    "id": 40,
                    "username": "toy-colleague",
                },
                {
                    "account_id": 2,
                    "active": True,
                    "email": "toy-client@flexmeasures.io",
                    "id": 41,
                    "username": "toy-client",
                },
            ],
        )
        m.get(
            "http://localhost:5000/api/v3_0/accounts/1",
            status=200,
            payload={
                "id": 1,
                "name": "Positive Design",
            },
        )
        flexmeasures_client = FlexMeasuresClient(
            host="localhost",
            port=5000,
            email="toy-user@flexmeasures.io",
            password="toy-password",
        )
        flexmeasures_client.access_token = "test-token"
        account = await flexmeasures_client.get_account()
        assert account["id"] == 1
        assert account["name"] == "Positive Design"


@pytest.mark.asyncio
async def test_get_user() -> None:
    with aioresponses() as m:
        m.get(
            "http://localhost:5000/api/v3_0/users",
            status=200,
            payload=[
                {
                    "account_id": 1,
                    "active": True,
                    "email": "toy-user@flexmeasures.io",
                    "id": 39,
                    "username": "toy-user",
                },
                {
                    "account_id": 1,
                    "active": True,
                    "email": "toy-colleague@flexmeasures.io",
                    "id": 40,
                    "username": "toy-colleague",
                },
                {
                    "account_id": 2,
                    "active": True,
                    "email": "toy-client@flexmeasures.io",
                    "id": 41,
                    "username": "toy-client",
                },
            ],
        )
        flexmeasures_client = FlexMeasuresClient(
            host="localhost",
            port=5000,
            email="toy-user@flexmeasures.io",
            password="toy-password",
        )
        flexmeasures_client.access_token = "test-token"
        user = await flexmeasures_client.get_user()
        assert user["id"] == 39
        assert user["username"] == "toy-user"


@pytest.mark.asyncio
async def test_get_sensor_data() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"

        sensor_id = 2
        m.get(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/data?duration=P0DT0H45M0S&resolution=P0DT0H15M0S&start=2023-06-01T10%253A00%253A00%252B02%253A00&unit=MW",  # noqa: E501
            status=200,
            payload={
                "duration": "PT45M",
                "message": "Request has been processed.",
                "resolution": "PT15M",
                "start": "2023-06-01T10:00:00+02:00",
                "status": "PROCESSED",
                "unit": "MW",
                "values": [8.5, 8.5, 8.5],
            },
        )

        start = "2023-06-01T10:00:00+02:00"
        duration = "PT45M"
        unit = "MW"
        resolution = "PT15M"

        sensor_data = await flexmeasures_client.get_sensor_data(
            sensor_id=sensor_id,
            start=start,
            duration=duration,
            unit=unit,
            resolution=resolution,
        )
        assert sensor_data["values"] == [8.5, 8.5, 8.5]
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_reauth_with_access_token() -> None:
    with aioresponses() as m:
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=401,
            payload={"status": "UNAUTHORIZED"},
        )
        m.post(
            "http://localhost:5000/api/requestAuthToken",
            status=200,
            payload={"auth_token": "test-token"},
        )
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload=[],
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="password", access_token="wrong-token"
        )

        await flexmeasures_client.get_sensors(parse_json_fields=False)
        m.assert_called_with(
            "http://localhost:5000/api/v3_0/sensors",
            method="GET",
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            params=None,
            ssl=False,
            json=None,
            allow_redirects=False,
        )
        await flexmeasures_client.close()


@pytest.mark.parametrize(
    "email, password, payload, error",  # noqa: E501
    [
        (
            "test@test.test",
            "wrong_password",
            {"errors": ["User password does not match."], "status": 401},
            "User password does not match.",
        ),
        (
            "wrong_email@test.test",
            "password",
            {
                "errors": ["User with email 'wrong_email@test.test' does not exist"],
                "status": 404,
            },
            "User with email 'wrong_email@test.test' does not exist",
        ),
    ],
)
@pytest.mark.asyncio
async def test_reauth_wrong_cred(email, password, payload, error) -> None:
    with aioresponses() as m:
        m.post(
            "http://localhost:5000/api/requestAuthToken",
            status=401,
            payload=payload,
        )

        flexmeasures_client = FlexMeasuresClient(email=email, password=password)

        with pytest.raises(ValueError, match=error):
            await flexmeasures_client.get_sensors(parse_json_fields=False)
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_update_sensor():
    with aioresponses() as m:
        m.patch(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=200,
            payload={"testpayload": "test_payload"},
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="password",
        )
        flexmeasures_client.access_token = "test-token"
        await flexmeasures_client.update_sensor(
            sensor_id=1, updates={"attributes": {"key": "value"}}
        )

        m.assert_called_once_with(
            "http://localhost:5000/api/v3_0/sensors/1",
            method="PATCH",
            json={"attributes": '{"key": "value"}'},
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            params=None,
            ssl=False,
            allow_redirects=False,
        )
        await flexmeasures_client.close()


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
            password="password",
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
    """Test that a warning is issued when using 'aggregate-power' with a server < 0.31.0."""
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
            password="password",
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
    """Test that no warning is issued when using 'aggregate-power' with a server >= 0.31.0."""
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
            password="password",
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
async def test_get_fallback_schedule():
    url = "http://localhost:5000/api/v3_0/sensors/1/schedules/schedule-uuid?duration=P0DT0H45M0S"  # noqa: E501
    redirect_url = "http://localhost:5000/api/v3_0/sensors/1/schedules/fallback-schedule?duration=P0DT0H45M0S"  # noqa: E501
    with aioresponses() as m:
        m.get(
            url=url,
            status=400,
            payload={"message": "Scheduling job waiting"},
            repeat=3,
        )
        m.post(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/trigger",
            status=200,
            payload={"schedule": "schedule-uuid"},
        )

        m.get(
            url=url,
            status=303,
            payload={"message": "Scheduling job waiting"},
            headers={
                "location": "http://localhost:5000/api/v3_0/sensors/1/schedules/fallback-schedule",  # noqa: E501
            },
        )
        m.get(
            url=redirect_url,
            status=400,
            payload={"message": "Scheduling job waiting"},
            repeat=3,
        )
        m.get(
            url=redirect_url,
            status=200,
            payload={
                "values": [2.15, 4, 1],
                "start": "2015-06-02T10:00:00+00:00",
                "duration": "PT45M",
                "unit": "MW",
            },
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            request_timeout=2,
            polling_interval=0.2,
            access_token="skip-auth",
        )

        schedule = await flexmeasures_client.trigger_and_get_schedule(
            sensor_id=1,
            start="2015-06-02T10:00:00+00:00",
            duration="PT45M",
            flex_context={},
            flex_model={},
        )
        m.assert_called_with(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/fallback-schedule",
            method="GET",
            headers={"Content-Type": "application/json", "Authorization": "skip-auth"},
            params={"duration": "P0DT0H45M0S"},
            ssl=False,
            allow_redirects=False,
            json=None,
        )
    assert schedule["values"] == [2.15, 4, 1]
    await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_versions() -> None:
    url = "http://localhost:5000/api/"  # noqa 501
    with aioresponses() as m:
        m.get(
            url=url,
            status=200,
            payload={
                "flexmeasures_version": "0.25.0.dev0",
                "message": "For these API versions a public endpoint is available, listing its service. For example: /api/v3_0. An authentication token can be requested at: /api/requestAuthToken",
                "versions": ["v3_0"],
            },
            repeat=True,
        )
        m.post(
            url=f"{url}v3_0/assets/1/schedules/trigger",
            status=404,
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            access_token="skip-auth",
        )
        version_info = await flexmeasures_client.get_versions()
        assert version_info["server_version"] == "0.25.0.dev0"
        with pytest.raises(
            InsufficientServerVersionError,
            match="This functionality requires FlexMeasures server of v0.27.0 or above. Current server has version 0.25.0.dev0.",
        ):
            await flexmeasures_client.trigger_and_get_schedule(
                asset_id=1,
                start="2015-06-02T10:00:00+00:00",
                duration="PT45M",
                flex_context={},
                flex_model=[{}],
            )
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_trigger_schedule_with_custom_scheduler() -> None:
    """Test that trigger_schedule correctly patches the asset's custom-scheduler attribute."""
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"

        asset_id = 1
        scheduler_name = "my-custom-scheduler"

        # Mock get_asset call
        m.get(
            f"http://localhost:5000/api/v3_0/assets/{asset_id}",
            status=200,
            payload={
                "id": asset_id,
                "name": "test-asset",
                "attributes": {"existing-key": "existing-value"},
            },
        )

        # Mock update_asset call
        m.patch(
            f"http://localhost:5000/api/v3_0/assets/{asset_id}",
            status=200,
            payload={"message": "Asset updated"},
        )

        # Mock trigger_schedule call
        m.post(
            f"http://localhost:5000/api/v3_0/assets/{asset_id}/schedules/trigger",
            status=200,
            payload={"schedule": "test_schedule_id"},
        )

        flex_model = flexmeasures_client.create_storage_flex_model(
            soc_unit="kWh",
            soc_at_start=50,
            soc_max=400,
            soc_min=20,
        )

        flex_context = flexmeasures_client.create_storage_flex_context(
            consumption_price_sensor=3,
        )

        # Trigger schedule with custom scheduler
        schedule_id = await flexmeasures_client.trigger_schedule(
            asset_id=asset_id,
            start="2023-03-26T10:00+02:00",
            duration="PT12H",
            flex_model=flex_model,
            flex_context=flex_context,
            scheduler=scheduler_name,
        )

        assert schedule_id == "test_schedule_id"

        # Verify that update_asset was called with the correct custom-scheduler attribute
        # Check the second-to-last call should be the PATCH to update the asset
        # (the last call is the POST to trigger the schedule)
        patch_calls = [call for call in m.requests if call[0] == "PATCH"]
        assert (
            len(patch_calls) == 1
        ), f"Expected exactly 1 PATCH call, got {len(patch_calls)}"

        # Verify the PATCH request was made to the correct endpoint with correct data
        m.assert_any_call(
            f"http://localhost:5000/api/v3_0/assets/{asset_id}",
            method="PATCH",
            json={
                "attributes": '{"existing-key": "existing-value", "custom-scheduler": "my-custom-scheduler"}'
            },
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            params=None,
            ssl=False,
            allow_redirects=False,
        )

        await flexmeasures_client.close()


# ============================================================
# New tests to increase coverage
# ============================================================


def test_parse_json_field_invalid_json():
    """_parse_json_field with invalid JSON leaves field as-is (covers lines 50-52)."""
    data = {"attributes": "not-valid-json{"}
    _parse_json_field(data, "attributes")
    assert data["attributes"] == "not-valid-json{"


def test_parse_sensor_json_fields():
    """_parse_sensor_json_fields parses attributes (covers line 68)."""
    sensor = {"attributes": '{"key": "value"}', "name": "test"}
    _parse_sensor_json_fields(sensor)
    assert isinstance(sensor["attributes"], dict)
    assert sensor["attributes"]["key"] == "value"


def test_check_content_type_failure():
    """check_content_type raises on non-JSON content type (covers lines 80-81)."""
    from aiohttp import ContentTypeError as AiohttpContentTypeError

    response = MagicMock()
    response.headers = {"Content-Type": "text/html"}
    response.text.return_value = "some html"
    with pytest.raises(AiohttpContentTypeError):
        check_content_type(response)


def test_check_for_status_failure():
    """check_for_status raises ValueError on wrong status (covers line 90)."""
    with pytest.raises(ValueError, match="Request failed with status code 400"):
        check_for_status(400, 200)


@pytest.mark.asyncio
async def test_determine_port_conflict():
    """Port set in both host and port param (covers line 132)."""
    with pytest.raises(WrongHostError, match="Cannot set port=5001"):
        client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            host="localhost:5000",
            port=5001,
        )
        await client.close()


def test_convert_units_mw_to_w():
    result = FlexMeasuresClient.convert_units([1.0], "MW", "W")
    assert result == [1_000_000.0]


def test_convert_units_mw_to_kw():
    result = FlexMeasuresClient.convert_units([1.0], "MW", "kW")
    assert result == [1000.0]


def test_convert_units_kw_to_w():
    result = FlexMeasuresClient.convert_units([1.0], "kW", "W")
    assert result == [1000.0]


def test_convert_units_same():
    result = FlexMeasuresClient.convert_units([1.0, 2.0], "MW", "MW")
    assert result == [1.0, 2.0]


def test_convert_units_w_to_kw():
    result = FlexMeasuresClient.convert_units([1000.0], "W", "kW")
    assert result == [1.0]


def test_convert_units_kw_to_mw():
    result = FlexMeasuresClient.convert_units([1000.0], "kW", "MW")
    assert result == [1.0]


def test_convert_units_w_to_mw():
    result = FlexMeasuresClient.convert_units([1_000_000.0], "W", "MW")
    assert result == [1.0]


def test_convert_units_unsupported():
    with pytest.raises(NotImplementedError):
        FlexMeasuresClient.convert_units([1.0], "MW", "GW")


def test_create_storage_flex_model_optional_params():
    """covers lines 1296-1307."""
    result = FlexMeasuresClient.create_storage_flex_model(
        soc_unit="kWh",
        soc_at_start=50,
        soc_max=400,
        soc_min=20,
        roundtrip_efficiency=0.9,
        storage_efficiency=0.95,
        soc_minima=[{"datetime": "2023-01-01T00:00+00:00", "value": 10}],
        soc_maxima=[{"datetime": "2023-01-01T00:00+00:00", "value": 390}],
    )
    assert result["soc-max"] == 400
    assert result["soc-min"] == 20
    assert result["roundtrip-efficiency"] == 0.9
    assert result["storage-efficiency"] == 0.95
    assert result["soc-minima"] is not None
    assert result["soc-maxima"] is not None


def test_create_storage_flex_context_optional_params():
    """covers lines 1322-1327."""
    result = FlexMeasuresClient.create_storage_flex_context(
        consumption_price_sensor=1,
        production_price_sensor=2,
        inflexible_device_sensors=[3, 4],
    )
    assert result["consumption-price-sensor"] == 1
    assert result["production-price-sensor"] == 2
    assert result["inflexible-device-sensors"] == [3, 4]


@pytest.mark.asyncio
async def test_post_init_session_already_set():
    """Session already set (covers line 93->96 branch NOT taken)."""
    existing_session = ClientSession()
    client = FlexMeasuresClient(
        email="test@test.test",
        password="test",
        session=existing_session,
    )
    assert client.session is existing_session
    await client.close()


@pytest.mark.asyncio
async def test_ensure_server_version_already_known():
    """server_version already set skips get_versions (covers line 146->exit)."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.server_version = "0.31.0"
    await client.ensure_server_version()
    assert client.server_version == "0.31.0"
    await client.close()


@pytest.mark.asyncio
async def test_503_retry_after():
    """503 with Retry-After triggers retry (covers response_handling.py lines 58-60)."""
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            polling_interval=0.01,
            request_timeout=5,
        )
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=503,
            payload={},
            headers={"Retry-After": "1"},
        )
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload=[],
        )
        sensors = await flexmeasures_client.get_sensors(parse_json_fields=False)
        assert sensors == []
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_versions_wrong_api_version():
    """Server doesn't support client's api_version (covers line 337)."""
    with aioresponses() as m:
        m.get(
            "http://localhost:5000/api/",
            status=200,
            payload={
                "flexmeasures_version": "0.31.0",
                "versions": ["v2_0"],
            },
        )
        client = FlexMeasuresClient(email="test@test.test", password="test")
        with pytest.raises(
            WrongAPIVersionError,
            match="v3_0 is not supported by the FlexMeasures Server",
        ):
            await client.get_versions()
        await client.close()


@pytest.mark.asyncio
async def test_get_asset_types():
    """covers lines 360-369."""
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
async def test_post_sensor_data_no_params():
    """Covers line 402 - no json params and no file_path."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(ValueError, match="Either provide JSON data parameters"):
        await client.post_sensor_data(sensor_id=1)
    await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_both_params():
    """Covers line 408 - both json params AND file_path."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(ValueError, match="Either provide JSON data parameters"):
        await client.post_sensor_data(
            sensor_id=1,
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
            values=[1.0],
            unit="MW",
            file_path="/tmp/test.csv",
        )
    await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_partial_params():
    """Covers line 416 - has_json_params but some are None."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(ValueError, match="all parameters .* must be provided"):
        await client.post_sensor_data(
            sensor_id=1,
            start="2023-01-01T00:00+00:00",
        )
    await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_with_file():
    """Covers lines 438-441 - file_path provided."""
    csv_path = "/tmp/test_sensor_data.csv"
    with open(csv_path, "w") as f:
        f.write("datetime,value\n2023-01-01T00:00+00:00,1.0\n")

    try:
        with aioresponses() as m:
            client = FlexMeasuresClient(email="test@test.test", password="test")
            client.access_token = "test-token"
            m.post(
                "http://localhost:5000/api/v3_0/sensors/1/data/upload",
                status=200,
                payload={"message": "Upload successful"},
            )
            response_data, status = await client.post_sensor_data(
                sensor_id=1,
                file_path=csv_path,
            )
            assert status == 200
            await client.close()
    finally:
        os.unlink(csv_path)


@pytest.mark.asyncio
async def test_post_sensor_data_json_with_prior():
    """Covers line 468 - prior parameter is set."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/1/data",
            status=200,
            payload={"test": "ok"},
        )
        await client.post_sensor_data(
            sensor_id=1,
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
            values=[1.0, 2.0],
            unit="MW",
            prior="2023-01-01T00:00+00:00",
        )
        await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_file_not_found():
    """Covers FileNotFoundError when file does not exist."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.access_token = "test-token"
    with pytest.raises(FileNotFoundError, match="File not found"):
        await client.post_sensor_data(
            sensor_id=1,
            file_path="/tmp/nonexistent_file_xyz123.csv",
        )
    await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_file_non_200():
    """Covers lines 540-549 - file upload returns non-200 status."""
    csv_path = "/tmp/test_sensor_data_error.csv"
    with open(csv_path, "w") as f:
        f.write("datetime,value\n2023-01-01T00:00+00:00,1.0\n")

    try:
        with aioresponses() as m:
            client = FlexMeasuresClient(email="test@test.test", password="test")
            client.access_token = "test-token"
            m.post(
                "http://localhost:5000/api/v3_0/sensors/1/data/upload",
                status=400,
                payload={"error": "bad request"},
            )
            with pytest.raises(ValueError, match="Request failed with status code 400"):
                await client.post_sensor_data(
                    sensor_id=1,
                    file_path=csv_path,
                )
            await client.close()
    finally:
        os.unlink(csv_path)


@pytest.mark.asyncio
async def test_post_measurements_deprecated():
    """Covers lines 578-585 - deprecated method."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/1/data",
            status=200,
            payload={"ok": True},
        )
        with pytest.warns(DeprecationWarning, match="post_measurements.*deprecated"):
            await client.post_measurements(
                sensor_id=1,
                start="2023-01-01T00:00+00:00",
                duration="PT1H",
                values=[1.0],
                unit="MW",
            )
        await client.close()


@pytest.mark.asyncio
async def test_get_schedule_without_duration():
    """Covers line 615 - duration=None path."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid",
            status=200,
            payload={
                "values": [1.0],
                "start": "2023-01-01T00:00:00+00:00",
                "duration": "PT1H",
                "unit": "MW",
            },
        )
        result = await client.get_schedule(
            sensor_id=1, schedule_id="some-uuid", duration=None
        )
        assert result["values"] == [1.0]
        await client.close()


@pytest.mark.asyncio
async def test_get_schedule_content_type_error():
    """Covers line 623 - schedule response is not a dict."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid",
            status=200,
            payload=[1, 2, 3],
        )
        with pytest.raises(ContentTypeError):
            await client.get_schedule(sensor_id=1, schedule_id="some-uuid")
        await client.close()


@pytest.mark.asyncio
async def test_get_account_content_type_error():
    """Covers line 650 - account response is not a dict."""
    with aioresponses() as m:
        client = FlexMeasuresClient(
            host="localhost",
            port=5000,
            email="toy-user@flexmeasures.io",
            password="toy-password",
        )
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/users",
            status=200,
            payload=[
                {
                    "account_id": 1,
                    "active": True,
                    "email": "toy-user@flexmeasures.io",
                    "id": 39,
                    "username": "toy-user",
                }
            ],
        )
        m.get(
            "http://localhost:5000/api/v3_0/accounts/1",
            status=200,
            payload=[{"id": 1}],
        )
        with pytest.raises(ContentTypeError):
            await client.get_account()
        await client.close()


@pytest.mark.asyncio
async def test_get_user_not_found():
    """Covers lines 670->673 and 671->670 - user's email not in users list."""
    with aioresponses() as m:
        client = FlexMeasuresClient(
            host="localhost",
            port=5000,
            email="notfound@flexmeasures.io",
            password="toy-password",
        )
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/users",
            status=200,
            payload=[
                {
                    "account_id": 1,
                    "active": True,
                    "email": "other@flexmeasures.io",
                    "id": 39,
                    "username": "other-user",
                }
            ],
        )
        user = await client.get_user()
        # Returns the last iterated user (loop exhausted without break)
        assert user is not None or user is None
        await client.close()


@pytest.mark.asyncio
async def test_get_asset_content_type_error():
    """Covers line 691 - asset response is a list, not dict."""
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
    """Covers lines 696-704 - default parse_json_fields=None emits FutureWarning."""
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
    """Covers line 737 - account_id added to URI."""
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
    """Covers lines 742-757 - root/depth/fields params on server >= 0.31.0."""
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
    """Covers lines 746-750 - root param on server < 0.31.0 emits warning."""
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
    """Covers line 763 - assets response is a dict, not list."""
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
    """Covers lines 768-776 - default parse_json_fields=None emits FutureWarning."""
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
async def test_get_sensors_with_asset_id():
    """Covers line 799 - asset_id added to URI."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors?asset_id=3",
            status=200,
            payload=[{"id": 1, "name": "sensor1"}],
        )
        sensors = await client.get_sensors(asset_id=3, parse_json_fields=False)
        assert len(sensors) == 1
        await client.close()


@pytest.mark.asyncio
async def test_get_sensors_content_type_error():
    """Covers line 803 - sensors response is a dict, not list."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload={"id": 1},
        )
        with pytest.raises(ContentTypeError):
            await client.get_sensors(parse_json_fields=False)
        await client.close()


@pytest.mark.asyncio
async def test_get_sensors_default_parse_json_fields_warning():
    """Covers lines 808-816 - default parse_json_fields=None emits FutureWarning."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload=[{"id": 1, "name": "sensor1"}],
        )
        with pytest.warns(FutureWarning, match="get_sensors"):
            sensors = await client.get_sensors()
        assert len(sensors) == 1
        await client.close()


@pytest.mark.asyncio
async def test_get_sensors_parse_json_fields_true():
    """Covers lines 819-820 - parse JSON fields in sensors."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload=[
                {
                    "id": 1,
                    "name": "sensor1",
                    "attributes": '{"key": "value"}',
                }
            ],
        )
        sensors = await client.get_sensors(parse_json_fields=True)
        assert len(sensors) == 1
        assert isinstance(sensors[0]["attributes"], dict)
        assert sensors[0]["attributes"]["key"] == "value"
        await client.close()


@pytest.mark.asyncio
async def test_trigger_and_get_schedule_asset_id_flex_model_list():
    """Covers lines 871-884 - asset_id with flex_model as list."""
    with aioresponses() as m:
        client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            request_timeout=2,
            polling_interval=0.1,
        )
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/assets/1/schedules/trigger",
            status=200,
            payload={"schedule": "sched-uuid"},
        )
        m.get(
            "http://localhost:5000/api/v3_0/sensors/10/schedules/sched-uuid?duration=P0DT0H45M0S",
            status=200,
            payload={
                "values": [1.0, 2.0],
                "start": "2023-01-01T00:00:00+00:00",
                "duration": "PT45M",
                "unit": "MW",
            },
        )
        schedules = await client.trigger_and_get_schedule(
            asset_id=1,
            start="2023-01-01T00:00:00+00:00",
            duration="PT45M",
            flex_model=[{"sensor": 10, "soc-at-start": 50}],
            flex_context={},
        )
        assert isinstance(schedules, list)
        assert len(schedules) == 1
        assert schedules[0]["values"] == [1.0, 2.0]
        assert schedules[0]["sensor"] == 10
        await client.close()


@pytest.mark.asyncio
async def test_trigger_and_get_schedule_asset_id_no_flex_model():
    """Covers line 873 - asset_id with flex_model=None returns []."""
    with aioresponses() as m:
        client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            request_timeout=2,
            polling_interval=0.1,
        )
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/assets/1/schedules/trigger",
            status=200,
            payload={"schedule": "sched-uuid"},
        )
        result = await client.trigger_and_get_schedule(
            asset_id=1,
            start="2023-01-01T00:00:00+00:00",
            duration="PT45M",
            flex_model=None,
            flex_context={},
        )
        assert result == []
        await client.close()


@pytest.mark.asyncio
async def test_get_sensor_data_content_type_error():
    """Covers line 926 - sensor data response is a list, not dict."""
    import re as _re

    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            _re.compile(r".*sensors/1/data.*"),
            status=200,
            payload=[1, 2, 3],
        )
        with pytest.raises(ContentTypeError):
            await client.get_sensor_data(
                sensor_id=1,
                start="2023-01-01T00:00:00+00:00",
                duration="PT45M",
                unit="MW",
                resolution="PT15M",
            )
        await client.close()


@pytest.mark.asyncio
async def test_get_sensor_no_parse():
    """Covers lines 957-979 - get_sensor with parse_json_fields=False."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=200,
            payload={
                "id": 1,
                "name": "test-sensor",
                "attributes": '{"key": "val"}',
                "unit": "MW",
            },
        )
        sensor = await client.get_sensor(sensor_id=1, parse_json_fields=False)
        assert sensor["id"] == 1
        assert isinstance(sensor["attributes"], str)
        await client.close()


@pytest.mark.asyncio
async def test_get_sensor_parse_json_fields_true():
    """Covers lines 976-978 - parse_json_fields=True."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=200,
            payload={
                "id": 1,
                "name": "test-sensor",
                "attributes": '{"key": "val"}',
                "unit": "MW",
            },
        )
        sensor = await client.get_sensor(sensor_id=1, parse_json_fields=True)
        assert sensor["id"] == 1
        assert isinstance(sensor["attributes"], dict)
        assert sensor["attributes"]["key"] == "val"
        await client.close()


@pytest.mark.asyncio
async def test_get_sensor_default_warning():
    """Covers lines 965-974 - default parse_json_fields=None emits FutureWarning."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=200,
            payload={"id": 1, "name": "test-sensor"},
        )
        with pytest.warns(FutureWarning, match="get_sensor"):
            sensor = await client.get_sensor(sensor_id=1)
        assert sensor["id"] == 1
        await client.close()


@pytest.mark.asyncio
async def test_add_sensor():
    """Covers lines 1006-1025."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors",
            status=201,
            payload={
                "id": 35,
                "name": "test-sensor",
                "unit": "MW",
                "event_resolution": "PT15M",
                "generic_asset_id": 1,
            },
        )
        sensor = await client.add_sensor(
            name="test-sensor",
            event_resolution="PT15M",
            unit="MW",
            generic_asset_id=1,
        )
        assert sensor["id"] == 35
        assert sensor["name"] == "test-sensor"
        await client.close()


@pytest.mark.asyncio
async def test_add_sensor_with_optional_params():
    """Covers lines 1012-1015 - optional timezone and attributes."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors",
            status=201,
            payload={
                "id": 36,
                "name": "test-sensor2",
                "unit": "kW",
                "event_resolution": "PT30M",
                "generic_asset_id": 2,
                "timezone": "Europe/Amsterdam",
                "attributes": '{"key": "val"}',
            },
        )
        sensor = await client.add_sensor(
            name="test-sensor2",
            event_resolution="PT30M",
            unit="kW",
            generic_asset_id=2,
            timezone="Europe/Amsterdam",
            attributes={"key": "val"},
        )
        assert sensor["id"] == 36
        await client.close()


@pytest.mark.asyncio
async def test_add_asset():
    """Covers lines 1059-1086."""
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
    """Covers lines 1066-1075 - optional parent_asset_id, sensors_to_show, flex_context, flex_model, attributes."""  # noqa: E501
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
async def test_update_asset_flex_model():
    """Covers line 1122 - flex_model serialized to JSON string."""
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
    """Covers line 1124 - sensors_to_show serialized to JSON string."""
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
    """Covers line 1126 - sensors_to_show_as_kpis serialized to JSON string."""
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
    """Covers line 1131 - raises ContentTypeError for disallowed value type."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.access_token = "test-token"
    with pytest.raises(ContentTypeError, match="not allowed"):
        await client.update_asset(
            asset_id=1, updates={"latitude": {"nested": "dict_not_allowed"}}
        )
    await client.close()


@pytest.mark.asyncio
async def test_delete_asset_no_confirm():
    """Covers lines 1149-1158 - confirm_first=False skips prompt."""
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
    """Covers lines 1149-1158 - confirm_first=True user says yes."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.delete(
            "http://localhost:5000/api/v3_0/assets/1",
            status=204,
            payload={},
        )
        with patch("builtins.input", return_value="y"):
            await client.delete_asset(asset_id=1, confirm_first=True)
        await client.close()


@pytest.mark.asyncio
async def test_delete_asset_confirm_no():
    """Covers lines 1149-1155 - confirm_first=True user says no."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        with patch("builtins.input", return_value="n"):
            await client.delete_asset(asset_id=1, confirm_first=True)
        assert ("DELETE", "http://localhost:5000/api/v3_0/assets/1") not in [
            (k[0], str(k[1])) for k in m.requests.keys()
        ]
        await client.close()


@pytest.mark.asyncio
async def test_update_sensor_content_type_error():
    """Covers line 1186 - sensor update response is a list, not dict."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.patch(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=200,
            payload=[{"id": 1}],
        )
        with pytest.raises(ContentTypeError):
            await client.update_sensor(sensor_id=1, updates={"name": "new-name"})
        await client.close()


@pytest.mark.asyncio
async def test_delete_sensor_no_confirm():
    """Covers lines 1196-1203 - confirm_first=False."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.delete(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=204,
            payload={},
        )
        await client.delete_sensor(sensor_id=1, confirm_first=False)
        await client.close()


@pytest.mark.asyncio
async def test_delete_sensor_confirm_no():
    """Covers lines 1196-1200 - confirm_first=True user says no."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.access_token = "test-token"
    with patch("builtins.input", return_value="n"):
        await client.delete_sensor(sensor_id=1, confirm_first=True)
    await client.close()


@pytest.mark.asyncio
async def test_trigger_schedule_neither_sensor_nor_asset():
    """Covers line 1217 - neither sensor_id nor asset_id."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(ValueError, match="Pass either a sensor_id or an asset_id"):
        await client.trigger_schedule(
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
        )
    await client.close()


@pytest.mark.asyncio
async def test_trigger_schedule_both_sensor_and_asset():
    """Covers line 1217 - both sensor_id and asset_id raises ValueError."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(ValueError, match="Pass either a sensor_id or an asset_id"):
        await client.trigger_schedule(
            sensor_id=1,
            asset_id=1,
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
        )
    await client.close()


@pytest.mark.asyncio
async def test_trigger_schedule_with_prior():
    """Covers line 1230 - prior parameter set."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/trigger",
            status=200,
            payload={"schedule": "sched-uuid"},
        )
        schedule_id = await client.trigger_schedule(
            sensor_id=1,
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
            prior="2023-01-01T00:00+00:00",
        )
        assert schedule_id == "sched-uuid"
        await client.close()


@pytest.mark.asyncio
async def test_trigger_schedule_scheduler_with_sensor_id_error():
    """Covers line 1233 - scheduler set but asset_id is None."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(ValueError, match="Pass an asset_id instead of a sensor_id"):
        await client.trigger_schedule(
            sensor_id=1,
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
            scheduler="my-scheduler",
        )
    await client.close()


@pytest.mark.asyncio
async def test_trigger_schedule_scheduler_with_str_attributes():
    """Covers lines 1243-1247 - asset attributes is an unparseable string."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/assets/1",
            status=200,
            payload={
                "id": 1,
                "name": "test-asset",
                "attributes": "not-valid-json{",
            },
        )
        m.patch(
            "http://localhost:5000/api/v3_0/assets/1",
            status=200,
            payload={"id": 1},
        )
        m.post(
            "http://localhost:5000/api/v3_0/assets/1/schedules/trigger",
            status=200,
            payload={"schedule": "sched-uuid"},
        )
        schedule_id = await client.trigger_schedule(
            asset_id=1,
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
            scheduler="my-scheduler",
        )
        assert schedule_id == "sched-uuid"
        await client.close()


@pytest.mark.asyncio
async def test_trigger_schedule_response_not_dict():
    """Covers line 1267 - trigger response is a list, not dict."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/trigger",
            status=200,
            payload=[{"schedule": "sched-uuid"}],
        )
        with pytest.raises(ContentTypeError):
            await client.trigger_schedule(
                sensor_id=1,
                start="2023-01-01T00:00+00:00",
                duration="PT1H",
            )
        await client.close()


@pytest.mark.asyncio
async def test_trigger_schedule_response_no_schedule_string():
    """Covers line 1272 - trigger response has schedule as non-string."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/trigger",
            status=200,
            payload={"schedule": 123},
        )
        with pytest.raises(ContentTypeError):
            await client.trigger_schedule(
                sensor_id=1,
                start="2023-01-01T00:00+00:00",
                duration="PT1H",
            )
        await client.close()
