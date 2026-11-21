"""Optional system-keyring storage for the HEMS refresh token."""

import os
import re
import sys

from flexmeasures_client import FlexMeasuresClient
from flexmeasures_client.exceptions import EmailValidationError
from flexmeasures_client.utils import prompt_for_input


async def configure_refresh_token_storage(client: FlexMeasuresClient) -> None:
    """Use the system keyring if installed and available, otherwise prompt at login."""
    try:
        import keyring
    except ModuleNotFoundError as exc:
        if exc.name != "keyring":
            raise
        print(
            "Keyring is not installed in the Python environment running this script. "
            "Installing it lets you save a refresh token and avoid entering your "
            "password on every run.\n"
            f'Install it for this Python: "{sys.executable}" -m pip install keyring\n'
            "If that Python has no pip, use: "
            f'uv pip install --python "{sys.executable}" keyring\n'
            "If using uv run, you can instead add `--with keyring` to that command."
        )
        return

    service = f"flexmeasures-hems:{client.scheme}://{client.host}:{client.port}"
    account_service = f"{service}:account"
    email = os.environ.get("FLEXMEASURES_HEMS_EMAIL") or client.email
    token = None
    try:
        backend = keyring.get_keyring()
        if backend.priority <= 0:
            raise keyring.errors.KeyringError("No usable keyring backend")
        if not email:
            email = keyring.get_password(account_service, "default")
            if not email:
                # Discover tokens saved by earlier HEMS versions where possible.
                credential = keyring.get_credential(service, None)
                if credential:
                    email, token = credential.username, credential.password
        if email and token is None:
            token = keyring.get_password(service, email)
    except keyring.errors.KeyringError as exc:
        print(f"System keyring unavailable ({exc}); login will not be saved.")
        return

    if not email:
        if not sys.stdin.isatty():
            print("An interactive terminal is needed to select a keyring account.")
            return
        print(
            "The system keyring will save your refresh token, so you can avoid "
            "entering your password on later runs."
        )
        email = prompt_for_input("FlexMeasures email: ", input)
    if not re.match(r".+\@.+\..+", email):
        raise EmailValidationError(f"{email} is not an email address format string")
    if token:
        print(f"Using saved FlexMeasures login for {email}.")
    client.email = email

    def save_token(refresh_token: str) -> None:
        try:
            keyring.set_password(service, email, refresh_token)
            keyring.set_password(account_service, "default", email)
        except keyring.errors.KeyringError as exc:
            raise RuntimeError(
                "Could not save the refreshed token in the system keyring. "
                "The previous token may no longer be valid; log in again next time."
            ) from exc

    client.refresh_token = token
    client.refresh_token_callback = save_token
