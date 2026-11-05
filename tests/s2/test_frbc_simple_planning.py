"""What makes FRBCSimple ask FlexMeasures for a schedule, and where that schedule starts.

A storage status is the planning request: it reports the state of charge a schedule has to start
from, and it is the message a resource manager keeps sending.
A system description says what the device can do, so it is stored and nothing is asked for.
The other way around, which is how this handler used to work, a resource manager that sends its
description once and then only statuses is planned for once and never again, and one that sends
its description before its first status - the order the specification suggests - is not planned
for at all, because there was no state of charge to start from yet.
"""

from __future__ import annotations

import asyncio
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

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.utils import get_unique_id

POWER_SENSOR_ID = 1
#: A clock set before the descriptions below are valid, so that their own times decide where a schedule starts.
BEFORE_THE_EXAMPLES = datetime(2022, 12, 1, tzinfo=timezone.utc)
SOC_SENSOR_ID = 3


def system_description(valid_from: str) -> FRBCSystemDescription:
    mode = FRBCOperationMode(
        id=get_unique_id(),
        diagnostic_label="on",
        elements=[
            FRBCOperationModeElement(
                fill_level_range=NumberRange(start_of_range=0, end_of_range=1),
                fill_rate=NumberRange(start_of_range=0, end_of_range=1),
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=1000,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                    )
                ],
            )
        ],
        abnormal_condition_only=False,
    )
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from=valid_from,
        actuators=[
            FRBCActuatorDescription(
                id=get_unique_id(),
                supported_commodities=[Commodity.ELECTRICITY],
                operation_modes=[mode],
                transitions=[],
                timers=[],
            )
        ],
        storage=FRBCStorageDescription(
            provides_leakage_behaviour=False,
            provides_fill_level_target_profile=False,
            provides_usage_forecast=False,
            fill_level_range=NumberRange(start_of_range=0, end_of_range=1),
        ),
    )


