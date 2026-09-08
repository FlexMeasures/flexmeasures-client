"""Tests for the HEMS example's reporting helpers."""

import sys
from pathlib import Path

import pytest

HEMS_DIR = Path(__file__).parents[2] / "examples" / "HEMS"
if str(HEMS_DIR) not in sys.path:
    sys.path.insert(0, str(HEMS_DIR))

from utils import reporter_utils  # noqa: E402
from utils.reporter_utils import (  # noqa: E402
    asset_id_for_outputs,
    build_reporter_parameters,
    load_reporter_config,
)


def test_reports_do_not_shell_out() -> None:
    """Test that report generation no longer invokes the CLI."""
    source = (HEMS_DIR / "utils" / "reporter_utils.py").read_text()
    assert "subprocess" not in source
    assert "add report" not in source
    for module in ("reporters.py", "scheduling.py"):
        assert "subprocess" not in (HEMS_DIR / module).read_text()


@pytest.mark.parametrize(
    "reporter_type", ["aggregate", "self-consumption", "total-energy-costs"]
)
def test_load_reporter_config(reporter_type: str) -> None:
    """Test that every reporter used by the example has a loadable config."""
    config = load_reporter_config(reporter_type)
    assert isinstance(config, dict)
    assert config


def test_build_reporter_parameters() -> None:
    """Test the parameters built for a multi-output report."""
    parameters = build_reporter_parameters(
        input_sensors=[{"pv-power": 1}, {"building-consumption": 2}],
        output_sensors=[
            {"name": "self-consumption", "id": 3},
            {"name": "daily-share-of-self-consumption", "id": 4},
        ],
        start="2026-08-17T00:00:00+02:00",
        end="2026-08-18T00:00:00+02:00",
        reporter_type="self-consumption",
    )

    assert parameters["input"] == [
        {
            "name": "pv-power",
            "sensor": 1,
            "exclude_source_types": ["scheduler", "forecaster"],
        },
        {
            "name": "building-consumption",
            "sensor": 2,
            "exclude_source_types": ["scheduler", "forecaster"],
        },
    ]
    assert parameters["output"] == [
        {"name": "self-consumption", "sensor": 3},
        {"name": "daily-share-of-self-consumption", "sensor": 4},
    ]
    assert parameters["start"] == "2026-08-17T00:00:00+02:00"
    assert parameters["end"] == "2026-08-18T00:00:00+02:00"


def test_build_reporter_parameters_for_aggregate() -> None:
    """Test that the aggregate reporter writes to a single, unnamed output."""
    parameters = build_reporter_parameters(
        input_sensors=[{"pv": 1}],
        output_sensors={"name": "electricity-aggregate", "id": 9},
        start="2026-08-17T00:00:00+02:00",
        end="2026-08-18T00:00:00+02:00",
        reporter_type="aggregate",
    )

    assert parameters["output"] == [{"sensor": 9}]


def test_asset_id_for_outputs() -> None:
    """Test that a report is scoped to the asset owning its output sensors."""
    assert (
        asset_id_for_outputs(
            [
                {"id": 3, "generic_asset_id": 7},
                {"id": 4, "generic_asset_id": 7},
            ]
        )
        == 7
    )
    assert asset_id_for_outputs({"id": 9, "generic_asset_id": 2}) == 2


def test_asset_id_for_outputs_rejects_mixed_assets() -> None:
    """Test that outputs spread over several assets are refused.

    The API requires every output to sit in the subtree of the asset in the
    URL, so such a report has to be split up.
    """
    with pytest.raises(ValueError, match="one asset"):
        asset_id_for_outputs(
            [
                {"id": 3, "generic_asset_id": 7},
                {"id": 4, "generic_asset_id": 8},
            ]
        )


def test_asset_id_for_outputs_rejects_sensors_without_asset() -> None:
    """Test the guard for sensors dumped as part of an asset listing."""
    with pytest.raises(ValueError, match="generic_asset_id"):
        asset_id_for_outputs({"id": 9, "name": "power"})


@pytest.mark.asyncio
async def test_run_report_reports_failures() -> None:
    """Test that a failing report is reported rather than raised."""

    class FailingClient:
        async def trigger_and_await_report(self, **kwargs):
            raise reporter_utils.JobFailedError("Job ended with status FAILED.")

    succeeded = await reporter_utils.run_report(
        client=FailingClient(),
        reporter="AggregatorReporter",
        reporter_type="aggregate",
        input_sensors=[{"pv": 1}],
        output_sensors={"id": 9, "generic_asset_id": 2},
        start="2026-08-17T00:00:00+02:00",
        end="2026-08-18T00:00:00+02:00",
    )

    assert succeeded is False


@pytest.mark.asyncio
async def test_run_report_triggers_against_the_owning_asset() -> None:
    """Test what run_report() sends for a site report."""
    calls = []

    class RecordingClient:
        async def trigger_and_await_report(self, **kwargs):
            calls.append(kwargs)
            return {"status": "FINISHED"}

    succeeded = await reporter_utils.run_report(
        client=RecordingClient(),
        reporter="AggregatorReporter",
        reporter_type="aggregate",
        input_sensors=[{"pv": 1}, {"consumption": 2}],
        output_sensors={"id": 9, "generic_asset_id": 2},
        start="2026-08-17T00:00:00+02:00",
        end="2026-08-18T00:00:00+02:00",
    )

    assert succeeded is True
    assert len(calls) == 1
    assert calls[0]["asset_id"] == 2
    assert calls[0]["reporter"] == "AggregatorReporter"
    assert calls[0]["config"] == load_reporter_config("aggregate")
    assert calls[0]["parameters"]["output"] == [{"sensor": 9}]
