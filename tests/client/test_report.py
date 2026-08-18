import pytest
from aioresponses import aioresponses
from yarl import URL

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.exceptions import (
    ContentTypeError,
    JobFailedError,
    JobTimeoutError,
)

ASSET_ID = 3
JOB_ID = "364bfd06-c1fa-430b-8d25-8f5a547651fb"
TRIGGER_URL = f"http://localhost:5000/api/v3_0/assets/{ASSET_ID}/reports/trigger"
JOB_URL = f"http://localhost:5000/api/v3_0/jobs/{JOB_ID}"

PARAMETERS = {
    "input": [{"name": "pv", "sensor": 1}],
    "output": [{"name": "self-consumption", "sensor": 2}],
    "start": "2026-08-18T00:00:00+02:00",
    "end": "2026-08-19T00:00:00+02:00",
}
ACCEPTED_PAYLOAD = {
    "status": "ACCEPTED",
    "message": "Request has been accepted for processing.",
    "job": JOB_ID,
    "job-url": f"/api/v3_0/jobs/{JOB_ID}",
}


def make_client() -> FlexMeasuresClient:
    client = FlexMeasuresClient(email="test@test.test", password="test")
    client.access_token = "test-token"
    return client


def job_payload(status: str, **extra) -> dict:
    payload = {
        "status": status,
        "message": f"Job is {status.lower()}.",
        "result": None,
        "origin": "flexmeasures:reporting",
        "exc-info": None,
    }
    payload.update(extra)
    return payload


@pytest.mark.asyncio
async def test_trigger_report() -> None:
    """Test triggering a report without an explicit reporter config."""
    with aioresponses() as m:
        client = make_client()
        m.post(TRIGGER_URL, status=202, payload=ACCEPTED_PAYLOAD)

        job_id = await client.trigger_report(
            asset_id=ASSET_ID,
            reporter="PandasReporter",
            parameters=PARAMETERS,
        )

        assert job_id == JOB_ID

        m.assert_called_once_with(
            TRIGGER_URL,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "test-token",
            },
            json={"reporter": "PandasReporter", "parameters": PARAMETERS},
            params=None,
            ssl=False,
            allow_redirects=False,
        )

        await client.close()


@pytest.mark.asyncio
async def test_trigger_report_with_config() -> None:
    """Test that a reporter config is passed on as its own field."""
    config = {"required_input": [{"name": "pv"}]}
    with aioresponses() as m:
        client = make_client()
        m.post(TRIGGER_URL, status=202, payload=ACCEPTED_PAYLOAD)

        await client.trigger_report(
            asset_id=ASSET_ID,
            reporter="PandasReporter",
            parameters=PARAMETERS,
            config=config,
        )

        m.assert_called_once_with(
            TRIGGER_URL,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "test-token",
            },
            json={
                "reporter": "PandasReporter",
                "parameters": PARAMETERS,
                "config": config,
            },
            params=None,
            ssl=False,
            allow_redirects=False,
        )

        await client.close()


@pytest.mark.asyncio
async def test_trigger_report_without_job_id() -> None:
    """Test that a malformed trigger response is reported clearly."""
    with aioresponses() as m:
        client = make_client()
        m.post(TRIGGER_URL, status=202, payload={"status": "ACCEPTED"})

        with pytest.raises(ContentTypeError):
            await client.trigger_report(
                asset_id=ASSET_ID,
                reporter="PandasReporter",
                parameters=PARAMETERS,
            )

        await client.close()


@pytest.mark.asyncio
async def test_get_job_status() -> None:
    """Test a single job status lookup."""
    with aioresponses() as m:
        client = make_client()
        m.get(JOB_URL, status=200, payload=job_payload("STARTED"))

        job = await client.get_job_status(JOB_ID)

        assert job["status"] == "STARTED"
        m.assert_called_once_with(
            JOB_URL,
            method="GET",
            headers={
                "Content-Type": "application/json",
                "Authorization": "test-token",
            },
            json=None,
            params=None,
            ssl=False,
            allow_redirects=False,
        )

        await client.close()


@pytest.mark.asyncio
async def test_get_job_status_without_status() -> None:
    """Test that a malformed job status response is reported clearly."""
    with aioresponses() as m:
        client = make_client()
        m.get(JOB_URL, status=200, payload={"message": "Hi."})

        with pytest.raises(ContentTypeError):
            await client.get_job_status(JOB_ID)

        await client.close()


@pytest.mark.asyncio
async def test_wait_for_job_polls_until_finished() -> None:
    """Test that polling continues through the non-terminal states."""
    with aioresponses() as m:
        client = make_client()
        m.get(JOB_URL, status=200, payload=job_payload("QUEUED"))
        m.get(JOB_URL, status=200, payload=job_payload("DEFERRED"))
        m.get(JOB_URL, status=200, payload=job_payload("STARTED"))
        m.get(JOB_URL, status=200, payload=job_payload("FINISHED"))

        job = await client.wait_for_job(JOB_ID, polling_interval=0.01)

        assert job["status"] == "FINISHED"
        assert len(m.requests[("GET", URL(JOB_URL))]) == 4

        await client.close()


@pytest.mark.asyncio
async def test_wait_for_job_raises_on_failed_job() -> None:
    """Test that a failed job surfaces the server message and traceback."""
    with aioresponses() as m:
        client = make_client()
        m.get(
            JOB_URL,
            status=200,
            payload=job_payload(
                "FAILED",
                message="Report job failed.",
                **{"exc-info": "Traceback: KeyError: 'pv'"},
            ),
        )

        with pytest.raises(JobFailedError) as exc_info:
            await client.wait_for_job(JOB_ID, polling_interval=0.01)

        message = str(exc_info.value)
        assert "FAILED" in message
        assert "Report job failed." in message
        assert "KeyError: 'pv'" in message

        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["STOPPED", "CANCELED"])
async def test_wait_for_job_raises_on_unsuccessful_job(status: str) -> None:
    """Test that jobs stopped or canceled by an operator also raise."""
    with aioresponses() as m:
        client = make_client()
        m.get(JOB_URL, status=200, payload=job_payload(status))

        with pytest.raises(JobFailedError):
            await client.wait_for_job(JOB_ID, polling_interval=0.01)

        await client.close()


@pytest.mark.asyncio
async def test_wait_for_job_times_out() -> None:
    """Test that a job that never finishes hits the timeout budget."""
    with aioresponses() as m:
        client = make_client()
        m.get(JOB_URL, status=200, payload=job_payload("QUEUED"), repeat=True)

        with pytest.raises(JobTimeoutError) as exc_info:
            await client.wait_for_job(JOB_ID, timeout=0.05, polling_interval=0.01)

        assert "QUEUED" in str(exc_info.value)

        await client.close()


@pytest.mark.asyncio
async def test_trigger_and_await_report() -> None:
    """Test triggering a report and waiting for the queued job in one call."""
    with aioresponses() as m:
        client = make_client()
        m.post(TRIGGER_URL, status=202, payload=ACCEPTED_PAYLOAD)
        m.get(JOB_URL, status=200, payload=job_payload("FINISHED"))

        job = await client.trigger_and_await_report(
            asset_id=ASSET_ID,
            reporter="PandasReporter",
            parameters=PARAMETERS,
            polling_interval=0.01,
        )

        assert job["status"] == "FINISHED"

        await client.close()
