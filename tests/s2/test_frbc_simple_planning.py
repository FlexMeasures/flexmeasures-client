"""What makes FRBCSimple ask FlexMeasures for a schedule, and where that schedule starts."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple


def frbc_simple() -> FRBCSimple:
    return FRBCSimple(
        power_sensor_id=1,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
    )


@pytest.mark.parametrize(
    "valid_from, expected_start",
    [
        # A resource manager's valid_from is just the moment it sent the description.
        ("2022-12-02T09:07:33.512+00:00", "2022-12-03T09:00:00+00:00"),
        ("2022-12-02T09:14:59+00:00", "2022-12-03T09:00:00+00:00"),
        ("2022-12-02T09:15:00+00:00", "2022-12-03T09:15:00+00:00"),
        ("2022-12-02T09:44:01+00:00", "2022-12-03T09:30:00+00:00"),
        ("2022-12-02T23:59:59+00:00", "2022-12-03T23:45:00+00:00"),
    ],
)
def test_a_schedule_starts_on_a_resolution_boundary(valid_from, expected_start):
    """An unaligned start leaves FlexMeasures with no prices for the window, and the job fails."""
    frbc = frbc_simple()

    start = frbc.schedule_start(datetime.fromisoformat(valid_from))

    assert start == datetime.fromisoformat(expected_start)
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    assert (start - midnight) % timedelta(minutes=15) == timedelta(0)
