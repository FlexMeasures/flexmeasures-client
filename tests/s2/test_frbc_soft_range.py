"""The declared FRBC fill-level range is a SOFT comfort band.

The CEM steers it via the soc-minima/-maxima profiles (breach-priced
server-side), clipped into the declared range, while the hard scalar
soc-min/soc-max in the flex model are wide safety rails only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from s2python.common import Duration, NumberRange
from s2python.frbc import (
    FRBCFillLevelTargetProfile,
    FRBCFillLevelTargetProfileElement,
    FRBCStorageStatus,
)

from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.control_types.FRBC.utils import (
    clip_fill_level_target_profile,
)
from flexmeasures_client.s2.utils import get_unique_id

RANGE_BOTTOM = 278.0
RANGE_TOP = 355.0


def series(values):
    index = pd.date_range(
        "2024-01-01T00:00:00+00:00", periods=len(values), freq="15min"
    )
    return pd.Series(values, index=index, dtype=float)


def test_clip_is_a_noop_inside_the_range():
    minima, maxima, n_crossed = clip_fill_level_target_profile(
        series([300, 310]), series([320, 330]), RANGE_BOTTOM, RANGE_TOP
    )
    assert minima.tolist() == [300, 310]
    assert maxima.tolist() == [320, 330]
    assert n_crossed == 0


def test_night_setback_minima_clip_up_to_the_range_bottom():
    # Night setback drops the target floor below the declared range bottom;
    # the posted minima must clip UP to the bottom (maxima untouched).
    minima, maxima, n_crossed = clip_fill_level_target_profile(
        series([250, 300]), series([320, 330]), RANGE_BOTTOM, RANGE_TOP
    )
    assert minima.tolist() == [RANGE_BOTTOM, 300]
    assert maxima.tolist() == [320, 330]
    assert n_crossed == 0


def test_maxima_clip_down_to_the_range_top():
    minima, maxima, n_crossed = clip_fill_level_target_profile(
        series([300]), series([400]), RANGE_BOTTOM, RANGE_TOP
    )
    assert minima.tolist() == [300]
    assert maxima.tolist() == [RANGE_TOP]
    assert n_crossed == 0


def test_target_band_entirely_outside_the_range_collapses_to_midpoint():
    # Target band entirely below the range bottom: clipped bounds cross
    # (minima -> bottom, maxima stays below it), so both collapse to the
    # midpoint of the clipped pair, keeping minima <= maxima pointwise.
    minima, maxima, n_crossed = clip_fill_level_target_profile(
        series([250, 300]), series([260, 330]), RANGE_BOTTOM, RANGE_TOP
    )
    expected_midpoint = (RANGE_BOTTOM + 260) / 2
    assert minima.tolist() == [expected_midpoint, 300]
    assert maxima.tolist() == [expected_midpoint, 330]
    assert (minima <= maxima).all()
    assert n_crossed == 1


def make_frbc(fill_level_scale: float = 1.0) -> FRBCSimple:
    frbc = FRBCSimple(
        power_sensor_id=1,
        soc_sensor_id=2,
        rm_discharge_sensor_id=3,
        price_sensor_id=4,
        production_price_sensor_id=5,
        soc_minima_sensor_id=6,
        soc_maxima_sensor_id=7,
        usage_forecast_sensor_id=8,
        leakage_behaviour_sensor_id=9,
        charging_efficiency_sensor_id=10,
        fill_level_scale=fill_level_scale,
        energy_unit="kWh",
    )
    # normally attached by CEM.register_control_type
    frbc._logger = logging.getLogger("test_frbc_soft_range")
    return frbc


@pytest.mark.asyncio
async def test_flex_model_declares_wide_safety_rails(frbc_system_description):
    """soc-min/soc-max are wide rails (0 and 1.5x the declared range top),
    not the declared fill-level range."""
    frbc = make_frbc()
    sd = frbc_system_description
    frbc._system_description_history[str(sd.message_id)] = sd
    status = FRBCStorageStatus(message_id=get_unique_id(), present_fill_level=0.5)
    frbc._storage_status_history[str(status.message_id)] = status

    frbc._fm_client = AsyncMock()
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        side_effect=RuntimeError("stop after capturing the flex model")
    )
    with pytest.raises(RuntimeError, match="stop after capturing"):
        await frbc.trigger_schedule(datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc))

    kwargs = frbc._fm_client.trigger_and_get_schedule.call_args.kwargs
    flex_model = kwargs["flex_model"]
    range_top = sd.storage.fill_level_range.end_of_range
    assert flex_model["soc-min"] == 0.0
    assert flex_model["soc-max"] == pytest.approx(range_top * 1.5)
    # comfort steering stays on the (clipped) profile sensors
    assert flex_model["soc-minima"] == {"sensor": 6}
    assert flex_model["soc-maxima"] == {"sensor": 7}


@pytest.mark.asyncio
async def test_safety_rails_still_widen_to_include_the_start_state(
    frbc_system_description,
):
    """A start state above the upper rail widens the rail (last resort
    against infeasibility)."""
    frbc = make_frbc()
    sd = frbc_system_description
    frbc._system_description_history[str(sd.message_id)] = sd
    range_top = sd.storage.fill_level_range.end_of_range
    status = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=range_top * 2
    )
    frbc._storage_status_history[str(status.message_id)] = status

    frbc._fm_client = AsyncMock()
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        side_effect=RuntimeError("stop after capturing the flex model")
    )
    with pytest.raises(RuntimeError, match="stop after capturing"):
        await frbc.trigger_schedule(datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc))

    flex_model = frbc._fm_client.trigger_and_get_schedule.call_args.kwargs["flex_model"]
    assert flex_model["soc-max"] == pytest.approx(range_top * 2)


@pytest.mark.asyncio
async def test_fill_level_target_profile_is_posted_clipped(frbc_system_description):
    """The posted soc-minima/-maxima series are clipped into the declared
    fill-level range (night setback clips up to the range bottom)."""
    frbc = make_frbc(fill_level_scale=RANGE_TOP)  # declared range: 0..1 -> 0..355
    sd = frbc_system_description
    frbc._system_description_history[str(sd.message_id)] = sd
    range_bottom = sd.storage.fill_level_range.start_of_range * RANGE_TOP  # 0.0
    range_top = sd.storage.fill_level_range.end_of_range * RANGE_TOP  # 355.0

    profile = FRBCFillLevelTargetProfile(
        message_id=get_unique_id(),
        start_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
        elements=[
            # night setback: target floor below the declared range bottom
            FRBCFillLevelTargetProfileElement(
                duration=Duration.from_timedelta(timedelta(minutes=15)),
                fill_level_range=NumberRange(start_of_range=-0.5, end_of_range=0.8),
            ),
            # daytime: target ceiling above the declared range top
            FRBCFillLevelTargetProfileElement(
                duration=Duration.from_timedelta(timedelta(minutes=15)),
                fill_level_range=NumberRange(start_of_range=0.8, end_of_range=1.2),
            ),
        ],
    )

    frbc._fm_client = AsyncMock()
    await frbc.send_fill_level_target_profile(profile)

    posts = {
        call.kwargs["sensor_id"]: call.kwargs["values"]
        for call in frbc._fm_client.post_sensor_data.call_args_list
    }
    minima, maxima = posts[6], posts[7]
    assert minima[0] == pytest.approx(range_bottom)  # clipped UP to the bottom
    assert maxima[0] == pytest.approx(0.8 * RANGE_TOP)  # untouched
    assert minima[1] == pytest.approx(0.8 * RANGE_TOP)  # untouched
    assert maxima[1] == pytest.approx(range_top)  # clipped DOWN to the top
    assert all(lo <= hi for lo, hi in zip(minima, maxima))
