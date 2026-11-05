"""A schedule is asked for against what the actuator converts drawn power into stored energy at.

An operation mode states a fill rate and a power range,
and the ratio between the two is what the actuator converts with:
for a heat pump, delivering 3800 W of heat into its buffer while drawing 883.7 W of electricity,
that ratio is a coefficient of performance of 4.3.
Left unsaid, the scheduler plans the storage as if the actuator converted power one for one,
so it plans to reach a fill-level target at a power the actuator cannot deliver it at.
These tests pin what is recorded for the scheduler to read, and when it is not.
"""

from __future__ import annotations

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
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.control_types.FRBC.utils import (
    fill_rate_in_watts,
    operation_mode_conversion_efficiency,
)
from flexmeasures_client.s2.utils import get_unique_id

CHARGING_EFFICIENCY_SENSOR = 7
RANGE_TOP = 80.0
FILL_LEVEL_AT_START = 30.0
VALID_FROM = datetime(2024, 1, 1, tzinfo=timezone.utc)
#: What the example apartment's heater draws, and what it delivers into its buffer while drawing it.
HEATER_POWER = 883.7
HEATER_HEAT_IN_WATTS = 3800.0
#: A fill rate in kWh/s that is HEATER_HEAT_IN_WATTS, since a kilowatt hour per second is 3.6 million watts.
HEATER_FILL_RATE = HEATER_HEAT_IN_WATTS / 3.6e6


