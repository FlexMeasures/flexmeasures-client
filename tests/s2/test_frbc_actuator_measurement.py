"""What the handler records about an actuator means what the sensor holding it means.

Three things decide that, and each of them was wrong in its own way.
S2 fixes one sign convention, positive for consumption,
while a FlexMeasures power sensor carries its own and defaults to the other,
so a measurement and the plan beside it pointed in opposite directions.
A power value was labelled with a unit nothing converted it into.
And two statuses inside one quarter-hour collided on the same belief,
so the later one was refused and, being posted from a task nobody awaits, lost without a word.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from s2python.common import Commodity, CommodityQuantity, NumberRange, PowerRange
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCActuatorStatus,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCSystemDescription,
)

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.exceptions import JobFailedError
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.utils import get_unique_id

from .conftest import power_sensor_recording

POWER_SENSOR_ID = 1
# The power the actuator draws when it runs, in watts, as S2 states it.
ON_W = 883.7


@pytest.fixture
def system_description() -> FRBCSystemDescription:
    """An actuator that is either off or drawing 883.7 W."""
    modes = [
        FRBCOperationMode(
            id=get_unique_id(),
            diagnostic_label=label,
            elements=[
                FRBCOperationModeElement(
                    fill_level_range=NumberRange(start_of_range=0, end_of_range=100),
                    fill_rate=NumberRange(start_of_range=rate, end_of_range=rate),
                    power_ranges=[
                        PowerRange(
                            start_of_range=power,
                            end_of_range=power,
                            commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                        )
                    ],
                )
            ],
            abnormal_condition_only=False,
        )
        for label, power, rate in (("off", 0.0, 0.0), ("on", ON_W, 0.005))
    ]
    actuator = FRBCActuatorDescription(
        id=get_unique_id(),
        supported_commodities=[Commodity.ELECTRICITY],
        operation_modes=modes,
        transitions=[],
        timers=[],
    )
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from="2024-01-01T00:00:00+00:00",
        actuators=[actuator],
        storage=FRBCStorageDescription(
            provides_leakage_behaviour=False,
            provides_fill_level_target_profile=False,
            provides_usage_forecast=False,
            fill_level_range=NumberRange(start_of_range=0, end_of_range=100),
        ),
    )


def _frbc(system_description, *, consumption_as_positive: bool, **kwargs) -> FRBCSimple:
    """A handler whose power sensor records consumption in the given sign."""
    frbc = FRBCSimple(
        power_sensor_id=POWER_SENSOR_ID,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        **kwargs,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._fm_client.get_sensor = AsyncMock(
        return_value=power_sensor_recording(
            consumption_as_positive, sensor_id=POWER_SENSOR_ID
        )
    )
    frbc._logger = logging.getLogger(__name__)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    return frbc


def _status(system_description, mode_index: int) -> FRBCActuatorStatus:
    actuator = system_description.actuators[0]
    return FRBCActuatorStatus(
        message_id=get_unique_id(),
        actuator_id=actuator.id,
        active_operation_mode_id=actuator.operation_modes[mode_index].id,
        operation_mode_factor=1.0,
    )


def _posts_to_the_power_sensor(frbc: FRBCSimple) -> list[dict]:
    return [
        call.kwargs
        for call in frbc._fm_client.post_sensor_data.await_args_list
        if call.kwargs.get("sensor_id", call.args[0] if call.args else None)
        == POWER_SENSOR_ID
    ]


@pytest.mark.asyncio
async def test_a_production_positive_sensor_is_posted_the_other_way_round(
    system_description,
):
    """FlexMeasures stores a plan to consume as negative on such a sensor, so a measurement of consuming is negative too.

    Posted as S2 states it, the measurement and the plan beside it point in opposite directions,
    which reads as a device doing the reverse of what it was asked.
    """
    frbc = _frbc(system_description, consumption_as_positive=False)

    await frbc.send_actuator_status(_status(system_description, 1))

    assert _posts_to_the_power_sensor(frbc)[0]["values"] == [-ON_W]


@pytest.mark.asyncio
async def test_a_consumption_positive_sensor_is_posted_as_s2_states_it(
    system_description,
):
    """A sensor that says it records consumption as positive needs no conversion."""
    frbc = _frbc(system_description, consumption_as_positive=True)

    await frbc.send_actuator_status(_status(system_description, 1))

    assert _posts_to_the_power_sensor(frbc)[0]["values"] == [ON_W]


@pytest.mark.asyncio
async def test_the_sensor_is_asked_once(system_description):
    """A sensor's convention is how it was set up, not something that changes between statuses."""
    frbc = _frbc(system_description, consumption_as_positive=False)

    await frbc.send_actuator_status(_status(system_description, 1))
    await frbc.send_actuator_status(_status(system_description, 0))

    assert frbc._fm_client.get_sensor.await_count == 1


