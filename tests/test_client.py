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

        resp = (
            await flexmeasures_client.get_access_token()
        )  # await session.get('http://example.com')
        print(resp)
        assert resp == "test-token"
