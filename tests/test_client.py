import pytest_asyncio
import pytest
import pytest_mock
from aiohttp import ClientSession
import aiohttp
import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
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
    mock = aiohttp.ClientSession
    mock.request = AsyncMock()
    mock.__aenter__.status = 200
    mock.__aenter__.headers.get = ["application/json"]
    mock.__aenter__.data = {"test":"test"}
    data = {}

    resp_dict = await flexmeasures_client.get_access_token()


def test_fail():
    assert 1 == 2
