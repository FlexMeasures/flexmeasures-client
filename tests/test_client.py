import pytest_asyncio
import pytest
import pytest_mock
from aiohttp import ClientSession
import aiohttp
import json
from unittest.mock import AsyncMock, patch, call
from aiohttp.test_utils import make_mocked_request

from src.flexmeasures_client.client import FlexmeasuresClient

flexmeasures_client = FlexmeasuresClient("test", "test")

class MockResponse:
    def __init__(self, text, status):
        self._text = text
        self.status = status

    async def text(self):
        return self._text

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def __aenter__(self):
        return self

@pytest.mark.asyncio
async def test_get_access_token(mocker):
    with patch("get_access_token", )
    mock = aiohttp.ClientSession
    mock.request = AsyncMock()
    mock.__aenter__.status = 200
    mock.__aenter__.headers.get("Content-Type", "") = ["application/json"]
    mock.__aenter__.data = {"test":"test", "auth_token":"test"}
    data = {}

    resp_dict = await flexmeasures_client.get_access_token()
    print(resp_dict)

    assert 1 == 2


