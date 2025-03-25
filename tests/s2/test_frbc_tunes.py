from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest
from s2python.common import ControlType, ReceptionStatus, ReceptionStatusValues

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.control_types.FRBC.frbc_tunes import (
    FillRateBasedControlTUNES,
)
from flexmeasures_client.s2.utils import get_unique_id


@pytest.fixture(scope="function")
async def setup_cem(resource_manager_details, rm_handshake):
    fm_client = AsyncMock(FlexMeasuresClient)
    cem = CEM(fm_client=fm_client)
    frbc = FillRateBasedControlTUNES(
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
        timezone="UTC",
        schedule_duration=timedelta(hours=12),
        max_size=100,
        valid_from_shift=timedelta(days=1),
    )

    # disable rate limiting for testing
    frbc.MIN_MEASUREMENT_PERIOD = 0

    cem.register_control_type(frbc)

    #############
    # Handshake #
    #############

    await cem.handle_message(rm_handshake)
    response = await cem.get_message()

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


@pytest.fixture(scope="function")
async def cem_in_frbc_control_type(setup_cem, frbc_system_description):
    cem, fm_client = await setup_cem

    ########
    # FRBC #
    ########

    await cem.handle_message(frbc_system_description)
    await cem.get_message()

    return cem, fm_client, frbc_system_description


@pytest.mark.asyncio
async def test_system_description(cem_in_frbc_control_type, frbc_system_description):
    cem, fm_client, frbc_system_description = await cem_in_frbc_control_type

    ########
    # FRBC #
    ########

    await cem.handle_message(frbc_system_description)
    frbc = cem._control_types_handlers[cem.control_type]

    tasks = get_pending_tasks()

    # check that we are sending the conversion efficiencies
    await tasks["send_conversion_efficiencies"]
    from flexmeasures_client.s2.control_types.FRBC.frbc_tunes import (
        CONVERSION_EFFICIENCY_DURATION,
        RESOLUTION,
    )

    N_SAMPLES = int(
        pd.Timedelta(CONVERSION_EFFICIENCY_DURATION) / pd.Timedelta(RESOLUTION)
    )

    # first call of post_measurements which corresponds to the THP efficiency
    first_call = fm_client.post_measurements.call_args_list[0][1]
    first_call_expected = {
        "sensor_id": frbc._thp_efficiency_sensor_id,
        "start": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "values": [0.2] * N_SAMPLES,
        "unit": "%",
        "duration": "PT24H",
    }
    for key in first_call.keys():
        assert first_call[key] == first_call_expected[key]

    # second call of post_measurements which corresponds to the NES efficiency
    second_call = fm_client.post_measurements.call_args_list[1][1]

    second_call_expected = {
        "sensor_id": frbc._nes_efficiency_sensor_id,
        "start": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "values": [0.1] * N_SAMPLES,
        "unit": "%",
        "duration": "PT24H",
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
    cem, fm_client, frbc_system_description = await cem_in_frbc_control_type

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

    first_call = fm_client.post_measurements.call_args_list[0][1]
    assert first_call["sensor_id"] == 2
    assert first_call["start"] == start
    assert np.isclose(first_call["values"].values, [0] * 4 + [10] * 8 + [20] * 12).all()

    second_call = fm_client.post_measurements.call_args_list[1][1]
    assert second_call["sensor_id"] == 3
    assert second_call["start"] == start
    assert np.isclose(
        second_call["values"].values, [100] * 4 + [90] * 8 + [80] * 12
    ).all()

    await cem.close()
    get_pending_tasks()


@pytest.mark.asyncio
async def test_fill_rate_relay(cem_in_frbc_control_type):
    """Check whether the fill rate from the Tarnoc or Nestor is relayed
    to the overall heating system's fill rate sensor, and the fill rate sensor ID
    corresponds correctly to the Tarnoc fill rate sensor or the Nestor fill rate sensor.
    """

    cem, fm_client, frbc_system_description = await cem_in_frbc_control_type
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

    first_call = fm_client.post_measurements.call_args_list[0][1]
    assert first_call["sensor_id"] == frbc._thp_fill_rate_sensor_id

    second_call = fm_client.post_measurements.call_args_list[1][1]
    assert second_call["sensor_id"] == frbc._fill_rate_sensor_id

    third_call = fm_client.post_measurements.call_args_list[2][1]
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

    first_call = fm_client.post_measurements.call_args_list[0][1]
    assert first_call["sensor_id"] == frbc._nes_fill_rate_sensor_id

    second_call = fm_client.post_measurements.call_args_list[1][1]
    assert second_call["sensor_id"] == frbc._fill_rate_sensor_id

    third_call = fm_client.post_measurements.call_args_list[2][1]
    assert third_call["sensor_id"] == frbc._active_actuator_id_sensor_id
    assert third_call["values"][0] == frbc._nes_fill_rate_sensor_id

    await cem.close()


@pytest.mark.asyncio
async def test_trigger_schedule(cem_in_frbc_control_type):
    """Work in progress.

    # todo: add steps

    Steps

    1.  Check whether the task starts and stops
    2.  Check call arguments
    3.  Check queue for results mocking the results of FM client

    # todo consider splitting up test
    S2 2 FM: converging system description to flex config
    FM 2 S2: schedules to instructions
    """
    cem, fm_client, frbc_system_description = await cem_in_frbc_control_type
    # frbc = cem._control_types_handlers[cem.control_type]

    tasks = get_pending_tasks()

    assert tasks["trigger_schedule_task"]._state == "PENDING"
    await cem.close()

    tasks = get_pending_tasks()

    assert tasks["trigger_schedule_task"]._state == "PENDING"

    await cem.close()
    get_pending_tasks()
