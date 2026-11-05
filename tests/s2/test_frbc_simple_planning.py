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
from datetime import datetime, timedelta
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
        production_price_sensor_id=5,
        soc_minima_sensor_id=6,
        soc_maxima_sensor_id=7,
        usage_forecast_sensor_id=8,
        leakage_behaviour_sensor_id=9,
        charging_efficiency_sensor_id=10,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    # The flex model's power bands take a different shape per server version, so the handler asks.
    frbc._fm_client._resolve_server_version = AsyncMock(return_value="1.1.0")
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

    # This handler posts to other sensors too (the conversion efficiencies a description
    # carries), so only what reached the state-of-charge sensor is counted here.
    soc_posts = [
        call
        for call in frbc._fm_client.post_measurements.await_args_list
        if call.args and call.args[0] == SOC_SENSOR_ID
    ]
    assert len(soc_posts) == 2
    assert [call.kwargs["values"][0] for call in soc_posts] == [0.6, 0.7]
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

    start = frbc.schedule_start(datetime.fromisoformat(valid_from))

    assert start == datetime.fromisoformat(expected_start)
    assert (
        start - start.replace(hour=0, minute=0, second=0, microsecond=0)
    ) % timedelta(minutes=15) == timedelta(0)


@pytest.mark.asyncio
async def test_the_schedule_is_asked_for_from_an_aligned_start():
    frbc = frbc_simple()

    await frbc.handle_message(system_description("2022-12-02T09:07:33.512+00:00"))
    await settle(frbc)
    await frbc.handle_message(storage_status())
    await settle(frbc)

    start = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs["start"]
    assert start == frbc.align_to_resolution(start)
    assert start.microsecond == 0
