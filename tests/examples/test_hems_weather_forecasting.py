"""Weather forecast vintages and their use by the HEMS PV forecaster."""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

HEMS_DIR = Path(__file__).parents[2] / "examples" / "HEMS"
if str(HEMS_DIR) not in sys.path:
    sys.path.insert(0, str(HEMS_DIR))

import forecasting  # noqa: E402
import HEMS_setup  # noqa: E402
from const import (  # noqa: E402
    SIMULATION_STEP_HOURS,
    TUTORIAL_START_DATE,
    weather_station_name,
)
from generate_solar_data import generate_rows  # noqa: E402
from synthetic_weather import generate_weather_forecasts  # noqa: E402
from utils.workflow_utils import (  # noqa: E402
    FORECASTING_PHASE,
    REPORTING_PHASE,
    SCHEDULING_PHASE,
    WORKFLOW_VERSION,
)


def test_weather_vintages_cover_training_and_every_rolling_forecast():
    forecasts = generate_weather_forecasts()
    assert forecasts == generate_weather_forecasts()
    truth = {datetime.fromisoformat(t): v for t, v in generate_rows()[0]}
    start = datetime.fromisoformat(TUTORIAL_START_DATE)
    # Exercise the first training day, both scheduling days and the last full
    # forecast window. Select only vintages actually published at the vantage.
    for day in (0, 14, 15, 19):
        for hour in range(0, 24, 4):
            vantage = start + timedelta(days=day, hours=hour)
            known = {}
            for forecast in forecasts:
                assert forecast["prior"] <= forecast["start"]
                assert forecast["duration"] == timedelta(hours=len(forecast["values"]))
                if forecast["prior"] <= vantage:
                    for i, value in enumerate(forecast["values"]):
                        known[forecast["start"] + timedelta(hours=i)] = value
            for lead in range(24):
                at = vantage + timedelta(hours=lead)
                assert at in known
                assert 0 <= known[at] <= 1000
                if truth[at] == 0:
                    assert known[at] == 0


def test_weather_has_errors_but_beats_previous_day_persistence():
    truth = {datetime.fromisoformat(t): v for t, v in generate_rows()[0]}
    forecast_errors, persistence_errors = [], []
    for forecast in generate_weather_forecasts():
        for i, value in enumerate(forecast["values"]):
            at = forecast["start"] + timedelta(hours=i)
            yesterday = at - timedelta(days=1)
            if at - forecast["prior"] >= timedelta(days=1):
                continue
            if truth[at] > 0 and yesterday in truth:
                forecast_errors.append(abs(value - truth[at]))
                persistence_errors.append(abs(truth[yesterday] - truth[at]))
    assert 0 < sum(forecast_errors) < sum(persistence_errors)
    # Revisions for the same sunny event must differ, rather than copying truth.
    at = datetime.fromisoformat(TUTORIAL_START_DATE) + timedelta(days=14, hours=12)
    revisions = [
        f["values"][int((at - f["start"]).total_seconds() / 3600)]
        for f in generate_weather_forecasts()
        if f["start"] <= at < f["start"] + f["duration"]
    ]
    assert len(set(revisions)) > 2
    assert all(value != truth[at] for value in revisions)


def test_weather_updates_each_step_and_improves_at_shorter_leads():
    forecasts = generate_weather_forecasts()
    for older, newer in zip(forecasts, forecasts[1:]):
        assert newer["prior"] - older["prior"] == timedelta(hours=SIMULATION_STEP_HOURS)

    truth = {datetime.fromisoformat(t): v for t, v in generate_rows()[0]}
    errors = {}
    for forecast in forecasts:
        for i, value in enumerate(forecast["values"]):
            at = forecast["start"] + timedelta(hours=i)
            if truth[at] > 0:
                errors.setdefault(at, []).append(abs(value - truth[at]))
    # For a fixed daylight event, later issuances have smaller errors (apart
    # from rounding to 0.01 W/m²). Nighttime zeros are tested separately.
    for revisions in errors.values():
        assert all(new <= old + 0.011 for old, new in zip(revisions, revisions[1:]))
    assert sum(v[-1] for v in errors.values()) < sum(v[0] for v in errors.values())
    assert sum(v[-1] for v in errors.values()) > 0


