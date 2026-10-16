from unittest.mock import patch

import pytest
from aioresponses import aioresponses

from flexmeasures_client import __version__
from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.constants import CLIENT_VERSION_HEADER
from flexmeasures_client.exceptions import InsufficientServerVersionError

ASSET_ID = 3
AUTOMATION_ID = 5
JOB_ID = "364bfd06-c1fa-430b-8d25-8f5a547651fb"
API_URL = "http://localhost:5000/api/v3_0"
AUTOMATIONS_URL = f"{API_URL}/assets/{ASSET_ID}/automations"
AUTOMATION_URL = f"{AUTOMATIONS_URL}/{AUTOMATION_ID}"
TRIGGER_URL = f"{AUTOMATION_URL}/trigger"
JOB_URL = f"{API_URL}/jobs/{JOB_ID}"
HEADERS = {
    "Content-Type": "application/json",
    CLIENT_VERSION_HEADER: __version__,
    "Authorization": "test-token",
}

AUTOMATION = {
    "id": AUTOMATION_ID,
    "created-at": "2026-07-11T00:00:00+00:00",
    "asset": ASSET_ID,
    "type": "forecasting",
    "name": "Day-ahead PV forecasts",
    "cron": "0 6 * * *",
    "timezone": "Europe/Amsterdam",
    "cursor": "2026-07-11T06:00:00+02:00",
    "next-run": "2026-07-12T06:00:00+02:00",
    "recurrence-description": "At 06:00",
    "schedule-revision": 1,
    "active": True,
}
ACCEPTED_PAYLOAD = {
    "status": "ACCEPTED",
    "job": JOB_ID,
    "job-url": f"/api/v3_0/jobs/{JOB_ID}",
    "n-jobs": 2,
    "message": "Request has been accepted for processing.",
}


def make_client(server_version: str | None = "1.1.0") -> FlexMeasuresClient:
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.access_token = "test-token"
    client.server_version = server_version
    return client


def assert_requested(m: aioresponses, url: str, method: str, **kwargs) -> None:
    """Check the one request sent, with the defaults every request of the client carries."""
    request = {"json": None, "params": None, **kwargs}
    m.assert_called_once_with(
        url,
        method=method,
        headers=HEADERS,
        ssl=False,
        allow_redirects=False,
        **request,
    )


@pytest.mark.asyncio
async def test_get_automations() -> None:
    listed = {**AUTOMATION, "asset-name": "Solar panels", "job-stats": {"finished": 3}}
    with aioresponses() as m:
        client = make_client()
        m.get(
            f"{AUTOMATIONS_URL}?include-child-assets=false",
            status=200,
            payload={"automations": [listed], "redis-connection-err": None},
        )

        automations = await client.get_automations(ASSET_ID, include_child_assets=False)

        assert automations == [listed]
        assert_requested(
            m,
            AUTOMATIONS_URL,
            "GET",
            params={"include-child-assets": "false"},
        )
        await client.close()


@pytest.mark.asyncio
async def test_get_automations_warns_without_job_stats(caplog) -> None:
    with aioresponses() as m:
        client = make_client()
        m.get(
            f"{AUTOMATIONS_URL}?include-child-assets=true",
            status=200,
            payload={
                "automations": [AUTOMATION],
                "redis-connection-err": "Redis is unavailable; job statistics could not be loaded.",
            },
        )

        assert await client.get_automations(ASSET_ID) == [AUTOMATION]

        assert any(
            "Redis is unavailable" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )
        await client.close()


@pytest.mark.asyncio
async def test_get_automation() -> None:
    details = {**AUTOMATION, "parameters": {"sensor": 2092}, "run-stats": {"total": 1}}
    with aioresponses() as m:
        client = make_client()
        m.get(AUTOMATION_URL, status=200, payload=details)

        assert await client.get_automation(ASSET_ID, AUTOMATION_ID) == details

        assert_requested(m, AUTOMATION_URL, "GET")
        await client.close()


