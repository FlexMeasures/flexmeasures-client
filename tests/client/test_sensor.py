from __future__ import annotations

import os
import re
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

import pandas as pd
import pytest
from aioresponses import aioresponses

from flexmeasures_client.client import ContentTypeError, FlexMeasuresClient
from flexmeasures_client.exceptions import InsufficientServerVersionError


@pytest.mark.asyncio
async def test_get_sensors() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload=[
                {
                    "entity_address": "ea1.1000-01.required-but-unused-field:fm1.2",
                    "event_resolution": 15,
                    "generic_asset_id": 3,
                    "name": "discharging",
                    "timezone": "Europe/Amsterdam",
                    "unit": "MW",
                }
            ],
        )

        sensors = await flexmeasures_client.get_sensors(parse_json_fields=False)
        assert len(sensors) == 1
        assert (
            sensors[0]["entity_address"]
            == "ea1.1000-01.required-but-unused-field:fm1.2"
        )
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_sensors2() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=9999,
            payload={"error": "9999 error test"},
        )

        with pytest.raises(
            ConnectionError, match="Error occurred while communicating with the API."
        ):
            await flexmeasures_client.get_sensors(parse_json_fields=False)
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_sensors_with_asset_id():
    """asset_id added to URI."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors?asset_id=3",
            status=200,
            payload=[{"id": 1, "name": "sensor1"}],
        )
        sensors = await client.get_sensors(asset_id=3, parse_json_fields=False)
        assert len(sensors) == 1
        await client.close()


@pytest.mark.asyncio
async def test_get_sensors_content_type_error():
    """sensors response is a dict, not list."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload={"id": 1},
        )
        with pytest.raises(ContentTypeError):
            await client.get_sensors(parse_json_fields=False)
        await client.close()


@pytest.mark.asyncio
async def test_get_sensors_default_parse_json_fields_warning():
    """default parse_json_fields=None emits FutureWarning."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload=[{"id": 1, "name": "sensor1"}],
        )
        with pytest.warns(FutureWarning, match="get_sensors"):
            sensors = await client.get_sensors()
        assert len(sensors) == 1
        await client.close()


@pytest.mark.asyncio
async def test_get_sensors_parse_json_fields_true():
    """parse JSON fields in sensors."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors",
            status=200,
            payload=[
                {
                    "id": 1,
                    "name": "sensor1",
                    "attributes": '{"key": "value"}',
                }
            ],
        )
        sensors = await client.get_sensors(parse_json_fields=True)
        assert len(sensors) == 1
        assert isinstance(sensors[0]["attributes"], dict)
        assert sensors[0]["attributes"]["key"] == "value"
        await client.close()


@pytest.mark.asyncio
async def test_get_sensor_no_parse():
    """get_sensor with parse_json_fields=False."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=200,
            payload={
                "id": 1,
                "name": "test-sensor",
                "attributes": '{"key": "val"}',
                "unit": "MW",
            },
        )
        sensor = await client.get_sensor(sensor_id=1, parse_json_fields=False)
        assert sensor["id"] == 1
        assert isinstance(sensor["attributes"], str)
        await client.close()


@pytest.mark.asyncio
async def test_get_sensor_parse_json_fields_true():
    """parse_json_fields=True parses attributes."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=200,
            payload={
                "id": 1,
                "name": "test-sensor",
                "attributes": '{"key": "val"}',
                "unit": "MW",
            },
        )
        sensor = await client.get_sensor(sensor_id=1, parse_json_fields=True)
        assert sensor["id"] == 1
        assert isinstance(sensor["attributes"], dict)
        assert sensor["attributes"]["key"] == "val"
        await client.close()


