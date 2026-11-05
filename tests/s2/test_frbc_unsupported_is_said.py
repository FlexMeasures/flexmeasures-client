"""What this CEM does not support is said when it arrives, rather than discovered after a solve, or never."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
from s2python.common import Duration, ReceptionStatusValues, Timer
from s2python.frbc import FRBCTimerStatus

from flexmeasures_client.s2.utils import get_unique_id

from .test_frbc_schedule_retry import _frbc, _heater


def _with(system_description, **actuator_update):
    actuator = system_description.actuators[0].model_copy(update=actuator_update)
    return system_description.model_copy(update={"actuators": [actuator]})


@pytest.mark.asyncio
async def test_a_description_of_several_actuators_is_refused_when_it_arrives(caplog):
    """Acknowledged, it failed in a task nobody awaited, after a solve, on every round."""
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._system_description_history.clear()
    two = system_description.model_copy(
        update={
            "message_id": get_unique_id(),
            "actuators": [
                system_description.actuators[0],
                system_description.actuators[0].model_copy(
                    update={"id": get_unique_id()}
                ),
            ],
        }
    )

    status = await frbc.handle_message(two.to_dict())

    assert status.status == ReceptionStatusValues.INVALID_CONTENT
    assert "exactly one actuator" in status.diagnostic_label
    assert not frbc._system_description_history
    assert not frbc.background_tasks


@pytest.mark.asyncio
async def test_a_description_with_timers_or_transitions_is_taken_with_a_warning(caplog):
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._storage_status_history.clear()
    timer = Timer(
        id=get_unique_id(),
        diagnostic_label="minimum on time",
        duration=Duration.from_milliseconds(60_000),
    )
    described = _with(system_description, timers=[timer])
    described = described.model_copy(update={"message_id": get_unique_id()})

    with caplog.at_level(logging.WARNING):
        status = await frbc.handle_message(described.to_dict())

    assert status.status == ReceptionStatusValues.OK
    assert "1 timers and 0 transitions" in caplog.text


@pytest.mark.asyncio
async def test_a_description_without_timers_says_nothing_of_them(caplog):
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._storage_status_history.clear()
    with caplog.at_level(logging.WARNING):
        status = await frbc.handle_message(
            system_description.model_copy(
                update={"message_id": get_unique_id()}
            ).to_dict()
        )
    # The description was taken, so silence is the handler's and not a description that never reached it.
    assert status.status == ReceptionStatusValues.OK
    assert len(frbc._system_description_history) == 2
    assert "does not honour" not in caplog.text


@pytest.mark.asyncio
async def test_a_timer_status_is_kept_and_said_to_be_unhonoured(caplog):
    system_description = _heater()
    frbc = _frbc(system_description)
    timer_status = FRBCTimerStatus(
        message_id=get_unique_id(),
        timer_id=get_unique_id(),
        actuator_id=system_description.actuators[0].id,
        finished_at=datetime(2024, 1, 1, 0, 5, tzinfo=timezone.utc),
    )

    with caplog.at_level(logging.WARNING):
        status = await frbc.handle_message(timer_status.to_dict())

    assert status.status == ReceptionStatusValues.OK
    assert str(timer_status.message_id) in frbc._timer_status_history
    assert "does not honour" in caplog.text


async def _flex_model(frbc, system_description) -> dict:
    await frbc.trigger_schedule(system_description.message_id)
    return frbc._fm_client.trigger_and_get_schedule.await_args.kwargs["flex_model"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provided", [True, False])
async def test_usage_is_planned_with_only_where_the_resource_manager_says_it_sends_it(
    provided,
):
    """Named regardless, the scheduler read a sensor nothing was ever recorded on."""
    system_description = _heater()
    storage = system_description.storage.model_copy(
        update={"provides_usage_forecast": provided}
    )
    system_description = system_description.model_copy(update={"storage": storage})
    frbc = _frbc(system_description, usage_forecast_sensor_id=7)

    flex_model = await _flex_model(frbc, system_description)

    assert ("soc-usage" in flex_model) is provided
