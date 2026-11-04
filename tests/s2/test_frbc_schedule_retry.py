"""A schedule that failed to arrive is asked for again, where asking again can answer differently.

A scheduling job can fail on a transient of its own,
from a worker crashing out of its solver to a pandas error over the windows two of these schedules overlap in.
Unanswered, that failure costs the storage its instruction for the window:
the resource manager is told nothing and goes on polling,
and the exception is logged and dropped by the task this runs as.
These tests pin which failures are asked about again, how often, and what is said when asking stops.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from s2python.common import Commodity, CommodityQuantity, NumberRange, PowerRange
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCStorageStatus,
    FRBCSystemDescription,
)

from flexmeasures_client.client import FlexMeasuresClient, _server_version_at_least
from flexmeasures_client.exceptions import JobFailedError
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import (
    SCHEDULE_ATTEMPTS,
    FRBCSimple,
)
from flexmeasures_client.s2.utils import get_unique_id

RANGE_TOP = 80.0
FILL_LEVEL_AT_START = 30.0
VALID_FROM = datetime(2024, 1, 1, tzinfo=timezone.utc)
SCHEDULE = {
    "values": [0.0] * 48,
    "start": "2024-01-01T00:00:00+00:00",
    "duration": "PT12H",
}
#: What a scheduling job that failed inside the server reports back,
#: which is the job's status description and says nothing about why it failed.
A_FAILED_JOB = JobFailedError(
    "Job 1 ended with status failed. Scheduling job failed with "
    "ValueError: cannot reindex on an axis with duplicate labels."
)


def _heater() -> FRBCSystemDescription:
    """An on-off heater, which is all the storage any of this needs."""
    actuator = FRBCActuatorDescription(
        id=get_unique_id(),
        supported_commodities=[Commodity.ELECTRICITY],
        operation_modes=[
            FRBCOperationMode(
                id=get_unique_id(),
                diagnostic_label="on",
                elements=[
                    FRBCOperationModeElement(
                        fill_level_range=NumberRange(
                            start_of_range=0, end_of_range=RANGE_TOP
                        ),
                        fill_rate=NumberRange(start_of_range=0.001, end_of_range=0.001),
                        power_ranges=[
                            PowerRange(
                                start_of_range=883.7,
                                end_of_range=883.7,
                                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                            )
                        ],
                    )
                ],
                abnormal_condition_only=False,
            )
        ],
        transitions=[],
        timers=[],
    )
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from=VALID_FROM,
        actuators=[actuator],
        storage=FRBCStorageDescription(
            provides_leakage_behaviour=False,
            provides_fill_level_target_profile=False,
            provides_usage_forecast=False,
            fill_level_range=NumberRange(start_of_range=0, end_of_range=RANGE_TOP),
        ),
    )


def _frbc(system_description, **kwargs) -> FRBCSimple:
    """A handler that has had a system description and a storage status, ready to plan.

    Attempts follow each other without waiting, unless a test is about the waiting itself.
    """
    kwargs.setdefault("schedule_retry_interval", timedelta(0))
    frbc = FRBCSimple(
        power_sensor_id=1,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        **kwargs,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._fm_client.server_version = "1.1.0"
    frbc._fm_client.server_version_at_least = AsyncMock(
        side_effect=lambda minimum: _server_version_at_least("1.1.0", minimum)
    )
    frbc._logger = logging.getLogger(__name__)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=FILL_LEVEL_AT_START
    )
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(return_value=SCHEDULE)
    frbc.send_instruction_batch = AsyncMock(return_value=0)
    return frbc


def _attempts(frbc: FRBCSimple) -> int:
    """How many times a schedule was asked for."""
    return frbc._fm_client.trigger_and_get_schedule.await_count


@pytest.mark.asyncio
async def test_a_schedule_that_arrives_is_asked_for_once():
    system_description = _heater()
    frbc = _frbc(system_description)

    await frbc.trigger_schedule(system_description.message_id)

    assert _attempts(frbc) == 1
    frbc.send_instruction_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_job_is_asked_about_again_and_the_instructions_still_go_out(
    caplog,
):
    """The point of the whole thing: one failed job no longer costs the storage its instruction."""
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        side_effect=[A_FAILED_JOB, SCHEDULE]
    )

    with caplog.at_level(logging.WARNING):
        await frbc.trigger_schedule(system_description.message_id)

    assert _attempts(frbc) == 2
    frbc.send_instruction_batch.assert_awaited_once()
    assert "worth asking again" in caplog.text


@pytest.mark.asyncio
async def test_asking_again_stops_after_the_last_attempt(caplog):
    """A failure that never answers differently is raised, and says what it cost."""
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(side_effect=A_FAILED_JOB)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(JobFailedError):
            await frbc.trigger_schedule(system_description.message_id)

    assert _attempts(frbc) == SCHEDULE_ATTEMPTS
    frbc.send_instruction_batch.assert_not_awaited()
    assert "left without an instruction" in caplog.text
    assert f"attempt {SCHEDULE_ATTEMPTS} of {SCHEDULE_ATTEMPTS}" in caplog.text


@pytest.mark.asyncio
async def test_a_schedule_is_asked_for_as_often_as_the_deployment_says():
    system_description = _heater()
    frbc = _frbc(system_description, schedule_attempts=2)
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(side_effect=A_FAILED_JOB)

    with pytest.raises(JobFailedError):
        await frbc.trigger_schedule(system_description.message_id)

    assert _attempts(frbc) == 2


@pytest.mark.asyncio
async def test_one_attempt_means_a_failure_is_never_answered_by_another():
    """What this handler did before the attempts were bounded at all, for a deployment that wants it back."""
    system_description = _heater()
    frbc = _frbc(system_description, schedule_attempts=1)
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(side_effect=A_FAILED_JOB)

    with pytest.raises(JobFailedError):
        await frbc.trigger_schedule(system_description.message_id)

    assert _attempts(frbc) == 1


def test_a_handler_that_would_never_ask_for_a_schedule_is_refused():
    with pytest.raises(ValueError, match="at least one"):
        FRBCSimple(
            power_sensor_id=1,
            price_sensor_id=2,
            soc_sensor_id=3,
            rm_discharge_sensor_id=4,
            schedule_attempts=0,
        )


@pytest.mark.asyncio
async def test_a_belief_the_scheduler_recorded_twice_is_not_asked_about_again():
    """A job that collides with its own first write collides again on every attempt."""
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        side_effect=JobFailedError(
            "Job 1 ended with status failed. Scheduling job failed with "
            "UniqueViolation: duplicate key value violates unique constraint "
            '"timed_belief_pkey".'
        )
    )

    with pytest.raises(JobFailedError):
        await frbc.trigger_schedule(system_description.message_id)

    assert _attempts(frbc) == 1


@pytest.mark.asyncio
async def test_an_infeasible_problem_is_not_asked_about_again():
    """The same flex model and context are planned from every time, so it stays infeasible."""
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        side_effect=JobFailedError(
            "Job 1 ended with status failed. Scheduling job failed with "
            "InfeasibleProblemException: the problem was found to be infeasible."
        )
    )

    with pytest.raises(JobFailedError):
        await frbc.trigger_schedule(system_description.message_id)

    assert _attempts(frbc) == 1


@pytest.mark.asyncio
async def test_a_trigger_refused_over_the_state_of_charge_is_asked_again():
    """A schedule can be asked for before the state of charge it plans from has landed."""
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        side_effect=[
            ValueError(
                "No recent state-of-charge value found for sensor 3, "
                "so the schedule could not be triggered."
            ),
            SCHEDULE,
        ]
    )

    await frbc.trigger_schedule(system_description.message_id)

    assert _attempts(frbc) == 2
    frbc.send_instruction_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_trigger_refused_on_what_was_asked_is_not_asked_again():
    """A flex model FlexMeasures will not take is not one it will take two seconds later."""
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        side_effect=ValueError(
            "Validation error: 'soc-max' is not a valid quantity for this sensor."
        )
    )

    with pytest.raises(ValueError):
        await frbc.trigger_schedule(system_description.message_id)

    assert _attempts(frbc) == 1


@pytest.mark.asyncio
async def test_each_attempt_says_what_is_known_when_it_is_made():
    """A prior carried over from the first attempt would claim knowledge from before it."""
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        side_effect=[A_FAILED_JOB, SCHEDULE]
    )
    moments = iter(
        [
            datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 10, 0, 2, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 10, 0, 4, tzinfo=timezone.utc),
        ]
    )
    frbc.now = lambda: next(moments)

    await frbc.trigger_schedule(system_description.message_id)

    priors = [
        call.kwargs["prior"]
        for call in frbc._fm_client.trigger_and_get_schedule.await_args_list
    ]
    assert len(priors) == 2
    assert priors[0] < priors[1]


@pytest.mark.asyncio
async def test_two_attempts_are_separated_by_the_configured_wait(monkeypatch):
    """Asking again immediately would ask the same worker pool that just failed."""
    system_description = _heater()
    frbc = _frbc(
        system_description, schedule_retry_interval=timedelta(milliseconds=250)
    )
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        side_effect=[A_FAILED_JOB, SCHEDULE]
    )
    waits = []

    async def _record_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _record_wait)

    await frbc.trigger_schedule(system_description.message_id)

    assert waits == [0.25]