@pytest.mark.asyncio
async def test_get_sensor_default_warning():
    """default parse_json_fields=None emits FutureWarning."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=200,
            payload={"id": 1, "name": "test-sensor"},
        )
        with pytest.warns(FutureWarning, match="get_sensor"):
            sensor = await client.get_sensor(sensor_id=1)
        assert sensor["id"] == 1
        await client.close()


@pytest.mark.asyncio
async def test_add_sensor():
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors",
            status=201,
            payload={
                "id": 35,
                "name": "test-sensor",
                "unit": "MW",
                "event_resolution": "PT15M",
                "generic_asset_id": 1,
            },
        )
        sensor = await client.add_sensor(
            name="test-sensor",
            event_resolution="PT15M",
            unit="MW",
            generic_asset_id=1,
        )
        assert sensor["id"] == 35
        assert sensor["name"] == "test-sensor"
        await client.close()


@pytest.mark.asyncio
async def test_add_sensor_with_optional_params():
    """Optional timezone and attributes."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors",
            status=201,
            payload={
                "id": 36,
                "name": "test-sensor2",
                "unit": "kW",
                "event_resolution": "PT30M",
                "generic_asset_id": 2,
                "timezone": "Europe/Amsterdam",
                "attributes": '{"key": "val"}',
            },
        )
        sensor = await client.add_sensor(
            name="test-sensor2",
            event_resolution="PT30M",
            unit="kW",
            generic_asset_id=2,
            timezone="Europe/Amsterdam",
            attributes={"key": "val"},
        )
        assert sensor["id"] == 36
        await client.close()


@pytest.mark.asyncio
async def test_update_sensor():
    with aioresponses() as m:
        m.patch(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=200,
            payload={"testpayload": "test_payload"},
        )
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test",
            password="test",
        )
        flexmeasures_client.access_token = "test-token"
        await flexmeasures_client.update_sensor(
            sensor_id=1, updates={"attributes": {"key": "value"}}
        )

        m.assert_called_once_with(
            "http://localhost:5000/api/v3_0/sensors/1",
            method="PATCH",
            json={"attributes": '{"key": "value"}'},
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            params=None,
            ssl=False,
            allow_redirects=False,
        )
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_update_sensor_content_type_error():
    """sensor update response is a list, not dict."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.patch(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=200,
            payload=[{"id": 1}],
        )
        with pytest.raises(ContentTypeError):
            await client.update_sensor(sensor_id=1, updates={"name": "new-name"})
        await client.close()


@pytest.mark.asyncio
async def test_delete_sensor_no_confirm():
    """confirm_first=False skips prompt."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.delete(
            "http://localhost:5000/api/v3_0/sensors/1",
            status=204,
            payload={},
        )
        await client.delete_sensor(sensor_id=1, confirm_first=False)
        await client.close()


