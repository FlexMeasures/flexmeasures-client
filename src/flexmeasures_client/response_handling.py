from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Callable

from aiohttp import ContentTypeError
from yarl import URL

from flexmeasures_client.constants import CONTENT_TYPE

if TYPE_CHECKING:  # Only imports the below statements during type checking
    from flexmeasures_client.client import FlexMeasuresClient

logger = logging.getLogger(__name__)


def parse_retry_after(
    value: str | None, *, now: datetime | None = None
) -> float | None:
    """Return the delay described by an HTTP Retry-After header.

    RFC 9110 permits either a number of seconds or an HTTP date. Invalid and
    negative values are ignored so callers can fall back to their local retry
    policy.
    """
    if value is None:
        return None

    try:
        delay = float(value)
    except (TypeError, ValueError):
        pass
    else:
        if math.isfinite(delay) and delay >= 0:
            return delay
        return None

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - current_time).total_seconds())


async def check_response(
    self: FlexMeasuresClient,
    response,
    polling_step: int,
    reauth_once: bool,
    url: URL,
    method: str = "GET",
    pass_through_statuses: frozenset[int] = frozenset(),
    rate_limit_callback: Callable[[str], None] | None = None,
) -> tuple[int, bool, URL]:
    """
    <300: passes
    202 on GET: job not ready yet
    303: redirect to new url
    400 + custom message: schedule not ready yet
    401: reauthenticate
    429: retry after the server-requested delay, or with local backoff
    503 + Retry-After header: retry after the server-requested delay
    otherwise: call error_handler

    Returns: tuple of (polling_step, reauth_once, url)
     - polling_step: incremented if we need to poll again, otherwise unchanged
     - reauth_once: set to False if we re-authenticated (on 401), otherwise unchanged
     - url: updated if we get a redirect (303), otherwise unchanged
    """
    status = response.status
    payload = await response.json()
    if payload is None:
        payload = {}
    headers = response.headers
    if status in pass_through_statuses:
        pass
    elif status == 202 and method.upper() == "GET":
        sleep_interval = self.request_retry_interval * (2**polling_step)
        job_status = payload.get("status")
        message = "Server accepted the request but the result is not ready yet."
        if job_status:
            message += f" Job status: {job_status}."
        message += f" Retrying in {sleep_interval} seconds..."
        self.logger.debug(message)
        polling_step += 1
        await asyncio.sleep(sleep_interval)
    elif status < 300:
        pass
    elif response.status == 303:
        message = f"Redirect to fallback schedule: {response.headers['location']}"  # noqa: E501
        self.logger.debug(message)
        url = response.headers["location"]
    elif status == 400 and (
        "Scheduling job waiting" in payload.get("message", "")
        or "Scheduling job in progress" in payload.get("message", "")
        or "Scheduling job has an unknown status" in payload.get("message", "")
    ):
        # can be removed in a later version GH issue #645 of the FlexMeasures repo
        sleep_interval = self.request_retry_interval * (2**polling_step)
        message = f"Server indicated to try again later. Retrying in {sleep_interval} seconds..."  # noqa: E501
        self.logger.debug(message)
        polling_step += 1
        await asyncio.sleep(sleep_interval)
    elif status == 401 and reauth_once:
        message = f"""Authentication failed with"
        status: {status}
        headers: {headers}
        payload: {payload}.
        Re-authenticating!"""
        self.logger.debug(message)
        await self.get_access_token()
        reauth_once = False
    elif status == 429 or (status == 503 and "Retry-After" in headers):
        sleep_interval = parse_retry_after(headers.get("Retry-After"))
        if sleep_interval is None:
            sleep_interval = self.request_retry_interval * (2**polling_step)
            delay_source = "local exponential backoff"
        else:
            delay_source = "Retry-After"
        message = (
            f"Rate limit reached for {method.upper()} {url.path}. Retrying in "
            f"{sleep_interval:g} seconds using {delay_source}."
        )
        if status == 429:
            if rate_limit_callback is not None:
                rate_limit_callback(message)
            else:
                self.logger.warning(message)
        else:
            self.logger.debug(message)
        polling_step += 1
        await asyncio.sleep(sleep_interval)
    elif payload.get("errors"):
        # try to raise any error messages from the response
        raise ValueError(" ,".join(payload.get("errors")))
    elif payload.get("message") and status != 404:
        # For most non-2xx responses with a JSON message, raise ValueError.
        # 404s are excluded so they fall through to response.raise_for_status(),
        # preserving the aiohttp.ClientError behavior used by version checks.
        raise ValueError(
            f"Request failed with status code {status}: {payload.get('message')}"
        )
    else:
        message = f"""
        status: {status}
        headers: {headers}
        payload: {payload}.
        """
        self.logger.error(message)
        # otherwise, raise if the status does not indicate okay
        response.raise_for_status()
    return polling_step, reauth_once, URL(url)


def check_content_type(response):
    """Check if response is in format application/json"""
    content_type = response.headers.get("Content-Type", "")
    if CONTENT_TYPE not in content_type:
        text = response.text()
        raise ContentTypeError(
            "Unexpected content type response from the API",
            {"Content-Type": content_type, "response": text},
        )


def check_for_status(status, expected_status):
    """Check if status is an acceptable 2xx status code.

    Accepts any 2xx status code as success. Logs at INFO level if the status
    differs from the expected status but is still a 2xx success code.
    Raises ValueError for non-2xx status codes.
    """
    if 200 <= status < 300:
        if status != expected_status:
            logger.info(
                f"Received HTTP status {status} instead of expected {expected_status}."
            )
    else:
        raise ValueError(f"Request failed with status code {status}")
