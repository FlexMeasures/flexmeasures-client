import asyncio
import logging

import pytest
from aioresponses import aioresponses

from flexmeasures_client import FlexMeasuresClient

BASE = "http://localhost:5000/api/"


@pytest.mark.asyncio
async def test_login_and_refresh_rotate_and_notify(caplog):
    saved = []
    with aioresponses() as mock:
        mock.post(
            BASE + "requestAuthToken",
            payload={"auth_token": "access1", "refresh-token": "refresh1"},
        )
        mock.get(BASE + "v3_0/sensors", status=401, payload={})
        mock.post(
            BASE + "refreshAuthToken",
            payload={"auth_token": "access2", "refresh-token": "refresh2"},
        )
        mock.get(BASE + "v3_0/sensors", payload=[])
        client = FlexMeasuresClient(
            "secret-password",
            "test@test.test",
            use_refresh_tokens=True,
            refresh_token_callback=saved.append,
        )
        with caplog.at_level(logging.DEBUG):
            await client.get_sensors(parse_json_fields=False)
        assert saved == ["refresh1", "refresh2"]
        assert client.access_token == "access2"
        assert client.refresh_token == "refresh2"
        assert mock.requests[
            ("POST", client.build_url("refreshAuthToken", path="/api/"))
        ][0].kwargs["json"] == {"refresh-token": "refresh1"}
        assert all(
            secret not in caplog.text
            for secret in [
                "secret-password",
                "access1",
                "access2",
                "refresh1",
                "refresh2",
            ]
        )
        assert "secret-password" not in repr(client) and "refresh2" not in repr(client)
        await client.close()


@pytest.mark.asyncio
async def test_old_server_unknown_field_falls_back():
    with aioresponses() as mock:
        mock.post(
            BASE + "requestAuthToken",
            status=422,
            payload={
                "message": {"json": {"include-refresh-token": ["Unknown field."]}}
            },
        )
        mock.post(BASE + "requestAuthToken", payload={"auth_token": "legacy"})
        client = FlexMeasuresClient(
            "password", "test@test.test", use_refresh_tokens=True
        )
        assert await client.get_access_token() == "legacy"
        assert client.refresh_token is None
        calls = mock.requests[
            ("POST", client.build_url("requestAuthToken", path="/api/"))
        ]
        assert calls[0].kwargs["json"] == {
            "email": client.email,
            "password": client.password,
            "include-refresh-token": True,
        }
        assert calls[1].kwargs["json"] == {
            "email": client.email,
            "password": client.password,
        }
        await client.close()


@pytest.mark.asyncio
async def test_rejected_refresh_logs_in_again():
    with aioresponses() as mock:
        mock.post(BASE + "refreshAuthToken", status=401, payload={})
        mock.post(
            BASE + "requestAuthToken",
            payload={"auth_token": "new", "refresh-token": "new-refresh"},
        )
        client = FlexMeasuresClient(
            "password",
            "test@test.test",
            use_refresh_tokens=True,
            refresh_token="expired",
        )
        assert (await client.get_headers(True))["Authorization"] == "new"
        assert client.refresh_token == "new-refresh"
        await client.close()


@pytest.mark.asyncio
async def test_concurrent_reauthentication_rotates_only_once():
    with aioresponses() as mock:
        mock.post(
            BASE + "refreshAuthToken",
            payload={"auth_token": "new", "refresh-token": "rotated"},
        )
        client = FlexMeasuresClient(
            "password",
            "test@test.test",
            use_refresh_tokens=True,
            access_token="old",
            refresh_token="original",
        )
        assert await asyncio.gather(
            client._authenticate("old"), client._authenticate("old")
        ) == ["new", "new"]
        assert client.refresh_token == "rotated"
        mock.assert_called_once()
        await client.close()


@pytest.mark.asyncio
async def test_refresh_503_does_not_retry_or_send_password():
    with aioresponses() as mock:
        mock.post(BASE + "refreshAuthToken", status=503, payload={})
        client = FlexMeasuresClient(
            "password",
            "test@test.test",
            use_refresh_tokens=True,
            refresh_token="original",
        )
        with pytest.raises(ConnectionError, match="503"):
            await client.get_headers(True)
        mock.assert_called_once()
        await client.close()


