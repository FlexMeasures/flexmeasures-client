"""A usage forecast that no plan can absorb is corrected towards zero, instead of making every plan infeasible.

S2 lets a resource manager expect any usage, negative included, but says nothing about what happens when the storage cannot take it.
FlexMeasures plans a forecast usage as forced, so a gain at a full storage, or a draw at an empty one that the actuator cannot keep up with,
left no feasible plan at all, and the storage without an instruction.
The handler now plans a correction: a second device on the storage's stock, which may only move each step's usage towards zero,
priced so that it is used only where nothing physical can keep the plan within the hard bounds.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
from s2python.common import (
    Commodity,
    CommodityQuantity,
    Duration,
    NumberRange,
    PowerRange,
)
from s2python.frbc import (
    FRBCActuatorDescription,
    FRBCOperationMode,
    FRBCOperationModeElement,
    FRBCStorageDescription,
    FRBCStorageStatus,
    FRBCSystemDescription,
    FRBCUsageForecast,
    FRBCUsageForecastElement,
)

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import (
    USAGE_CORRECTION_COMMODITY,
    FRBCSimple,
    _usage_correction_limits,
    _usage_correction_price,
)
from flexmeasures_client.s2.utils import get_unique_id

USAGE_FORECAST_SENSOR_ID = 12
CORRECTION_SENSOR_ID = 20
ASSET_ID = 7
SOC_SENSOR_ID = 3
PRICE_SENSOR_ID = 2
START = pd.Timestamp("2024-01-01T00:00:00+00:00")
STEP = timedelta(minutes=15)


@pytest.fixture
def system_description() -> FRBCSystemDescription:
    """A storage declaring a fill-level range of [20, 80], with one operation mode from 0 to 1000 W."""
    operation_mode = FRBCOperationMode(
        id=get_unique_id(),
        diagnostic_label="charge",
        elements=[
            FRBCOperationModeElement(
                fill_level_range=NumberRange(start_of_range=20, end_of_range=80),
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
        valid_from="2024-01-01T00:00:00+00:00",
        actuators=[
            FRBCActuatorDescription(
                id=get_unique_id(),
                supported_commodities=[Commodity.ELECTRICITY],
                operation_modes=[operation_mode],
                transitions=[],
                timers=[],
            )
        ],
        storage=FRBCStorageDescription(
            provides_leakage_behaviour=True,
            provides_fill_level_target_profile=True,
            provides_usage_forecast=True,
            fill_level_range=NumberRange(start_of_range=20, end_of_range=80),
        ),
    )


def _frbc(system_description, **kwargs) -> FRBCSimple:
    """A handler that has had a system description and a storage status, ready to plan."""
    frbc = FRBCSimple(
        power_sensor_id=1,
        price_sensor_id=PRICE_SENSOR_ID,
        soc_sensor_id=SOC_SENSOR_ID,
        rm_discharge_sensor_id=4,
        **{"usage_forecast_sensor_id": USAGE_FORECAST_SENSOR_ID, **kwargs},
    )
    frbc._fm_client = AsyncMock(FlexMeasuresClient)
    frbc._logger = logging.getLogger(__name__)
    frbc._system_description_history = {
        system_description.message_id: system_description
    }
    frbc._storage_status_history[get_unique_id()] = FRBCStorageStatus(
        message_id=get_unique_id(), present_fill_level=30.0
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


def _forecast(*rates: float) -> FRBCUsageForecast:
    return FRBCUsageForecast(
        message_id=get_unique_id(),
        start_time=START.isoformat(),
        elements=[
            FRBCUsageForecastElement(
                duration=Duration.from_timedelta(STEP), usage_rate_expected=rate
            )
            for rate in rates
        ],
    )


def _correcting(
    system_description,
    *,
    prices=(100.0, -40.0, 450.0),
    soc_resolution="PT0M",
    stored=None,
    **kwargs,
):
    """A handler with a correction sensor, against a server that can plan one, with readable prices."""
    frbc = _frbc(
        system_description, usage_correction_sensor_id=CORRECTION_SENSOR_ID, **kwargs
    )
    frbc._fm_client.server_version_at_least = AsyncMock(return_value=True)

    async def get_sensor(sensor_id, **_):
        if sensor_id == SOC_SENSOR_ID:
            return {"event_resolution": soc_resolution, "unit": "kWh"}
        return {
            "event_resolution": "PT15M",
            "unit": "EUR/kWh",
            "generic_asset_id": ASSET_ID,
        }

    frbc._fm_client.get_sensor = AsyncMock(side_effect=get_sensor)
    frbc._fm_client.get_asset = AsyncMock(
        return_value={"id": ASSET_ID, "flex_context": stored or {}}
    )
    frbc._fm_client.get_sensor_data = AsyncMock(return_value={"values": list(prices)})
    # Pin the window, so that it starts where the forecast does.
    frbc.schedule_start = lambda valid_from: START.to_pydatetime()
    return frbc


async def _request(frbc, system_description):
    await frbc.trigger_schedule(system_description.message_id)
    kwargs = frbc._fm_client.trigger_and_get_schedule.await_args.kwargs
    return kwargs["flex_model"], kwargs["flex_context"]


def test_the_correction_may_only_move_each_step_towards_zero():
    usage = pd.Series(
        [2.0, -1.0, 0.0, -1.0], index=pd.date_range(START, periods=4, freq=STEP)
    )

    up, down = _usage_correction_limits(usage, START, 6, STEP, "kW")

    # Where usage is expected, it may give that much back; where a gain is, it may take that much away; nothing else.
    assert [(s["start"], pd.Timedelta(s["duration"]), s["value"]) for s in up] == [
        (START.isoformat(), STEP, "2.0 kW"),
        ((START + STEP).isoformat(), 5 * STEP, "0.0 kW"),
    ]
    assert [s["value"] for s in down] == [
        "0.0 kW",
        "1.0 kW",
        "0.0 kW",
        "1.0 kW",
        "0.0 kW",
    ]
    assert (
        pd.Timedelta(down[-1]["duration"]) == 2 * STEP
    ), "steps the forecast does not cover allow no correction"


def test_the_correction_price_bounds_every_worth_of_a_unit_of_stock():
    # The probe's case: market prices up to 450, band breaches at ten times that, a day of quarter hours.
    price = _usage_correction_price(
        max_abs_price=450,
        soc_breach_price=4500,
        site_breach_price=0,
        charging_efficiency=1,
        storage_efficiency=1,
        steps=96,
        step_hours=0.25,
    )
    assert price == pytest.approx(2 * (450 + 2 * 4500 * (4 + 96)))
    # A unit of stock is worth more where buying it loses more on the way in, or leaks while it waits,
    # and where exceeding the site's capacity costs something.
    lossy = _usage_correction_price(450, 4500, 0, 0.8, 0.99, 96, 0.25)
    breachable = _usage_correction_price(450, 4500, 45000, 1, 1, 96, 0.25)
    assert lossy - price == pytest.approx(2 * 450 * (1 / (0.8 * 0.99**96) - 1))
    assert breachable - price == pytest.approx(2 * 45000 * 5)


@pytest.mark.asyncio
async def test_a_schedule_is_asked_for_with_a_correction_on_the_storages_stock(
    system_description,
):
    frbc = _correcting(system_description)
    frbc._usage_forecast_history["f"] = _forecast(0.01, -0.02)

    flex_model, flex_context = await _request(frbc, system_description)

    actuator, stock, correction = flex_model
    assert actuator["sensor"] == 1 and actuator["commodity"] == "electricity"
    assert "consumption-capacity" in actuator
    assert "soc-at-start" not in actuator and "soc-usage" not in actuator
    assert (
        stock["state-of-charge"]
        == {"sensor": SOC_SENSOR_ID}
        == actuator["state-of-charge"]
    )
    assert "sensor" not in stock and "soc-minima" in stock
    # The usage planned is the forecast the limits come from, not whatever the sensor holds.
    assert [s["value"] for s in stock["soc-usage"][0]] == [
        "0.01 kWh/s",
        "-0.02 kWh/s",
    ]
    assert correction["sensor"] == CORRECTION_SENSOR_ID
    assert correction["commodity"] == USAGE_CORRECTION_COMMODITY
    assert correction["state-of-charge"] == {"sensor": SOC_SENSOR_ID}
    # Usage of 0.01 kWh/s, then a gain of 0.02 kWh/s: each may only be moved towards zero.
    assert correction["consumption-capacity"][0]["value"] == "0.01 kWh/s"
    assert correction["consumption-capacity"][1]["value"] == "0.0 kWh/s"
    assert correction["production-capacity"][0]["value"] == "0.0 kWh/s"
    assert correction["production-capacity"][1]["value"] == "0.02 kWh/s"


@pytest.mark.asyncio
async def test_the_request_carries_a_coherent_set_of_prices(system_description):
    frbc = _correcting(system_description)
    frbc._usage_forecast_history["f"] = _forecast(0.01)

    _, flex_context = await _request(frbc, system_description)

    electricity, correction = flex_context["commodities"]
    assert electricity["consumption-price"] == {"sensor": PRICE_SENSOR_ID}
    assert "site-power-capacity" in electricity
    # Breach prices relative to the highest absolute market price, 450.
    assert flex_context["soc-minima-breach-price"] == "4500.0 EUR/MWh"
    assert flex_context["soc-maxima-breach-price"] == "4500.0 EUR/MWh"
    # The site's capacity is left to FlexMeasures, which may relax it, so its breach is priced too.
    assert electricity["site-consumption-breach-price"] == "45000.0 EUR/MW"
    steps = 48  # the handler's default twelve hours
    expected = _usage_correction_price(450.0, 4500.0, 45000.0, 1.0, 1.0, steps, 0.25)
    assert correction == {
        "commodity": USAGE_CORRECTION_COMMODITY,
        "consumption-price": f"{expected} EUR/MWh",
        "production-price": f"{-expected} EUR/MWh",
    }


@pytest.mark.asyncio
async def test_a_hard_site_capacity_has_no_breach_price(system_description):
    frbc = _correcting(system_description, relax_site_capacity=False)
    frbc._usage_forecast_history["f"] = _forecast(0.01)

    _, flex_context = await _request(frbc, system_description)

    electricity, correction = flex_context["commodities"]
    assert "site-consumption-breach-price" not in electricity
    assert flex_context["relax-site-capacity-constraints"] is False
    expected = _usage_correction_price(450.0, 4500.0, 0.0, 1.0, 1.0, 48, 0.25)
    assert correction["consumption-price"] == f"{expected} EUR/MWh"


@pytest.mark.asyncio
async def test_a_negative_usage_is_recorded_as_stated_where_it_is_corrected(
    system_description,
):
    frbc = _correcting(system_description)

    await frbc.send_usage_forecast(_forecast(0.01, -0.02))

    posted = frbc._fm_client.post_sensor_data.await_args.kwargs
    assert posted["values"] == pytest.approx([0.01, -0.02])


@pytest.mark.asyncio
async def test_the_schedule_is_read_back_with_the_usage_it_was_planned_with(
    system_description,
):
    frbc = _correcting(system_description)
    frbc._usage_forecast_history["f"] = _forecast(-0.5)

    with patch(
        "flexmeasures_client.s2.control_types.FRBC.frbc_simple.fm_schedule_to_instructions",
        return_value=[],
    ) as translate:
        await frbc.trigger_schedule(system_description.message_id)

    read_back = translate.call_args.kwargs["usage_forecast"]
    assert [e.usage_rate_expected for e in read_back.elements] == [-0.5]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "why, setup, said",
    [
        (
            "no prices",
            dict(prices=()),
            "market prices over the window, which could not be read",
        ),
        (
            "a state-of-charge sensor with a resolution",
            dict(soc_resolution="PT1M"),
            "does not record instants",
        ),
    ],
)
async def test_without_what_a_correction_needs_negative_usage_is_planned_as_none(
    system_description, caplog, why, setup, said
):
    frbc = _correcting(system_description, **setup)
    frbc._usage_forecast_history["f"] = _forecast(0.01, -0.02)

    flex_model, flex_context = await _request(frbc, system_description)

    assert isinstance(flex_model, dict), why
    assert "commodities" not in flex_context
    assert [s["value"] for s in flex_model["soc-usage"][0]] == [
        "0.01 kWh/s",
        "0.0 kWh/s",
    ]
    assert said in caplog.text


@pytest.mark.asyncio
async def test_an_older_server_is_asked_as_before(system_description):
    frbc = _correcting(system_description)
    frbc._fm_client.server_version_at_least = AsyncMock(
        side_effect=lambda version: version != "1.1.0"
    )
    frbc._usage_forecast_history["f"] = _forecast(0.01, -0.02)

    flex_model, _ = await _request(frbc, system_description)

    assert isinstance(flex_model, dict)
    assert flex_model["soc-usage"] == [{"sensor": USAGE_FORECAST_SENSOR_ID}]


@pytest.mark.asyncio
async def test_without_a_correction_sensor_nothing_changes(system_description):
    frbc = _frbc(system_description)
    frbc._fm_client.server_version_at_least = AsyncMock(return_value=True)
    frbc._usage_forecast_history["f"] = _forecast(0.01, -0.02)

    flex_model, flex_context = await _request(frbc, system_description)

    assert isinstance(flex_model, dict)
    assert flex_model["soc-usage"] == [{"sensor": USAGE_FORECAST_SENSOR_ID}]
    assert (
        "commodities" not in flex_context
        and "soc-minima-breach-price" not in flex_context
    )


def test_a_breach_price_factor_of_zero_is_refused(system_description):
    with pytest.raises(ValueError, match="greater than zero"):
        _frbc(system_description, soc_breach_price_factor=0)


@pytest.mark.asyncio
async def test_the_correction_price_counts_the_efficiencies_the_schedule_is_asked_with(
    system_description,
):
    """Buying a unit of stock through a lossy actuator, or holding it while it leaks, makes it worth more."""
    frbc = _correcting(
        system_description,
        charging_efficiency_sensor_id=30,
        leakage_behaviour_sensor_id=31,
    )
    frbc._usage_forecast_history["f"] = _forecast(0.01)
    frbc._record_charging_efficiency = AsyncMock(return_value=True)
    frbc._record_storage_efficiency = AsyncMock(return_value=True)
    frbc._planned_charging_efficiency = 0.8
    frbc._planned_storage_efficiency = 0.99

    _, flex_context = await _request(frbc, system_description)

    expected = _usage_correction_price(450.0, 4500.0, 45000.0, 0.8, 0.99, 48, 0.25)
    assert flex_context["commodities"][1]["consumption-price"] == f"{expected} EUR/MWh"


@pytest.mark.asyncio
async def test_a_market_without_prices_still_prices_a_breach(system_description):
    frbc = _correcting(system_description, prices=(0.0, 0.0))
    frbc._usage_forecast_history["f"] = _forecast(0.01)

    _, flex_context = await _request(frbc, system_description)

    assert flex_context["soc-minima-breach-price"] == "10.0 EUR/MWh"


@pytest.mark.asyncio
async def test_price_sensors_in_different_currencies_are_not_combined(
    system_description, caplog
):
    frbc = _correcting(system_description, production_price_sensor_id=5)

    async def get_sensor(sensor_id, **_):
        if sensor_id == SOC_SENSOR_ID:
            return {"event_resolution": "PT0M", "unit": "kWh"}
        return {
            "event_resolution": "PT15M",
            "unit": "EUR/kWh" if sensor_id == PRICE_SENSOR_ID else "SEK/kWh",
        }

    frbc._fm_client.get_sensor = AsyncMock(side_effect=get_sensor)
    frbc._usage_forecast_history["f"] = _forecast(0.01)

    flex_model, _ = await _request(frbc, system_description)

    assert isinstance(flex_model, dict)
    assert "different currencies, EUR and SEK" in caplog.text


@pytest.mark.asyncio
async def test_the_efficiencies_recorded_for_a_schedule_are_the_ones_it_is_priced_with(
    system_description,
):
    """The correction price uses the very values recorded for the window: the charging efficiency, and the storage efficiency as a share."""
    from s2python.frbc import FRBCLeakageBehaviour, FRBCLeakageBehaviourElement

    frbc = _correcting(
        system_description,
        charging_efficiency_sensor_id=30,
        leakage_behaviour_sensor_id=31,
    )
    leakage = FRBCLeakageBehaviour(
        message_id=get_unique_id(),
        valid_from=START.isoformat(),
        elements=[
            FRBCLeakageBehaviourElement(
                fill_level_range=NumberRange(start_of_range=0, end_of_range=100),
                leakage_rate=0.001,
            )
        ],
    )
    frbc._leakage_behaviour_history[str(leakage.message_id)] = leakage

    assert await frbc._record_charging_efficiency(
        system_description, START.to_pydatetime(), 30.0
    )
    charging_posted = frbc._fm_client.post_sensor_data.await_args.kwargs["values"][0]
    assert await frbc._record_storage_efficiency(START.to_pydatetime(), 30.0)
    storage_posted = frbc._fm_client.post_sensor_data.await_args.kwargs["values"][0]

    assert frbc._planned_charging_efficiency == charging_posted
    assert frbc._planned_storage_efficiency == pytest.approx(storage_posted / 100)
    assert 0 < frbc._planned_storage_efficiency < 1


@pytest.mark.asyncio
async def test_a_forecast_that_is_not_planned_with_is_not_corrected(system_description):
    """Without a usage-forecast sensor, the plan has no usage to correct, even if a forecast arrived."""
    frbc = _correcting(system_description, usage_forecast_sensor_id=None)
    frbc._usage_forecast_history["f"] = _forecast(0.01, -0.02)

    flex_model, flex_context = await _request(frbc, system_description)

    assert isinstance(flex_model, dict) and "soc-usage" not in flex_model
    assert "commodities" not in flex_context


@pytest.mark.asyncio
async def test_a_server_whose_version_cannot_be_read_is_asked_as_before(
    system_description,
):
    frbc = _correcting(system_description)

    async def version_at_least(version):
        if version == "1.1.0":
            raise ConnectionError("unreachable")
        return True

    frbc._fm_client.server_version_at_least = AsyncMock(side_effect=version_at_least)
    frbc._usage_forecast_history["f"] = _forecast(0.01, -0.02)

    flex_model, _ = await _request(frbc, system_description)

    assert isinstance(flex_model, dict)
    assert flex_model["soc-usage"] == [{"sensor": USAGE_FORECAST_SENSOR_ID}]


@pytest.mark.asyncio
async def test_prices_that_cannot_be_read_leave_the_plan_without_a_correction(
    system_description, caplog
):
    frbc = _correcting(system_description)
    frbc._fm_client.get_sensor_data = AsyncMock(
        side_effect=ConnectionError("unreachable")
    )
    frbc._usage_forecast_history["f"] = _forecast(0.01, -0.02)

    flex_model, _ = await _request(frbc, system_description)

    assert isinstance(flex_model, dict)
    assert "Could not read the prices on sensor 2: unreachable" in caplog.text


@pytest.mark.asyncio
async def test_before_any_forecast_arrives_the_plan_goes_without_usage(
    system_description,
):
    """The sensor may hold negative usage recorded as stated before a restart, which no plan without a correction can absorb."""
    frbc = _correcting(system_description)

    flex_model, flex_context = await _request(frbc, system_description)

    assert isinstance(flex_model, dict)
    assert "soc-usage" not in flex_model
    assert "commodities" not in flex_context


@pytest.mark.asyncio
async def test_the_sites_stored_electricity_context_is_kept_and_its_prices_bounded(
    system_description,
):
    """FlexMeasures reads stored electricity fields only where a request names no electricity commodity, so they are carried over."""
    stored = {
        "site-consumption-capacity": "10 kW",
        "site-peak-consumption": "5 kW",
        "site-peak-consumption-price": "260 EUR/MW",
        "commitments": [
            {
                "name": "contract",
                "baseline": "0 kW",
                "up-price": "0.5 EUR/kW",
                "down-price": "0 EUR/kW",
            }
        ],
        "soc-minima-breach-price": "1000 EUR/kWh",
        "consumption-price": {"sensor": 99},
    }
    frbc = _correcting(system_description, stored=stored)
    frbc._usage_forecast_history["f"] = _forecast(0.01)

    _, flex_context = await _request(frbc, system_description)

    electricity, correction = flex_context["commodities"]
    assert electricity["site-consumption-capacity"] == "10 kW"
    assert electricity["site-peak-consumption-price"] == "260 EUR/MW"
    assert electricity["commitments"] == stored["commitments"]
    # What the request sets itself wins, and top-level fields stay at the top.
    assert electricity["consumption-price"] == {"sensor": PRICE_SENSOR_ID}
    assert "soc-minima-breach-price" not in electricity
    assert flex_context["relax-capacity-constraints"] is False
    expected = _usage_correction_price(
        450.0, 4500.0, 45000.0, 1.0, 1.0, 48, 0.25, other_prices_per_mw=260 + 500
    )
    assert correction["consumption-price"] == f"{expected} EUR/MWh"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored, said",
    [
        (
            {"site-peak-consumption-price": {"sensor": 5}},
            "stored site-peak-consumption-price is not a fixed price per MW",
        ),
        (
            {
                "commitments": [
                    {"name": "c", "baseline": "0 kW", "up-price": "1 SEK/kW"}
                ]
            },
            "stored commitment 'c' has a up-price",
        ),
        ({"commodities": []}, "stores a flex context with commodities"),
    ],
)
async def test_a_stored_price_that_cannot_be_bounded_leaves_the_plan_without_a_correction(
    system_description, caplog, stored, said
):
    frbc = _correcting(system_description, stored=stored)
    frbc._usage_forecast_history["f"] = _forecast(0.01, -0.02)

    flex_model, _ = await _request(frbc, system_description)

    assert isinstance(flex_model, dict)
    assert said in caplog.text


def test_efficiencies_above_one_do_not_lower_the_bound():
    """A heat pump's COP, or a storage that gains by itself, makes neither buying nor holding stock cheaper than one for one."""
    one = _usage_correction_price(450, 4500, 0, 1.0, 1.0, 48, 0.25)
    assert _usage_correction_price(450, 4500, 0, 4.0, 1.01, 48, 0.25) == one


