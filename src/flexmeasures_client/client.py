from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from typing import Any, cast

import async_timeout
import pandas as pd
from aiohttp.client import ClientError, ClientSession
from yarl import URL

from flexmeasures_client.response_handling import (
    check_response,
    check_content_type,
    check_for_status,
)

CONTENT_TYPE_HEADERS = {
    "Content-Type": "application/json",
}
API_VERSIOM = "v3_0"

MAX_POLLING_STEPS: int = 10  # seconds
POLLING_TIMEOUT = 200.0  # seconds
REQUEST_TIMEOUT = 20.0  # seconds
POLLING_INTERVAL = 10.0  # seconds


@dataclass
class FlexMeasuresClient:
    """Main class for connecting to the FlexMeasures API"""

    password: str
    email: str
    access_token: str = None
    host: str = "localhost:5000"
    scheme: str = ""
    ssl: bool | None = None
    api_version: str = API_VERSIOM
    path: str = f"/api/{api_version}/"
    consumption_price_sensor: int = (
        3  # TODO find sensor and use sensor through API or set in config
    )
    reauth_once: bool = True

    polling_step: int = 0
    max_polling_steps: int = MAX_POLLING_STEPS
    polling_timeout: float = POLLING_TIMEOUT  # seconds
    request_timeout: float = REQUEST_TIMEOUT  # seconds
    polling_interval: float = POLLING_INTERVAL  # seconds
    session: ClientSession | None = None

    def __post_init__(self):
        if not self.scheme:
            self.scheme: str = "http" if "localhost" in self.host else "https"
        if self.ssl is None:
            self.ssl: bool = False if "localhost" in self.host else True

    async def close(self):
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
        """
        url = self.build_url(uri, path=path)
        print(url)

        headers = await self.get_headers(include_auth=include_auth)
        self.start_session()

        self.polling_step = 0
        self.reauth_once = True  # reset this counter once when starting polling
        try:
            async with async_timeout.timeout(self.polling_timeout):
                while self.polling_step < self.max_polling_steps:
                    try:
                        async with async_timeout.timeout(self.request_timeout):
                            response = await self.request_once(
                                method=method,
                                url=url,
                                params=params,
                                headers=headers,
                                json=json,
                            )
                            break
                    except asyncio.TimeoutError:
                        print(
                            f"Client request timeout occurred while connecting to the API. Retrying in {self.polling_interval} seconds..."
                        )
                        self.polling_step += 1
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
        await check_response(
            self,
            response,
        )
        print(response.headers)
        return response

    def start_session(self):
        """If there is no session, start one"""
        if self.session is None:
            self.session = ClientSession()

    async def get_headers(self, include_auth: bool) -> dict:
        """If the request needs to be authenticated check if there is a access_token or request one. Then create the headers dict"""
        headers = CONTENT_TYPE_HEADERS
        if include_auth:
            if self.access_token is None:
                await self.get_access_token()
            headers |= {"Authorization": self.access_token}
        print(headers)
        return headers

    def build_url(self, uri: str, path: str = path):
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
        print(response, _status)
        self.access_token = response["auth_token"]

    async def post_measurements(
        self,
        sensor_id: int,
        start: str,
        duration: str,
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

        response, status = await self.request(
            uri="sensors/data",
            json=json,
        )
        check_for_status(status, 200)
        print("Sensor data sent successfully.")

    async def trigger_storage_schedule(
        self,
        sensor_id: int,
        start: str,
        soc_unit: str,
        soc_at_start: float,
        soc_targets: list,
        consumption_price_sensor: int | None = None,
        production_price_sensor: int | None = None,
        inflexible_device_sensors: list[int] | None = None,
    ):
        """Post schedule trigger with initial and target states of charge (soc)."""
        message = {
            "start": pd.Timestamp(
                start
            ).isoformat(),  # for example: 2021-10-13T00:00+02:00
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
        print("Schedule triggered successfully.")

    async def get_schedule(
        self,
        sensor_id: int,
        schedule_id: str,
        duration: str,
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

        return response, status

    async def get_assets(self):
        """Get all the assets available to the current user"""
        response, status = await self.request(uri="assets", method="GET")
        check_for_status(status, 200)
        return response, status

    async def get_sensors(self):
        """Get all the sensors available to the current user"""
        response, status = await self.request(uri="sensors", method="GET")
        check_for_status(status, 200)
        return response, status
