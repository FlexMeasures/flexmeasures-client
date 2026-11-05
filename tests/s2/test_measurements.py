"""Recording a resource manager's measured power, live and in a co-simulation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.measurements import MeasurementRecorder

T = datetime(2022, 3, 1, 12, 0, tzinfo=timezone.utc)
Q = timedelta(minutes=15)


def _recorder(resolution: str = "PT15M") -> tuple[MeasurementRecorder, AsyncMock]:
    fm = AsyncMock(FlexMeasuresClient)
    fm.get_sensor = AsyncMock(return_value={"event_resolution": resolution})
    return MeasurementRecorder(fm), fm


def _posts(fm) -> list[dict]:
    return [
        c.kwargs | {"sensor_id": c.args[0]} for c in fm.post_sensor_data.await_args_list
    ]


@pytest.mark.asyncio
async def test_a_co_simulations_burst_of_past_quarter_hours_is_recorded_in_one_request():
    """Its resource manager sends a window's measurements once the window has passed, each stamped at its quarter hour's start.

    Binned by the moment they arrived, none of them fell in the step the CEM's clock was in, and none was recorded.
    """
    recorder, fm = _recorder()
    window_end = T + 4 * Q
    for i, watts in enumerate([1000, 2000, 0, 500]):
        await recorder.add(7, T + i * Q, watts, now=window_end)
    (post,) = _posts(fm)[-1:]
    recorded = {}
    for p in _posts(fm):
        for k, v in enumerate(p["values"]):
            recorded[p["start"] + k * Q] = v
    assert recorded == {T: 1000, T + Q: 2000, T + 2 * Q: 0, T + 3 * Q: 500}
    assert (
        post["unit"] == "W"
    )  # as S2 states power; it used to go out labelled kW, a thousand times too large
    assert (
        post["prior"] == post["start"] + len(post["values"]) * Q
    )  # known at the end of what it measures


@pytest.mark.asyncio
async def test_a_live_resource_managers_samples_are_averaged_per_step_once_it_is_over():
    recorder, fm = _recorder()
    await recorder.add(7, T + timedelta(minutes=1), 1000, now=T + timedelta(minutes=1))
    await recorder.add(7, T + timedelta(minutes=8), 2000, now=T + timedelta(minutes=8))
    fm.post_sensor_data.assert_not_awaited()  # the step is not over
    await recorder.add(
        7, T + Q + timedelta(minutes=1), 800, now=T + Q + timedelta(minutes=1)
    )
    (post,) = _posts(fm)
    assert (post["start"], post["values"], post["duration"]) == (T, [1500], Q)


@pytest.mark.asyncio
async def test_a_step_sent_again_is_not_recorded_again():
    """A co-simulation's resource manager reconnects every round and sends its series again."""
    recorder, fm = _recorder()
    await recorder.add(7, T, 1000, now=T + Q)
    await recorder.add(7, T, 1000, now=T + Q)
    await recorder.add(
        7, T, 999, now=T + Q
    )  # the first value recorded for a step stands
    assert len(_posts(fm)) == 1


@pytest.mark.asyncio
async def test_a_step_that_could_not_be_recorded_is_tried_again(caplog):
    recorder, fm = _recorder()
    fm.post_sensor_data.side_effect = [ConnectionError("down"), None]
    await recorder.add(7, T, 1000, now=T + Q)
    assert "trying again later" in caplog.text
    await recorder.add(7, T + Q, 2000, now=T + 2 * Q)
    retried = _posts(fm)[-1]
    assert (retried["start"], retried["values"]) == (T, [1000, 2000])


@pytest.mark.asyncio
async def test_steps_that_do_not_follow_one_another_go_in_requests_of_their_own():
    recorder, fm = _recorder()
    await recorder.add(7, T, 1, now=T)
    await recorder.add(7, T + 2 * Q, 3, now=T)
    await recorder.record(7, now=T + 3 * Q)
    assert [(p["start"], p["values"]) for p in _posts(fm)] == [
        (T, [1]),
        (T + 2 * Q, [3]),
    ]


