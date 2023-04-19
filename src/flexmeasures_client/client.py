"""authoristaion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import socket
from typing import Any, cast

from aiohttp.client import ClientError, ClientSession
import async_timeout
from yarl import URL
import pandas as pd


@dataclass
class FlexmeasuresClient:
    """Main class for connecting to the Flexmeasures API"""

    password: str
    email: str
    scheme: str = "http"
    host: str = "localhost:5000"
    local: bool = False
    version: str = "/v3_0/"

    request_timeout: float = 10.0
    session: ClientSession | None = None

    _close_session: bool = False

    async def request(
        self,
        uri: str,
        *,
        method: str = "POST",
        path: str = f"/api{version}",
        params: dict[str, Any] | None = None,
        json: dict | None = None,
        headers: dict | None = None,
    ) -> Any:

        url = URL.build(scheme="http", host="localhost:5000", path=path).join(
            URL(uri),
        )
        print(url)
        if not headers:
            headers: dict = {}

        if self.session is None:
            self.session = ClientSession()
            self._close_session = True

        try:
            async with async_timeout.timeout(self.request_timeout):
                response = await self.session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json,
                    ssl=False,
                )
                print(await response.json())
                response.raise_for_status()
        except asyncio.TimeoutError as exception:
            msg = "Timeout occurred while connecting to the API."
            raise ConnectionError(
                msg,
            ) from exception
        except (ClientError, socket.gaierror) as exception:
            msg = "Error occurred while communicating with the API."
            raise ConnectionError(
                msg,
            ) from exception

        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            text = await response.text()
            msg = "Unexpected content type response from the EnergyZero API"
            raise TypeError(
                msg,
                {"Content-Type": content_type, "response": text},
            )

        return cast(dict[str, Any], await response.json())

    async def get_access_token(self):
        """lalalal.Missing argument descriptions in the docstring: `json`, `method`, `params`, `path`, `uri`."""
        response = await self.request(
            uri="requestAuthToken",
            path="/api/",
            json={
                "email": self.email,
                "password": self.password,
            },
        )
        print(response)
        return response["auth_token"]

    async def post_measurements(
        self,
        access_token: str,
        sensor_id: int,
        start: str,
        duration: str,
        values: list[float],
        unit: str,
    ) -> str:
        """Post sensor data for the given time range."""

        # POST data
        response = await self.request(
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
            headers={"Content-Type": "application/json", "Authorization": access_token},
        )
        if response.status_code != 200:
            raise ValueError(
                f"Request failed with status code {response.status_code} and message: {response.json()}"
            )
        return response.json()


fm = FlexmeasuresClient(email="guus@seita.nl", password="test")
access_token = await fm.get_access_token()
print(access_token)

post_response = await fm.post_measurements(
    access_token=access_token,
    sensor_id=1,
    start="2023-03-26T10:00+02:00",  # bare in mind DST transitions in case of POSTing local times (for NL, +02:00 becomes +01:00 and vice versa), or stick to POSTing times in UTC (+00:00)
    duration="PT6H",
    values=[15.3, 0, -3.9, 100, 0, -100],
    unit="kW",
)
print(post_response)