@pytest.mark.asyncio
async def test_get_automation_of_another_asset() -> None:
    """The server's message says that the automation is not defined on this asset."""
    with aioresponses() as m:
        client = make_client()
        m.get(
            AUTOMATION_URL,
            status=404,
            payload={
                "message": f"Asset {ASSET_ID} has no automation with id {AUTOMATION_ID}."
            },
        )

        with pytest.raises(ValueError, match="has no automation with id"):
            await client.get_automation(ASSET_ID, AUTOMATION_ID)
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("server_version", ["1.1.0rc2", "1.1.0.dev5"])
async def test_a_prerelease_of_the_minimum_version_has_automations(
    server_version: str,
) -> None:
    """A 404 from a v1.1.0 pre-release is about the automation, not the server version."""
    with aioresponses() as m:
        client = make_client(server_version)
        m.get(AUTOMATION_URL, status=404, payload={"message": "No such automation."})

        with pytest.raises(ValueError, match="No such automation"):
            await client.get_automation(ASSET_ID, AUTOMATION_ID)
        await client.close()


@pytest.mark.asyncio
async def test_an_older_server_has_no_automations() -> None:
    with aioresponses() as m:
        client = make_client("1.0.2")
        m.get(AUTOMATION_URL, status=404, payload={"message": "Not Found"})

        with pytest.raises(InsufficientServerVersionError, match="1.1.0 or above"):
            await client.get_automation(ASSET_ID, AUTOMATION_ID)
        await client.close()


@pytest.mark.asyncio
async def test_add_automation_sends_only_the_given_fields() -> None:
    with aioresponses() as m:
        client = make_client()
        m.post(AUTOMATIONS_URL, status=201, payload={**AUTOMATION, "warnings": []})

        automation = await client.add_automation(
            ASSET_ID,
            name="Day-ahead PV forecasts",
            cron="0 6 * * *",
            parameters={"sensor": 2092},
        )

        assert automation["id"] == AUTOMATION_ID
        assert_requested(
            m,
            AUTOMATIONS_URL,
            "POST",
            json={
                "name": "Day-ahead PV forecasts",
                "cron": "0 6 * * *",
                "type": "forecasting",
                "active": True,
                "parameters": {"sensor": 2092},
            },
        )
        await client.close()


@pytest.mark.asyncio
async def test_add_automation_with_all_fields(caplog) -> None:
    with aioresponses() as m:
        client = make_client()
        m.post(
            AUTOMATIONS_URL,
            status=201,
            payload={**AUTOMATION, "warnings": ["Sensor 2093 has no data yet."]},
        )

        await client.add_automation(
            ASSET_ID,
            name="Day-ahead PV forecasts",
            cron="0 6 * * *",
            automation_type="reporting",
            parameters={"start-offset": "DB", "duration": "P1D"},
            timezone="Europe/Amsterdam",
            active=False,
            data_generator="PandasReporter",
            config={"method": "sum"},
        )

        assert_requested(
            m,
            AUTOMATIONS_URL,
            "POST",
            json={
                "name": "Day-ahead PV forecasts",
                "cron": "0 6 * * *",
                "type": "reporting",
                "active": False,
                "parameters": {"start-offset": "DB", "duration": "P1D"},
                "timezone": "Europe/Amsterdam",
                "data-generator": "PandasReporter",
                "config": {"method": "sum"},
            },
        )
        assert any(
            "Sensor 2093 has no data yet." in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )
        await client.close()


@pytest.mark.asyncio
async def test_add_automation_on_an_existing_source() -> None:
    with aioresponses() as m:
        client = make_client()
        m.post(AUTOMATIONS_URL, status=201, payload=AUTOMATION)

        await client.add_automation(
            ASSET_ID, name="Day-ahead PV forecasts", cron="0 6 * * *", source=6
        )

        assert_requested(
            m,
            AUTOMATIONS_URL,
            "POST",
            json={
                "name": "Day-ahead PV forecasts",
                "cron": "0 6 * * *",
                "type": "forecasting",
                "active": True,
                "source": 6,
            },
        )
        await client.close()