@pytest.mark.asyncio
async def test_delete_sensor_confirm_no():
    """confirm_first default (True), user says no."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.access_token = "test-token"
    with patch("builtins.input", return_value="n"):
        await client.delete_sensor(sensor_id=1)
    await client.close()


@pytest.mark.asyncio
async def test_delete_sensor_data_preserves_sensor():
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        client.server_version = "0.33.0"
        m.delete(
            "http://localhost:5000/api/v3_0/sensors/7/data",
            status=204,
            payload={},
        )

        await client.delete_sensor_data(sensor_id=7, confirm_first=False)

        m.assert_called_once_with(
            "http://localhost:5000/api/v3_0/sensors/7/data",
            method="DELETE",
            json={},
            headers={
                "Content-Type": "application/json",
                "Authorization": "test-token",
            },
            params=None,
            ssl=False,
            allow_redirects=False,
        )
        await client.close()


@pytest.mark.asyncio
async def test_delete_sensor_data_confirmation_declined():
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.access_token = "test-token"
    with (
        patch("builtins.input", return_value="n"),
        patch.object(client, "request", new_callable=AsyncMock) as request,
    ):
        await client.delete_sensor_data(sensor_id=7)
    request.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"

        sensor_id = 5
        m.post(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/data",
            status=200,
            payload={"test": "test"},
        )

        start = "2023-03-26T10:00+02:00"
        duration = "PT6H"
        values = "test"
        unit = "test"

        response, status = await flexmeasures_client.post_sensor_data(
            sensor_id=sensor_id,
            start=start,
            duration=duration,
            values=values,
            unit=unit,
        )
        assert response == {"test": "test"}
        assert status == 200
        m.assert_called_once_with(
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/data",
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "test-token"},
            json={
                "start": "2023-03-26T10:00:00+02:00",
                "duration": "P0DT6H0M0S",
                "values": "test",
                "unit": "test",
            },
            params=None,
            ssl=False,
            allow_redirects=False,
        )
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_json_accepted_returns_ingestion_job() -> None:
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/5/data",
            status=202,
            payload={
                "job": "ingestion-job-id",
                "status": "ACCEPTED",
            },
        )

        response, status = await client.post_sensor_data(
            sensor_id=5,
            start="2023-03-26T10:00+02:00",
            duration="PT1H",
            values=[1.0],
            unit="kW",
        )

        assert response["job"] == "ingestion-job-id"
        assert status == 202
        await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_no_params():
    """No json params and no file_path raises ValueError."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(
        ValueError,
        match="Either provide JSON data parameters \\(start, duration, values\\) or a file_path parameter, but not neither\\.",
    ):
        await client.post_sensor_data(sensor_id=1)
    await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_both_params():
    """Both json params AND file_path raises ValueError."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(
        ValueError,
        match="Either provide JSON data parameters \\(start, duration, values\\) or a file_path parameter, but not both\\.",
    ):
        await client.post_sensor_data(
            sensor_id=1,
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
            values=[1.0],
            unit="MW",
            file_path="/tmp/test.csv",
        )
    await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_partial_params():
    """has_json_params but some are None raises ValueError."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(ValueError, match="all parameters .* must be provided"):
        await client.post_sensor_data(
            sensor_id=1,
            start="2023-01-01T00:00+00:00",
        )
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"unit": "MW"},
        {"prior": "2023-01-01T00:00+00:00"},
        {"unit": "MW", "prior": "2023-01-01T00:00+00:00"},
    ],
)
async def test_post_sensor_data_only_unit_or_prior(kwargs):
    """A lone unit/prior points at the missing params, not at "you passed nothing".

    Neither counts towards has_json_params, but reporting that neither mode was
    chosen is misleading when the caller clearly attempted a JSON upload.
    """
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(ValueError, match="all parameters .* must be provided"):
        await client.post_sensor_data(sensor_id=1, **kwargs)
    await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_with_file():
    """file_path provided triggers file upload endpoint and accepts a unit."""
    csv_path = "/tmp/test_sensor_data.csv"
    with open(csv_path, "w") as f:
        f.write("datetime,value\n2023-01-01T00:00+00:00,1.0\n")

    try:
        with aioresponses() as m:
            client = FlexMeasuresClient(email="test@test.test", password="test")
            client.access_token = "test-token"
            client.server_version = "0.30.0"
            m.post(
                "http://localhost:5000/api/v3_0/sensors/1/data/upload",
                status=200,
                payload={"message": "Upload successful"},
            )
            response_data, status = await client.post_sensor_data(
                sensor_id=1,
                file_path=csv_path,
                unit="kW",
            )
            assert status == 200
            request = next(iter(m.requests.values()))[0]
            form_data = request.kwargs["data"]
            fields = {field[0]["name"]: field[2] for field in form_data._fields}
            assert fields["unit"] == "kW"
            await client.close()
    finally:
        os.unlink(csv_path)


@pytest.mark.asyncio
async def test_post_sensor_data_file_unit_requires_server_0_30():
    """Servers before v0.30.0 silently ignore the unit, so refuse to upload at all.

    Such a server would answer 200 while storing the values unconverted, as if they
    were already in the sensor's unit.
    """
    csv_path = "/tmp/test_sensor_data_old_server.csv"
    with open(csv_path, "w") as f:
        f.write("datetime,value\n2023-01-01T00:00+00:00,1.0\n")

    try:
        with aioresponses() as m:
            client = FlexMeasuresClient(email="test@test.test", password="test")
            client.access_token = "test-token"
            client.server_version = "0.29.0"
            with pytest.raises(
                InsufficientServerVersionError,
                match="requires a FlexMeasures server of 0.30.0 or above",
            ):
                await client.post_sensor_data(
                    sensor_id=1,
                    file_path=csv_path,
                    unit="kW",
                )
            # The upload must not have been attempted.
            assert m.requests == {}
            await client.close()
    finally:
        os.unlink(csv_path)


