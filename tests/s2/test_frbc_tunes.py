from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from s2python.common import ControlType, ReceptionStatus, ReceptionStatusValues

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.control_types.FRBC.frbc_tunes import (
    FillRateBasedControlTUNES,
)
from flexmeasures_client.s2.utils import get_unique_id


@pytest_asyncio.fixture(scope="function")
async def setup_cem(resource_manager_details, rm_handshake):
    fm_client = AsyncMock(FlexMeasuresClient)

    # Mock trigger_and_get_schedule response
    fm_client.trigger_and_get_schedule.return_value = {
        "start": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
        "duration": "PT1H",
        "values": [0.0, 0.5, 1.5, 0.0] + [0] * 92,
    }

    fm_client.get_sensor_data = AsyncMock(
        side_effect=lambda sensor_id, *args, **kwargs: {
            "start": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
            "duration": "PT24H",
            "values": (
                [0.002] * 96
                if sensor_id == 9  # THP efficiency
                else (
                    [0.001] * 96 if sensor_id == 11 else [0.0] * 96  # NES efficiency
                )  # default for others
            ),
        }
    )

    cem = CEM(fm_client=fm_client)

    frbc = FillRateBasedControlTUNES(
        asset_id=1,
        soc_minima_sensor_id=2,
        soc_maxima_sensor_id=3,
        rm_discharge_sensor_id=4,
        fill_level_sensor_id=7,
        thp_fill_rate_sensor_id=8,
        thp_efficiency_sensor_id=9,
        nes_fill_rate_sensor_id=10,
        nes_efficiency_sensor_id=11,
        usage_forecast_sensor_id=12,
        fill_rate_sensor_id=13,
        active_actuator_id_sensor_id=14,
        leakage_behaviour_sensor_id=15,
        production_price_sensor=16,
        consumption_price_sensor=17,
        state_of_charge_sensor_id=18,
        timezone="UTC",
        schedule_duration=timedelta(hours=12),
        max_size=100,
        valid_from_shift=timedelta(days=1),
        fill_level_scale=1,
    )

    # disable rate limiting for testing
    frbc._is_timer_due = lambda x: True

    cem.register_control_type(frbc)

    #############
    # Handshake #
    #############

    await cem.handle_message(rm_handshake)
    response = await cem.get_message()  # ReceptionStatus for Handshake
    response = await cem.get_message()  # HandshakeResponse

    ##########################
    # ResourceManagerDetails #
    ##########################
    await cem.handle_message(resource_manager_details)
    response = await cem.get_message()

    #########################
    # Activate control type #
    #########################

    await cem.activate_control_type(ControlType.FILL_RATE_BASED_CONTROL)
    message = await cem.get_message()

    response = ReceptionStatus(
        subject_message_id=message.get("message_id"), status=ReceptionStatusValues.OK
    )

    await cem.handle_message(response)

    return cem, fm_client


@pytest_asyncio.fixture(scope="function")
async def cem_in_frbc_control_type(setup_cem, frbc_system_description):
    cem, fm_client = setup_cem

    ########
    # FRBC #
    ########

    await cem.handle_message(frbc_system_description)
    await cem.get_message()

    return cem, fm_client, frbc_system_description


@pytest.mark.asyncio
async def test_system_description(cem_in_frbc_control_type, frbc_system_description):
    cem, fm_client, frbc_system_description = cem_in_frbc_control_type

    ########
    # FRBC #
    ########

    await cem.handle_message(frbc_system_description)
    frbc = cem._control_types_handlers[cem.control_type]

    # first call of post_sensor_data which corresponds to the THP efficiency
    first_call = fm_client.post_sensor_data.call_args_list[0][1]
    first_call_expected = {
        "sensor_id": frbc._thp_efficiency_sensor_id,
        "start": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "values": [7.2],
        "unit": "dimensionless",
        "duration": "PT99H",
    }
    for key in first_call.keys():
        assert first_call[key] == first_call_expected[key]

    # second call of post_sensor_data which corresponds to the NES efficiency
    second_call = fm_client.post_sensor_data.call_args_list[1][1]

    second_call_expected = {
        "sensor_id": frbc._nes_efficiency_sensor_id,
        "start": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "values": [3.6],
        "unit": "dimensionless",
        "duration": "PT99H",
    }
    for key in second_call.keys():
        assert second_call[key] == second_call_expected[key]

    await cem.close()
    get_pending_tasks()


def get_pending_tasks():
    pending = asyncio.all_tasks()

    tasks = {}

    # get all pending tasks
    for task in pending:
        func_name = task.get_coro().cr_code.co_name
        tasks[func_name] = task

    return tasks


