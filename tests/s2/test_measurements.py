"""Recording a resource manager's measured power, live and in a co-simulation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2 import measurements
from flexmeasures_client.s2.measurements import MeasurementRecorder, step_of

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


def _recorded(fm) -> dict[datetime, float]:
    return {p["start"]: p["values"][0] for p in _posts(fm)}


@pytest.mark.asyncio
async def test_a_co_simulations_burst_of_past_quarter_hours_is_recorded():
    """Its resource manager sends a window's measurements once the window has passed, each stamped at its quarter hour's start.

    Binned by the moment they arrived, none of them fell in the step the CEM's clock was in, and none was recorded.
    """
    recorder, fm = _recorder()
    window_end = T + 4 * Q
    for i, watts in enumerate([1000, 2000, 0, 500]):
        await recorder.add(7, T + i * Q, watts, now=window_end)
    await recorder.settle()
    assert _recorded(fm) == {T: 1000, T + Q: 2000, T + 2 * Q: 0, T + 3 * Q: 500}
    for post in _posts(fm):
        assert (
            post["unit"] == "W"
        )  # as S2 states power; it went out labelled kW, a thousand times too large
        assert post["duration"] == Q
        assert (
            post["prior"] == post["start"] + Q
        )  # each known at the end of what it measures


@pytest.mark.asyncio
async def test_a_live_resource_managers_samples_are_averaged_per_step_once_it_is_over():
    recorder, fm = _recorder()
    await recorder.add(7, T + timedelta(minutes=1), 1000, now=T + timedelta(minutes=1))
    await recorder.add(7, T + timedelta(minutes=8), 2000, now=T + timedelta(minutes=8))
    await recorder.settle()
    fm.post_sensor_data.assert_not_awaited()  # the step is not over
    await recorder.add(
        7, T + Q + timedelta(minutes=1), 800, now=T + Q + timedelta(minutes=1)
    )
    await recorder.settle()
    assert _recorded(fm) == {T: 1500}


@pytest.mark.asyncio
async def test_a_step_sent_again_is_not_recorded_again():
    """A co-simulation's resource manager reconnects every round and sends its series again."""
    recorder, fm = _recorder()
    await recorder.add(7, T, 1000, now=T + Q)
    await recorder.settle()
    await recorder.add(7, T, 1000, now=T + Q)
    await recorder.add(
        7, T, 999, now=T + Q
    )  # the first value recorded for a step stands
    await recorder.settle()
    assert len(_posts(fm)) == 1


@pytest.mark.asyncio
async def test_a_step_that_could_not_be_recorded_is_tried_again_without_another_measurement(
    caplog,
):
    """The final window of a co-simulation has no measurement after it to try again with."""
    recorder, fm = _recorder()
    fm.post_sensor_data.side_effect = [ConnectionError("down"), None]
    with patch.object(measurements, "RETRY_AFTER", timedelta(milliseconds=10)):
        await recorder.add(7, T, 1000, now=T + Q)
        await recorder.settle()
    assert "trying again later" in caplog.text
    assert [p["start"] for p in _posts(fm)] == [T, T]


@pytest.mark.asyncio
async def test_a_step_that_keeps_failing_is_given_up_on_and_does_not_hold_up_the_rest(
    caplog,
):
    """Posted together, one step the server always refused failed every request after it, and nothing was recorded again."""
    recorder, fm = _recorder()

    async def refuse_the_first_step(sensor_id, **kwargs):
        if kwargs["start"] == T:
            raise ValueError("422")

    fm.post_sensor_data.side_effect = refuse_the_first_step
    with patch.object(measurements, "RETRY_AFTER", timedelta(milliseconds=10)):
        await recorder.add(7, T, 1000, now=T + Q)
        await recorder.add(7, T + Q, 2000, now=T + 2 * Q)
        await recorder.settle()
    assert [p["start"] for p in _posts(fm)].count(T) == measurements.ATTEMPTS
    assert T + Q in [p["start"] for p in _posts(fm)]
    assert "Giving up" in caplog.text
    assert recorder._pending[7] == {}


@pytest.mark.asyncio
async def test_two_measurements_for_one_sensor_at_once_post_each_step_once():
    """Two CEMs on one sensor, or a sample arriving while its step's post is in flight, posted it twice and then raised."""
    recorder, fm = _recorder()
    release = asyncio.Event()

    async def slow(sensor_id, **kwargs):
        await release.wait()

    fm.post_sensor_data.side_effect = slow
    await recorder.add(7, T, 100, now=T + Q)
    await asyncio.sleep(0)
    await recorder.add(7, T + timedelta(minutes=5), 200, now=T + Q)
    release.set()
    await recorder.settle()
    assert [p["start"] for p in _posts(fm)].count(T) == 1