@pytest.mark.asyncio
async def test_post_sensor_data_file_without_unit_works_on_old_server():
    """The version guard only applies when a unit is passed."""
    csv_path = "/tmp/test_sensor_data_old_server_no_unit.csv"
    with open(csv_path, "w") as f:
        f.write("datetime,value\n2023-01-01T00:00+00:00,1.0\n")

    try:
        with aioresponses() as m:
            client = FlexMeasuresClient(email="test@test.test", password="test")
            client.access_token = "test-token"
            client.server_version = "0.28.0"
            m.post(
                "http://localhost:5000/api/v3_0/sensors/1/data/upload",
                status=200,
                payload={"message": "Upload successful"},
            )
            _response_data, status = await client.post_sensor_data(
                sensor_id=1,
                file_path=csv_path,
            )
            assert status == 200
            request = next(iter(m.requests.values()))[0]
            fields = {
                field[0]["name"]: field[2] for field in request.kwargs["data"]._fields
            }
            assert "unit" not in fields
            await client.close()
    finally:
        os.unlink(csv_path)


@pytest.mark.asyncio
async def test_post_sensor_data_file_unit_when_server_version_unknown():
    """A server that reports no version can't be shown to support the unit field.

    Refusing is the safe reading: a server that does not honour the unit records
    the file's values unconverted while still answering 200.
    """
    csv_path = "/tmp/test_sensor_data_unknown_version.csv"
    with open(csv_path, "w") as f:
        f.write("datetime,value\n2023-01-01T00:00+00:00,1.0\n")

    try:
        with aioresponses() as m:
            m.get(
                "http://localhost:5000/api/",
                status=200,
                payload={"versions": ["v3_0"]},  # no flexmeasures_version key
                repeat=True,
            )
            client = FlexMeasuresClient(
                email="test@test.test", password="test", access_token="skip-auth"
            )
            assert client.server_version is None
            with pytest.raises(
                InsufficientServerVersionError,
                match="requires a FlexMeasures server of 0.30.0 or above",
            ):
                await client.post_sensor_data(
                    sensor_id=1,
                    file_path=csv_path,
                    unit="kW",
                )
            assert [key for key in m.requests if key[0] == "POST"] == []
            await client.close()
    finally:
        os.unlink(csv_path)


@pytest.mark.asyncio
async def test_post_sensor_data_file_unit_allowed_on_dev_build():
    """A 0.30.0 pre-release already exposes the unit field, so don't reject it."""
    csv_path = "/tmp/test_sensor_data_dev_server.csv"
    with open(csv_path, "w") as f:
        f.write("datetime,value\n2023-01-01T00:00+00:00,1.0\n")

    try:
        with aioresponses() as m:
            client = FlexMeasuresClient(email="test@test.test", password="test")
            client.access_token = "test-token"
            client.server_version = "0.30.0.dev5"
            m.post(
                "http://localhost:5000/api/v3_0/sensors/1/data/upload",
                status=200,
                payload={"message": "Upload successful"},
            )
            _response_data, status = await client.post_sensor_data(
                sensor_id=1,
                file_path=csv_path,
                unit="kW",
            )
            assert status == 200
            await client.close()
    finally:
        os.unlink(csv_path)


@pytest.mark.asyncio
async def test_post_sensor_data_with_file_accepted():
    """202 Accepted (asynchronous processing) is treated as success, not an error."""
    csv_path = "/tmp/test_sensor_data_accepted.csv"
    with open(csv_path, "w") as f:
        f.write("datetime,value\n2023-01-01T00:00+00:00,1.0\n")

    try:
        with aioresponses() as m:
            client = FlexMeasuresClient(email="test@test.test", password="test")
            client.access_token = "test-token"
            m.post(
                "http://localhost:5000/api/v3_0/sensors/1/data/upload",
                status=202,
                payload={
                    "job_id": "test-job-id",
                    "message": "Sensor data has been accepted for processing.",
                    "status": "ACCEPTED",
                },
            )
            response_data, status = await client.post_sensor_data(
                sensor_id=1,
                file_path=csv_path,
            )
            assert status == 202
            await client.close()
    finally:
        os.unlink(csv_path)