@pytest.mark.asyncio
async def test_a_sensor_that_cannot_be_read_is_posted_as_s2_states_it(
    system_description, caplog
):
    """Better this handler's own convention, said out loud, than no measurement at all."""
    frbc = _frbc(system_description, consumption_as_positive=False)
    frbc._fm_client.get_sensor = AsyncMock(side_effect=ValueError("no such sensor"))

    with caplog.at_level(logging.WARNING):
        await frbc.send_actuator_status(_status(system_description, 1))

    assert _posts_to_the_power_sensor(frbc)[0]["values"] == [ON_W]
    assert "could not read which sign" in caplog.text.lower()


@pytest.mark.asyncio
async def test_a_power_value_is_converted_into_the_unit_it_is_labelled_with(
    system_description,
):
    """S2 states a power in watts, and power_unit says what a posted value is labelled with.

    Nothing converted between the two,
    so a deployment recording its power sensor in kW had 883.7 W posted as 883.7 kW,
    which FlexMeasures stored as the thousandfold it says it is.
    """
    frbc = _frbc(system_description, consumption_as_positive=True, power_unit="kW")

    await frbc.send_actuator_status(_status(system_description, 1))

    posted = _posts_to_the_power_sensor(frbc)[0]
    assert posted["unit"] == "kW"
    assert posted["values"] == [pytest.approx(ON_W / 1000)]


@pytest.mark.asyncio
async def test_watts_are_posted_unconverted(system_description):
    """The default, and the only unit S2 itself states, so there is nothing to convert."""
    frbc = _frbc(system_description, consumption_as_positive=True)

    await frbc.send_actuator_status(_status(system_description, 1))

    posted = _posts_to_the_power_sensor(frbc)[0]
    assert posted["unit"] == "W"
    assert posted["values"] == [ON_W]


@pytest.mark.asyncio
async def test_a_second_status_in_one_second_supersedes_the_first(system_description):
    """FlexMeasures rounds the moment a belief was formed down to the second.

    Two statuses inside one second are then one belief,
    and the later one is refused rather than replacing the earlier,
    so the value that survives is the one the actuator has already left behind.
    """
    frbc = _frbc(system_description, consumption_as_positive=True)
    transition = datetime(2024, 1, 1, 12, 7, 13, tzinfo=timezone.utc)

    # Both statuses are for the same moment, as a resource manager echoing two instructions sends them.
    for mode_index in (1, 0):
        status = _status(system_description, mode_index)
        status.transition_timestamp = transition
        await frbc.send_actuator_status(status)

    posts = _posts_to_the_power_sensor(frbc)
    assert [post["values"][0] for post in posts] == [ON_W, 0.0]
    first, second = (post["prior"] for post in posts)
    assert second - first >= timedelta(seconds=1)


@pytest.mark.asyncio
async def test_a_status_for_another_moment_keeps_its_own_belief_time(
    system_description,
):
    """Only a collision is worth stepping around; everything else is recorded as it happened."""
    frbc = _frbc(system_description, consumption_as_positive=True)

    for minute in (7, 23):
        status = _status(system_description, 1)
        status.transition_timestamp = datetime(
            2024, 1, 1, 12, minute, 0, tzinfo=timezone.utc
        )
        await frbc.send_actuator_status(status)

    first, second = (post["prior"] for post in _posts_to_the_power_sensor(frbc))
    assert second - first < timedelta(seconds=1)


@pytest.mark.asyncio
async def test_a_refused_measurement_is_reported(system_description, caplog):
    """The post runs in a task nobody awaits, so a failure here is otherwise lost."""
    frbc = _frbc(system_description, consumption_as_positive=True)
    frbc._fm_client.post_sensor_data = AsyncMock(
        side_effect=JobFailedError("ingestion job failed")
    )

    with caplog.at_level(logging.ERROR):
        await frbc.send_actuator_status(_status(system_description, 1))

    assert "could not record" in caplog.text.lower()
    assert str(POWER_SENSOR_ID) in caplog.text


