from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from aioresponses import aioresponses

from flexmeasures_client.client import ContentTypeError, FlexMeasuresClient


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
async def test_trigger_schedule_with_custom_scheduler() -> None:
    """trigger_schedule correctly patches the asset's custom-scheduler attribute."""
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


@pytest.mark.asyncio
async def test_get_schedule_without_duration():
    """duration=None path omits params."""
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
    """schedule response is not a dict."""
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
async def test_trigger_and_get_schedule_asset_id_flex_model_list():
    """asset_id with flex_model as list triggers one schedule per sensor."""
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
    """asset_id with flex_model=None returns []."""
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
async def test_trigger_schedule_neither_sensor_nor_asset():
    """Neither sensor_id nor asset_id raises ValueError."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(ValueError, match="Pass either a sensor_id or an asset_id"):
        await client.trigger_schedule(
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
        )
    await client.close()


@pytest.mark.asyncio
async def test_trigger_schedule_both_sensor_and_asset():
    """Both sensor_id and asset_id raises ValueError."""
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
    """prior parameter is included in the schedule trigger message."""
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
    """scheduler set but asset_id is None raises ValueError."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(
        ValueError,
        match="Pass an asset_id instead of a sensor_id if selecting a custom scheduler\\.",
    ):
        await client.trigger_schedule(
            sensor_id=1,
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
            scheduler="my-scheduler",
        )
    await client.close()


@pytest.mark.asyncio
async def test_trigger_schedule_scheduler_with_str_attributes():
    """asset attributes is an unparseable string - falls back to empty dict."""
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
    """trigger response is a list, not dict raises ContentTypeError."""
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
    """trigger response has schedule as non-string raises ContentTypeError."""
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