@pytest.mark.asyncio
async def test_post_sensor_data_json_with_prior():
    """prior parameter is included in payload."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/1/data",
            status=200,
            payload={"test": "ok"},
        )
        await client.post_sensor_data(
            sensor_id=1,
            start="2023-01-01T00:00+00:00",
            duration="PT1H",
            values=[1.0, 2.0],
            unit="MW",
            prior="2023-01-01T00:00+00:00",
        )
        await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_file_not_found():
    """FileNotFoundError raised when file does not exist."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.access_token = "test-token"
    with pytest.raises(FileNotFoundError, match="File not found"):
        await client.post_sensor_data(
            sensor_id=1,
            file_path="/tmp/nonexistent_file_xyz123.csv",
        )
    await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_file_non_200():
    """File upload returns non-200 status raises ValueError."""
    csv_path = "/tmp/test_sensor_data_error.csv"
    with open(csv_path, "w") as f:
        f.write("datetime,value\n2023-01-01T00:00+00:00,1.0\n")

    try:
        with aioresponses() as m:
            client = FlexMeasuresClient(email="test@test.test", password="test")
            client.access_token = "test-token"
            m.post(
                "http://localhost:5000/api/v3_0/sensors/1/data/upload",
                status=400,
                payload={"error": "bad request"},
            )
            with pytest.raises(ValueError, match="Request failed with status code 400"):
                await client.post_sensor_data(
                    sensor_id=1,
                    file_path=csv_path,
                )
            await client.close()
    finally:
        os.unlink(csv_path)


@pytest.mark.asyncio
async def test_post_measurements_deprecated():
    """post_measurements emits DeprecationWarning."""
    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.post(
            "http://localhost:5000/api/v3_0/sensors/1/data",
            status=200,
            payload={"ok": True},
        )
        with pytest.warns(DeprecationWarning, match="post_measurements.*deprecated"):
            await client.post_measurements(
                sensor_id=1,
                start="2023-01-01T00:00+00:00",
                duration="PT1H",
                values=[1.0],
                unit="MW",
            )
        await client.close()


@pytest.mark.asyncio
async def test_get_sensor_data() -> None:
    with aioresponses() as m:
        flexmeasures_client = FlexMeasuresClient(
            email="test@test.test", password="test"
        )
        flexmeasures_client.access_token = "test-token"

        sensor_id = 2
        m.get(
            re.compile(rf"http://localhost:5000/api/v3_0/sensors/{sensor_id}/data\?.*"),
            status=200,
            payload={
                "duration": "PT45M",
                "message": "Request has been processed.",
                "resolution": "PT15M",
                "start": "2023-06-01T10:00:00+02:00",
                "status": "PROCESSED",
                "unit": "MW",
                "values": [8.5, 8.5, 8.5],
            },
        )

        start = "2023-06-01T10:00:00+02:00"
        duration = "PT45M"
        unit = "MW"
        resolution = "PT15M"

        sensor_data = await flexmeasures_client.get_sensor_data(
            sensor_id=sensor_id,
            start=start,
            duration=duration,
            unit=unit,
            resolution=resolution,
        )
        assert sensor_data["values"] == [8.5, 8.5, 8.5]

        # Check the requested query params. Durations are compared as
        # timedeltas, because pandas renders "PT45M" as "P0DT0H45M0S" on
        # some versions, and both are valid ISO 8601. The start param is
        # unquoted first, because yarl versions differ in requote behavior.
        ((request_key, _),) = m.requests.items()
        query = request_key[1].query
        assert pd.Timedelta(query["duration"]) == pd.Timedelta(duration)
        assert pd.Timedelta(query["resolution"]) == pd.Timedelta(resolution)
        assert unquote(query["start"]) == start
        assert query["unit"] == unit
        await flexmeasures_client.close()


@pytest.mark.asyncio
async def test_get_sensor_data_content_type_error():
    """sensor data response is a list, not dict."""
    import re as _re

    with aioresponses() as m:
        client = FlexMeasuresClient(email="test@test.test", password="test")
        client.access_token = "test-token"
        m.get(
            _re.compile(r".*sensors/1/data.*"),
            status=200,
            payload=[1, 2, 3],
        )
        with pytest.raises(ContentTypeError):
            await client.get_sensor_data(
                sensor_id=1,
                start="2023-01-01T00:00:00+00:00",
                duration="PT45M",
                unit="MW",
                resolution="PT15M",
            )
        await client.close()
