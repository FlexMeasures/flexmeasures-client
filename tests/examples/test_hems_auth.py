"""Exercise HEMS refresh-token storage without requiring a system keyring."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from aioresponses import aioresponses

sys.path.insert(0, str(Path(__file__).parents[2] / "examples" / "HEMS"))

from utils.auth_utils import configure_refresh_token_storage  # noqa: E402

from flexmeasures_client import FlexMeasuresClient  # noqa: E402


def mock_terminal(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "user@example.com")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "password")


@pytest.mark.asyncio
async def test_missing_keyring_explains_benefit_before_login(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "keyring", None)
    client = FlexMeasuresClient()
    try:
        await configure_refresh_token_storage(client)
        notice = capsys.readouterr().out
        assert "--with keyring" in notice
        assert f'"{sys.executable}" -m pip install keyring' in notice
        assert f'uv pip install --python "{sys.executable}" keyring' in notice
        assert "If using uv run" in notice
        assert "password" in notice
        assert client.email is None
        assert client.refresh_token is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_keyring_persists_and_rotates_between_runs(monkeypatch):
    store = {}
    keyring = SimpleNamespace(
        get_keyring=lambda: SimpleNamespace(priority=1),
        get_password=lambda service, email: store.get((service, email)),
        get_credential=lambda service, email: None,
        set_password=lambda service, email, token: store.__setitem__(
            (service, email), token
        ),
        errors=SimpleNamespace(KeyringError=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    mock_terminal(monkeypatch)
    service = "flexmeasures-hems:http://localhost:5000"
    with aioresponses() as mock:
        mock.post(
            "http://localhost:5000/api/requestAuthToken",
            payload={"auth_token": "access1", "refresh-token": "token1"},
        )
        mock.post(
            "http://localhost:5000/api/refreshAuthToken",
            payload={"auth_token": "access2", "refresh-token": "token2"},
        )
        first = FlexMeasuresClient()
        try:
            await configure_refresh_token_storage(first)
            assert await first.get_access_token() == "access1"
            assert store[(service, "user@example.com")] == "token1"
        finally:
            await first.close()
        second = FlexMeasuresClient()
        try:
            monkeypatch.setattr(
                "builtins.input", lambda prompt: pytest.fail("Unexpected email prompt")
            )
            await configure_refresh_token_storage(second)
            assert await second.get_access_token() == "access2"
            assert store[(service, "user@example.com")] == "token2"
            assert second.password is None
        finally:
            await second.close()


@pytest.mark.asyncio
async def test_existing_token_can_be_discovered_without_an_email_prompt(monkeypatch):
    stored = {}
    keyring = SimpleNamespace(
        get_keyring=lambda: SimpleNamespace(priority=1),
        get_password=lambda service, email: stored.get((service, email)),
        get_credential=lambda service, email: SimpleNamespace(
            username="user@example.com", password="original"
        ),
        set_password=lambda service, email, token: stored.__setitem__(
            (service, email), token
        ),
        errors=SimpleNamespace(KeyringError=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setattr(
        "builtins.input", lambda prompt: pytest.fail("Unexpected prompt")
    )
    client = FlexMeasuresClient()
    try:
        await configure_refresh_token_storage(client)
        assert client.email == "user@example.com"
        assert client.refresh_token == "original"
        assert client.refresh_token_callback is not None
        client.refresh_token_callback("rotated")
        assert (
            stored[("flexmeasures-hems:http://localhost:5000:account", "default")]
            == "user@example.com"
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_email_override_selects_a_different_account(monkeypatch):
    service = "flexmeasures-hems:http://localhost:5000"
    stored = {(service, "other@example.com"): "other-token"}
    keyring = SimpleNamespace(
        get_keyring=lambda: SimpleNamespace(priority=1),
        get_password=lambda service, email: stored.get((service, email)),
        get_credential=lambda service, email: pytest.fail(
            "Unexpected account discovery"
        ),
        set_password=lambda service, email, token: stored.__setitem__(
            (service, email), token
        ),
        errors=SimpleNamespace(KeyringError=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setenv("FLEXMEASURES_HEMS_EMAIL", "other@example.com")
    client = FlexMeasuresClient()
    try:
        await configure_refresh_token_storage(client)
        assert client.email == "other@example.com"
        assert client.refresh_token == "other-token"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unavailable_backend_falls_back_to_interactive_login(monkeypatch, capsys):
    keyring = SimpleNamespace(
        get_keyring=lambda: SimpleNamespace(priority=0),
        errors=SimpleNamespace(KeyringError=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    mock_terminal(monkeypatch)
    client = FlexMeasuresClient()
    try:
        await configure_refresh_token_storage(client)
        assert client.email is None
        assert client.refresh_token_callback is None
        assert "login will not be saved" in capsys.readouterr().out
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_keyring_write_failure_reports_that_token_may_have_rotated(monkeypatch):
    def fail_to_save(service, email, token):
        raise RuntimeError("keyring locked")

    keyring = SimpleNamespace(
        get_keyring=lambda: SimpleNamespace(priority=1),
        get_password=lambda service, email: (
            "user@example.com" if email == "default" else "previous-token"
        ),
        set_password=fail_to_save,
        errors=SimpleNamespace(KeyringError=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    mock_terminal(monkeypatch)
    client = FlexMeasuresClient()
    try:
        await configure_refresh_token_storage(client)
        with aioresponses() as mock:
            mock.post(
                "http://localhost:5000/api/refreshAuthToken",
                payload={"auth_token": "access", "refresh-token": "rotated-token"},
            )
            with pytest.raises(
                RuntimeError, match="previous token may no longer be valid"
            ):
                await client.get_access_token()
        assert client.refresh_token == "rotated-token"
    finally:
        await client.close()
