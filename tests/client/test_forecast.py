import pytest
from aioresponses import aioresponses

from flexmeasures_client.client import FlexMeasuresClient


@pytest.mark.asyncio
async def test_trigger_forecast() -> None:
    """Test triggering a forecast with basic parameters."""
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"

        sensor_id = 1
        m.post(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/forecasts/trigger",
            status=200,
            payload={"forecast": "test-forecast-uuid"},
        )

        forecast_id = await flexmeasures_client.trigger_forecast(
            sensor_id=sensor_id,
            start="2025-01-05T00:00:00+00:00",
            end="2025-01-07T00:00:00+00:00",
        )

        assert forecast_id == "test-forecast-uuid"

        m.assert_called_once_with(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/forecasts/trigger",
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            json={
                "start": "2025-01-05T00:00:00+00:00",
                "end": "2025-01-07T00:00:00+00:00",
            },
            params=None,
            ssl=False,
            allow_redirects=False,
        )

        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_trigger_forecast_with_config() -> None:
    """Test triggering a forecast with training config parameters."""
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"

        sensor_id = 1
        m.post(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/forecasts/trigger",
            status=200,
            payload={"forecast": "test-forecast-uuid"},
        )

        forecast_id = await flexmeasures_client.trigger_forecast(
            sensor_id=sensor_id,
            start="2025-01-05T00:00:00+00:00",
            end="2025-01-07T00:00:00+00:00",
            train_start="2025-01-01T00:00:00+00:00",
            retrain_frequency="PT24H",
            future_regressors=[2, 3],
        )

        assert forecast_id == "test-forecast-uuid"

        m.assert_called_once_with(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/forecasts/trigger",
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            json={
                "start": "2025-01-05T00:00:00+00:00",
                "end": "2025-01-07T00:00:00+00:00",
                "config": {
                    "train-start": "2025-01-01T00:00:00+00:00",
                    "retrain-frequency": "P1DT0H0M0S",
                    "future-regressors": [2, 3],
                },
            },
            params=None,
            ssl=False,
            allow_redirects=False,
        )

        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_forecast_polling() -> None:
    """Test getting a forecast with polling (202 -> 202 -> 200)."""
    sensor_id = 1
    forecast_id = "test-uuid"
    url = f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/forecasts/{forecast_id}"

    with aioresponses() as m:
        # First call returns 202 (QUEUED)
        m.get(
            url=url,
            status=202,
            payload={"status": "QUEUED"},
        )
        # Second call returns 202 (STARTED)
        m.get(
            url=url,
            status=202,
            payload={"status": "STARTED"},
        )
        # Third call returns 200 (completed)
        m.get(
            url=url,
            status=200,
            payload={
                "values": [1.2, 1.5],
                "start": "2025-01-05T00:00:00+00:00",
                "duration": "PT2H",
                "unit": "kW",
            },
        )

        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            request_timeout=2,
            polling_interval=0.2,
            access_token="skip-auth",
        )

        forecast = await flexmeasures_client.get_forecast(
            sensor_id=sensor_id, forecast_id=forecast_id
        )

        assert forecast["values"] == [1.2, 1.5]
        assert forecast["start"] == "2025-01-05T00:00:00+00:00"
        assert forecast["duration"] == "PT2H"
        assert forecast["unit"] == "kW"

        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_trigger_and_get_forecast() -> None:
    """Test triggering and getting a forecast in one call."""
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
            request_timeout=2,
            polling_interval=0.2,
        )
        flexmeasures_client.access_token = "test-token"

        sensor_id = 1
        forecast_uuid = "test-forecast-uuid"

        # Mock the trigger request
        m.post(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/forecasts/trigger",
            status=200,
            payload={"forecast": forecast_uuid},
        )

        # Mock the get forecast request (with immediate 200 response)
        m.get(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/forecasts/{forecast_uuid}",
            status=200,
            payload={
                "values": [1.2, 1.5, 1.8],
                "start": "2025-01-05T00:00:00+00:00",
                "duration": "PT3H",
                "unit": "kW",
            },
        )

        forecast = await flexmeasures_client.trigger_and_get_forecast(
            sensor_id=sensor_id,
            start="2025-01-05T00:00:00+00:00",
            end="2025-01-07T00:00:00+00:00",
        )

        assert "values" in forecast
        assert forecast["values"] == [1.2, 1.5, 1.8]
        assert forecast["start"] == "2025-01-05T00:00:00+00:00"
        assert forecast["duration"] == "PT3H"
        assert forecast["unit"] == "kW"

        await flexmeasures_client.close()
