from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import async_timeout
import pandas as pd
from aiohttp.client import ClientError, ClientResponse, ClientSession
from yarl import URL

from flexmeasures_client.constants import (
    API_VERSION,
    CONTENT_TYPE_HEADERS,
    ENTITY_ADDRESS_PLACEHOLDER,
)
from flexmeasures_client.exceptions import (
    ContentTypeError,
    EmailValidationError,
    EmptyPasswordError,
    WrongAPIVersionError,
    WrongHostError,
)
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
    host: str = "localhost:5000"
    ssl: bool = False
    api_version: str = API_VERSION
    path: str = f"/api/{api_version}/"
    access_token: str | None = None

    max_polling_steps: int = MAX_POLLING_STEPS
    polling_timeout: float = POLLING_TIMEOUT  # seconds
    request_timeout: float = REQUEST_TIMEOUT  # seconds
    polling_interval: float = POLLING_INTERVAL  # seconds
    session: ClientSession = ClientSession()

    def __post_init__(self):
        if not re.match(r".+\@.+\..+", self.email):
            raise EmailValidationError(
                f"{self.email} is not an email address format string"
            )
        if self.api_version not in API_VERSIONS_LIST:
            raise WrongAPIVersionError(
                f"Version {self.api_version} not in versions list: {API_VERSIONS_LIST}"
            )
        # if ssl then scheme is https.
        if self.ssl:
            self.scheme = "https"
        else:
            self.scheme = "http"
        if re.match(r"^http\:\/\/", self.host):
            host_without_scheme = self.host.removeprefix("http://")
            raise WrongHostError(
                f"http:// should not be included in {self.host}."
                f"Instead use host={host_without_scheme}"
            )
        if re.match(r"^https\:\/\/", self.host):
            host_without_scheme = self.host.removeprefix("https://")
            raise WrongHostError(
                f"https:// should not be included in {self.host}."
                f"To use https:// set ssl=True and host={host_without_scheme}"
            )
        if len(self.password) < 1:
            raise EmptyPasswordError("password cannot be empty")

    async def close(self):
        """Function to close FlexMeasuresClient session when all requests are done"""
        await self.session.close()

    async def request(
        self,
        uri: str,
        *,
        json_payload: dict | None = None,
        method: str = "POST",
        path: str = path,
        params: dict[str, Any] | None = None,
        include_auth: bool = True,
    ) -> tuple[dict | list, int]:
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

        self.start_session()

        polling_step = 0  # reset this counter once when starting polling
        # we allow retrying once if we include authentication headers
        reauth_once = True if include_auth else False
        try:
            async with async_timeout.timeout(self.polling_timeout):
                while polling_step < self.max_polling_steps:
                    headers = await self.get_headers(include_auth=include_auth)
                    try:
                        async with async_timeout.timeout(self.request_timeout):
                            (
                                response,
                                polling_step,
                                reauth_once,
                                url,
                            ) = await self.request_once(
                                method=method,
                                url=url,
                                params=params,
                                headers=headers,
                                json_payload=json_payload,
                                polling_step=polling_step,
                                reauth_once=reauth_once,
                            )
                            if response.status < 300:
                                break
                    except asyncio.TimeoutError:
                        message = f"Client request timeout occurred while connecting to the API. Polling step: {polling_step}. Retrying in {self.polling_interval} seconds..."  # noqa: E501
                        logging.debug(message)
                        polling_step += 1
                        await asyncio.sleep(self.polling_interval)
                    except (ClientError, socket.gaierror) as exception:
                        logging.debug(exception)
                        raise ConnectionError(
                            "Error occurred while communicating with the API."
                        ) from exception
        except asyncio.TimeoutError as exception:
            raise ConnectionError(
                "Client polling timeout while connection to the API."
            ) from exception

        check_content_type(response)

        return await response.json(), response.status

    async def request_once(
        self,
        method: str,
        url: URL,
        params: dict[str, Any] | None = None,
        headers: dict | None = None,
        json_payload: dict | None = None,
        polling_step: int = 0,
        reauth_once: bool = True,
    ) -> tuple[ClientResponse, int, bool, URL]:
        url_msg = f"url: {url}"
        json_msg = f"payload: {json_payload}"
        params_msg = f"params: {params}"
        method_msg = f"method: {method}"
        headers_msg = f"headers: {headers}"
        logging.debug("===== Request =====")
        logging.debug(url_msg)
        logging.debug(json_msg)
        logging.debug(params_msg)
        logging.debug(method_msg)
        logging.debug(headers_msg)
        logging.debug("=" * 14)

        """Sends a single request to FlexMeasures and checks the response"""
        response = await self.session.request(
            method=method,
            url=url,
            params=params,
            headers=headers,
            json=json_payload,
            ssl=self.ssl,
            allow_redirects=False,
        )
        payload = await response.json()
        status_msg = f"status: {response.status}"
        response_payload_msg = f"payload: {payload}"
        headers_msg = f"headers: {response.headers}"

        logging.debug("===== Response =====")
        logging.debug(status_msg)
        logging.debug(response_payload_msg)
        logging.debug(headers_msg)
        logging.debug("=" * 14)

        polling_step, reauth_once, url = await check_response(
            self, response, polling_step, reauth_once, url
        )
        return response, polling_step, reauth_once, url

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

    def build_url(self, uri: str, path: str = path) -> URL:
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
            json_payload={
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
        prior: str | None = None,
    ):
        """
        Post sensor data for the given time range.
        This function raises a ValueError when an unhandled status code is returned
        """
        json_payload = dict(
            sensor=f"{ENTITY_ADDRESS_PLACEHOLDER}.{sensor_id}",
            start=pd.Timestamp(
                start
            ).isoformat(),  # for example: 2021-10-13T00:00+02:00
            duration=pd.Timedelta(duration).isoformat(),  # for example: PT1H
            values=values,
            unit=unit,
        )
        if prior:
            json_payload["prior"] = prior

        _response, status = await self.request(
            uri="sensors/data",
            json_payload=json_payload,
        )
        check_for_status(status, 200)
        logging.info("Sensor data sent successfully.")

    async def get_schedule(
        self,
        sensor_id: int,
        schedule_id: str,
        duration: str | timedelta,
    ) -> dict:
        """Get schedule with given ID.

        :returns: schedule as dictionary, for example:
                  {
                      'values': [2.15, 3, 2],
                      'start': '2015-06-02T10:00:00+00:00',
                      'duration': 'PT45M',
                      'unit': 'MW'
                  }
        """
        schedule, status = await self.request(
            uri=f"sensors/{sensor_id}/schedules/{schedule_id}",
            method="GET",
            params={
                "duration": pd.Timedelta(duration).isoformat(),  # for example: PT1H
            },
        )
        check_for_status(status, 200)
        if not isinstance(schedule, dict):
            raise ContentTypeError(
                f"Expected a dictionary schedule, but got {type(schedule)}",
            )
        return schedule

    async def get_assets(self) -> list[dict]:
        """Get all the assets available to the current user.

        :returns: list of assets as dictionaries

        This function raises a ValueError when an unhandled status code is returned
        """
        assets, status = await self.request(uri="assets", method="GET")
        check_for_status(status, 200)

        if not isinstance(assets, list):
            raise ContentTypeError(
                f"Expected a list of assets, but got {type(assets)}",
            )
        return assets

    async def get_sensors(self) -> list[dict]:
        """Get all the sensors available to the current user.

        :returns: list of sensors as dictionaries
        """
        sensors, status = await self.request(uri="sensors", method="GET")
        check_for_status(status, 200)
        if not isinstance(sensors, list):
            raise ContentTypeError(
                f"Expected a list of sensors, but got {type(sensors)}",
            )
        return sensors

    async def trigger_and_get_schedule(
        self,
        sensor_id: int,
        start: str | datetime,
        duration: str | timedelta,
        flex_model: dict,
        flex_context: dict,
    ) -> dict:
        """Trigger a schedule and then fetch it.

        :returns: schedule as dictionary, for example:
                {
                    'values': [2.15, 3, 2],
                    'start': '2015-06-02T10:00:00+00:00',
                    'duration': 'PT45M',
                    'unit': 'MW'
                }
        This function raises a ValueError when an unhandled status code is returned
        """
        schedule_id = await self.trigger_schedule(
            sensor_id=sensor_id,
            start=start,
            duration=duration,
            flex_model=flex_model,
            flex_context=flex_context,
        )

        schedule = await self.get_schedule(
            sensor_id=sensor_id, schedule_id=schedule_id, duration=duration
        )
        return schedule

    async def get_sensor_data(
        self,
        sensor_id: int,
        start: str | datetime,
        duration: str | timedelta,
        unit: str,
        resolution: str | timedelta,
        **kwargs,
    ) -> dict:
        """Get sensor data for the given time range.

        :returns: sensor data as dictionary, for example:
                {
                    'values': [2.15, 3, 2],
                    'start': '2015-06-02T10:00:00+00:00',
                    'duration': 'PT45M',
                    'unit': 'MW'
                }

        This function raises a ValueError when an unhandled status code is returned
        """
        params = dict(
            sensor=f"{ENTITY_ADDRESS_PLACEHOLDER}.{sensor_id}",
            start=pd.Timestamp(
                start
            ).isoformat(),  # for example: 2021-10-13T00:00+02:00
            duration=pd.Timedelta(duration).isoformat(),  # for example: PT1H
            unit=unit,
            resolution=resolution,
            **kwargs,
        )

        response, status = await self.request(
            uri="sensors/data", method="GET", params=params
        )
        check_for_status(status, 200)
        if not isinstance(response, dict):
            raise ContentTypeError(
                f"Expected a sensor data dictionary, but got {type(response)}",
            )
        data_fields = ("values", "start", "duration", "unit")
        sensor_data = {k: v for k, v in response.items() if k in data_fields}
        return sensor_data

    async def get_sensor(self, sensor_id: int) -> dict:
        """Get a single sensor

        :returns: sensor as dictionary, for example:
                {
                    'attributes': '{}',
                    'entity_address': 'ea1.2023-09.localhost:fm1.35',
                    'event_resolution': 'PT5M',
                    'generic_asset_id': 24,
                    'id': 35,
                    'name': 'availability',
                    'timezone': 'Europe/Amsterdam',
                    'unit': '%'
                }

        This function raises a ValueError when an unhandled status code is returned
        """
        uri = f"sensors/{sensor_id}"
        sensor, status = await self.request(uri=uri, method="GET")
        check_for_status(status, 200)
        if not isinstance(sensor, dict):
            raise ContentTypeError(
                f"Expected a sensor dictionary, but got {type(sensor)}",
            )
        return sensor

    async def add_sensor(
        self,
        name: str,
        event_resolution: str,
        unit: str,
        generic_asset_id: int,
        timezone: str | None = None,
        attributes: dict | None = None,
    ) -> dict:
        """Post a sensor

        :returns: sensor as dictionary, for example:
                {
                    'attributes': '{}',
                    'entity_address': 'ea1.2023-09.localhost:fm1.35',
                    'event_resolution': 'PT5M',
                    'generic_asset_id': 24,
                    'id': 35,
                    'name': 'availability',
                    'timezone': 'Europe/Amsterdam',
                    'unit': '%'
                }

        This function raises a ValueError when an unhandled status code is returned
        """
        sensor = dict(
            name=name,
            event_resolution=event_resolution,
            unit=unit,
            generic_asset_id=generic_asset_id,
        )
        if timezone:
            sensor["timezone"] = timezone
        if attributes:
            sensor["attributes"] = json.dumps(attributes)
        uri = "sensors"
        new_sensor, status = await self.request(
            uri=uri, json_payload=sensor, method="POST"
        )
        check_for_status(status, 201)
        if not isinstance(new_sensor, dict):
            raise ContentTypeError(
                f"Expected a sensor dictionary, but got {type(new_sensor)}",
            )
        return new_sensor

    async def add_asset(
        self,
        name: str,
        account_id: int,
        latitude: float,
        longitude: float,
        generic_asset_type_id: int,
        attributes: dict | None = None,
    ) -> dict:
        """Post an asset

        :returns: asset as dictionary, for example:
                {
                    'account_id': 2,
                    'attributes': '{"sensors_to_show": [14, 37, 38, 39]}',
                    'generic_asset_type_id': 5,
                    'id': 25,
                    'latitude': 51.999,
                    'longitude': 4.4833,
                    'name': 'Test Name Asset17',
                    'status': 200
                }

        This function raises a ValueError when an unhandled status code is returned
        """
        asset = dict(
            name=name,
            account_id=account_id,
            latitude=latitude,
            longitude=longitude,
            generic_asset_type_id=generic_asset_type_id,
        )
        if attributes:
            asset["attributes"] = json.dumps(attributes)

        uri = "assets"
        new_asset, status = await self.request(
            uri=uri, json_payload=asset, method="POST"
        )
        check_for_status(status, 201)
        if not isinstance(new_asset, dict):
            raise ContentTypeError(
                f"Expected an asset dictionary, but got {type(new_asset)}",
            )
        return new_asset

    async def update_asset(self, asset_id: int, updates: dict) -> dict:
        """Patch an asset

        :returns: asset as dictionary, for example:
                {
                    'account_id': 2,
                    'attributes': '{"sensors_to_show": [14, 37, 38, 39]}',
                    'generic_asset_type_id': 5,
                    'id': 25,
                    'latitude': 51.999,
                    'longitude': 4.4833,
                    'name': 'Test Name Asset17',
                    'status': 200
                }

        This function raises a ValueError when an unhandled status code is returned
        """
        uri = f"assets/{asset_id}"
        if updates.get("attributes"):
            updates["attributes"] = json.dumps(updates["attributes"])
        updated_asset, status = await self.request(
            uri=uri, json_payload=updates, method="PATCH"
        )
        check_for_status(status, 200)
        if not isinstance(updated_asset, dict):
            raise ContentTypeError(
                f"Expected an asset dictionary, but got {type(updated_asset)}",
            )
        return updated_asset

    async def update_sensor(self, sensor_id: int, updates: dict) -> dict:
        """Patch a sensor

        :returns: sensor as dictionary, for example:
                {
                    'attributes': '{}',
                    'entity_address': 'ea1.2023-09.localhost:fm1.35',
                    'event_resolution': 'PT5M',
                    'generic_asset_id': 24,
                    'id': 35,
                    'name': 'availability',
                    'timezone': 'Europe/Amsterdam',
                    'unit': '%'
                }

        This function raises a ValueError when an unhandled status code is returned
        """
        uri = f"sensors/{sensor_id}"
        if updates.get("attributes"):
            updates["attributes"] = json.dumps(updates["attributes"])
        updated_sensor, status = await self.request(
            uri=uri, json_payload=updates, method="PATCH"
        )
        # Raise ValueError
        check_for_status(status, 200)
        if not isinstance(updated_sensor, dict):
            raise ContentTypeError(
                f"Expected a sensor dictionary, but got {type(updated_sensor)}",
            )
        return updated_sensor

    async def trigger_schedule(
        self,
        sensor_id: int,
        start: str | datetime,
        duration: str | timedelta,
        flex_model: dict,
        flex_context: dict,
    ) -> str:
        message = {
            "start": pd.Timestamp(
                start
            ).isoformat(),  # for example: 2021-10-13T00:00+02:00
            "duration": pd.Timedelta(duration).isoformat(),
            "flex-model": flex_model,
            "flex-context": flex_context,
        }
        response, status = await self.request(
            uri=f"sensors/{sensor_id}/schedules/trigger",
            json_payload=message,
        )
        check_for_status(status, 200)

        logging.info("Schedule triggered successfully.")
        if not isinstance(response, dict):
            raise ContentTypeError(
                f"Expected a dictionary, but got {type(response)}",
            )

        if not isinstance(response.get("schedule"), str):
            raise ContentTypeError(
                f"Expected a schedule ID, but got {type(response.get('schedule'))}",
            )
        schedule_id = response["schedule"]
        return schedule_id

    @staticmethod
    def create_storage_flex_model(
        soc_unit: str,
        soc_at_start: float,
        soc_max: float | None = None,
        soc_min: float | None = None,
        soc_targets: list | None = None,
        roundtrip_efficiency: float | None = None,
        storage_efficiency: float | None = None,
        soc_minima: list | None = None,
        soc_maxima: list | None = None,
    ) -> dict:
        flex_model = {
            "soc-unit": soc_unit,
            "soc-at-start": soc_at_start,
        }

        if soc_max is not None:
            flex_model["soc-max"] = soc_max
        if soc_min is not None:
            flex_model["soc-min"] = soc_min
        if roundtrip_efficiency is not None:
            flex_model["roundtrip-efficiency"] = roundtrip_efficiency
        if storage_efficiency is not None:
            flex_model["storage-efficiency"] = storage_efficiency
        if soc_minima:
            flex_model["soc-minima"] = soc_minima
        if soc_maxima:
            flex_model["soc-maxima"] = soc_maxima
        if soc_targets:
            flex_model["soc-targets"] = soc_targets

        return flex_model

    # add type hints
    @staticmethod
    def create_storage_flex_context(
        consumption_price_sensor: int | None = None,
        production_price_sensor: int | None = None,
        inflexible_device_sensors: int | list[int] | None = None,
    ) -> dict:
        flex_context: dict = {}
        # Set optional flex context
        if consumption_price_sensor is not None:
            flex_context["consumption-price-sensor"] = consumption_price_sensor
        if production_price_sensor is not None:
            flex_context["production-price-sensor"] = production_price_sensor
        if inflexible_device_sensors:
            flex_context["inflexible-device-sensors"] = inflexible_device_sensors

        return flex_context

    @staticmethod
    def convert_units(values: list[int | float], from_unit: str, to_unit: str) -> list:
        """Convert values between W, kW and MW, as required."""
        if from_unit == "MW" and to_unit == "W":
            values = [v * 10**6 for v in values]
        elif (from_unit == "MW" and to_unit == "kW") or (
            from_unit == "kW" and to_unit == "W"
        ):
            values = [v * 10**3 for v in values]
        elif from_unit == to_unit:
            pass
        elif (from_unit == "W" and to_unit == "kW") or (
            from_unit == "kW" and to_unit == "MW"
        ):
            values = [v * 10**-3 for v in values]
        elif from_unit == "W" and to_unit == "MW":
            values = [v * 10**-6 for v in values]
        else:
            raise NotImplementedError(
                f"Power conversion from {from_unit} to {to_unit} is not supported."
            )
        return values
