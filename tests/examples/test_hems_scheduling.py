"""Tests for community and per-site scheduling in the HEMS example."""

import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

HEMS_DIR = Path(__file__).parents[2] / "examples" / "HEMS"
if str(HEMS_DIR) not in sys.path:
    sys.path.insert(0, str(HEMS_DIR))

import scheduling  # noqa: E402


def community_sensors() -> dict:
    sensors = {"electricity-price": {"id": 1}}
    for index in (1, 2):
        offset = index * 100
        sensors.update(
            {
                f"electricity-aggregate-{index}": {"id": offset + 1},
                f"building-consumption-{index}": {"id": offset + 2},
                f"pv-production-{index}": {"id": offset + 3},
                f"max-consumption-capacity-{index}": {"id": offset + 4},
                f"max-production-capacity-{index}": {"id": offset + 5},
                f"battery-soc-{index}": {"id": offset + 6},
                f"evse1-soc-{index}": {"id": offset + 7},
                f"evse2-soc-{index}": {"id": offset + 8},
                f"heating-soc-{index}": {"id": offset + 9},
            }
        )
    return sensors


def test_build_community_flex_models_groups_all_site_flows() -> None:
    sensors = community_sensors()
    site_flex_models = [
        [{"sensor": 110, "power-capacity": "5 kW"}],
        [{"sensor": 210, "power-capacity": "5 kW"}],
    ]

    models = scheduling.build_community_flex_models(site_flex_models, sensors)

    for index, device_sensor in ((1, 110), (2, 210)):
        group_sensor = sensors[f"electricity-aggregate-{index}"]["id"]
        group_reference = {"sensor": group_sensor}
        assert (
            next(model for model in models if model.get("sensor") == device_sensor)[
                "group"
            ]
            == group_reference
        )
        assert {
            "sensor": sensors[f"building-consumption-{index}"]["id"],
            "inflexible-consumption": {
                "sensor": sensors[f"building-consumption-{index}"]["id"]
            },
            "group": group_reference,
        } in models
        assert {
            "sensor": sensors[f"pv-production-{index}"]["id"],
            "inflexible-production": {
                "sensor": sensors[f"pv-production-{index}"]["id"]
            },
            "group": group_reference,
        } in models
        assert {
            "sensor": group_sensor,
            "power-capacity": scheduling.SITE_POWER_CAPACITY,
            "consumption-capacity": {
                "sensor": sensors[f"max-consumption-capacity-{index}"]["id"]
            },
            "production-capacity": {
                "sensor": sensors[f"max-production-capacity-{index}"]["id"]
            },
        } in models

    assert all("sensor" in model or "asset" in model for model in models)
    assert "group" not in site_flex_models[0][0]


def test_curtailable_pv_is_not_counted_twice_as_inflexible() -> None:
    sensors = community_sensors()

    models = scheduling.build_community_flex_models(
        [[{"sensor": 110}], [{"sensor": 210}]],
        sensors,
        pv_mode="curtailable",
    )

    assert all("inflexible-production" not in model for model in models)


@pytest.mark.asyncio
async def test_compute_community_schedules_triggers_one_job_on_community() -> None:
    sensors = community_sensors()
    site_flex_models = [
        [{"sensor": 110, "power-capacity": "5 kW"}],
        [{"sensor": 210, "power-capacity": "5 kW"}],
    ]
    client = AsyncMock()
    client.trigger_schedule.return_value = "community-job"
    client.get_schedule.side_effect = lambda **kwargs: {
        "values": [],
        "scheduler_info": {"scheduler": "StorageScheduler"},
    }
    current_time = pd.Timestamp("2030-01-15T00:00:00+01:00")

    schedules = await scheduling.compute_community_schedules(
        client=client,
        community_asset={"id": 900},
        site_flex_models=site_flex_models,
        sensors=sensors,
        current_time=current_time,
        simulate_live_corrections=False,
    )

    client.trigger_schedule.assert_awaited_once()
    trigger = client.trigger_schedule.await_args.kwargs
    assert trigger["asset_id"] == 900
    assert trigger["flex_context"] == {
        "consumption-price": {"sensor": sensors["electricity-price"]["id"]}
    }
    assert trigger["prior"] == current_time
    assert len(schedules) == 2
    assert {
        call.kwargs["schedule_id"] for call in client.get_schedule.await_args_list
    } == {"community-job"}


@pytest.mark.asyncio
async def test_compute_site_schedules_keeps_independent_site_target() -> None:
    client = AsyncMock()
    client.trigger_schedule.return_value = "site-job"
    expected = ([], {}, {}, {}, {})
    current_time = pd.Timestamp("2030-01-15T00:00:00+01:00")

    with (
        patch.object(
            scheduling,
            "build_site_flex_models",
            return_value=[{"sensor": 10}],
        ),
        patch.object(
            scheduling,
            "retrieve_site_schedules",
            new_callable=AsyncMock,
            return_value=expected,
        ),
    ):
        schedules = await scheduling.compute_site_schedules(
            client=client,
            site_asset={"id": 42},
            index=1,
            sensors={},
            step_num=1,
            current_time=current_time,
            battery_soc_at_start=8,
            evse1_flex_model={},
            evse2_flex_model={},
            simulate_live_corrections=False,
        )

    assert schedules == expected
    client.trigger_schedule.assert_awaited_once_with(
        start=current_time,
        duration=timedelta(hours=scheduling.FORECAST_HORIZON_HOURS),
        flex_model=[{"sensor": 10}],
        asset_id=42,
        prior=current_time,
    )


def test_find_capacity_breaches_aligns_hourly_capacity_to_quarter_hours() -> None:
    power_data = {
        "start": "2030-01-15T00:00:00+01:00",
        "values": [34, 31, 29, -32, 31, 31, 31, 31],
    }
    capacity_data = {
        "start": "2030-01-15T00:00:00+01:00",
        "values": [30, 32],
    }

    breaches = scheduling.find_capacity_breaches(power_data, capacity_data)

    assert [breach["timestamp"].isoformat() for breach in breaches] == [
        "2030-01-15T00:00:00+01:00",
        "2030-01-15T00:15:00+01:00",
        "2030-01-15T00:45:00+01:00",
    ]
    assert [breach["direction"] for breach in breaches] == [
        "consumption",
        "consumption",
        "production",
    ]
    assert breaches[0]["excess"] == 4


@pytest.mark.asyncio
async def test_report_community_capacity_breaches_prints_timestamp(capsys) -> None:
    client = AsyncMock()
    client.get_sensor_data.side_effect = [
        {
            "start": "2030-01-15T00:00:00+01:00",
            "values": [31, 29, 29, 29],
        },
        {"start": "2030-01-15T00:00:00+01:00", "values": [30]},
    ]

    breaches = await scheduling.report_community_capacity_breaches(
        client=client,
        power_sensor={"id": 1},
        capacity_sensor={"id": 2},
        current_time=pd.Timestamp("2030-01-15T00:00:00+01:00"),
        step_end_time=pd.Timestamp("2030-01-15T01:00:00+01:00"),
    )

    assert len(breaches) == 1
    assert client.get_sensor_data.await_args_list[0].kwargs["source_type"] == "reporter"
    output = capsys.readouterr().out
    assert "[COMMUNITY-CAPACITY-BREACH]" in output
    assert "2030-01-15T00:00:00+01:00" in output
    assert "exceeding the 30.00 kW limit by 1.00 kW" in output