@pytest.mark.asyncio
async def test_refresh_timeout_does_not_retry():
    with aioresponses() as mock:
        mock.post(BASE + "refreshAuthToken", exception=asyncio.TimeoutError())
        client = FlexMeasuresClient(
            "password",
            "test@test.test",
            use_refresh_tokens=True,
            refresh_token="original",
        )
        with pytest.raises(ConnectionError, match="outcome is unknown"):
            await client.get_headers(True)
        mock.assert_called_once()
        await client.close()


@pytest.mark.asyncio
async def test_unrelated_login_validation_error_does_not_fall_back():
    with aioresponses() as mock:
        mock.post(
            BASE + "requestAuthToken",
            status=422,
            payload={"message": {"email": ["Invalid email"]}},
        )
        client = FlexMeasuresClient(
            "password", "test@test.test", use_refresh_tokens=True
        )
        with pytest.raises(ValueError, match="Invalid email"):
            await client.get_access_token()
        mock.assert_called_once()
        await client.close()


@pytest.mark.asyncio
async def test_get_access_token_refreshes_without_sending_password():
    with aioresponses() as mock:
        mock.post(
            BASE + "refreshAuthToken",
            payload={"auth_token": "new", "refresh-token": "rotated"},
        )
        client = FlexMeasuresClient(
            "password", "test@test.test", refresh_token="original"
        )
        assert await client.get_access_token() == "new"
        assert client.refresh_token == "rotated"
        mock.assert_called_once()
        await client.close()


@pytest.mark.asyncio
async def test_refresh_tokens_can_be_disabled():
    with aioresponses() as mock:
        mock.post(BASE + "requestAuthToken", payload={"auth_token": "new"})
        client = FlexMeasuresClient(
            "password", "test@test.test", use_refresh_tokens=False
        )
        assert await client.get_access_token() == "new"
        calls = mock.requests[
            ("POST", client.build_url("requestAuthToken", path="/api/"))
        ]
        assert calls[0].kwargs["json"] == {
            "email": client.email,
            "password": client.password,
        }
        await client.close()


@pytest.mark.asyncio
async def test_token_only_authentication():
    with aioresponses() as mock:
        mock.post(
            BASE + "refreshAuthToken",
            payload={"auth_token": "new", "refresh-token": "rotated"},
        )
        client = FlexMeasuresClient(refresh_token="original")
        assert await client.get_access_token() == "new"
        mock.assert_called_once()
        await client.close()


@pytest.mark.asyncio
async def test_interactive_login_and_rejected_token(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "user@example.com")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "secret")
    with aioresponses() as mock:
        mock.post(BASE + "refreshAuthToken", status=401, payload={})
        mock.post(
            BASE + "requestAuthToken",
            payload={"auth_token": "new", "refresh-token": "rotated"},
        )
        client = FlexMeasuresClient(refresh_token="expired")
        assert await client.get_access_token() == "new"
        calls = mock.requests[
            ("POST", client.build_url("requestAuthToken", path="/api/"))
        ]
        assert calls[0].kwargs["json"] == {
            "email": "user@example.com",
            "password": "secret",
            "include-refresh-token": True,
        }
        await client.close()


@pytest.mark.asyncio
async def test_noninteractive_login_requires_credentials(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    client = FlexMeasuresClient()
    with pytest.raises(RuntimeError, match="no interactive terminal"):
        await client.get_access_token()
    await client.close()


@pytest.mark.asyncio
async def test_interactive_prompts_only_for_missing_password(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "builtins.input", lambda prompt: pytest.fail("Unexpected email prompt")
    )
    monkeypatch.setattr("getpass.getpass", lambda prompt: "secret")
    with aioresponses() as mock:
        mock.post(BASE + "requestAuthToken", payload={"auth_token": "new"})
        client = FlexMeasuresClient(email="user@example.com")
        assert await client.get_access_token() == "new"
        await client.close()
