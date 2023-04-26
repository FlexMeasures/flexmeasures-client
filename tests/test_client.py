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


@pytest.mark.asyncio
async def test_post_measurements() -> None:
    with aioresponses() as m:
        payload = {"zzzz": "test"}

        m.post(
            "http://localhost:5000/api/v3_0/sensors/data",
            status=200,
            payload=payload,
        )
        flexmeasures_client = FlexmeasuresClient("test", "test")

        sensor_id = "test"
        start = "2023-03-26T10:00+02:00"
        duration = "PT6H"
        values = "test"
        unit = "test"

        await flexmeasures_client.post_measurements(
            sensor_id, start, duration, values, unit
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
            "test", "test", request_timeout=2, polling_delay=0.2
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
            "test", "test", polling_timeout=0.5, request_timeout=0.2, polling_delay=0.1
        )

        sensor_id = 1
        schedule_id = "some-uuid"
        duration = "PT45M"

        with pytest.raises(ConnectionError):
            await flexmeasures_client.get_schedule(sensor_id, schedule_id, duration)
    await flexmeasures_client.close()