def _operation_mode(power: float, fill_rate: float, label: str) -> FRBCOperationMode:
    """One mode running at one power and one fill rate, as an on-off actuator declares them."""
    return FRBCOperationMode(
        id=get_unique_id(),
        diagnostic_label=label,
        elements=[
            FRBCOperationModeElement(
                fill_level_range=NumberRange(start_of_range=0, end_of_range=RANGE_TOP),
                fill_rate=NumberRange(start_of_range=fill_rate, end_of_range=fill_rate),
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


def _system_description(*operation_modes: FRBCOperationMode) -> FRBCSystemDescription:
    actuator = FRBCActuatorDescription(
        id=get_unique_id(),
        supported_commodities=[Commodity.ELECTRICITY],
        operation_modes=list(operation_modes),
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


def _heater() -> FRBCSystemDescription:
    """An on-off heater: idle, or drawing HEATER_POWER and delivering HEATER_HEAT_IN_WATTS of heat."""
    return _system_description(
        _operation_mode(0.0, 0.0, "off"),
        _operation_mode(HEATER_POWER, HEATER_FILL_RATE, "on"),
    )


def _frbc(system_description, **kwargs) -> FRBCSimple:
    """A handler that has had a system description and a storage status, ready to plan."""
    kwargs.setdefault("charging_efficiency_sensor_id", CHARGING_EFFICIENCY_SENSOR)
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
    frbc._fm_client.trigger_and_get_schedule = AsyncMock(
        return_value={
            "values": [0.0] * 48,
            "start": "2024-01-01T00:00:00+00:00",
            "duration": "PT12H",
        }
    )
    frbc.send_instruction_batch = AsyncMock(return_value=0)
    return frbc


async def _asked_for(frbc: FRBCSimple, system_description) -> dict:
    """The flex model one schedule was asked for with."""
    await frbc.trigger_schedule(system_description.message_id)
    return frbc._fm_client.trigger_and_get_schedule.await_args.kwargs["flex_model"]


def _recordings(frbc: FRBCSimple) -> list[dict]:
    """What was recorded on the charging efficiency sensor, as keyword arguments per post."""
    return [
        call.kwargs | dict(zip(["sensor_id"], call.args))
        for call in frbc._fm_client.post_sensor_data.await_args_list
        if (call.args[:1] or (call.kwargs.get("sensor_id"),))[0]
        == CHARGING_EFFICIENCY_SENSOR
    ]


@pytest.mark.asyncio
async def test_the_efficiency_of_an_operation_mode_is_recorded_and_planned_against():
    """The ratio between the heat the mode delivers and the power it draws, as the scheduler reads it."""
    system_description = _heater()
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert flex_model["charging-efficiency"] == {"sensor": CHARGING_EFFICIENCY_SENSOR}
    recorded = _recordings(frbc)
    assert len(recorded) == 1
    assert recorded[0]["values"] == [pytest.approx(HEATER_HEAT_IN_WATTS / HEATER_POWER)]
    assert recorded[0]["unit"] == "dimensionless"


@pytest.mark.asyncio
async def test_the_efficiency_is_recorded_over_the_window_the_schedule_covers():
    """A value outside the window the scheduler reads is a value it plans without."""
    system_description = _heater()
    frbc = _frbc(system_description, schedule_duration=timedelta(hours=6))

    await frbc.trigger_schedule(system_description.message_id)

    recorded = _recordings(frbc)[0]
    triggered = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs
    assert recorded["start"] == triggered["start"]
    assert recorded["duration"] == triggered["duration"] == timedelta(hours=6)


@pytest.mark.asyncio
async def test_no_sensor_means_no_efficiency_is_recorded_or_planned_against():
    """Without a sensor to record it on, the scheduler is not pointed at one holding nothing."""
    system_description = _heater()
    frbc = _frbc(system_description, charging_efficiency_sensor_id=None)

    flex_model = await _asked_for(frbc, system_description)

    assert "charging-efficiency" not in flex_model
    assert _recordings(frbc) == []


@pytest.mark.asyncio
async def test_an_actuator_that_draws_nothing_has_no_efficiency_to_record(caplog):
    """An idle mode converts nothing, and a ratio over its zero power is not a number at all."""
    system_description = _system_description(_operation_mode(0.0, 0.0, "off"))
    frbc = _frbc(system_description)

    with caplog.at_level(logging.INFO):
        flex_model = await _asked_for(frbc, system_description)

    assert "charging-efficiency" not in flex_model
    assert _recordings(frbc) == []
    assert "no operation mode" in caplog.text


@pytest.mark.asyncio
async def test_a_mode_that_only_feeds_back_is_no_charging_efficiency():
    """Discharging is converted the other way round, which FlexMeasures takes separately."""
    system_description = _system_description(
        _operation_mode(0.0, 0.0, "off"),
        _operation_mode(-500.0, -HEATER_FILL_RATE, "discharging"),
    )
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)

    assert "charging-efficiency" not in flex_model
    assert _recordings(frbc) == []


@pytest.mark.asyncio
async def test_modes_that_disagree_are_planned_against_the_lowest_of_them(caplog):
    """FlexMeasures takes one efficiency per device, and converting less than the actuator does reaches a target early rather than late."""
    system_description = _system_description(
        _operation_mode(HEATER_POWER, HEATER_FILL_RATE, "full"),
        # Half the power for a third of the heat: a worse conversion than the mode above.
        _operation_mode(HEATER_POWER / 2, HEATER_FILL_RATE / 3, "partial"),
    )
    frbc = _frbc(system_description)

    with caplog.at_level(logging.WARNING):
        flex_model = await _asked_for(frbc, system_description)

    assert flex_model["charging-efficiency"] == {"sensor": CHARGING_EFFICIENCY_SENSOR}
    full = HEATER_HEAT_IN_WATTS / HEATER_POWER
    assert _recordings(frbc)[0]["values"] == [pytest.approx(full * 2 / 3)]
    assert "different efficiencies" in caplog.text


@pytest.mark.asyncio
async def test_the_same_value_for_the_same_window_is_recorded_once():
    """A belief saying what the one before it says is a row for nothing, and two in a second collide."""
    system_description = _heater()
    frbc = _frbc(system_description)

    flex_model = await _asked_for(frbc, system_description)
    again = await _asked_for(frbc, system_description)

    assert len(_recordings(frbc)) == 1
    # Still planned against, since the sensor holds the value from the first time.
    assert flex_model["charging-efficiency"] == again["charging-efficiency"]


@pytest.mark.asyncio
async def test_a_changed_efficiency_is_recorded_again():
    """A resource manager that re-declares its modes is planned against what it now says."""
    system_description = _heater()
    frbc = _frbc(system_description)
    await frbc.trigger_schedule(system_description.message_id)

    halved = _system_description(
        _operation_mode(0.0, 0.0, "off"),
        _operation_mode(HEATER_POWER, HEATER_FILL_RATE / 2, "on, derated"),
    )
    frbc._system_description_history[halved.message_id] = halved
    await frbc.trigger_schedule(halved.message_id)

    recorded = _recordings(frbc)
    assert len(recorded) == 2
    assert recorded[1]["values"] == [
        pytest.approx(recorded[0]["values"][0] / 2),
    ]


@pytest.mark.asyncio
async def test_a_refused_recording_leaves_the_schedule_to_be_planned_without_it(caplog):
    """A plan that assumes the actuator converts power one for one beats no plan at all, as long as it is said out loud."""
    system_description = _heater()
    frbc = _frbc(system_description)
    frbc._fm_client.post_sensor_data = AsyncMock(
        side_effect=JobFailedError("the ingestion job failed")
    )

    with caplog.at_level(logging.ERROR):
        flex_model = await _asked_for(frbc, system_description)

    assert "charging-efficiency" not in flex_model
    # The schedule itself still went out.
    frbc._fm_client.trigger_and_get_schedule.assert_awaited_once()
    assert "could not record" in caplog.text.lower()


@pytest.mark.asyncio
async def test_a_fill_level_scale_is_applied_to_the_efficiency():
    """The scale converts a fill level, and so a fill rate, into the unit it is recorded in."""
    system_description = _heater()
    frbc = _frbc(system_description, fill_level_scale=10)

    await frbc.trigger_schedule(system_description.message_id)

    assert _recordings(frbc)[0]["values"] == [
        pytest.approx(10 * HEATER_HEAT_IN_WATTS / HEATER_POWER)
    ]


def test_an_energy_unit_that_cannot_be_compared_to_watts_is_refused_at_once():
    """A fill rate in a unit nothing converts would be recorded thousandfold off, so the handler is not made at all."""
    with pytest.raises(ValueError, match="energy_unit has to be one of"):
        FRBCSimple(
            power_sensor_id=1,
            price_sensor_id=2,
            soc_sensor_id=3,
            rm_discharge_sensor_id=4,
            energy_unit="therm",
        )


def test_a_fill_rate_is_stated_in_watts_by_the_unit_the_fill_level_is_in():
    """A kilowatt hour per second is 3.6 million watts; a joule per second is one watt."""
    assert fill_rate_in_watts(1, "J") == 1
    assert fill_rate_in_watts(1, "kWh") == pytest.approx(3.6e6)
    assert fill_rate_in_watts(2.5, "Wh") == pytest.approx(9000)
    with pytest.raises(NotImplementedError):
        fill_rate_in_watts(1, "therm")


def test_an_efficiency_is_taken_at_the_top_of_the_element_that_applies():
    """A mode states an element per stretch of fill level, and the efficiency is that element's, at full tilt.

    The ratio, and not the slope over the element's range:
    a mode's ranges can be degenerate, where the actuator runs at one power and one fill rate only,
    and a slope over such a range is zero while the ratio is the efficiency the mode really runs at.
    """
    mode = _operation_mode(HEATER_POWER, HEATER_FILL_RATE, "on")
    # A second stretch of fill level, converting half as well as the first, nearer the top of the storage.
    mode.elements.append(
        FRBCOperationModeElement(
            fill_level_range=NumberRange(
                start_of_range=RANGE_TOP, end_of_range=2 * RANGE_TOP
            ),
            fill_rate=NumberRange(
                start_of_range=HEATER_FILL_RATE / 2, end_of_range=HEATER_FILL_RATE / 2
            ),
            power_ranges=[
                PowerRange(
                    start_of_range=HEATER_POWER,
                    end_of_range=HEATER_POWER,
                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                )
            ],
        )
    )

    below, above = (
        operation_mode_conversion_efficiency(mode, 1, "kWh", fill_level)
        for fill_level in (RANGE_TOP / 2, 1.5 * RANGE_TOP)
    )

    assert below == pytest.approx(HEATER_HEAT_IN_WATTS / HEATER_POWER)
    assert above == pytest.approx(HEATER_HEAT_IN_WATTS / HEATER_POWER / 2)


@pytest.mark.asyncio
async def test_a_schedule_is_asked_for_against_the_efficiency_where_it_starts():
    """A heat pump that converts less once its buffer is warm is planned with the warm efficiency for a warm start."""
    cold = HEATER_HEAT_IN_WATTS / HEATER_POWER
    warm = cold / 2
    mode = _operation_mode(HEATER_POWER, HEATER_FILL_RATE, "on")
    mode.elements[0].fill_level_range.end_of_range = RANGE_TOP / 2
    mode.elements.append(
        FRBCOperationModeElement(
            fill_level_range=NumberRange(
                start_of_range=RANGE_TOP / 2, end_of_range=RANGE_TOP
            ),
            fill_rate=NumberRange(
                start_of_range=HEATER_FILL_RATE / 2, end_of_range=HEATER_FILL_RATE / 2
            ),
            power_ranges=[
                PowerRange(
                    start_of_range=HEATER_POWER,
                    end_of_range=HEATER_POWER,
                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                )
            ],
        )
    )
    system_description = _system_description(mode)
    frbc = _frbc(system_description)
    # The latest status: a buffer warmed past the middle of its range since the one _frbc recorded.
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=0.75 * RANGE_TOP
    )

    await _asked_for(frbc, system_description)

    assert _recordings(frbc)[0]["values"] == [pytest.approx(warm)]
    assert warm != pytest.approx(cold)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "energy_unit, soc_unit, per_unit",
    [
        ("MJ", "kWh", 1 / 3.6),
        ("Wh", "kWh", 1 / 1000),
        ("J", "kWh", 1 / 3.6e6),
        ("kWh", "kWh", 1.0),
        ("MWh", "MWh", 1.0),
    ],
)
async def test_a_state_of_charge_goes_out_in_a_unit_flexmeasures_takes(
    energy_unit, soc_unit, per_unit
):
    """FlexMeasures takes kWh or MWh only, so any other energy unit is converted at the edge, not refused by the server."""
    system_description = _heater()
    frbc = _frbc(system_description, energy_unit=energy_unit)
    flex_model = await _asked_for(frbc, system_description)
    assert flex_model["soc-unit"] == soc_unit
    assert flex_model["soc-at-start"] == pytest.approx(FILL_LEVEL_AT_START * per_unit)
    assert flex_model["soc-max"] == pytest.approx(1.5 * RANGE_TOP * per_unit)


@pytest.mark.asyncio
async def test_the_bounds_read_back_stay_in_the_recorded_unit():
    """Only what goes to FlexMeasures is converted: the schedule is matched against the operation modes in their own units."""
    from unittest.mock import patch

    system_description = _heater()
    frbc = _frbc(system_description, energy_unit="MJ")
    with patch(
        "flexmeasures_client.s2.control_types.FRBC.frbc_simple.fm_schedule_to_instructions",
        return_value=[],
    ) as translate:
        await frbc.trigger_schedule(system_description.message_id)
    assert translate.call_args.kwargs["fill_level_bounds"] == (
        pytest.approx(0.0),
        pytest.approx(1.5 * RANGE_TOP),
    )


@pytest.mark.asyncio
async def test_the_measured_state_of_charge_is_not_named_in_the_flex_model():
    """The state of charge goes out as a number, never as the sensor it is measured on.

    Named there, FlexMeasures would save the state of charge it plans onto that sensor,
    beside the measurements, where a later schedule would read a plan as if it had been measured.
    """
    system_description = _heater()
    frbc = _frbc(system_description)
    flex_model = await _asked_for(frbc, system_description)
    assert "state-of-charge" not in flex_model
    assert isinstance(flex_model["soc-at-start"], float)


@pytest.mark.asyncio
async def test_feeding_back_is_priced_by_its_own_sensor_where_there_is_one():
    system_description = _heater()
    frbc = _frbc(system_description, production_price_sensor_id=42)
    await frbc.trigger_schedule(system_description.message_id)
    flex_context = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs[
        "flex_context"
    ]
    assert flex_context["production-price"] == {"sensor": 42}
    assert flex_context["consumption-price"] == {"sensor": 2}


@pytest.mark.asyncio
async def test_without_its_own_sensor_feeding_back_is_priced_as_drawing():
    system_description = _heater()
    frbc = _frbc(system_description)
    await frbc.trigger_schedule(system_description.message_id)
    flex_context = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs[
        "flex_context"
    ]
    assert flex_context["production-price"] == {"sensor": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("relax", [False, True])
async def test_the_site_capacity_is_relaxed_only_where_asked(relax):
    """A home's connection is a physical limit; a community member's may be a target it can breach at a price."""
    system_description = _heater()
    frbc = _frbc(system_description, relax_site_capacity=relax)
    await frbc.trigger_schedule(system_description.message_id)
    flex_context = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs[
        "flex_context"
    ]
    assert flex_context.get("relax-site-capacity-constraints", False) is relax


@pytest.mark.asyncio
async def test_a_schedule_overtaken_while_it_was_planned_is_not_translated():
    """A newer storage status arrived during the round trip, so turning this schedule into instructions is wasted."""
    from unittest.mock import patch

    from flexmeasures_client.s2.control_types.FRBC import STORAGE_STATUS_REQUESTS

    system_description = _heater()
    frbc = _frbc(system_description)
    request_number = frbc._count_request(STORAGE_STATUS_REQUESTS)
    planned = frbc._fm_client.trigger_and_get_schedule.return_value

    async def overtaken(**kwargs):
        frbc._count_request(STORAGE_STATUS_REQUESTS)  # a newer status, mid round trip
        return planned

    frbc._fm_client.trigger_and_get_schedule = AsyncMock(side_effect=overtaken)
    with patch(
        "flexmeasures_client.s2.control_types.FRBC.frbc_simple.fm_schedule_to_instructions"
    ) as translate:
        await frbc.trigger_schedule(
            system_description.message_id, request_number=request_number
        )
    translate.assert_not_called()
    frbc.send_instruction_batch.assert_not_awaited()


def test_a_batch_of_instructions_is_logged_once_and_by_name(caplog):
    from flexmeasures_client.s2.control_types.FRBC.utils import (
        fm_schedule_to_instructions,
    )

    system_description = _heater()
    with caplog.at_level(logging.DEBUG):
        fm_schedule_to_instructions(
            {
                "start": "2024-01-01T00:00:00+00:00",
                "duration": "PT1H",
                "values": [0.0, HEATER_POWER, HEATER_POWER, 0.0],
            },
            system_description,
            initial_fill_level=FILL_LEVEL_AT_START,
        )
    batches = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("Instructions: ")
    ]
    assert len(batches) == 1
    assert "'on'" in batches[0] and "'off'" in batches[0]