@pytest.mark.asyncio
async def test_pv_forecast_uses_separate_future_and_past_weather_sensors():
    client = AsyncMock()
    with patch.object(
        forecasting,
        "find_sensors_by_asset",
        return_value={
            "electricity-production": {"id": 1},
            "irradiation": {"id": 2},
            "irradiation-forecast": {"id": 3},
        },
    ):
        await forecasting.generate_sensor_forecasts(
            client,
            "electricity-production",
            "PV",
            "Community",
            regressors=[("irradiation", weather_station_name)],
            future_regressors=[("irradiation-forecast", weather_station_name)],
        )
    kwargs = client.trigger_forecast.await_args.kwargs
    assert kwargs["past_regressors"] == [2]
    assert kwargs["future_regressors"] == [3]


@pytest.mark.asyncio
async def test_shared_weather_is_ingested_before_any_pv_forecast():
    client = AsyncMock()
    client.post_sensor_data.return_value = ({"job": "weather-job"}, 202)
    events = []

    async def wait_for_job(job_id):
        assert job_id == "weather-job"
        events.append("ingested")

    async def forecast_sensor(*args, **kwargs):
        assert events
        events.append(kwargs)

    client.wait_for_job.side_effect = wait_for_job
    with (
        patch.object(
            forecasting, "find_sensors_by_asset", return_value={"weather": {"id": 9}}
        ),
        patch.object(
            forecasting, "generate_sensor_forecasts", side_effect=forecast_sensor
        ),
    ):
        await forecasting.generate_forecasts(client, "Community", ["A", "B"])
    calls = client.post_sensor_data.await_args_list
    assert len(calls) == 1 + len(generate_weather_forecasts())
    assert all(c.kwargs["sensor_id"] == 9 for c in calls)
    # Historical weather must be realized, with each measurement becoming known
    # at event end. Early forecast vintages alone cannot populate training data.
    assert calls[0].kwargs["file_path"].endswith("data/irradiation_data.csv")
    assert calls[0].kwargs["belief_time_measured_instantly"] is True
    assert "prior" not in calls[0].kwargs
    assert all(c.kwargs["await_ingestion"] is False for c in calls[1:])
    assert [c.kwargs["prior"] for c in calls[1:]] == [
        f["prior"] for f in generate_weather_forecasts()
    ]
    # PV alone receives the future weather input; building and heat forecasts do not.
    configs = [event for event in events if isinstance(event, dict)]
    assert len(configs) == 6
    assert all(
        bool(config["future_regressors"])
        == (config["sensor_name"] == "electricity-production")
        for config in configs
    )


@pytest.mark.asyncio
async def test_failed_weather_ingestion_stops_pv_forecasting():
    client = AsyncMock()
    client.post_sensor_data.return_value = ({"job": "weather-job"}, 202)
    client.wait_for_job.side_effect = RuntimeError("Weather ingestion failed")
    with (
        patch.object(
            forecasting, "find_sensors_by_asset", return_value={"weather": {"id": 9}}
        ),
        patch.object(forecasting, "generate_sensor_forecasts") as forecast,
        pytest.raises(RuntimeError, match="Weather ingestion failed"),
    ):
        await forecasting.generate_forecasts(client, "Community", ["A", "B"])
    forecast.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_version", [2, 3, 4])
async def test_upgrade_reruns_forecasting_and_its_downstream_phases(
    old_version, capsys
):
    with (
        patch.object(HEMS_setup, "create_community_asset", return_value={"id": 1}),
        patch.object(
            HEMS_setup,
            "upgrade_workflow_state",
            return_value={"workflow-version": WORKFLOW_VERSION, "completed-phases": []},
        ) as upgrade,
    ):
        await HEMS_setup.upgrade_existing_setup(
            client=AsyncMock(),
            account={"id": 9},
            community_asset={"id": 1},
            community_name="Community",
            site_names=["A", "B"],
            state={"workflow-version": old_version},
        )
    assert upgrade.await_args.kwargs["phases_to_rerun"] == (
        FORECASTING_PHASE,
        SCHEDULING_PHASE,
        REPORTING_PHASE,
    )
    output = capsys.readouterr().out
    assert f"from tutorial version {old_version} to {WORKFLOW_VERSION}" in output
    assert (
        f"Upgrade complete. Stored tutorial version is now {WORKFLOW_VERSION}" in output
    )
    assert "Still to run:" in output
