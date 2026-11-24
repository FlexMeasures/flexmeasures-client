"""Tests for parsing recourse rules and evaluating them, without a server."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from flexmeasures_client.recourse import (
    InvalidResponseError,
    ResponseRule,
    ResponseSchedule,
    clip_to_band,
    convert_response_units,
    parse_response,
    residuals,
    respond,
)

MODES_BAND = [(-0.0074, -0.0014), (0.0, 0.0), (0.0014, 0.0074)]


def make_schedule(values, response=None, **overrides) -> dict:
    schedule = {
        "values": values,
        "start": "2026-11-30T00:00:00+01:00",
        "duration": "PT1H",
        "unit": "MW",
    }
    if response is not None:
        schedule["response"] = response
    return schedule | overrides


def test_parse_response_absent_is_none():
    assert parse_response(make_schedule([1.0, 2.0])) is None


def test_parse_response_one_rule_per_value():
    rules = parse_response(
        make_schedule(
            [1.0, 2.0],
            [
                {"reference": 0.4, "react": 1, "band": [[None, 7.4]]},
                {
                    "reference": 0,
                    "react": 0,
                    "band": [[-7.4, -1.4], [0, 0], [1.4, 7.4]],
                },
            ],
        )
    )
    assert rules == [
        ResponseRule(reference=0.4, react=1.0, band=[(None, 7.4)]),
        ResponseRule(
            reference=0.0, react=0.0, band=[(-7.4, -1.4), (0.0, 0.0), (1.4, 7.4)]
        ),
    ]
    assert all(isinstance(rule.reference, float) for rule in rules)


def test_parse_response_length_mismatch_rejected():
    schedule = make_schedule(
        [1.0, 2.0], [{"reference": 0, "react": 0, "band": [[None, None]]}]
    )
    with pytest.raises(InvalidResponseError, match="1 entries for 2 values"):
        parse_response(schedule)
    with pytest.raises(ValueError):
        ResponseSchedule.from_schedule(schedule)


@pytest.mark.parametrize(
    ("response", "match"),
    [
        ("not a list", "to be a list"),
        ([42], "should be an object"),
        ([{"reference": 0, "react": 0}], "misses band"),
        ([{"reference": "0", "react": 0, "band": []}], "reference"),
        ([{"reference": 0, "react": True, "band": []}], "react"),
        ([{"reference": 0, "react": 0, "band": [[1, 2, 3]]}], "pair"),
        ([{"reference": 0, "react": 0, "band": [[2, 1]]}], "low .* above its high"),
        ([{"reference": 0, "react": 0, "band": [["1", 2]]}], "low"),
    ],
)
def test_parse_response_rejects_malformed_entries(response, match):
    with pytest.raises(InvalidResponseError, match=match):
        parse_response(make_schedule([1.0], response))


def test_response_schedule_without_response_follows_the_plan():
    schedule = ResponseSchedule.from_schedule(
        make_schedule([1.0, 2.0, 3.0, 4.0], duration="PT1H", sensor=8)
    )
    assert schedule.rules is None
    assert not schedule.has_rules
    assert schedule.sensor == 8
    assert schedule.resolution == timedelta(minutes=15)
    assert schedule.start == datetime(2026, 11, 29, 23, tzinfo=timezone.utc)
    assert len(schedule) == 4
    assert [rule for _, _, rule in schedule] == [None] * 4
    at = datetime(2026, 11, 30, 0, 20, tzinfo=timezone(timedelta(hours=1)))
    assert schedule.slot(at) == 1
    assert schedule.respond_at(at, residual=5.0) == 2.0


def test_response_schedule_with_rules():
    schedule = ResponseSchedule.from_schedule(
        make_schedule(
            [1.0, 2.0],
            [
                {"reference": 0.4, "react": 1, "band": [[0, 3]]},
                {"reference": 0.4, "react": 0, "band": [[0, 3]]},
            ],
        )
    )
    assert schedule.has_rules
    assert schedule.resolution == timedelta(minutes=30)
    assert schedule.end == schedule.start + timedelta(hours=1)
    first_slot = schedule.start + timedelta(minutes=10)
    second_slot = schedule.start + timedelta(minutes=40)
    assert schedule.respond_at(first_slot, residual=0.5) == 0.5
    assert schedule.respond_at(second_slot, residual=0.5) == 2.0
    with pytest.raises(IndexError):
        schedule.slot(schedule.end)
    with pytest.raises(ValueError, match="timezone-aware"):
        schedule.slot(datetime(2026, 11, 30))
    slots = list(schedule)
    assert slots[1] == (second_slot - timedelta(minutes=10), 2.0, schedule.rules[1])


def test_response_schedule_empty_values():
    schedule = ResponseSchedule.from_schedule(make_schedule([], []))
    assert schedule.rules == []
    assert len(schedule) == 0
    assert schedule.resolution == timedelta(hours=1)


@pytest.mark.parametrize("react", [0, 1, 0.5])
@pytest.mark.parametrize(
    "band", [[(None, None)], [(0.0, 7.4)], MODES_BAND, [(None, -1.4), (1.4, None)]]
)
@pytest.mark.parametrize("p", [-0.0074, -0.002, 0.0, 0.0014, 0.003, 0.0074])
def test_zero_residual_returns_the_plan(react, band, p):
    rule = ResponseRule(reference=0.0, react=react, band=band)
    if rule.contains(p):
        assert respond(rule, p, 0.0) == p
        assert rule.respond(p, 0.0) == p


def test_react_zero_returns_the_plan_whatever_the_residual():
    rule = ResponseRule(reference=0.0, react=0, band=[(1.0, 2.0)])
    for residual in (-10.0, -1.0, 0.0, 1.0, 10.0):
        assert respond(rule, 1.5, residual) == 1.5
    # Even a plan outside the band is returned as is, since react 0 means "follow the plan".
    assert respond(rule, 5.0, 1.0) == 5.0


def test_react_one_absorbs_the_residual():
    rule = ResponseRule(reference=0.0, react=1, band=[(0.0, 7.4)])
    assert respond(rule, 3.0, 0.5) == 2.5
    assert respond(rule, 3.0, -0.5) == 3.5


def test_intermediate_react_absorbs_a_share():
    rule = ResponseRule(reference=0.0, react=0.5, band=[(0.0, 7.4)])
    assert respond(rule, 3.0, 1.0) == 2.5
    assert respond(rule, 3.0, -1.0) == 3.5
    assert respond(rule, 3.0, 100.0) == 0.0


@pytest.mark.parametrize("react", [-1, -0.001, 1.001, 2])
def test_parse_response_rejects_react_outside_the_unit_interval(react):
    response = [{"reference": 0, "react": react, "band": [[None, None]]}]
    with pytest.raises(InvalidResponseError, match=r"share in \[0, 1\]"):
        parse_response(make_schedule([1.0], response))


@pytest.mark.parametrize("react", [0, 0.5, 1])
def test_parse_response_accepts_react_within_the_unit_interval(react):
    response = [{"reference": 0, "react": react, "band": [[None, None]]}]
    (rule,) = parse_response(make_schedule([1.0], response))
    assert rule.react == react


def test_clipping_to_band_edges():
    rule = ResponseRule(reference=0.0, react=1, band=[(0.0, 7.4)])
    assert respond(rule, 3.0, 10.0) == 0.0
    assert respond(rule, 3.0, -10.0) == 7.4
    assert respond(rule, 3.0, -4.4) == 7.4


def test_none_sides_are_unbounded():
    assert respond(ResponseRule(0.0, 1, [(None, None)]), 3.0, -1000.0) == 1003.0
    assert respond(ResponseRule(0.0, 1, [(None, None)]), 3.0, 1000.0) == -997.0
    lower_only = ResponseRule(0.0, 1, [(1.4, None)])
    assert respond(lower_only, 3.0, -1000.0) == 1003.0
    assert respond(lower_only, 3.0, 1000.0) == 1.4
    upper_only = ResponseRule(0.0, 1, [(None, 7.4)])
    assert respond(upper_only, 3.0, 1000.0) == -997.0
    assert respond(upper_only, 3.0, -1000.0) == 7.4


def test_a_value_in_a_hole_is_never_returned():
    rule = ResponseRule(reference=0.0, react=1, band=MODES_BAND)
    p = 0.003
    for step in range(-2000, 2001):
        residual = step / 100_000
        setpoint = respond(rule, p, residual)
        assert rule.contains(setpoint), (residual, setpoint)
        continuous = p - residual
        if rule.contains(continuous):
            assert setpoint == continuous


def test_hole_resolves_to_the_nearest_edge():
    rule = ResponseRule(reference=0.0, react=1, band=MODES_BAND)
    # 0.003 - 0.0025 = 0.0005, nearer to 0 than to 0.0014.
    assert respond(rule, 0.003, 0.0025) == 0.0
    # 0.003 - 0.0021 = 0.0009, nearer to 0.0014 than to 0.
    assert respond(rule, 0.003, 0.0021) == 0.0014
    # Past the outermost edges, the outermost edge.
    assert respond(rule, 0.003, 0.1) == -0.0074
    assert respond(rule, 0.003, -0.1) == 0.0074


def test_hole_tie_breaks_towards_the_plan():
    band = [(0.0, 0.0), (0.0014, 0.0074)]
    # 0.0007 is equally far from 0 and from 0.0014.
    assert clip_to_band(0.0007, band, towards=0.003) == 0.0014
    assert clip_to_band(0.0007, band, towards=0.0) == 0.0
    assert clip_to_band(0.0007, band) == 0.0
    assert clip_to_band(0.0007, band, towards=0.0007) == 0.0
    assert clip_to_band(0.0, [(None, -1.0), (1.0, None)], towards=2.0) == 1.0
    assert clip_to_band(0.0, [(None, -1.0), (1.0, None)], towards=-2.0) == -1.0


def test_clip_to_band_ignores_range_order():
    band = [(1.4, 7.4), (0.0, 0.0), (-7.4, -1.4)]
    assert clip_to_band(0.0005, band, towards=3.0) == 0.0
    assert clip_to_band(100.0, band) == 7.4
    assert clip_to_band(3.0, band) == 3.0


def test_empty_band_leaves_the_value():
    assert clip_to_band(5.0, []) == 5.0
    assert respond(ResponseRule(0.0, 1, []), 3.0, 1.0) == 2.0


def test_residuals_walk_the_chain():
    plans = {"battery": -2.0, "charger": 7.4, "pv": -3.0, "heat pump": 1.0}
    realized = {"battery": -1.5, "charger": 7.4, "pv": -2.0, "heat pump": 0.0}
    surprise = 0.8
    # The heat pump has react 0 this slot, so it sits outside the chain and its deviation is handed to everyone.
    chain = ["battery", "charger", "pv"]
    result = residuals(surprise, chain, plans, realized)
    assert list(result) == chain
    outside = realized["heat pump"] - plans["heat pump"]
    assert result["battery"] == pytest.approx(surprise + outside)
    assert result["charger"] == pytest.approx(surprise + outside + 0.5)
    assert result["pv"] == pytest.approx(surprise + outside + 0.5 + 0.0)


def test_residuals_without_deviations_equal_the_surprise():
    plans = {"a": 1.0, "b": 2.0}
    assert residuals(0.3, ["a", "b"], plans, dict(plans)) == {"a": 0.3, "b": 0.3}
    assert residuals(0.3, [], plans, dict(plans)) == {}


def test_residuals_missing_realized_power_is_an_error():
    with pytest.raises(KeyError):
        residuals(0.0, ["a"], {"a": 1.0}, {})


def test_convert_response_units_moves_reference_and_band_edges():
    response = [
        {"reference": 0.4, "react": 1, "band": [[None, 7.4], [8, None]]},
        {"reference": -0.1, "react": 0, "band": []},
    ]
    converted = convert_response_units(response, lambda xs: [x * 1000 for x in xs])
    assert converted == [
        {"reference": 400.0, "react": 1, "band": [[None, 7400.0], [8000, None]]},
        {"reference": -100.0, "react": 0, "band": []},
    ]
    # The served entries are left alone.
    assert response[0]["reference"] == 0.4
    assert response[0]["band"] == [[None, 7.4], [8, None]]
