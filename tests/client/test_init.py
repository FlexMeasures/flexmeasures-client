from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.client import ClientSession
from aioresponses import CallbackResult, aioresponses

from flexmeasures_client.client import (
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
            email="test@test.test", password="test", access_token="wrong-token"
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
async def test_post_init_session_already_set():
    """Session already set skips creating a new one."""
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
    """server_version already set skips get_versions."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.server_version = "0.31.0"
    await client.ensure_server_version()
    assert client.server_version == "0.31.0"
    await client.close()


@pytest.mark.asyncio
async def test_determine_port_conflict():
    """Port set in both host and port param raises WrongHostError."""
    with pytest.raises(WrongHostError, match="Cannot set port=5001"):
        client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            host="localhost:5000",
            port=5001,
        )
        await client.close()


@pytest.mark.asyncio
async def test_503_retry_after():
    """503 with Retry-After triggers retry."""
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
    """Server doesn't support client's api_version."""
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


def test_parse_json_field_invalid_json():
    """_parse_json_field with invalid JSON leaves field as-is."""
    data = {"attributes": "not-valid-json{"}
    _parse_json_field(data, "attributes")
    assert data["attributes"] == "not-valid-json{"


def test_parse_sensor_json_fields():
    """_parse_sensor_json_fields parses attributes."""
    sensor = {"attributes": '{"key": "value"}', "name": "test"}
    _parse_sensor_json_fields(sensor)
    assert isinstance(sensor["attributes"], dict)
    assert sensor["attributes"]["key"] == "value"


def test_check_content_type_failure():
    """check_content_type raises on non-JSON content type."""
    from aiohttp import ContentTypeError as AiohttpContentTypeError

    response = MagicMock()
    response.headers = {"Content-Type": "text/html"}
    response.text.return_value = "some html"
    with pytest.raises(AiohttpContentTypeError):
        check_content_type(response)


def test_check_for_status_failure():
    """check_for_status raises ValueError on wrong status."""
    with pytest.raises(ValueError, match="Request failed with status code 400"):
        check_for_status(400, 200)