@pytest.mark.asyncio
async def test_each_sensor_is_recorded_at_its_own_resolution():
    recorder, fm = _recorder("PT1H")
    await recorder.add(7, T + timedelta(minutes=20), 10, now=T + timedelta(minutes=20))
    await recorder.add(7, T + timedelta(minutes=40), 30, now=T + timedelta(hours=1))
    await recorder.settle()
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
    await recorder.settle()
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
    await recorder.settle()
    await recorder.add(7, T + timedelta(days=8), 1, now=T + timedelta(days=8))
    await recorder.settle()
    assert T not in recorder._recorded[7]
    await recorder.add(7, T, 1000, now=T + timedelta(days=8))
    await recorder.settle()
    assert [p["start"] for p in _posts(fm)].count(T) == 2


def test_a_step_in_the_repeated_autumn_hour_is_found_in_the_pass_the_moment_is_in():
    """pandas cannot floor such a moment by its pass alone, and the CEM answered every resend with a temporary error."""
    amsterdam = ZoneInfo("Europe/Amsterdam")
    second_pass = datetime(2026, 10, 25, 1, 37, tzinfo=timezone.utc).astimezone(
        amsterdam
    )
    assert second_pass.fold == 1
    step = step_of(second_pass, Q)
    assert step.astimezone(timezone.utc) == datetime(
        2026, 10, 25, 1, 30, tzinfo=timezone.utc
    )


def test_steps_are_on_the_moments_own_clock():
    kolkata = ZoneInfo("Asia/Kolkata")
    assert step_of(
        datetime(2026, 11, 5, 14, 40, tzinfo=kolkata), timedelta(hours=1)
    ) == datetime(2026, 11, 5, 14, 0, tzinfo=kolkata)


def _measurement(*values: tuple[str, float]):
    from s2python.common import CommodityQuantity, PowerMeasurement, PowerValue

    from flexmeasures_client.s2.utils import get_unique_id

    return PowerMeasurement(
        message_id=get_unique_id(),
        measurement_timestamp=T,
        values=[
            PowerValue(commodity_quantity=CommodityQuantity(q), value=v)
            for q, v in values
        ],
    )


@pytest.mark.asyncio
async def test_the_cem_hands_measured_power_to_its_recorder_on_its_own_clock():
    from s2python.common import ReceptionStatusValues

    from flexmeasures_client.s2.cem import CEM
    from flexmeasures_client.s2.clock import SimulatedClock

    recorder, fm = _recorder()
    clock = SimulatedClock(T + timedelta(minutes=5))
    cem = CEM(
        fm_client=fm,
        clock=clock,
        power_sensor_id={"ELECTRIC.POWER.L1": 7},
        measurement_recorder=recorder,
    )
    await cem.handle_message(_measurement(("ELECTRIC.POWER.L1", 750.0)))
    assert (await cem.get_message())["status"] == ReceptionStatusValues.OK.value
    await recorder.settle()
    fm.post_sensor_data.assert_not_awaited()  # its step is not over by the simulated clock
    clock.set(T + Q)
    await cem.handle_message(_measurement(("ELECTRIC.POWER.L1", 750.0)))
    await cem.get_message()
    await recorder.settle()
    assert _recorded(fm) == {T: 750.0}


@pytest.mark.asyncio
async def test_a_message_the_cem_cannot_take_in_full_is_taken_not_at_all():
    """Asked for again, a message taken in part had the part taken twice, which skewed its average."""
    from s2python.common import ReceptionStatusValues

    from flexmeasures_client.s2.cem import CEM
    from flexmeasures_client.s2.clock import SimulatedClock

    recorder, fm = _recorder()

    async def readable(sensor_id, **kwargs):
        if sensor_id == 8:
            raise ConnectionError("down")
        return {"event_resolution": "PT15M"}

    fm.get_sensor = AsyncMock(side_effect=readable)
    cem = CEM(
        fm_client=fm,
        clock=SimulatedClock(T + timedelta(minutes=5)),
        power_sensor_id={"ELECTRIC.POWER.L1": 7, "ELECTRIC.POWER.L2": 8},
        measurement_recorder=recorder,
    )
    await cem.handle_message(
        _measurement(("ELECTRIC.POWER.L1", 750.0), ("ELECTRIC.POWER.L2", 10.0))
    )
    assert (await cem.get_message())[
        "status"
    ] == ReceptionStatusValues.TEMPORARY_ERROR.value
    assert recorder._pending[7] == {}


def test_a_cem_has_a_recorder_of_its_own_unless_given_one():
    from flexmeasures_client.s2.cem import CEM

    assert isinstance(CEM(fm_client=None)._measurement_recorder, MeasurementRecorder)


@pytest.mark.asyncio
async def test_a_defect_while_recording_is_logged_rather_than_lost(caplog):
    """An exception no post is expected to raise surfaces in the log, not as a task exception never retrieved."""
    recorder, fm = _recorder()
    fm.post_sensor_data.side_effect = TypeError("a malformed error payload")
    await recorder.add(7, T, 1000, now=T + Q)
    await recorder.settle()
    assert "Recording measured power failed" in caplog.text
