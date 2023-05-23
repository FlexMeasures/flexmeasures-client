from __future__ import annotations

import asyncio
import logging
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

import async_timeout
import pandas as pd
from aiohttp.client import ClientError, ClientSession
from yarl import URL

from flexmeasures_client.constants import API_VERSION, CONTENT_TYPE_HEADERS
from flexmeasures_client.response_handling import (
    check_content_type,
    check_for_status,
    check_response,
)

MAX_POLLING_STEPS: int = 10  # seconds
POLLING_TIMEOUT = 200.0  # seconds
REQUEST_TIMEOUT = 20.0  # seconds
POLLING_INTERVAL = 10.0  # seconds
API_VERSIONS_LIST = ("v3_0",)


@dataclass
class FlexMeasuresClient:
    """Main class for connecting to the FlexMeasures API"""

    password: str
    email: str
    access_token: str = None
    host: str = "localhost:5000"
    ssl: bool = False
    api_version: str = API_VERSION
    path: str = f"/api/{api_version}/"
    reauth_once: bool = True

    max_polling_steps: int = MAX_POLLING_STEPS
    polling_timeout: float = POLLING_TIMEOUT  # seconds
    request_timeout: float = REQUEST_TIMEOUT  # seconds
    polling_interval: float = POLLING_INTERVAL  # seconds
    session: ClientSession | None = None

    def __post_init__(self):
        if not re.match(r".+\@.+\..+", self.email):
            raise ValueError(f"{self.email} is not an email address format string")
        if self.api_version not in API_VERSIONS_LIST:
            raise ValueError(f"version not in versions list: {API_VERSIONS_LIST}")
        # if ssl then scheme is https.
        if self.ssl:
            self.scheme = "https"
        else:
            self.scheme = "http"
        if re.match(r"^http\:\/\/", self.host):
            host_without_scheme = self.host.removeprefix("http://")
            raise ValueError(
                f"http:// should not be included in {self.host}."
                f"Instead use host={host_without_scheme}"
            )
        if re.match(r"^https\:\/\/", self.host):
            host_without_scheme = self.host.removeprefix("https://")
            raise ValueError(
                f"https:// should not be included in {self.host}."
                f"To use https:// set ssl=True and host={host_without_scheme}"
            )
        if len(self.password) < 1:
            raise ValueError("password cannot be empty")

    async def close(self):
        """Function to close FlexMeasuresClient session when all requests are done"""
        await self.session.close()

    async def request(
        self,
        uri: str,
        *,
        json: dict | None = None,
        method: str = "POST",
        path: str = path,
        params: dict[str, Any] | None = None,
        include_auth: bool = True,
    ) -> tuple[dict, int]:
        """Send a request to FlexMeasures.

        Retries if:
        - the client request timed out (as indicated by the client's self.request_timeout)
        - the server response indicates a 408 (Request Timeout) status
        - the server response indicates a 503 (Service Unavailable) status with a Retry-After response header

        Fails if:
        - the server response indicated a status code of 400 or higher
        - the client polling timed out (as indicated by the client's self.polling_timeout)
        """  # noqa: E501
        url = self.build_url(uri, path=path)

        headers = await self.get_headers(include_auth=include_auth)
        self.start_session()

        polling_step = 0
        self.reauth_once = True  # reset this counter once when starting polling
        try:
            async with async_timeout.timeout(self.polling_timeout):
                while polling_step < self.max_polling_steps:
                    try:
                        async with async_timeout.timeout(self.request_timeout):
                            response = await self.request_once(
                                method=method,
                                url=url,
                                params=params,
                                headers=headers,
                                json=json,
                                polling_step=polling_step,
                            )
                            if response.status < 300:
                                break
                    except asyncio.TimeoutError:
                        message = f"Client request timeout occurred while connecting to the API. Retrying in {self.polling_interval} seconds..."  # noqa: E501
                        logging.info(message)
                        polling_step += 1
                        await asyncio.sleep(self.polling_interval)
                    except (ClientError, socket.gaierror) as exception:
                        raise ConnectionError(
                            "Error occurred while communicating with the API."
                        ) from exception
        except asyncio.TimeoutError as exception:
            raise ConnectionError(
                "Client polling timeout while connection to the API."
            ) from exception

        check_content_type(response)

        return cast(dict[str, Any], await response.json()), response.status

    async def request_once(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict | None = None,
        json: dict | None = None,
        polling_step: int = 0,
    ):
        """Sends a single request to FlexMeasures and checks the response"""
        response = await self.session.request(
            method=method,
            url=url,
            params=params,
            headers=headers,
            json=json,
            ssl=self.ssl,
        )
        polling_step = await check_response(self, response, polling_step)
        return response

    def start_session(self):
        """If there is no session, start one"""
        if self.session is None:
            self.session = ClientSession()

    async def get_headers(self, include_auth: bool) -> dict:
        """Create HTTP headers dictionary with content type and, optionally, access token."""  # noqa: E501
        headers = CONTENT_TYPE_HEADERS
        if include_auth:
            if self.access_token is None:
                await self.get_access_token()
            headers |= {"Authorization": self.access_token}
        return headers

    def build_url(self, uri: str, path: str = path):
        """Build url for request"""
        url = URL.build(scheme=self.scheme, host=self.host, path=path).join(
            URL(uri),
        )
        return url

    async def get_access_token(self):
        """Get access token and store it on the FlexMeasuresClient."""
        response, _status = await self.request(
            uri="requestAuthToken",
            path="/api/",
            json={
                "email": self.email,
                "password": self.password,
            },
            include_auth=False,
        )
        self.access_token = response["auth_token"]

    async def post_measurements(
        self,
        sensor_id: int,
        start: str | datetime,
        duration: str | timedelta,
        values: list[float],
        unit: str,
        entity_address: str,
        prior: str | None = None,
    ):
        """Post sensor data for the given time range."""
        json = dict(
            sensor=f"{entity_address}.{sensor_id}",
            start=pd.Timestamp(
                start
            ).isoformat(),  # for example: 2021-10-13T00:00+02:00
            duration=pd.Timedelta(duration).isoformat(),  # for example: PT1H
            values=values,
            unit=unit,
        )
        if prior:
            json["prior"] = prior

        _response, status = await self.request(
            uri="sensors/data",
            json=json,
        )
        check_for_status(status, 200)
        logging.info("Sensor data sent successfully.")

    async def trigger_storage_schedule(
        self,
        sensor_id: int,
        start: str | datetime,
        duration: str | timedelta,
        soc_unit: str,
        soc_at_start: float,
        soc_targets: list | None = None,
        consumption_price_sensor: int | None = None,
        production_price_sensor: int | None = None,
        inflexible_device_sensors: list[int] | None = None,
    ) -> str:
        """Post schedule trigger with initial and target states of charge (soc)."""
        if not soc_targets:
            soc_targets = []
        message = {
            "start": pd.Timestamp(
                start
            ).isoformat(),  # for example: 2021-10-13T00:00+02:00
            "duration": pd.Timedelta(duration).isoformat(),
            "flex-model": {
                "soc-unit": soc_unit,
                "soc-at-start": soc_at_start,
                "soc-targets": soc_targets,
            },
            "flex-context": {},
        }

        # Set optional flex context
        if consumption_price_sensor is not None:
            message["flex-context"][
                "consumption-price-sensor"
            ] = consumption_price_sensor
        if production_price_sensor is not None:
            message["flex-context"]["production-price-sensor"] = production_price_sensor
        if inflexible_device_sensors is not None:
            message["flex-context"][
                "inflexible-device-sensors"
            ] = inflexible_device_sensors

        response, status = await self.request(
            uri=f"sensors/{sensor_id}/schedules/trigger",
            json=message,
        )
        check_for_status(status, 200)
        logging.info("Schedule triggered successfully.")

        return response.get("schedule")

    async def get_schedule(
        self,
        sensor_id: int,
        schedule_id: str,
        duration: str | timedelta,
    ):
        """Get schedule with given ID."""
        response, status = await self.request(
            uri=f"sensors/{sensor_id}/schedules/{schedule_id}",
            method="GET",
            json={
                "duration": pd.Timedelta(duration).isoformat(),  # for example: PT1H
            },
        )
        check_for_status(status, 200)

        return response

    async def get_assets(self):
        """Get all the assets available to the current user"""
        response, status = await self.request(uri="assets", method="GET")
        check_for_status(status, 200)
        return response

    async def get_sensors(self):
        """Get all the sensors available to the current user"""
        response, status = await self.request(uri="sensors", method="GET")
        check_for_status(status, 200)
        return response
