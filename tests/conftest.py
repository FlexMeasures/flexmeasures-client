"""Shared test setup."""

from inspect import signature
from unittest.mock import Mock

import aiohttp
import aioresponses.core
import pytest


@pytest.fixture(autouse=True)
def support_aiohttp_314_in_aioresponses(monkeypatch):
    """Provide the stream writer that aioresponses omits for aiohttp 3.14+."""
    if "stream_writer" not in signature(aiohttp.ClientResponse).parameters:
        return

    class MockClientResponse(aiohttp.ClientResponse):
        def __init__(self, method, url, **kwargs):
            kwargs.setdefault("stream_writer", Mock(output_size=0))
            super().__init__(method, url, **kwargs)

    monkeypatch.setattr(aioresponses.core, "ClientResponse", MockClientResponse)