def test_other_per_mw_prices_raise_the_bound_by_their_worth_per_mwh_in_one_step():
    """A peak or commitment price per MW costs at most that price over one step's length per MWh."""
    without = _usage_correction_price(450, 4500, 0, 1.0, 1.0, 48, 0.25)
    with_peak = _usage_correction_price(
        450, 4500, 0, 1.0, 1.0, 48, 0.25, other_prices_per_mw=760
    )
    assert with_peak - without == pytest.approx(2 * 760 / 0.25)


@pytest.mark.parametrize(
    "value, per_mw",
    [
        ("260 EUR/MW", 260.0),
        ("0.26 EUR/kW", 260.0),
        ("-0.00026 EUR/W", 260.0),
        ("260 SEK/MW", None),
        ("260 EUR/MWh", None),
        ("lots EUR/MW", None),
        ("260", None),
        ({"sensor": 5}, None),
    ],
)
def test_only_a_fixed_price_per_mw_in_the_currency_can_be_bounded(value, per_mw):
    from flexmeasures_client.s2.control_types.FRBC.frbc_simple import _price_per_mw

    assert _price_per_mw(value, "EUR") == per_mw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "power_sensor, said",
    [
        (ConnectionError("unreachable"), "could not be read: unreachable"),
        ({"unit": "W"}, "could not be read: power sensor 1 states no asset"),
    ],
)
async def test_a_stored_flex_context_that_cannot_be_read_leaves_the_plan_without_a_correction(
    system_description, caplog, power_sensor, said
):
    frbc = _correcting(system_description)
    lookup = frbc._fm_client.get_sensor.side_effect

    async def get_sensor(sensor_id, **kwargs):
        if sensor_id == 1:
            if isinstance(power_sensor, Exception):
                raise power_sensor
            return power_sensor
        return await lookup(sensor_id, **kwargs)

    frbc._fm_client.get_sensor = AsyncMock(side_effect=get_sensor)
    frbc._usage_forecast_history["f"] = _forecast(0.01, -0.02)

    flex_model, _ = await _request(frbc, system_description)

    assert isinstance(flex_model, dict)
    assert said in caplog.text


@pytest.mark.asyncio
async def test_a_commitment_with_one_price_is_bounded_by_that_price(system_description):
    stored = {
        "commitments": [{"name": "c", "baseline": "0 kW", "up-price": "100 EUR/MW"}]
    }
    frbc = _correcting(system_description, stored=stored)
    frbc._usage_forecast_history["f"] = _forecast(0.01)

    _, flex_context = await _request(frbc, system_description)

    expected = _usage_correction_price(
        450.0, 4500.0, 45000.0, 1.0, 1.0, 48, 0.25, other_prices_per_mw=100
    )
    assert flex_context["commodities"][1]["consumption-price"] == f"{expected} EUR/MWh"