@pytest.mark.asyncio
async def test_fill_level_target_profile(cem_in_frbc_control_type):
    cem, fm_client, frbc_system_description = cem_in_frbc_control_type

    fill_level_target_profile = {
        "start_time": "2024-01-01T00:00:00+01:00",
        "message_type": "FRBC.FillLevelTargetProfile",
        "message_id": get_unique_id(),
        "elements": [
            {
                "duration": 1e3 * 3600,
                "fill_level_range": {"start_of_range": 0, "end_of_range": 100},
            },
            {
                "duration": 1e3 * 2 * 3600,
                "fill_level_range": {"start_of_range": 10, "end_of_range": 90},
            },
            {
                "duration": 1e3 * 3 * 3600,
                "fill_level_range": {"start_of_range": 20, "end_of_range": 80},
            },
        ],
    }

    await cem.handle_message(fill_level_target_profile)

    tasks = get_pending_tasks()

    # clear mock state because it contains previous such as
    # the ones used to process the system description
    fm_client.reset_mock()

    # wait for the task send_fill_level_target_profile to finish
    await tasks["send_fill_level_target_profile"]

    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone(timedelta(seconds=3600)))

    first_call = fm_client.post_sensor_data.call_args_list[0][1]
    assert first_call["sensor_id"] == 2
    assert first_call["start"] == start

    assert np.isclose(first_call["values"], [0] * 4 + [10] * 8 + [20] * 12).all()

    second_call = fm_client.post_sensor_data.call_args_list[1][1]
    assert second_call["sensor_id"] == 3
    assert second_call["start"] == start
    assert np.isclose(second_call["values"], [100] * 4 + [90] * 8 + [80] * 12).all()

    await cem.close()
    get_pending_tasks()


@pytest.mark.asyncio
async def test_fill_rate_relay(cem_in_frbc_control_type):
    """Check whether the fill rate from the Tarnoc or Nestor is relayed
    to the overall heating system's fill rate sensor, and the fill rate sensor ID
    corresponds correctly to the Tarnoc fill rate sensor or the Nestor fill rate sensor.
    """
    cem, fm_client, frbc_system_description = cem_in_frbc_control_type
    frbc = cem._control_types_handlers[cem.control_type]

    actuator_status = {
        "active_operation_mode_id": frbc_system_description.actuators[0]
        .operation_modes[0]
        .id,  # ID representing Tarnoc operation mode
        "actuator_id": frbc_system_description.actuators[0].id,  # ID of the actuator
        "message_type": "FRBC.ActuatorStatus",
        "message_id": get_unique_id(),
        "operation_mode_factor": 0.0,
    }

    await cem.handle_message(actuator_status)

    tasks = get_pending_tasks()

    # clear mock state because it contains previous such as
    # the ones used to process the system description
    fm_client.reset_mock()

    # wait for the task send_actuator_status to finish
    await tasks["send_actuator_status"]

    first_call = fm_client.post_sensor_data.call_args_list[0][1]
    assert first_call["sensor_id"] == frbc._thp_fill_rate_sensor_id

    second_call = fm_client.post_sensor_data.call_args_list[1][1]
    assert second_call["sensor_id"] == frbc._fill_rate_sensor_id

    third_call = fm_client.post_sensor_data.call_args_list[2][1]
    assert third_call["sensor_id"] == frbc._active_actuator_id_sensor_id
    assert third_call["values"][0] == frbc._thp_fill_rate_sensor_id

    # Switch operation mode to Nestore
    actuator_status["active_operation_mode_id"] = (
        frbc_system_description.actuators[0].operation_modes[1].id
    )  # ID representing NEStore operation mode

    await cem.handle_message(actuator_status)
    tasks = get_pending_tasks()

    # clear mock state because it contains previous such as
    # the ones used to process the system description
    fm_client.reset_mock()

    # wait for the task send_actuator_status to finish
    await tasks["send_actuator_status"]

    first_call = fm_client.post_sensor_data.call_args_list[0][1]
    assert first_call["sensor_id"] == frbc._nes_fill_rate_sensor_id

    second_call = fm_client.post_sensor_data.call_args_list[1][1]
    assert second_call["sensor_id"] == frbc._fill_rate_sensor_id

    third_call = fm_client.post_sensor_data.call_args_list[2][1]
    assert third_call["sensor_id"] == frbc._active_actuator_id_sensor_id
    assert third_call["values"][0] == frbc._nes_fill_rate_sensor_id

    await cem.close()


