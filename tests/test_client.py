import pytest
from aioresponses import aioresponses
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
    with aioresponses() as m:
        flexmeasures_client = FlexmeasuresClient("test", "test")
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors/3/schedules/schedule-id-string",
            status=200,
            payload={"test": "test"},
        )
        await flexmeasures_client.get_schedule(
            sensor_id=3, schedule_id="schedule-id-string", duration="PT24H"
        )
        m.assert_called_once_with(
            url="http://localhost:5000/api/v3_0/sensors/3/schedules/schedule-id-string",
            params=None,
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            json={"duration": "P1DT0H0M0S"},
            ssl=False
        )
