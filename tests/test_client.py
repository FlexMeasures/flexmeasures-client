import asyncio
import pytest
from aioresponses import aioresponses, CallbackResult
from flexmeasures_client.client import FlexmeasuresClient


@pytest.mark.asyncio
async def test_get_access_token() -> None:
    with aioresponses() as m:
        m.post(
            "http://localhost:5000/api/requestAuthToken",
            status=200,
            payload={"auth_token": "test-token"},
        )
        flexmeasures_client = FlexmeasuresClient("test", "test")

        await flexmeasures_client.get_access_token()
        assert flexmeasures_client.access_token == "test-token"
    await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_post_measurements() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexmeasuresClient("test", "test")
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
async def test_trigger_schedule() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexmeasuresClient("test", "test")
        flexmeasures_client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/3/schedules/trigger",
            status=200,
            payload={"test": "test"},
        )

        await flexmeasures_client.post_schedule_trigger(
            sensor_id=3,
            start="2023-03-26T10:00+02:00",
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

        m.assert_called_once_with(
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            json={
                "start": "2023-03-26T10:00:00+02:00",
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


@pytest.mark.asyncio
async def test_get_schedule() -> None:
    # todo: relies on https://github.com/pnuckowski/aioresponses/pull/237
    with aioresponses() as m:
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid",
            status=400,
            payload={"message": "Scheduling job waiting"},
            repeat=3,
        )
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1/schedules/some-uuid",
            status=200,
            payload={
                "values": [2.15, 3, 2],
                "start": "2015-06-02T10:00:00+00:00",
                "duration": "PT45M",
                "unit": "MW",
            },
        )
        flexmeasures_client = FlexmeasuresClient(
            "test", "test", request_timeout=2, polling_interval=0.2
        )

        sensor_id = 1
        schedule_id = "some-uuid"
        duration = "PT45M"

        schedule, status = await flexmeasures_client.get_schedule(
            sensor_id, schedule_id, duration
        )
    assert schedule["values"] == [2.15, 3, 2]
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
        flexmeasures_client = FlexmeasuresClient(
            "test", "test", polling_timeout=0.5, request_timeout=0.2, polling_interval=0.1
        )

        sensor_id = 1
        schedule_id = "some-uuid"
        duration = "PT45M"

        with pytest.raises(ConnectionError):
            await flexmeasures_client.get_schedule(sensor_id, schedule_id, duration)
    await flexmeasures_client.close()
