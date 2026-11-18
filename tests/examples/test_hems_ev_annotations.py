"""Check sparse labels for the HEMS EV state-of-charge targets."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

HEMS_DIR = Path(__file__).parents[2] / "examples" / "HEMS"
if str(HEMS_DIR) not in sys.path:
    sys.path.insert(0, str(HEMS_DIR))

import scheduling  # noqa: E402
from utils.ev_utils import generate_ev_soc_target_annotations  # noqa: E402


def test_ev_target_annotations_mark_only_deadlines() -> None:
    annotations = generate_ev_soc_target_annotations(
        pd.Timestamp("2030-01-15T00:00:00+01:00"),
        pd.Timestamp("2030-01-17T00:00:00+01:00"),
    )

    assert annotations == [
        {
            "content": "Depart for work: 60% SoC by 07:00 (configured)",
            "start": "2030-01-15T06:45:00+01:00",
            "end": "2030-01-15T07:15:00+01:00",
            "prior": "2030-01-14T07:00:00+01:00",
        },
        {
            "content": "Depart for work: 60% SoC by 08:00 (configured)",
            "start": "2030-01-16T07:45:00+01:00",
            "end": "2030-01-16T08:15:00+01:00",
            "prior": "2030-01-15T08:00:00+01:00",
        },
    ]


def test_flexible_day_targets_fall_at_end_of_day() -> None:
    annotations = generate_ev_soc_target_annotations(
        pd.Timestamp("2030-01-18T00:00:00+01:00"),
        pd.Timestamp("2030-01-21T00:00:00+01:00"),
    )

    assert [annotation["content"] for annotation in annotations] == [
        "EOD target: 60% SoC by midnight (configured)",
        "EOD target: 40% SoC by midnight (configured)",
        "EOD target: 40% SoC by midnight (configured)",
    ]
    assert [annotation["end"] for annotation in annotations] == [
        "2030-01-19T00:15:00+01:00",
        "2030-01-20T00:15:00+01:00",
        "2030-01-21T00:15:00+01:00",
    ]
    assert [annotation["prior"] for annotation in annotations] == [
        "2030-01-18T00:00:00+01:00",
        "2030-01-19T00:00:00+01:00",
        "2030-01-20T00:00:00+01:00",
    ]


def test_no_ev_target_annotation_without_a_deadline_in_the_window() -> None:
    assert (
        generate_ev_soc_target_annotations(
            pd.Timestamp("2030-01-15T09:00:00+01:00"),
            pd.Timestamp("2030-01-15T20:00:00+01:00"),
        )
        == []
    )


@pytest.mark.asyncio
async def test_ev_targets_are_annotated_on_each_soc_sensor() -> None:
    client = SimpleNamespace(add_sensor_annotation=AsyncMock(return_value={"id": 1}))
    sensors = {
        f"evse{evse_number}-soc-{site_index}": {"id": site_index * 100 + evse_number}
        for site_index in (1, 2)
        for evse_number in (1, 2)
    }

    await scheduling.add_ev_soc_target_annotations(
        client=client,
        sensors=sensors,
        site_count=2,
        start=pd.Timestamp("2030-01-15T00:00:00+01:00"),
        end=pd.Timestamp("2030-01-17T00:00:00+01:00"),
    )

    assert client.add_sensor_annotation.await_count == 8
    calls = client.add_sensor_annotation.await_args_list
    assert [call.kwargs["sensor_id"] for call in calls] == [
        101,
        101,
        102,
        102,
        201,
        201,
        202,
        202,
    ]
    assert all("Depart for work" in call.kwargs["content"] for call in calls)
    assert [call.kwargs["prior"] for call in calls[:2]] == [
        "2030-01-14T07:00:00+01:00",
        "2030-01-15T08:00:00+01:00",
    ]
