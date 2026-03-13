from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from aioresponses import aioresponses

from flexmeasures_client.client import ContentTypeError, FlexMeasuresClient


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

        await flexmeasures_client.post_sensor_data(
            sensor_id=sensor_id,
            start=start,
            duration=duration,
            values=values,
            unit=unit,
        )
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
async def test_post_sensor_data_no_params():
    """No json params and no file_path raises ValueError."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(
        ValueError,
        match="Either provide JSON data parameters \\(start, duration, values, unit\\) or a file_path parameter, but not neither\\.",
    ):
        await client.post_sensor_data(sensor_id=1)
    await client.close()


@pytest.mark.asyncio
async def test_post_sensor_data_both_params():
    """Both json params AND file_path raises ValueError."""
    client = FlexMeasuresClient(email="test@test.test", password="test")
    with pytest.raises(
        ValueError,
        match="Either provide JSON data parameters \\(start, duration, values, unit\\) or a file_path parameter, but not both\\.",
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
async def test_post_sensor_data_with_file():
    """file_path provided triggers file upload endpoint."""
    csv_path = "/tmp/test_sensor_data.csv"
    with open(csv_path, "w") as f:
        f.write("datetime,value\n2023-01-01T00:00+00:00,1.0\n")

    try:
        with aioresponses() as m:
            client = FlexMeasuresClient(email="test@test.test", password="test")
            client.access_token = "test-token"
            m.post(
                "http://localhost:5000/api/v3_0/sensors/1/data/upload",
                status=200,
                payload={"message": "Upload successful"},
            )
            response_data, status = await client.post_sensor_data(
                sensor_id=1,
                file_path=csv_path,
            )
            assert status == 200
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
            f"http://localhost:5000/api/v3_0/sensors/{sensor_id}/data?duration=P0DT0H45M0S&resolution=P0DT0H15M0S&start=2023-06-01T10%253A00%253A00%252B02%253A00&unit=MW",  # noqa: E501
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