@pytest.mark.asyncio
async def test_a_leakage_behaviour_is_recorded_rather_than_refused(
    cem_in_frbc_control_type,
):
    """A leakage behaviour is posted as a storage efficiency, and not answered as a permanent error.

    The handler read its fill-level scale from an attribute whose name was pasted in twice,
    and the AttributeError that raised was caught as if the post had failed,
    so every leakage behaviour was refused and none was recorded.
    """
    from s2python.common import NumberRange
    from s2python.frbc import FRBCLeakageBehaviour, FRBCLeakageBehaviourElement

    cem, fm_client, _ = cem_in_frbc_control_type
    frbc = cem._control_types_handlers[cem.control_type]
    frbc.send_message = AsyncMock()

    leakage = FRBCLeakageBehaviour(
        message_id=get_unique_id(),
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        elements=[
            FRBCLeakageBehaviourElement(
                fill_level_range=NumberRange(start_of_range=0, end_of_range=100),
                leakage_rate=0.001,
            )
        ],
    )
    await frbc.send_leakage_behaviour(leakage)

    sent = [call.args[0] for call in frbc.send_message.await_args_list]
    assert not any(
        getattr(message, "status", None) == ReceptionStatusValues.PERMANENT_ERROR
        for message in sent
    ), sent
    posts = [
        call
        for call in fm_client.post_sensor_data.await_args_list
        if (call.args[0] if call.args else call.kwargs.get("sensor_id")) == 15
    ]
    assert len(posts) == 1, fm_client.post_sensor_data.await_args_list
    # What the translation makes of this message on a quarter-hour grid, which is what TUNES says it posts.
    # The fill-level scale is not exercised here: it multiplies both the leakage and the range it is a share of, and cancels.
    from flexmeasures_client.s2.control_types.translations import (
        leakage_behaviour_to_storage_efficiency,
    )

    assert posts[0].kwargs["values"] == [
        leakage_behaviour_to_storage_efficiency(
            message=leakage, resolution=timedelta(minutes=15), fill_level_scale=1
        )
    ]
    assert posts[0].kwargs["unit"] == "%"


@pytest.mark.asyncio
async def test_an_operation_mode_without_a_label_does_not_stop_tunes(
    cem_in_frbc_control_type,
):
    """S2 makes a diagnostic label optional, and TUNES tells its modes apart by label.

    It looked for "THP" and "NES" inside a label that may be None, which raised a TypeError,
    so one unlabelled mode stopped the conversion efficiencies of every mode from being recorded.
    """
    cem, fm_client, system_description = cem_in_frbc_control_type
    frbc = cem._control_types_handlers[cem.control_type]
    unlabelled = system_description.model_copy(deep=True)
    unlabelled.actuators[0].operation_modes[-1].diagnostic_label = None
    fm_client.post_sensor_data.reset_mock()

    await frbc.send_conversion_efficiencies(unlabelled)

    assert fm_client.post_sensor_data.await_count > 0


@pytest.mark.asyncio
async def test_planning_does_not_stop_on_an_unlabelled_operation_mode(
    cem_in_frbc_control_type,
):
    """The same optional label, where TUNES picks the mode to plan with."""
    from s2python.frbc import FRBCStorageStatus

    cem, fm_client, system_description = cem_in_frbc_control_type
    frbc = cem._control_types_handlers[cem.control_type]
    unlabelled = system_description.model_copy(deep=True)
    for operation_mode in unlabelled.actuators[0].operation_modes:
        operation_mode.diagnostic_label = None
    frbc._system_description_history[str(unlabelled.message_id)] = unlabelled
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=10.0
    )

    await frbc.trigger_schedule()


@pytest.mark.asyncio
async def test_a_usage_forecast_is_posted_per_quarter_hour(cem_in_frbc_control_type):
    """S2 states usage per second; TUNES posts it per quarter hour, on a quarter-hour grid, for as long as it was forecast."""
    from s2python.common import Duration
    from s2python.frbc import FRBCUsageForecast, FRBCUsageForecastElement

    cem, fm_client, _ = cem_in_frbc_control_type
    frbc = cem._control_types_handlers[cem.control_type]
    fm_client.post_sensor_data.reset_mock()

    await frbc.send_usage_forecast(
        FRBCUsageForecast(
            message_id=get_unique_id(),
            start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            elements=[
                FRBCUsageForecastElement(
                    duration=Duration(root=2 * 3600 * 1000),
                    usage_rate_expected=0.001,
                )
            ],
        )
    )

    posts = [
        call
        for call in fm_client.post_sensor_data.await_args_list
        if call.kwargs.get("sensor_id") == 12
    ]
    assert len(posts) == 1, fm_client.post_sensor_data.await_args_list
    values = posts[0].kwargs["values"]
    assert values == [pytest.approx(0.001 * 15 * 60)] * len(values)
    assert posts[0].kwargs["duration"] == str(pd.Timedelta(minutes=15) * len(values))


