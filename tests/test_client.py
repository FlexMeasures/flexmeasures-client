import asyncio

import pytest
from aioresponses import aioresponses, CallbackResult
from flexmeasures_client.client import FlexMeasuresClient


def test__init__():
    flexmeasures_localhost = FlexMeasuresClient("password", "email")
    assert flexmeasures_localhost.__dict__ == {'password': 'password', 'email': 'email', 'access_token': None, 'host': 'localhost:5000', 'scheme': 'http', 'ssl': False, 'api_version': 'v3_0', 'path': '/api/v3_0/', 'consumption_price_sensor': 3, 'reauth_once': True, 'polling_step': 0, 'max_polling_steps': 10, 'polling_timeout': 200.0, 'request_timeout': 20.0, 'polling_interval': 10.0, 'session': None}

    flexmeasures_not_localhost = FlexMeasuresClient("password","email", host="test_host.test")
    assert flexmeasures_not_localhost.__dict__ == {'password': 'password', 'email': 'email', 'access_token': None, 'host': 'test_host.test', 'scheme': 'https', 'ssl': True, 'api_version': 'v3_0', 'path': '/api/v3_0/', 'consumption_price_sensor': 3, 'reauth_once': True, 'polling_step': 0, 'max_polling_steps': 10, 'polling_timeout': 200.0, 'request_timeout': 20.0, 'polling_interval': 10.0, 'session': None}

    flexmeasures_custom_ssl_and_scheme = FlexMeasuresClient("password", "email", ssl=True, scheme="test")
    assert flexmeasures_custom_ssl_and_scheme.__dict__ == {'password': 'password', 'email': 'email', 'access_token': None, 'host': 'localhost:5000', 'scheme': 'test', 'ssl': True, 'api_version': 'v3_0', 'path': '/api/v3_0/', 'consumption_price_sensor': 3, 'reauth_once': True, 'polling_step': 0, 'max_polling_steps': 10, 'polling_timeout': 200.0, 'request_timeout': 20.0, 'polling_interval': 10.0, 'session': None}





def test__init__():
    flexmeasures_localhost = FlexMeasuresClient("password", "email")
    assert flexmeasures_localhost.__dict__ == {
        "password": "password",
        "email": "email",
        "access_token": None,
        "host": "localhost:5000",
        "scheme": "http",
        "ssl": False,
        "api_version": "v3_0",
        "path": "/api/v3_0/",
        "reauth_once": True,
        "polling_step": 0,
        "max_polling_steps": 10,
        "polling_timeout": 200.0,
        "request_timeout": 20.0,
        "polling_interval": 10.0,
        "session": None,
    }

    flexmeasures_not_localhost = FlexMeasuresClient(
        "password", "email", host="test_host.test"
    )
    assert flexmeasures_not_localhost.__dict__ == {
        "password": "password",
        "email": "email",
        "access_token": None,
        "host": "test_host.test",
        "scheme": "https",
        "ssl": True,
        "api_version": "v3_0",
        "path": "/api/v3_0/",
        "reauth_once": True,
        "polling_step": 0,
        "max_polling_steps": 10,
        "polling_timeout": 200.0,
        "request_timeout": 20.0,
        "polling_interval": 10.0,
        "session": None,
    }

    flexmeasures_custom_ssl_and_scheme = FlexMeasuresClient(
        "password", "email", ssl=True, scheme="test"
    )
    assert flexmeasures_custom_ssl_and_scheme.__dict__ == {
        "password": "password",
        "email": "email",
        "access_token": None,
        "host": "localhost:5000",
        "scheme": "test",
        "ssl": True,
        "api_version": "v3_0",
        "path": "/api/v3_0/",
        "reauth_once": True,
        "polling_step": 0,
        "max_polling_steps": 10,
        "polling_timeout": 200.0,
        "request_timeout": 20.0,
        "polling_interval": 10.0,
        "session": None,
    }


def test_build_url():
    flexmeasures_client = FlexMeasuresClient("password", "email")
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
        )
    await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_post_measurements() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient("test", "test")
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
        entity_address = "ea1.2022-04.nl.seita.flexmeasures:fm1"

        await flexmeasures_client.post_measurements(
            sensor_id=sensor_id,
            start=start,
            duration=duration,
            values=values,
            unit=unit,
            entity_address=entity_address,
        )
        m.assert_called_once_with(
            "http://localhost:5000/api/v3_0/sensors/data",
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            json={
                "sensor": "ea1.2022-04.nl.seita.flexmeasures:fm1.test",
                "start": "2023-03-26T10:00:00+02:00",
                "duration": "P0DT6H0M0S",
                "values": "test",
                "unit": "test",
            },
            params=None,
            ssl=False,
        )

    await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_trigger_storage_schedule() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient("test", "test")
        flexmeasures_client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/3/schedules/trigger",
            status=200,
            payload={"schedule": "test_schedule_id"},
        )

        await flexmeasures_client.trigger_storage_schedule(
            sensor_id=3,
            start="2023-03-26T10:00+02:00",
            duration="PT12H",
            soc_unit="kWh",
            soc_at_start=50,
            soc_targets=[
                {
                    "value": 100,
                    "datetime": "2023-03-03T11:00+02:00",
                }
            ],
            consumption_price_sensor=3,
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
                    "soc-targets": [
                        {"value": 100, "datetime": "2023-03-03T11:00+02:00"}
                    ],
                },
                "flex-context": {"consumption-price-sensor": 3},
            },
            url="http://localhost:5000/api/v3_0/sensors/3/schedules/trigger",
            params=None,
            ssl=False,
        )

    await flexmeasures_client.close()


# @pytest.mark.asyncio
# async def test_get_schedule() -> None:
#     # todo: relies on https://github.com/pnuckowski/aioresponses/pull/237
#     with aioresponses() as m:
#         m.get(
#             "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid",
#             status=400,
#             payload={"message": "Scheduling job waiting"},
#             repeat=3,
#         )
#         m.get(
#             "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid",
#             status=200,
#             payload={
#                 "values": [2.15, 3, 2],
#                 "start": "2015-06-02T10:00:00+00:00",
#                 "duration": "PT45M",
#                 "unit": "MW",
#             },
#         )
#         flexmeasures_client = FlexMeasuresClient(
#             "test", "test", request_timeout=2, polling_interval=0.2
#         )

#         schedule, status = await flexmeasures_client.get_schedule(
#             sensor_id=1, schedule_id="some-uuid", duration="PT45M"
#         )
#     assert schedule["values"] == [2.15, 3, 2]
#     await flexmeasures_client.close()


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
            "test",
            "test",
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
        flexmeasures_client = FlexMeasuresClient("test", "test")
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

        response = await flexmeasures_client.get_assets()
        assert len(response) == 1
        assert response[0]["account_id"] == 2

    await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_sensors() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient("test", "test")
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload=[
                {
                    "entity_address": "ea1.2023-06.localhost:fm1.2",
                    "event_resolution": 15,
                    "generic_asset_id": 3,
                    "name": "discharging",
                    "timezone": "Europe/Amsterdam",
                    "unit": "MW",
                }
            ],
        )

        response = await flexmeasures_client.get_sensors()
        assert len(response) == 1
        assert response[0]["entity_address"] == "ea1.2023-06.localhost:fm1.2"

    await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_sensors2() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient("test", "test")
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

    await flexmeasures_client.close()