def frbc_simple() -> FRBCSimple:
    frbc = FRBCSimple(
        power_sensor_id=POWER_SENSOR_ID,
        price_sensor_id=2,
        soc_sensor_id=SOC_SENSOR_ID,
        rm_discharge_sensor_id=4,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    # Normally the CEM hands its queue to a handler when it registers one.
    frbc._sending_queue = asyncio.Queue()
    # A schedule the way the client hands one back, so that it can become instructions.
    frbc._fm_client.trigger_and_get_schedule.return_value = {
        "start": "2022-12-03T00:00:00+00:00",
        "duration": "PT1H",
        "values": [100.0, 100.0, 0.0, 0.0],
    }
    return frbc


def storage_status(fill_level: float = 0.5) -> FRBCStorageStatus:
    return FRBCStorageStatus(message_id=get_unique_id(), present_fill_level=fill_level)


async def settle(frbc: FRBCSimple) -> None:
    """Let the tasks the handlers spawned run to completion."""
    await asyncio.gather(*frbc.background_tasks)


@pytest.mark.asyncio
async def test_a_storage_status_asks_for_a_schedule():
    frbc = frbc_simple()

    await frbc.handle_message(system_description("2022-12-02T00:00:00+00:00"))
    await settle(frbc)
    await frbc.handle_message(storage_status(0.4))
    await settle(frbc)

    frbc._fm_client.trigger_and_get_schedule.assert_awaited_once()
    assert frbc._sending_queue.qsize() > 0, "the schedule must become instructions"
    posted = frbc._fm_client.post_measurements.await_args
    assert posted.args[0] == SOC_SENSOR_ID
    assert posted.kwargs["values"] == [0.4]


@pytest.mark.asyncio
async def test_a_system_description_asks_for_nothing():
    """Storing it is all a description does, so nothing reaches FlexMeasures."""
    frbc = frbc_simple()

    await frbc.handle_message(system_description("2022-12-02T00:00:00+00:00"))
    await settle(frbc)

    assert len(frbc._system_description_history) == 1
    frbc._fm_client.trigger_and_get_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_state_of_charge_is_recorded_even_when_the_plan_is_superseded():
    """A measurement is data; only the planning behind it is worth dropping."""
    frbc = frbc_simple()
    await frbc.handle_message(system_description("2022-12-02T00:00:00+00:00"))
    await settle(frbc)

    # Two statuses back to back: both are recorded, only the newer one is planned for.
    await frbc.handle_message(storage_status(0.6))
    await frbc.handle_message(storage_status(0.7))
    await settle(frbc)

    assert frbc._fm_client.post_measurements.await_count == 2
    assert [
        call.kwargs["values"][0]
        for call in frbc._fm_client.post_measurements.await_args_list
    ] == [0.6, 0.7]
    frbc._fm_client.trigger_and_get_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_storage_status_before_any_description_still_records_the_measurement():
    frbc = frbc_simple()

    await frbc.handle_message(storage_status(0.3))
    await settle(frbc)

    frbc._fm_client.post_measurements.assert_awaited_once()
    frbc._fm_client.trigger_and_get_schedule.assert_not_awaited()


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
    frbc.now = lambda: BEFORE_THE_EXAMPLES

    start = frbc.schedule_start(datetime.fromisoformat(valid_from))

    assert start == datetime.fromisoformat(expected_start)
    assert (
        start - start.replace(hour=0, minute=0, second=0, microsecond=0)
    ) % timedelta(minutes=15) == timedelta(0)


@pytest.mark.asyncio
async def test_the_schedule_is_asked_for_from_an_aligned_start():
    frbc = frbc_simple()
    frbc.now = lambda: BEFORE_THE_EXAMPLES

    await frbc.handle_message(system_description("2022-12-02T09:07:33.512+00:00"))
    await settle(frbc)
    await frbc.handle_message(storage_status())
    await settle(frbc)

    start = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs["start"]
    assert start == datetime(2022, 12, 3, 9, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_planning_without_a_storage_status_is_logged_not_printed(capsys, caplog):
    """A handler runs unattended, so giving up on a schedule goes to the log, where it can be found.

    It used to go to standard output, which a deployment does not keep.
    """
    frbc = frbc_simple()
    await frbc.handle_message(system_description("2022-12-02T00:00:00+00:00"))
    await settle(frbc)
    system_description_id = list(frbc._system_description_history)[-1]

    with caplog.at_level("WARNING", logger="FRBCSimple"):
        await frbc.trigger_schedule(system_description_id)

    frbc._fm_client.trigger_and_get_schedule.assert_not_awaited()
    assert capsys.readouterr().out == ""
    assert any(
        record.levelname == "WARNING" and "FRBC.StorageStatus" in record.getMessage()
        for record in caplog.records
    ), caplog.records


@pytest.mark.asyncio
async def test_a_revoked_object_is_known_by_its_id_as_text():
    """Revocations are recorded by the id as text, which is how every other id in a handler is kept."""
    from s2python.common import RevokableObjects, RevokeObject

    frbc = frbc_simple()
    object_id = get_unique_id()
    await frbc.handle_message(
        RevokeObject(
            message_id=get_unique_id(),
            object_type=RevokableObjects.FRBC_Instruction,
            object_id=object_id,
        )
    )
    assert frbc.is_revoked(str(object_id))


def test_a_handler_recognises_a_message_in_any_form_it_accepts():
    """supports_message takes a model, a dict or JSON text, and reads the type from each the same way."""
    frbc = frbc_simple()
    description = system_description("2022-12-02T00:00:00+00:00")
    as_dict = description.model_dump(mode="json")
    assert frbc.supports_message(description)
    assert frbc.supports_message(as_dict)
    assert frbc.supports_message(description.model_dump_json())
    assert not frbc.supports_message(as_dict | {"message_type": "Handshake"})


@pytest.mark.asyncio
async def test_the_cem_records_a_revocation_by_its_id_as_text():
    """With no control type active, the CEM itself takes the revocation and hands it to the handler it is for."""
    from s2python.common import RevokableObjects, RevokeObject

    from flexmeasures_client.s2.cem import CEM

    cem = CEM(fm_client=frbc_simple()._fm_client)
    frbc = frbc_simple()
    cem.register_control_type(frbc)
    object_id = get_unique_id()
    await cem.handle_message(
        RevokeObject(
            message_id=get_unique_id(),
            object_type=RevokableObjects.FRBC_Instruction,
            object_id=object_id,
        )
    )
    assert frbc.is_revoked(str(object_id))


def test_a_handler_turns_away_a_model_it_does_not_handle():
    from s2python.common import EnergyManagementRole, Handshake

    handshake = Handshake(
        message_id=get_unique_id(),
        role=EnergyManagementRole.CEM,
        supported_protocol_versions=["0.0.2-beta"],
    )
    assert not frbc_simple().supports_message(handshake)


def test_a_schedule_never_starts_before_now():
    """A description days old still plans from the next boundary, rather than a window that has already passed.

    Nor from the start of the step under way: the fill level reported now is taken as the level at the start,
    so the plan would count on the whole step where only part of it is left.
    """
    frbc = frbc_simple()
    frbc.now = lambda: datetime(2022, 12, 10, 14, 7, tzinfo=timezone.utc)
    assert frbc.schedule_start(
        datetime(2022, 12, 2, 9, 0, tzinfo=timezone.utc)
    ) == datetime(2022, 12, 10, 14, 15, tzinfo=timezone.utc)


def test_a_schedule_asked_for_on_a_boundary_starts_there():
    frbc = frbc_simple()
    frbc.now = lambda: datetime(2022, 12, 10, 14, 15, tzinfo=timezone.utc)
    assert frbc.schedule_start(
        datetime(2022, 12, 2, 9, 0, tzinfo=timezone.utc)
    ) == datetime(2022, 12, 10, 14, 15, tzinfo=timezone.utc)


def test_a_schedule_whose_shifted_start_is_in_the_step_under_way_starts_at_the_next_boundary():
    frbc = frbc_simple()
    frbc._valid_from_shift = timedelta(0)
    frbc.now = lambda: datetime(2022, 12, 10, 14, 7, tzinfo=timezone.utc)
    assert frbc.schedule_start(
        datetime(2022, 12, 10, 14, 6, tzinfo=timezone.utc)
    ) == datetime(2022, 12, 10, 14, 15, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_status_before_its_description_is_planned_for_when_the_description_arrives():
    """The order the example resource manager used to send them in, which left it without a single instruction."""
    frbc = frbc_simple()
    await frbc.handle_message(storage_status())
    await settle(frbc)
    frbc._fm_client.trigger_and_get_schedule.assert_not_awaited()

    await frbc.handle_message(system_description("2022-12-02T00:00:00+00:00"))
    await settle(frbc)

    frbc._fm_client.trigger_and_get_schedule.assert_awaited_once()
    assert not frbc._sending_queue.empty()


@pytest.mark.asyncio
async def test_a_changed_description_is_planned_for_against_the_latest_status():
    """What the device can do changed, so the plan made for what it could do before no longer answers."""
    frbc = frbc_simple()
    await frbc.handle_message(system_description("2022-12-02T00:00:00+00:00"))
    await frbc.handle_message(storage_status())
    await settle(frbc)
    assert frbc._fm_client.trigger_and_get_schedule.await_count == 1

    await frbc.handle_message(system_description("2022-12-02T06:00:00+00:00"))
    await settle(frbc)

    assert frbc._fm_client.trigger_and_get_schedule.await_count == 2


@pytest.mark.asyncio
async def test_the_soc_is_not_reposted_when_a_description_asks_for_a_schedule():
    """The latest status is planned from, not recorded again as if it had been measured now."""
    frbc = frbc_simple()
    await frbc.handle_message(storage_status())
    await settle(frbc)
    posts_after_the_status = frbc._fm_client.post_measurements.await_count

    await frbc.handle_message(system_description("2022-12-02T00:00:00+00:00"))
    await settle(frbc)

    assert frbc._fm_client.post_measurements.await_count == posts_after_the_status


def test_a_step_boundary_in_the_repeated_autumn_hour_is_not_an_hour_early():
    """At 02:07 the second time round, on the night the clocks go back, the step began at 02:00 of that second pass."""
    from zoneinfo import ZoneInfo

    frbc = frbc_simple()
    amsterdam = ZoneInfo("Europe/Amsterdam")
    second_pass = datetime(2026, 10, 25, 1, 7, tzinfo=timezone.utc).astimezone(
        amsterdam
    )
    assert second_pass.fold == 1
    aligned = frbc.align_to_resolution(second_pass)
    assert aligned.astimezone(timezone.utc) == datetime(
        2026, 10, 25, 1, 0, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_a_plan_asked_for_by_a_late_description_supersedes_one_in_flight():
    """A status's plan is still out when a changed description asks for its own: only the newer batch goes out."""
    frbc = frbc_simple()
    release = asyncio.Event()
    planned = frbc._fm_client.trigger_and_get_schedule.return_value

    async def slow_then_quick(**kwargs):
        if frbc._fm_client.trigger_and_get_schedule.await_count == 1:
            await release.wait()
        return planned

    frbc._fm_client.trigger_and_get_schedule.side_effect = slow_then_quick
    batches: list[tuple[int, int]] = []
    send = frbc.send_instruction_batch

    async def recording(*args, **kwargs):
        sent = await send(*args, **kwargs)
        batches.append((kwargs["request_number"], sent))
        return sent

    frbc.send_instruction_batch = recording
    await frbc.handle_message(system_description("2022-12-02T00:00:00+00:00"))
    await frbc.handle_message(storage_status())
    await asyncio.sleep(0.05)
    await frbc.handle_message(system_description("2022-12-02T06:00:00+00:00"))
    await asyncio.sleep(0.05)
    release.set()
    await settle(frbc)
    assert frbc._fm_client.trigger_and_get_schedule.await_count == 2
    sent = dict(batches)
    assert sent[2] > 0  # the description's plan, which is the newer request
    assert sent[1] == 0  # the status's plan, overtaken while it was out


@pytest.mark.asyncio
async def test_a_state_of_charge_is_recorded_as_a_reading_at_an_instant():
    """On a sensor that records instants, and over a minute on one with a resolution, which refuses an instant."""
    for resolution, duration in (
        ("PT0M", timedelta(0)),
        ("PT15M", timedelta(minutes=1)),
    ):
        frbc = frbc_simple()
        frbc._fm_client.get_sensor.return_value = {"event_resolution": resolution}
        await frbc.handle_message(storage_status())
        await settle(frbc)
        posted = frbc._fm_client.post_measurements.await_args.kwargs
        assert posted["duration"] == duration


@pytest.mark.asyncio
async def test_interleaved_statuses_about_one_step_get_belief_times_a_second_apart():
    """A status about step A, one about step B, then A again, all within a second: the two about A must not collide."""
    from s2python.frbc import FRBCActuatorStatus

    from flexmeasures_client.s2.utils import get_unique_id

    frbc = frbc_simple()
    frbc._power_sensor_takes_consumption_as_positive = True
    await frbc.handle_message(system_description("2022-12-02T00:00:00+00:00"))
    actuator = frbc._system_description_history[
        list(frbc._system_description_history)[-1]
    ].actuators[0]
    mode = actuator.operation_modes[0].id
    a = datetime(2022, 12, 2, 0, 5, tzinfo=timezone.utc)
    b = datetime(2022, 12, 2, 0, 20, tzinfo=timezone.utc)
    for when in (a, b, a):
        await frbc.send_actuator_status(
            FRBCActuatorStatus(
                message_id=get_unique_id(),
                actuator_id=actuator.id,
                active_operation_mode_id=mode,
                operation_mode_factor=1.0,
                transition_timestamp=when,
            )
        )
    priors = [
        c.kwargs["prior"]
        for c in frbc._fm_client.post_sensor_data.await_args_list
        if c.kwargs.get("start") == a
    ]
    assert len(priors) == 2
    assert priors[1] - priors[0] >= timedelta(seconds=1)


def test_a_step_boundary_is_on_the_local_clock_where_the_zone_is_half_an_hour_off_utc():
    """FlexMeasures floors a series on the sensor's clock, so an hourly step in Kolkata starts on the hour there."""
    from zoneinfo import ZoneInfo

    frbc = frbc_simple()
    frbc._resolution = timedelta(hours=1)
    moment = datetime(2026, 11, 5, 14, 40, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert frbc.align_to_resolution(moment) == datetime(
        2026, 11, 5, 14, 0, tzinfo=ZoneInfo("Asia/Kolkata")
    )


def test_a_boundary_in_the_repeated_hour_is_the_later_one_not_after_the_moment():
    """At two-hourly steps, 03:10 on the night the clocks go back is past 02:00 of the second pass, which is the boundary meant."""
    from zoneinfo import ZoneInfo

    frbc = frbc_simple()
    frbc._resolution = timedelta(hours=2)
    amsterdam = ZoneInfo("Europe/Amsterdam")
    moment = datetime(2026, 10, 25, 3, 10, tzinfo=amsterdam)
    aligned = frbc.align_to_resolution(moment)
    assert aligned.astimezone(timezone.utc) == datetime(
        2026, 10, 25, 1, 0, tzinfo=timezone.utc
    )


def test_a_naive_moment_is_floored_on_its_clock():
    frbc = frbc_simple()
    assert frbc.align_to_resolution(datetime(2026, 1, 1, 12, 7)) == datetime(
        2026, 1, 1, 12, 0
    )


@pytest.mark.asyncio
async def test_a_state_of_charge_sensor_stating_no_duration_is_posted_over_a_minute(
    caplog,
):
    frbc = frbc_simple()
    frbc._fm_client.get_sensor.return_value = {"event_resolution": "fortnightly"}
    assert await frbc._soc_duration() == timedelta(minutes=1)
    assert "not a duration" in caplog.text


@pytest.mark.asyncio
async def test_a_sensor_that_could_not_be_read_is_asked_again():
    frbc = frbc_simple()
    frbc._fm_client.get_sensor.side_effect = [
        ConnectionError("down"),
        {"event_resolution": "PT0M"},
    ]
    assert await frbc._soc_duration() == timedelta(minutes=1)
    assert await frbc._soc_duration() == timedelta(0)


@pytest.mark.asyncio
async def test_belief_times_are_forgotten_for_steps_more_than_a_day_old():
    from s2python.frbc import FRBCActuatorStatus

    from flexmeasures_client.s2.utils import get_unique_id

    frbc = frbc_simple()
    frbc._power_sensor_takes_consumption_as_positive = True
    await frbc.handle_message(system_description("2022-12-02T00:00:00+00:00"))
    actuator = frbc._system_description_history[
        list(frbc._system_description_history)[-1]
    ].actuators[0]
    for when in (
        datetime(2022, 12, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2022, 12, 2, 6, 0, tzinfo=timezone.utc),
    ):
        await frbc.send_actuator_status(
            FRBCActuatorStatus(
                message_id=get_unique_id(),
                actuator_id=actuator.id,
                active_operation_mode_id=actuator.operation_modes[0].id,
                operation_mode_factor=1.0,
                transition_timestamp=when,
            )
        )
    assert list(frbc._power_recorded_at) == [
        datetime(2022, 12, 2, 6, 0, tzinfo=timezone.utc)
    ]
