from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from json import loads
from typing import Any, cast

import async_timeout
import pandas as pd
from aiohttp.client import ClientError, ClientSession
from yarl import URL


@dataclass
class FlexmeasuresClient:
    """Main class for connecting to the Flexmeasures API"""

    password: str
    email: str
    access_token: str = None
    scheme: str = "http"
    host: str = "localhost:5000"
    local: bool = False
    version: str = "/v3_0/"
    path: str = f"/api{version}"

    max_polling_steps: int = 10  # seconds
    polling_timeout: float = 200.0  # seconds
    request_timeout: float = 20.0  # seconds
    request_step: float = 10  # seconds
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
    ) -> tuple[dict, int]:
        url = URL.build(scheme="http", host="localhost:5000", path=path).join(
            URL(uri),
        )
        print(method)
        print(url)
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.access_token,
        }

        if self.session is None:
            self.session = ClientSession()

        retry_function = lambda x, y: getattr(x, "status") == 400 and (
            "Scheduling job waiting" in y.get("message", "") or "Scheduling job in progress" in y.get("message", "")
        )

        poll_step = 0
        async with async_timeout.timeout(self.polling_timeout):
            while poll_step < self.max_polling_steps:
                try:
                    async with async_timeout.timeout(self.request_timeout):
                        response = await self.session.request(
                            method=method,
                            url=url,
                            params=params,
                            headers=headers,
                            json=json,
                            ssl=False if "localhost" in self.host else True,
                        )
                        payload = await response.json()
                        response.raise_for_status()
                        break
                except asyncio.TimeoutError as exception:
                    msg = "Timeout occurred while connecting to the API."
                    raise ConnectionError(
                        msg,
                    ) from exception
                except (ClientError, socket.gaierror) as exception:
                    if retry_function(exception, payload):
                        poll_step += 1
                        await asyncio.sleep(self.request_step)
                    else:
                        msg = "Error occurred while communicating with the API."
                        raise ConnectionError(
                            msg,
                        ) from exception

        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            text = await response.text()
            msg = "Unexpected content type response from the API"
            raise TypeError(
                msg,
                {"Content-Type": content_type, "response": text},
            )

        return cast(dict[str, Any], await response.json()), response.status

    async def get_access_token(self):
        """Get access token and store it on the FlexMeasuresClient."""
        response, status = await self.request(
            uri="requestAuthToken",
            path="/api/",
            json={
                "email": self.email,
                "password": self.password,
            },
        )
        self.access_token = response["auth_token"]

    async def post_measurements(
        self,
        sensor_id: int,
        start: str,
        duration: str,
        values: list[float],
        unit: str,
    ):
        """Post sensor data for the given time range."""

        # POST data
        response, status = await self.request(
            uri="sensors/data",
            json=dict(
                sensor=f"ea1.2022-04.nl.seita.flexmeasures:fm1.{sensor_id}",
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
    ):
        """Post schedule trigger with initial and target states of charge (soc)."""
        response, status = await self.request(
            uri=f"sensors/{sensor_id}/schedules/trigger",
            json={
                "start": pd.Timestamp(
                    start
                ).isoformat(),  # for example: 2021-10-13T00:00+02:00
                "flex-model": {
                    "soc-unit": soc_unit,
                    "soc-at-start": soc_at_start,
                    "soc-targets": soc_targets,
                },
                # TODO remove hardcoded sensor id
                "flex-context": {
                    "consumption-price-sensor": 3,
                },
            },
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
