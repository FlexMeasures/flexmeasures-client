from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from typing import Any, cast

import async_timeout
import pandas as pd
from aiohttp import ContentTypeError
from aiohttp.client import ClientError, ClientSession
from yarl import URL

from flexmeasures_client.response_handling import check_response

CONTENT_TYPE_HEADERS = {
    "Content-Type": "application/json",
}


@dataclass
class FlexmeasuresClient:
    """Main class for connecting to the Flexmeasures API"""

    password: str
    email: str
    access_token: str = None
    host: str = "localhost:5000"
    scheme: str = "http" if "localhost" in host else "https"
    ssl: bool = False if "localhost" in host else True
    api_version: str = "v3_0"
    path: str = f"/api/{api_version}/"
    consumption_price_sensor: int = 3

    max_polling_steps: int = 10  # seconds
    polling_timeout: float = 200.0  # seconds
    request_timeout: float = 20.0  # seconds
    polling_interval: float = 10  # seconds
    session: ClientSession | None = None

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
        headers: dict | None = None,
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
        url = URL.build(scheme=self.scheme, host=self.host, path=path).join(
            URL(uri),
        )
        print(url)
        headers = self.create_headers()

        if self.session is None:
            self.session = ClientSession()

        # def client_should_retry(exception, payload) -> bool:
        #     return getattr(exception, "status") == 400 and (
        #         "Scheduling job waiting" in payload.get("message", "")
        #         or "Scheduling job in progress" in payload.get("message", "")
        #     )

        polling_step = 0
        reauth_step = 0  # reset this counter once when starting polling
        try:
            async with async_timeout.timeout(self.polling_timeout):
                while polling_step < self.max_polling_steps:
                    try:
                        async with async_timeout.timeout(self.request_timeout):
                            response = await self.session.request(
                                method=method,
                                url=url,
                                params=params,
                                headers=headers,
                                json=json,
                                ssl=self.ssl,
                            )
                            payload = await response.json()
                            check_response(
                                self,
                                response.status,
                                payload,
                                response.headers,
                                reauth_step,
                                response.raise_for_status,
                            )
                            print(response.headers)
                            break
                    except asyncio.TimeoutError:
                        print(
                            f"Client request timeout occurred while connecting to the API. Retrying in {self.polling_interval} seconds..."
                        )
                        polling_step += 1
                        await asyncio.sleep(self.polling_interval)
                    except (ClientError, socket.gaierror) as exception:
                        if self.client_should_retry(exception, payload):
                            print(
                                f"Server indicated to try again later. Retrying in {self.polling_interval} seconds..."
                            )
                            polling_step += 1
                            await asyncio.sleep(self.polling_interval)
                        else:
                            raise ConnectionError(
                                "Error occurred while communicating with the API."
                            ) from exception
        except asyncio.TimeoutError as exception:
            raise ConnectionError(
                "Client polling timeout while connection to the API."
            ) from exception

        await self.check_content_type(response)

        return cast(dict[str, Any], await response.json()), response.status

    async def check_content_type(self, response):
        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            text = await response.text()
            raise ContentTypeError(
                "Unexpected content type response from the API",
                {"Content-Type": content_type, "response": text},
            )


    def client_should_retry(self, exception, payload) -> bool:
        return getattr(exception, "status") == 400 and (
            "Scheduling job waiting" in payload.get("message", "")
            or "Scheduling job in progress" in payload.get("message", "")
        )

    def build_url(self):
        pass

    def create_headers(self):
        headers = CONTENT_TYPE_HEADERS
        if self.access_token:
            headers |= {"Authorization": self.access_token}
        print(headers)
        return headers

    async def get_access_token(self):
        """Get access token and store it on the FlexMeasuresClient."""
        response, _status = await self.request(
            uri="requestAuthToken",
            path="/api/",
            json={
                "email": self.email,
                "password": self.password,
            },
            headers={},
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
    ):
        """Post sensor data for the given time range."""
        # TODO add option to add prior to post.
        # POST data
        response, status = await self.request(
            uri="sensors/data",
            json=dict(
                sensor=f"{entity_address}.{sensor_id}",
                start=pd.Timestamp(
                    start
                ).isoformat(),  # for example: 2021-10-13T00:00+02:00
                duration=pd.Timedelta(duration).isoformat(),  # for example: PT1H
                values=values,
                unit=unit,
            ),
        )
        if status != 200:
            raise ValueError(
                f"Request failed with status code {status} and message: {response}"
            )
        print("Sensor data sent successfully.")

    async def post_schedule_trigger(
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
        if status != 200:
            raise ValueError(
                f"Request failed with status code {status} and message: {response}"
            )
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
        if status != 200:
            raise ValueError(
                f"Request failed with status code {status} and message: {response}"
            )

        return response, status

    async def get_assets(self):
        """Get all the assets available to the current user"""
        response, status = await self.request(uri="assets", method="GET")
        return response, status

    async def get_sensors(self):
        """Get all the sensors available to the current user"""
        response, status = await self.request(uri="sensors", method="GET")
        return response, status
