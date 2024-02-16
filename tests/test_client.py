from __future__ import annotations

import asyncio

import pytest
from aioresponses import CallbackResult, aioresponses

from flexmeasures_client.client import (
    EmailValidationError,
    EmptyPasswordError,
    FlexMeasuresClient,
    WrongAPIVersionError,
    WrongHostError,
)


@pytest.mark.parametrize(
    "ssl, host, api_version, email, password, asserted_ssl, asserted_host, asserted_version , asserted_scheme",  # noqa: E501
    [
        (
            False,
            "localhost:5000",
            "v3_0",
            "test@test.test",
            "password",
            False,
            "localhost:5000",
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
            "localhost:5000",
            "v3_0",
            "https",
        ),
    ],
)
def test__init__(
    ssl,
    host,
    api_version,
    email,
    password,
    asserted_ssl,
    asserted_host,
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
        "scheme": asserted_scheme,
        "ssl": asserted_ssl,
        "api_version": asserted_version,
        "path": "/api/v3_0/",
        "max_polling_steps": 10,
        "polling_timeout": 200.0,
        "request_timeout": 20.0,
        "polling_interval": 10.0,
    }
    init_dict = flexmeasures_client.__dict__
    init_dict.pop("session")
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
            "http:// should not be included in http://test." "Instead use host=test",
        ),
        (
            {
                "host": "https://test",
                "email": "test@test.test",
                "password": "test_password",
            },
            WrongHostError,
            "https:// should not be included in https://test."
            "To use https:// set ssl=True and host=test",
        ),
        (
            {
                "api_version": "v123",
                "email": "test@test.test",
                "password": "test_password",
            },
            WrongAPIVersionError,
            "Version v123 not in versions list: ",
        ),
        (
            {"password": "", "email": "test@test.test"},
            EmptyPasswordError,
            "password cannot be empty",
        ),
    ],
)
def test__post_init__(kwargs, error_type, error_text):
    with pytest.raises(error_type, match=error_text):
        FlexMeasuresClient(**kwargs)


def test_build_url():
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


@pytest.mark.asyncio
async def test_post_measurements() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/data",
            status=200,
            payload={"test": "test"},
        )

        sensor_id = "test"
        start = "2023-03-26T10:00+02:00"
        duration = "PT6H"
        values = "test"
        unit = "test"

        await flexmeasures_client.post_measurements(
            sensor_id=sensor_id,
            start=start,
            duration=duration,
            values=values,
            unit=unit,
        )
        m.assert_called_once_with(
            "http://localhost:5000/api/v3_0/sensors/data",
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            json={
                "sensor": "ea1.1000-01.required-but-unused-field:fm1.test",
                "start": "2023-03-26T10:00:00+02:00",
                "duration": "P0DT6H0M0S",
                "values": "test",
                "unit": "test",
            },
            params=None,
            ssl=False,
            allow_redirects=False,
        )


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


@pytest.mark.asyncio
async def test_get_schedule_polling() -> None:
    # todo: relies on https://github.com/pnuckowski/aioresponses/pull/237 to use repeat instead of 3 times the same aioresponse. # noqa: E501

    url = "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid?duration=P0DT0H45M0S"  # noqa 501
    with aioresponses() as m:
        # m.get(
        #     "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid",
        #     status=400,
        #     payload={"message": "Scheduling job waiting"},
        #     repeat=3
        # )
        m.get(
            url=url,
            status=400,
            payload={"message": "Scheduling job waiting"},
        )
        m.get(
            url=url,
            status=400,
            payload={"message": "Scheduling job waiting"},
        )
        m.get(
            url=url,
            status=400,
            payload={"message": "Scheduling job waiting"},
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


@pytest.mark.asyncio
async def test_get_assets() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/assets",
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

        assets = await flexmeasures_client.get_assets()
        assert len(assets) == 1
        assert assets[0]["account_id"] == 2


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

        sensors = await flexmeasures_client.get_sensors()
        assert len(sensors) == 1
        assert (
            sensors[0]["entity_address"]
            == "ea1.1000-01.required-but-unused-field:fm1.2"
        )


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
            await flexmeasures_client.get_sensors()


@pytest.mark.asyncio
async def test_trigger_and_get_schedule() -> None:
    # todo: relies on https://github.com/pnuckowski/aioresponses/pull/237 to use repeat instead of 3 times the same aioresponse. # noqa: E501

    url = "http://localhost:5000/api/v3_0/sensors/1/schedules/schedule-uuid?duration=P0DT0H45M0S"  # noqa 501
    with aioresponses() as m:
        # m.get(
        #     "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid",
        #     status=400,
        #     payload={"message": "Scheduling job waiting"},
        #     repeat=3
        # )
        m.post(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/trigger",
            status=200,
            payload={"schedule": "schedule-uuid"},
        )

        m.get(
            url=url,
            status=400,
            payload={"message": "Scheduling job waiting"},
        )
        m.get(
            url=url,
            status=400,
            payload={"message": "Scheduling job waiting"},
        )
        m.get(
            url=url,
            status=400,
            payload={"message": "Scheduling job waiting"},
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


@pytest.mark.asyncio
async def test_get_sensor_data() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors/data?duration=P0DT0H45M0S&resolution=PT15M&sensor=ea1.1000-01.required-but-unused-field%253Afm1.2&start=2023-06-01T10%253A00%253A00%252B02%253A00&unit=MW",  # noqa: E501
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

        sensor_id = 2
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

        await flexmeasures_client.get_sensors()
        m.assert_called_with(
            "http://localhost:5000/api/v3_0/sensors",
            method="GET",
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            params=None,
            ssl=False,
            json=None,
            allow_redirects=False,
        )


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
            await flexmeasures_client.get_sensors()


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


@pytest.mark.asyncio
async def test_get_fallback_schedule():
    # todo: relies on https://github.com/pnuckowski/aioresponses/pull/237 to use repeat instead of 3 times the same aioresponse. # noqa: E501

    url = "http://localhost:5000/api/v3_0/sensors/1/schedules/schedule-uuid?duration=P0DT0H45M0S"  # noqa: E501
    redirect_url = "http://localhost:5000/api/v3_0/sensors/1/schedules/fallback-schedule?duration=P0DT0H45M0S"  # noqa: E501
    with aioresponses() as m:
        # m.get(
        #     "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid",
        #     status=400,
        #     payload={"message": "Scheduling job waiting"},
        #     repeat=3
        # )
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