@pytest.mark.asyncio
async def test_each_sensor_is_recorded_at_its_own_resolution():
    recorder, fm = _recorder("PT1H")
    await recorder.add(7, T + timedelta(minutes=20), 10, now=T + timedelta(minutes=20))
    await recorder.add(7, T + timedelta(minutes=40), 30, now=T + timedelta(hours=1))
    (post,) = _posts(fm)
    assert (post["start"], post["values"], post["duration"]) == (
        T,
        [20],
        timedelta(hours=1),
    )
    fm.get_sensor.assert_awaited_once()  # asked once


@pytest.mark.asyncio
async def test_on_an_instantaneous_sensor_each_measurement_is_its_own_reading():
    recorder, fm = _recorder("PT0M")
    await recorder.add(7, T, 10, now=T)
    await recorder.add(7, T + timedelta(seconds=30), 20, now=T + timedelta(seconds=30))
    assert [(p["start"], p["values"], p["duration"]) for p in _posts(fm)] == [
        (T, [10], timedelta(0)),
        (T + timedelta(seconds=30), [20], timedelta(0)),
    ]


@pytest.mark.asyncio
async def test_a_sensor_stating_no_resolution_cannot_take_measurements():
    recorder, fm = _recorder()
    fm.get_sensor = AsyncMock(return_value={})
    with pytest.raises(ValueError, match="no resolution"):
        await recorder.add(7, T, 10, now=T + Q)


@pytest.mark.asyncio
async def test_what_was_recorded_is_forgotten_after_a_week():
    recorder, fm = _recorder()
    await recorder.add(7, T, 1000, now=T + Q)
    await recorder.record(7, now=T + timedelta(days=8))
    await recorder.add(7, T, 1000, now=T + timedelta(days=8))
    assert len(_posts(fm)) == 2


@pytest.mark.asyncio
async def test_the_cem_hands_measured_power_to_its_recorder_and_asks_again_where_it_cannot():
    from s2python.common import (
        CommodityQuantity,
        PowerMeasurement,
        PowerValue,
        ReceptionStatusValues,
    )

    from flexmeasures_client.s2.cem import CEM
    from flexmeasures_client.s2.clock import SimulatedClock
    from flexmeasures_client.s2.utils import get_unique_id

    recorder, fm = _recorder()
    clock = SimulatedClock(T + timedelta(minutes=5))
    cem = CEM(
        fm_client=fm,
        clock=clock,
        power_sensor_id={"ELECTRIC.POWER.L1": 7},
        measurement_recorder=recorder,
    )

    def measurement() -> PowerMeasurement:
        return PowerMeasurement(
            message_id=get_unique_id(),
            measurement_timestamp=T,
            values=[
                PowerValue(
                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1, value=750.0
                )
            ],
        )

    await cem.handle_message(measurement())
    assert (await cem.get_message())["status"] == ReceptionStatusValues.OK.value
    fm.post_sensor_data.assert_not_awaited()  # its step is not over by the simulated clock
    clock.set(T + Q)
    await cem.handle_message(measurement())
    assert (await cem.get_message())["status"] == ReceptionStatusValues.OK.value
    assert _posts(fm)[0]["values"] == [750.0]

    fm.get_sensor = AsyncMock(side_effect=ConnectionError("down"))
    recorder._resolutions.clear()
    await cem.handle_message(measurement())
    assert (await cem.get_message())[
        "status"
    ] == ReceptionStatusValues.TEMPORARY_ERROR.value


def test_a_cem_has_a_recorder_of_its_own_unless_given_one():
    from flexmeasures_client.s2.cem import CEM

    assert isinstance(CEM(fm_client=None)._measurement_recorder, MeasurementRecorder)
