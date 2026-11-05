"""Which element of an operation mode, or of a leakage behaviour, answers a question.

S2 lets those elements cover the fill-level range in pieces, and lets them arrive in any order:
s2python's own contiguity check sorts a copy of them and leaves the list as it was sent.
So no answer may depend on the order they are listed in.

Where a fill level is known, the element that applies at it answers,
and a boundary two elements share belongs to the upper one.
Where FlexMeasures takes one number for a whole schedule, the most cautious element answers:
the one converting drawn power into stored energy least, or the one leaking most.
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
    FRBCLeakageBehaviour,
    FRBCLeakageBehaviourElement,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCStorageStatus,
    FRBCSystemDescription,
)

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple
from flexmeasures_client.s2.control_types.FRBC.utils import (
    element_for_fill_level,
    operation_mode_conversion_efficiency,
    power_to_fill_rate_with_metrics,
)
from flexmeasures_client.s2.control_types.translations import (
    leakage_behaviour_to_storage_efficiency,
)
from flexmeasures_client.s2.utils import get_unique_id

POWER_SENSOR = 1
CHARGING_EFFICIENCY_SENSOR = 7
RANGE_TOP = 80.0
KWH_PER_SECOND_IN_WATTS = 3.6e6


def _element(
    lowest: float,
    highest: float,
    power: float,
    heat_in_watts: float,
) -> FRBCOperationModeElement:
    """A stretch of fill level over which a mode runs from nothing up to a power, delivering up to some heat."""
    return FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=lowest, end_of_range=highest),
        fill_rate=NumberRange(
            start_of_range=0, end_of_range=heat_in_watts / KWH_PER_SECOND_IN_WATTS
        ),
        power_ranges=[
            PowerRange(
                start_of_range=0,
                end_of_range=power,
                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
            )
        ],
    )


#: A heat pump that derates as its buffer warms: 2000 W at a COP of 4 below 40, 1200 W at a COP of 3 above.
COLD = _element(0, 40, power=2000.0, heat_in_watts=8000.0)
WARM = _element(40, RANGE_TOP, power=1200.0, heat_in_watts=3600.0)


def _mode(
    *elements: FRBCOperationModeElement, label: str = "heat"
) -> FRBCOperationMode:
    return FRBCOperationMode(
        id=get_unique_id(),
        diagnostic_label=label,
        elements=list(elements),
        abnormal_condition_only=False,
    )


def _range(element) -> tuple[float, float]:
    return (
        element.fill_level_range.start_of_range,
        element.fill_level_range.end_of_range,
    )


# ---------------------------------------------------------------------------
# The selector itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fill_level", [-5.0, 0.0, 20.0, 40.0, 60.0, 80.0, 95.0])
def test_the_element_for_a_fill_level_does_not_depend_on_the_order_listed(fill_level):
    assert _range(element_for_fill_level([COLD, WARM], fill_level)) == _range(
        element_for_fill_level([WARM, COLD], fill_level)
    )


@pytest.mark.parametrize(
    "fill_level, expected",
    [
        (0.0, (0, 40)),
        (39.9, (0, 40)),
        # A boundary two elements share belongs to the upper one,
        (40.0, (40, RANGE_TOP)),
        # except at the very top, where there is no upper one.
        (RANGE_TOP, (40, RANGE_TOP)),
        # Outside the range, the nearest element answers: a storage may be outside its declared range.
        (-5.0, (0, 40)),
        (95.0, (40, RANGE_TOP)),
    ],
)
def test_the_element_that_applies_at_a_fill_level(fill_level, expected):
    for listed in ([COLD, WARM], [WARM, COLD]):
        assert _range(element_for_fill_level(listed, fill_level)) == expected


def test_an_element_that_is_a_single_point_applies_at_that_point():
    """S2 accepts an element whose range is one fill level, as long as the elements stay contiguous.

    At the top of a buffer, a heat pump may run at its minimum only, and say so with such an element.
    It is the upper of the two at their shared boundary, so it applies there.
    """
    below = _element(0, RANGE_TOP, power=2000.0, heat_in_watts=8000.0)
    at_the_top = _element(RANGE_TOP, RANGE_TOP, power=300.0, heat_in_watts=900.0)
    for listed in ([below, at_the_top], [at_the_top, below]):
        assert element_for_fill_level(listed, RANGE_TOP) is at_the_top
        assert element_for_fill_level(listed, RANGE_TOP - 1) is below


def test_past_either_end_the_answer_is_that_at_the_end():
    """A storage outside its declared range is answered as at the nearest end, so the answer does not jump there."""
    at_the_bottom = _element(0, 0, power=0.0, heat_in_watts=0.0)
    middle = _element(0, RANGE_TOP, power=2000.0, heat_in_watts=8000.0)
    at_the_top = _element(RANGE_TOP, RANGE_TOP, power=300.0, heat_in_watts=900.0)
    for listed in (
        [at_the_bottom, middle, at_the_top],
        [at_the_top, middle, at_the_bottom],
    ):
        assert element_for_fill_level(listed, RANGE_TOP + 15) is at_the_top
        assert element_for_fill_level(listed, -5.0) is element_for_fill_level(
            listed, 0.0
        )
        # At the bottom, the point element shares its only fill level with the element above it, which is upper.
        assert element_for_fill_level(listed, 0.0) is middle


def test_a_fill_level_in_a_gap_is_answered_by_the_nearer_element_and_on_a_tie_the_lower():
    """S2 rules gaps out, but the selector takes raw elements and should not depend on their order there either."""
    low = _element(0, 30, power=2000.0, heat_in_watts=8000.0)
    high = _element(50, RANGE_TOP, power=1200.0, heat_in_watts=3600.0)
    for listed in ([low, high], [high, low]):
        assert element_for_fill_level(listed, 35.0) is low
        assert element_for_fill_level(listed, 45.0) is high
        assert element_for_fill_level(listed, 40.0) is low


def test_leakage_elements_are_selected_the_same_way():
    low = FRBCLeakageBehaviourElement(
        fill_level_range=NumberRange(start_of_range=0, end_of_range=40),
        leakage_rate=1e-5,
    )
    high = FRBCLeakageBehaviourElement(
        fill_level_range=NumberRange(start_of_range=40, end_of_range=RANGE_TOP),
        leakage_rate=3e-5,
    )
    assert element_for_fill_level([high, low], 10.0) is low
    assert element_for_fill_level([low, high], 40.0) is high


# ---------------------------------------------------------------------------
# Where a fill level is known: the measured power, and the schedule translation
# ---------------------------------------------------------------------------


def _system_description(*modes: FRBCOperationMode) -> FRBCSystemDescription:
    return FRBCSystemDescription(
        message_id=get_unique_id(),
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        actuators=[
            FRBCActuatorDescription(
                id=get_unique_id(),
                supported_commodities=[Commodity.ELECTRICITY],
                operation_modes=list(modes),
                transitions=[],
                timers=[],
            )
        ],
        storage=FRBCStorageDescription(
            provides_leakage_behaviour=False,
            provides_fill_level_target_profile=False,
            provides_usage_forecast=False,
            fill_level_range=NumberRange(start_of_range=0, end_of_range=RANGE_TOP),
        ),
    )


def _frbc(system_description: FRBCSystemDescription, **kwargs) -> FRBCSimple:
    frbc = FRBCSimple(
        power_sensor_id=POWER_SENSOR,
        price_sensor_id=2,
        soc_sensor_id=3,
        rm_discharge_sensor_id=4,
        **kwargs,
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._logger = logging.getLogger("FRBCSimple")
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    # What the power sensor means by a sign is not the question here.
    frbc._as_the_power_sensor_reads_it = AsyncMock(side_effect=lambda power: power)
    return frbc


async def _measured_power(frbc: FRBCSimple, mode: FRBCOperationMode, factor: float):
    actuator = list(frbc._system_description_history.values())[-1].actuators[0]
    await frbc.send_actuator_status(
        FRBCActuatorStatus(
            message_id=get_unique_id(),
            actuator_id=actuator.id,
            active_operation_mode_id=mode.id,
            operation_mode_factor=factor,
        )
    )
    posts = [
        call
        for call in frbc._fm_client.post_sensor_data.await_args_list
        if (call.args[:1] or (call.kwargs.get("sensor_id"),))[0] == POWER_SENSOR
    ]
    assert len(posts) == 1, posts
    return posts[0].kwargs["values"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("listed", [(COLD, WARM), (WARM, COLD)])
async def test_the_measured_power_is_that_of_the_element_at_the_present_fill_level(
    listed,
):
    """A warm buffer runs on the derated element, so full tilt there is 1200 W, not 2000 W."""
    mode = _mode(*listed)
    frbc = _frbc(_system_description(mode))
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=70.0
    )
    assert await _measured_power(frbc, mode, factor=1.0) == pytest.approx(1200.0)


@pytest.mark.asyncio
async def test_without_a_fill_level_the_measured_power_is_a_guess_said_out_loud(caplog):
    """Before any storage status, the bottom element answers, and the log says it was a guess."""
    mode = _mode(WARM, COLD)
    frbc = _frbc(_system_description(mode))
    with caplog.at_level(logging.WARNING, logger="FRBCSimple"):
        assert await _measured_power(frbc, mode, factor=1.0) == pytest.approx(2000.0)
    assert any(
        "no usable FRBC.StorageStatus" in record.getMessage()
        for record in caplog.records
    ), caplog.records


@pytest.mark.parametrize("fill_level", [20.0, 40.0, 60.0])
def test_a_schedule_is_translated_the_same_whichever_order_the_elements_come_in(
    fill_level,
):
    forward = power_to_fill_rate_with_metrics(_mode(COLD, WARM), 1000.0, fill_level)
    backward = power_to_fill_rate_with_metrics(_mode(WARM, COLD), 1000.0, fill_level)
    assert forward[:4] == pytest.approx(backward[:4])
    assert _range(forward[4]) == _range(backward[4])


# ---------------------------------------------------------------------------
# Where one number covers a whole schedule: the charging and the storage efficiency
# ---------------------------------------------------------------------------


def _efficiency(mode: FRBCOperationMode, fill_level: float) -> float | None:
    return operation_mode_conversion_efficiency(
        mode, fill_level_scale=1, energy_unit="kWh", fill_level=fill_level
    )


@pytest.mark.parametrize("fill_level", [-5.0, 20.0, 40.0, 60.0, 95.0])
def test_the_conversion_efficiency_does_not_depend_on_the_order_listed(fill_level):
    assert _efficiency(_mode(COLD, WARM), fill_level) == _efficiency(
        _mode(WARM, COLD), fill_level
    )


@pytest.mark.parametrize("fill_level, cop", [(20.0, 4.0), (60.0, 3.0)])
def test_a_mode_states_the_efficiency_of_the_element_where_the_schedule_starts(
    fill_level, cop
):
    """A COP of 4 when cold and 3 when warm is 4 for a schedule starting cold, and 3 for one starting warm.

    Not the lowest of the two for both: that overstates what a cold buffer needs,
    so the plan overshoots its upper band and buys more than it uses,
    and against a usage it cannot relax it can find a problem the heat pump can meet infeasible.
    The start is what matters most, since it runs before the next storage status asks for a new plan.
    """
    assert _efficiency(_mode(COLD, WARM), fill_level) == pytest.approx(cop)


def test_an_element_that_tapers_off_applies_only_where_it_applies():
    """Near its highest flow temperature a heat pump may barely fill its buffer, and that does not hold for a cold one."""
    tapering = _element(50, 60, power=1200.0, heat_in_watts=10.0)
    mode = _mode(_element(0, 50, power=1200.0, heat_in_watts=4800.0), tapering)
    assert _efficiency(mode, 30.0) == pytest.approx(4.0)
    assert _efficiency(mode, 55.0) == pytest.approx(10.0 / 1200.0)


@pytest.mark.parametrize(
    "heat_in_watts",
    [0.0, -50.0],
    ids=[
        "standby, drawing power and delivering nothing",
        "circulation, drawing power and losing heat",
    ],
)
def test_a_mode_that_draws_power_without_filling_states_no_efficiency(heat_in_watts):
    """Drawing power while the storage does not fill is not a conversion one ratio can state."""
    standby = FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=0, end_of_range=RANGE_TOP),
        fill_rate=NumberRange(
            start_of_range=heat_in_watts / KWH_PER_SECOND_IN_WATTS,
            end_of_range=heat_in_watts / KWH_PER_SECOND_IN_WATTS,
        ),
        power_ranges=[
            PowerRange(
                start_of_range=5.0,
                end_of_range=5.0,
                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
            )
        ],
    )
    assert _efficiency(_mode(standby), 10.0) is None


@pytest.mark.asyncio
async def test_a_standby_mode_does_not_drag_the_recorded_efficiency_to_zero():
    """Off, standby at 5 W, and on at a COP of 4.3: the scheduler is told 4.3, not 0.

    At 0, the stock could not rise at any power, so the cheapest plan would be never to run the heat pump,
    and with the fill-level band priced rather than hard, that plan would be feasible rather than refused.
    """
    off = _mode(_element(0, RANGE_TOP, power=0.0, heat_in_watts=0.0), label="off")
    standby_element = FRBCOperationModeElement(
        fill_level_range=NumberRange(start_of_range=0, end_of_range=RANGE_TOP),
        fill_rate=NumberRange(start_of_range=0, end_of_range=0),
        power_ranges=[
            PowerRange(
                start_of_range=5.0,
                end_of_range=5.0,
                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
            )
        ],
    )
    standby = _mode(standby_element, label="standby")
    on = _mode(_element(0, RANGE_TOP, power=883.7, heat_in_watts=3800.0), label="on")
    system_description = _system_description(off, standby, on)
    frbc = _frbc(
        system_description, charging_efficiency_sensor_id=CHARGING_EFFICIENCY_SENSOR
    )
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    assert (
        await frbc._record_charging_efficiency(system_description, start, 30.0) is True
    )
    recorded = [
        call.kwargs["values"]
        for call in frbc._fm_client.post_sensor_data.await_args_list
        if (call.args[:1] or (call.kwargs.get("sensor_id"),))[0]
        == CHARGING_EFFICIENCY_SENSOR
    ]
    assert recorded == [[pytest.approx(3800.0 / 883.7)]]


def _leakage(*elements: FRBCLeakageBehaviourElement) -> FRBCLeakageBehaviour:
    return FRBCLeakageBehaviour(
        message_id=get_unique_id(),
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        elements=list(elements),
    )


LEAKS_LITTLE = FRBCLeakageBehaviourElement(
    fill_level_range=NumberRange(start_of_range=0, end_of_range=40), leakage_rate=3e-5
)
LEAKS_MOST = FRBCLeakageBehaviourElement(
    fill_level_range=NumberRange(start_of_range=40, end_of_range=60), leakage_rate=9e-5
)
LEAKS_SOME = FRBCLeakageBehaviourElement(
    fill_level_range=NumberRange(start_of_range=60, end_of_range=RANGE_TOP),
    leakage_rate=6e-5,
)


# The storage efficiencies below are compared exactly, not approximately.
# They are the same arithmetic on the same numbers, so equal means equal,
# and a leakage rate per second becomes an efficiency so close to 100% that pytest.approx's default tolerance
# would call two different leakage rates the same, and pass with the defect these tests are about.


def test_the_storage_efficiency_does_not_depend_on_the_order_listed():
    efficiency = leakage_behaviour_to_storage_efficiency(
        _leakage(LEAKS_LITTLE, LEAKS_MOST, LEAKS_SOME), resolution=timedelta(minutes=15)
    )
    assert efficiency == leakage_behaviour_to_storage_efficiency(
        _leakage(LEAKS_SOME, LEAKS_LITTLE, LEAKS_MOST),
        resolution=timedelta(minutes=15),
    )


def test_the_storage_efficiency_states_the_leakiest_element():
    """The leakiest element is not always the top one, so it is looked for rather than assumed to come last."""
    leakiest = leakage_behaviour_to_storage_efficiency(
        _leakage(LEAKS_MOST), resolution=timedelta(minutes=15), fill_level_scale=1
    )
    # Alone, the leakiest element is measured against its own top; together, against the range's top.
    together = leakage_behaviour_to_storage_efficiency(
        _leakage(LEAKS_LITTLE, LEAKS_MOST, LEAKS_SOME),
        resolution=timedelta(minutes=15),
    )
    top_only = leakage_behaviour_to_storage_efficiency(
        _leakage(
            FRBCLeakageBehaviourElement(
                fill_level_range=NumberRange(start_of_range=0, end_of_range=RANGE_TOP),
                leakage_rate=LEAKS_MOST.leakage_rate,
            )
        ),
        resolution=timedelta(minutes=15),
    )
    assert together == top_only
    assert leakiest != top_only


# ---------------------------------------------------------------------------
# Which fill level: the latest, as the resource manager states it
# ---------------------------------------------------------------------------


def _report_fill_level(frbc: FRBCSimple, fill_level: float) -> None:
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=fill_level
    )


@pytest.mark.asyncio
async def test_the_measured_power_follows_the_latest_storage_status():
    """A buffer that has warmed since its first status runs on the derated element now."""
    mode = _mode(COLD, WARM)
    frbc = _frbc(_system_description(mode))
    _report_fill_level(frbc, 10.0)
    _report_fill_level(frbc, 70.0)
    assert await _measured_power(frbc, mode, factor=1.0) == pytest.approx(1200.0)


@pytest.mark.asyncio
async def test_the_fill_level_is_compared_in_the_resource_managers_own_units():
    """The scale converts fill levels for FlexMeasures; the elements' ranges are still in the resource manager's units.

    At 7 in its own units the buffer is cold, though scaled by 10 it would read as 70, which is warm.
    """
    mode = _mode(COLD, WARM)
    frbc = _frbc(_system_description(mode), fill_level_scale=10)
    _report_fill_level(frbc, 7.0)
    assert await _measured_power(frbc, mode, factor=1.0) == pytest.approx(2000.0)


@pytest.mark.asyncio
async def test_with_one_element_there_is_nothing_to_guess(caplog):
    mode = _mode(_element(0, RANGE_TOP, power=2000.0, heat_in_watts=8000.0))
    frbc = _frbc(_system_description(mode))
    with caplog.at_level(logging.WARNING, logger="FRBCSimple"):
        await _measured_power(frbc, mode, factor=1.0)
    assert not caplog.records, caplog.records


@pytest.mark.asyncio
async def test_a_fill_level_that_is_not_a_number_counts_as_unknown(caplog):
    """s2python lets a NaN fill level through, and no element applies at it, so it is treated as no fill level at all."""
    mode = _mode(WARM, COLD)
    frbc = _frbc(_system_description(mode))
    _report_fill_level(frbc, float("nan"))
    with caplog.at_level(logging.WARNING, logger="FRBCSimple"):
        assert await _measured_power(frbc, mode, factor=1.0) == pytest.approx(2000.0)
    assert any("no usable FRBC.StorageStatus" in r.getMessage() for r in caplog.records)


def test_no_element_applies_at_a_fill_level_that_is_not_a_number():
    with pytest.raises(ValueError, match="not a number"):
        element_for_fill_level([COLD, WARM], float("nan"))


def test_the_storage_efficiency_at_a_fill_level_is_that_elements():
    """Where the fill level is known, the leakage element that applies there answers, not the leakiest."""
    leakage = _leakage(LEAKS_LITTLE, LEAKS_MOST, LEAKS_SOME)
    at_20 = leakage_behaviour_to_storage_efficiency(
        leakage, resolution=timedelta(minutes=15), fill_level=20.0
    )
    only_little = leakage_behaviour_to_storage_efficiency(
        _leakage(
            FRBCLeakageBehaviourElement(
                fill_level_range=NumberRange(start_of_range=0, end_of_range=RANGE_TOP),
                leakage_rate=LEAKS_LITTLE.leakage_rate,
            )
        ),
        resolution=timedelta(minutes=15),
    )
    assert at_20 == only_little
    assert at_20 != leakage_behaviour_to_storage_efficiency(
        leakage, resolution=timedelta(minutes=15)
    )


@pytest.mark.asyncio
async def test_the_handler_records_leakage_at_the_latest_fill_level():
    leakage = _leakage(LEAKS_LITTLE, LEAKS_MOST, LEAKS_SOME)
    frbc = _frbc(_system_description(_mode(COLD, WARM)), leakage_behaviour_sensor_id=9)
    _report_fill_level(frbc, 20.0)
    await frbc.send_leakage_behaviour(leakage)
    posted = [
        call.kwargs["values"]
        for call in frbc._fm_client.post_sensor_data.await_args_list
        if call.kwargs.get("sensor_id") == 9
    ]
    assert posted == [
        [
            leakage_behaviour_to_storage_efficiency(
                leakage, resolution=timedelta(minutes=15), fill_level=20.0
            )
        ]
    ]