@pytest.mark.asyncio
async def test_add_automation_reports_what_the_server_refused() -> None:
    with aioresponses() as m:
        client = make_client()
        m.post(
            AUTOMATIONS_URL,
            status=422,
            payload={
                "message": {"json": {"parameters": {"start": ["Unknown field."]}}}
            },
        )

        with pytest.raises(ValueError, match="parameters"):
            await client.add_automation(
                ASSET_ID,
                name="Day-ahead PV forecasts",
                cron="0 6 * * *",
                parameters={"start": "2026-07-11T00:00:00+02:00"},
            )
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("updates", "kwargs", "expected"),
    [
        ({"active": False}, {}, {"active": False}),
        (None, {"active": False}, {"active": False}),
        (
            {"cron": "0 7 * * *"},
            {"timezone": "UTC"},
            {"cron": "0 7 * * *", "timezone": "UTC"},
        ),
        # A field the server may make editable later, which the client does not need to know about.
        (
            None,
            {"depends_on": [{"automation": 1}]},
            {"depends-on": [{"automation": 1}]},
        ),
        (
            {"depends-on": [{"automation": 1}]},
            {},
            {"depends-on": [{"automation": 1}]},
        ),
    ],
)
async def test_update_automation(updates, kwargs, expected) -> None:
    with aioresponses() as m:
        client = make_client()
        m.patch(AUTOMATION_URL, status=200, payload={**AUTOMATION, **expected})

        if updates is None:
            automation = await client.update_automation(
                ASSET_ID, AUTOMATION_ID, **kwargs
            )
        else:
            automation = await client.update_automation(
                ASSET_ID, AUTOMATION_ID, updates, **kwargs
            )

        assert automation == {**AUTOMATION, **expected}
        assert_requested(m, AUTOMATION_URL, "PATCH", json=expected)
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("updates", "kwargs", "error"),
    [
        (None, {}, "No fields to update"),
        ({}, {}, "No fields to update"),
        ({"active": True}, {"active": False}, "given both"),
        ({"depends-on": []}, {"depends_on": []}, "given both"),
    ],
)
async def test_update_automation_refuses_ambiguous_updates(
    updates, kwargs, error
) -> None:
    with aioresponses() as m:
        client = make_client()

        with pytest.raises(ValueError, match=error):
            await client.update_automation(ASSET_ID, AUTOMATION_ID, updates, **kwargs)

        assert not m.requests
        await client.close()


@pytest.mark.asyncio
async def test_delete_automation() -> None:
    with aioresponses() as m:
        client = make_client()
        m.delete(AUTOMATION_URL, status=204, payload={})

        await client.delete_automation(ASSET_ID, AUTOMATION_ID, confirm_first=False)

        assert_requested(m, AUTOMATION_URL, "DELETE")
        await client.close()


@pytest.mark.asyncio
async def test_delete_automation_can_be_aborted() -> None:
    with aioresponses() as m:
        client = make_client()

        with patch("builtins.input", return_value="n"):
            await client.delete_automation(ASSET_ID, AUTOMATION_ID)

        assert not m.requests
        await client.close()


@pytest.mark.asyncio
async def test_trigger_automation() -> None:
    with aioresponses() as m:
        client = make_client()
        m.post(TRIGGER_URL, status=202, payload=ACCEPTED_PAYLOAD)

        response = await client.trigger_automation(ASSET_ID, AUTOMATION_ID)

        assert response["job"] == JOB_ID
        assert response["n-jobs"] == 2
        assert_requested(m, TRIGGER_URL, "POST")
        await client.close()


@pytest.mark.asyncio
async def test_trigger_automation_that_cannot_run() -> None:
    with aioresponses() as m:
        client = make_client()
        m.post(
            TRIGGER_URL,
            status=422,
            payload={"message": {"json": "Sensor 2092 is unknown."}},
        )

        with pytest.raises(ValueError, match="Sensor 2092 is unknown"):
            await client.trigger_automation(ASSET_ID, AUTOMATION_ID)
        await client.close()


@pytest.mark.asyncio
async def test_trigger_automation_and_wait() -> None:
    with aioresponses() as m:
        client = make_client()
        m.post(TRIGGER_URL, status=202, payload=ACCEPTED_PAYLOAD)
        m.get(JOB_URL, status=202, payload={"status": "STARTED"})
        m.get(JOB_URL, status=200, payload={"status": "FINISHED"})

        with patch("asyncio.sleep") as sleep:
            job = await client.trigger_automation_and_wait(
                ASSET_ID, AUTOMATION_ID, polling_interval=1.0
            )

        assert job["status"] == "FINISHED"
        sleep.assert_called_once_with(1.0)
        await client.close()