@pytest.mark.asyncio
async def test_two_statuses_a_few_seconds_apart_still_supersede(system_description):
    """FlexMeasures floors an event to the sensor's resolution, so seconds apart is the same event.

    A resource manager timestamps each status as it happens,
    so two of them never carry the same moment to the microsecond,
    and comparing the moments themselves would have missed every real collision.
    """
    frbc = _frbc(system_description, consumption_as_positive=True)

    for seconds, mode_index in ((52, 1), (55, 0)):
        status = _status(system_description, mode_index)
        status.transition_timestamp = datetime(
            2024, 1, 1, 12, 7, seconds, tzinfo=timezone.utc
        )
        await frbc.send_actuator_status(status)

    first, second = (post["prior"] for post in _posts_to_the_power_sensor(frbc))
    assert second - first >= timedelta(seconds=1)


@pytest.mark.asyncio
async def test_zero_is_posted_as_zero(system_description):
    """Zero has no direction, so flipping its sign leaves a negative zero and nothing else."""
    frbc = _frbc(system_description, consumption_as_positive=False)

    await frbc.send_actuator_status(_status(system_description, 0))

    assert _posts_to_the_power_sensor(frbc)[0]["values"] == [0.0]
    assert str(_posts_to_the_power_sensor(frbc)[0]["values"][0]) == "0.0"


@pytest.mark.asyncio
async def test_a_sensor_that_will_never_answer_is_asked_once(
    system_description, caplog
):
    """A sensor that cannot be read at all will not read any better on the next status."""
    frbc = _frbc(system_description, consumption_as_positive=False)
    frbc._fm_client.get_sensor = AsyncMock(side_effect=ValueError("no such sensor"))

    with caplog.at_level(logging.WARNING):
        await frbc.send_actuator_status(_status(system_description, 1))
        await frbc.send_actuator_status(_status(system_description, 1))

    assert frbc._fm_client.get_sensor.await_count == 1
    assert caplog.text.lower().count("could not read which sign") == 1


@pytest.mark.asyncio
async def test_a_sensor_that_could_not_be_reached_is_asked_again(system_description):
    """A connection that failed says nothing about the sensor, so the next status asks again."""
    frbc = _frbc(system_description, consumption_as_positive=False)
    frbc._fm_client.get_sensor = AsyncMock(
        side_effect=[
            ConnectionError("no route"),
            power_sensor_recording(False, sensor_id=POWER_SENSOR_ID),
        ]
    )

    await frbc.send_actuator_status(_status(system_description, 1))
    await frbc.send_actuator_status(_status(system_description, 1))

    assert frbc._fm_client.get_sensor.await_count == 2
    # The first went out as S2 states it, the second the way the sensor reads it.
    assert [post["values"][0] for post in _posts_to_the_power_sensor(frbc)] == [
        ON_W,
        -ON_W,
    ]


@pytest.mark.asyncio
async def test_a_unit_nothing_converts_between_is_reported(system_description, caplog):
    """Everything on the way to a recorded measurement is reported, not only the post itself."""
    frbc = _frbc(system_description, consumption_as_positive=True, power_unit="hp")

    with caplog.at_level(logging.ERROR):
        await frbc.send_actuator_status(_status(system_description, 1))

    assert "could not record" in caplog.text.lower()
    frbc._fm_client.post_sensor_data.assert_not_awaited()
    # The value is still in watts, since converting it is what failed,
    # so that is the unit it is reported in: a pair that disagrees is worse than none.
    assert f"{ON_W} W" in caplog.text
    assert f"{ON_W} hp" not in caplog.text


@pytest.mark.asyncio
async def test_an_actuator_status_before_any_description_is_said_and_skipped(
    system_description, caplog
):
    """After a reconnection one can arrive first; indexing the empty history raised an IndexError in a task nobody awaited."""
    frbc = _frbc(system_description, consumption_as_positive=True)
    status = _status(system_description, 0)
    frbc._system_description_history.clear()

    await frbc.send_actuator_status(status)

    assert _posts_to_the_power_sensor(frbc) == []
    assert "no FRBC.SystemDescription has arrived yet" in caplog.text
