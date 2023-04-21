import pytest
from aioresponses import aioresponses
import json
from unittest.mock import MagicMock
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

        resp = (
            await flexmeasures_client.get_access_token()
        )  # await session.get('http://example.com')
        print(resp)
        assert resp == "test-token"


@pytest.mark.asyncio
async def test_post_measurements() -> None:
    with aioresponses() as m:
        payload = {"zzzz": "test", "status_code" :200}

        m.post(
            "http://localhost:5000/api/v3_0/sensors/data",
            status=200,
            payload = payload
            #json.dumps({"auth_token": "test-token", "status_code" :200})
            # payload={"auth_token": "test-token", "status_code" :200},
        )
        flexmeasures_client = FlexmeasuresClient("test", "test")

        access_token = "test"
        sensor_id = "test"
        start = "2023-03-26T10:00+02:00"
        duration = "PT6H"
        values = "test"
        unit = "test"

        resp = (
            await flexmeasures_client.post_measurements(access_token, sensor_id, start, duration, values, unit)
        )  # await session.get('http://example.com')
        print(resp)
        assert resp == "test-token"
