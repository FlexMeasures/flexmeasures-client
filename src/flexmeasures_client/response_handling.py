from __future__ import annotations
from typing import TYPE_CHECKING
import asyncio
from aiohttp import ContentTypeError

from flexmeasures_client.constants import CONTENT_TYPE


if TYPE_CHECKING:  # Only imports the below statements during type checking
    from flexmeasures_client.client import FlexMeasuresClient

if TYPE_CHECKING:  # Only imports the below statements during type checking
    from flexmeasures_client.client import FlexMeasuresClient

async def check_response(self: FlexMeasuresClient, response):
    """
    <300: passes
    401: reauthenticate
    todo: 503 + Retry-After header: poll again
    otherwise: call error_handler
    """
    status = response.status
    payload = await response.json()
    headers = response.headers
    if status < 300:
        pass
    elif status == 401:
        self.get_access_token()
        self.reauth_once = False
    elif status == 503 and "Retry-After" in headers:
        self.polling_step += 1
        await asyncio.sleep(self.polling_interval)
    elif status == 400 and (
        "Scheduling job waiting" in payload.get("message", "")
        or "Scheduling job in progress" in payload.get("message", "")
    ):
        # can be removed in a later version GH issue #645 of the FlexMeasures repo
        print(
            f"Server indicated to try again later. Retrying in {self.polling_interval} seconds..."  # noqa: E501
        )
        self.polling_step += 1
        await asyncio.sleep(self.polling_interval)
    else:
        response.raise_for_status()


def check_content_type(response):
    content_type = response.headers.get("Content-Type", "")
    if CONTENT_TYPE not in content_type:
        text = response.text()
        raise ContentTypeError(
            "Unexpected content type response from the API",
            {"Content-Type": content_type, "response": text},
        )


def check_for_status(status, expected_status):
    if status != expected_status:
        raise ValueError(
            f"Request failed with status code {status}"
        )