@pytest.mark.asyncio
async def test_a_usage_forecast_is_posted_from_the_quarter_hour_it_starts_in(
    cem_in_frbc_control_type,
):
    from s2python.common import Duration
    from s2python.frbc import FRBCUsageForecast, FRBCUsageForecastElement

    cem, fm_client, _ = cem_in_frbc_control_type
    frbc = cem._control_types_handlers[cem.control_type]
    fm_client.post_sensor_data.reset_mock()

    await frbc.send_usage_forecast(
        FRBCUsageForecast(
            message_id=get_unique_id(),
            start_time=datetime(2024, 1, 1, 0, 7, tzinfo=timezone.utc),
            elements=[
                FRBCUsageForecastElement(
                    duration=Duration(root=3600 * 1000), usage_rate_expected=0.001
                )
            ],
        )
    )

    posts = [
        c
        for c in fm_client.post_sensor_data.await_args_list
        if c.kwargs.get("sensor_id") == 12
    ]
    assert posts[0].kwargs["start"] == datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_usage_forecast_is_averaged_within_each_quarter_hour(
    cem_in_frbc_control_type,
):
    """Two halves of a quarter hour, at 1 and 3 thousandths per second, are posted as 2 thousandths, per quarter hour."""
    from s2python.common import Duration
    from s2python.frbc import FRBCUsageForecast, FRBCUsageForecastElement

    cem, fm_client, _ = cem_in_frbc_control_type
    frbc = cem._control_types_handlers[cem.control_type]
    fm_client.post_sensor_data.reset_mock()
    half = Duration(root=int(7.5 * 60 * 1000))

    await frbc.send_usage_forecast(
        FRBCUsageForecast(
            message_id=get_unique_id(),
            start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            elements=[
                FRBCUsageForecastElement(duration=half, usage_rate_expected=0.001),
                FRBCUsageForecastElement(duration=half, usage_rate_expected=0.003),
            ],
        )
    )

    posts = [
        c
        for c in fm_client.post_sensor_data.await_args_list
        if c.kwargs.get("sensor_id") == 12
    ]
    assert posts[0].kwargs["values"][0] == pytest.approx(0.002 * 15 * 60)


@pytest.mark.asyncio
async def test_planning_reads_its_usage_forecast_back_per_second(
    cem_in_frbc_control_type,
):
    """TUNES posts a usage forecast per quarter hour; the schedule translation takes a rate per second, as S2 states it."""
    from unittest.mock import patch

    from s2python.common import NumberRange
    from s2python.frbc import (
        FRBCLeakageBehaviour,
        FRBCLeakageBehaviourElement,
        FRBCStorageStatus,
    )

    cem, fm_client, _ = cem_in_frbc_control_type
    frbc = cem._control_types_handlers[cem.control_type]
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=10.0
    )

    def sensor_data(sensor_id, start=None, duration=None, **kwargs):
        # As many quarter hours as were asked for: the fixture's fixed day of values fits no other window.
        steps = int(pd.Timedelta(duration) / pd.Timedelta(minutes=15))
        # The usage forecast as posted, 900 per quarter hour, and anything else as a plain efficiency.
        value = 900.0 if sensor_id == 12 else 1.0
        return {
            "start": str(start),
            "duration": str(duration),
            "values": [value] * steps,
        }

    fm_client.get_sensor_data.side_effect = sensor_data
    # Not 1, which would hide a missing division: TUNES posts usage scaled into FlexMeasures' units.
    frbc._fill_level_scale = 0.1
    leakage = FRBCLeakageBehaviour(
        message_id=get_unique_id(),
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        elements=[
            FRBCLeakageBehaviourElement(
                fill_level_range=NumberRange(start_of_range=0, end_of_range=100),
                leakage_rate=0.001,
            )
        ],
    )
    frbc._leakage_behaviour_history[str(leakage.message_id)] = leakage
    # TUNES asks for a schedule by trigger and get, rather than trigger_and_get_schedule, over six hours.
    fm_client.get_schedule.return_value = {"values": [0.0] * 24}
    with patch(
        "flexmeasures_client.s2.control_types.FRBC.frbc_tunes.fm_schedule_to_instructions",
        return_value=[],
    ) as translate:
        await frbc.trigger_schedule()

    frame = translate.call_args.args[0]
    usage = list(frame["usage_forecast"].dropna())
    # 900 per quarter hour, scaled by 0.1 on the way out: 10 per second in the resource manager's own units.
    assert usage and usage == [pytest.approx(10.0)] * len(usage)
    assert translate.call_args.kwargs["leakage_behaviour"] is leakage
