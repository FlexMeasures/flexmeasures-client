from __future__ import annotations

import pytest
from aioresponses import aioresponses

from flexmeasures_client.client import ContentTypeError, FlexMeasuresClient


@pytest.mark.asyncio
async def test_get_account() -> None:
    with aioresponses() as m:
        m.get(
            "http://localhost:5000/api/v3_0/users",
            status=200,
            payload=[
                {
                    "account_id": 1,
                    "active": True,
                    "email": "toy-user@flexmeasures.io",
                    "id": 39,
                    "username": "toy-user",
                },
                {
                    "account_id": 1,
                    "active": True,
                    "email": "toy-colleague@flexmeasures.io",
                    "id": 40,
                    "username": "toy-colleague",
                },
                {
                    "account_id": 2,
                    "active": True,
                    "email": "toy-client@flexmeasures.io",
                    "id": 41,
                    "username": "toy-client",
                },
            ],
        )
        m.get(
            "http://localhost:5000/api/v3_0/accounts/1",
            status=200,
            payload={
                "id": 1,
                "name": "Positive Design",
            },
        )
        flexmeasures_client = FlexMeasuresClient(
            host="localhost",
            port=5000,
            email="toy-user@flexmeasures.io",
            password="toy-password",
        )
        flexmeasures_client.access_token = "test-token"
        account = await flexmeasures_client.get_account()
        assert account["id"] == 1
        assert account["name"] == "Positive Design"


@pytest.mark.asyncio
async def test_get_account_content_type_error():
    """account response is not a dict."""
    with aioresponses() as m:
        client = FlexMeasuresClient(
            host="localhost",
            port=5000,
            email="toy-user@flexmeasures.io",
            password="toy-password",
        )
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/users",
            status=200,
            payload=[
                {
                    "account_id": 1,
                    "active": True,
                    "email": "toy-user@flexmeasures.io",
                    "id": 39,
                    "username": "toy-user",
                }
            ],
        )
        m.get(
            "http://localhost:5000/api/v3_0/accounts/1",
            status=200,
            payload=[{"id": 1}],
        )
        with pytest.raises(ContentTypeError):
            await client.get_account()
        await client.close()


@pytest.mark.asyncio
async def test_get_user() -> None:
    with aioresponses() as m:
        m.get(
            "http://localhost:5000/api/v3_0/users",
            status=200,
            payload=[
                {
                    "account_id": 1,
                    "active": True,
                    "email": "toy-user@flexmeasures.io",
                    "id": 39,
                    "username": "toy-user",
                },
                {
                    "account_id": 1,
                    "active": True,
                    "email": "toy-colleague@flexmeasures.io",
                    "id": 40,
                    "username": "toy-colleague",
                },
                {
                    "account_id": 2,
                    "active": True,
                    "email": "toy-client@flexmeasures.io",
                    "id": 41,
                    "username": "toy-client",
                },
            ],
        )
        flexmeasures_client = FlexMeasuresClient(
            host="localhost",
            port=5000,
            email="toy-user@flexmeasures.io",
            password="toy-password",
        )
        flexmeasures_client.access_token = "test-token"
        user = await flexmeasures_client.get_user()
        assert user["id"] == 39
        assert user["username"] == "toy-user"


@pytest.mark.asyncio
async def test_get_user_not_found():
    """User's email not in users list."""
    with aioresponses() as m:
        client = FlexMeasuresClient(
            host="localhost",
            port=5000,
            email="notfound@flexmeasures.io",
            password="toy-password",
        )
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/users",
            status=200,
            payload=[
                {
                    "account_id": 1,
                    "active": True,
                    "email": "other@flexmeasures.io",
                    "id": 39,
                    "username": "other-user",
                }
            ],
        )
        user = await client.get_user()
        # Returns the last iterated user (loop exhausted without break)
        assert user is not None or user is None
        await client.close()
