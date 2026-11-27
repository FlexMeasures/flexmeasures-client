"""Tests for parsing reaction rules and evaluating them, without a server."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from flexmeasures_client.reaction import (
    InvalidReactionError,
    ModePolicy,
    ReactionRule,
    ReactionSchedule,
    clip_to_band,
    convert_reaction_units,
    parse_reaction,
    react,
    residuals,
)

MODES_BAND = [(-0.0074, -0.0014), (0.0, 0.0), (0.0014, 0.0074)]


def make_schedule(values, reaction=None, **overrides) -> dict:
    schedule = {
        "values": values,
        "start": "2026-11-30T00:00:00+01:00",
        "duration": "PT1H",
        "unit": "MW",
    }
    if reaction is not None:
        schedule["reaction"] = reaction
    return schedule | overrides


def test_parse_reaction_absent_is_none():
    assert parse_reaction(make_schedule([1.0, 2.0])) is None


def test_parse_reaction_one_rule_per_value():
    rules = parse_reaction(
        make_schedule(
            [1.0, 2.0],
            [
                {"reference": 0.4, "react": 1, "band": [[-7.4, 7.4]]},
                {
                    "reference": 0,
                    "react": 0,
                    "band": [[-7.4, -1.4], [0, 0], [1.4, 7.4]],
                },
            ],
        )
    )
    assert rules == [
        ReactionRule(reference=0.4, react=1.0, band=[(-7.4, 7.4)]),
        ReactionRule(
            reference=0.0, react=0.0, band=[(-7.4, -1.4), (0.0, 0.0), (1.4, 7.4)]
        ),
    ]
    assert all(isinstance(rule.reference, float) for rule in rules)


def test_parse_reaction_length_mismatch_rejected():
    schedule = make_schedule(
        [1.0, 2.0], [{"reference": 0, "react": 0, "band": [[-7.4, 7.4]]}]
    )
    with pytest.raises(InvalidReactionError, match="1 entries for 2 values"):
        parse_reaction(schedule)
    with pytest.raises(ValueError):
        ReactionSchedule.from_schedule(schedule)


@pytest.mark.parametrize(
    ("reaction", "match"),
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
def test_parse_reaction_rejects_malformed_entries(reaction, match):
    with pytest.raises(InvalidReactionError, match=match):
        parse_reaction(make_schedule([1.0], reaction))


def test_parse_reaction_refuses_a_plan_outside_its_band():
    rules = [
        {"reference": 0.4, "react": 1, "band": [[1.4, 7.4]]},
        {"reference": 0.4, "react": 1, "band": [[-7.4, -1.4], [0, 0], [1.4, 7.4]]},
    ]
    with pytest.raises(
        InvalidReactionError, match="entry 0: the plan value 0.8 lies outside its band"
    ):
        parse_reaction(make_schedule([0.8, 2.0], rules, unit="kW"))
    with pytest.raises(
        InvalidReactionError, match="entry 1: the plan value 0.7 lies outside"
    ):
        parse_reaction(make_schedule([2.0, 0.7], rules, unit="kW"))
    # A plan with react 0 must lie in its band too, as the rule rests on the plan being feasible.
    rules[0]["react"] = 0
    with pytest.raises(InvalidReactionError, match="entry 0"):
        parse_reaction(make_schedule([0.8, 2.0], rules, unit="kW"))


@pytest.mark.parametrize(
    ("unit", "just_outside", "too_far"),
    [
        ("kW", 0.0004, 0.0006),
        ("W", 0.4, 0.6),
        ("MW", 4e-7, 6e-7),
        ("%", 0.0004, 0.0006),
    ],
)
def test_parse_reaction_allows_half_a_watt_of_rounding(unit, just_outside, too_far):
    rules = [{"reference": 0.0, "react": 1, "band": [[1.0, 2.0]]}]
    (rule,) = parse_reaction(make_schedule([1.0 - just_outside], rules, unit=unit))
    assert rule.band == [(1.0, 2.0)]
    (rule,) = parse_reaction(make_schedule([2.0 + just_outside], rules, unit=unit))
    with pytest.raises(InvalidReactionError, match="lies outside its band"):
        parse_reaction(make_schedule([1.0 - too_far], rules, unit=unit))
    with pytest.raises(InvalidReactionError, match="lies outside its band"):
        parse_reaction(make_schedule([2.0 + too_far], rules, unit=unit))


def test_parse_reaction_does_not_check_a_plan_against_an_empty_band():
    rules = [{"reference": 0.4, "react": 0, "band": []}]
    (rule,) = parse_reaction(make_schedule([123.0], rules))
    assert rule.band == []


def test_reaction_schedule_without_reaction_follows_the_plan():
    schedule = ReactionSchedule.from_schedule(
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
    assert schedule.react_at(at, residual=5.0) == 2.0


def test_reaction_schedule_with_rules():
    schedule = ReactionSchedule.from_schedule(
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
    assert schedule.react_at(first_slot, residual=0.5) == 0.5
    assert schedule.react_at(second_slot, residual=0.5) == 2.0
    with pytest.raises(IndexError):
        schedule.slot(schedule.end)
    with pytest.raises(ValueError, match="timezone-aware"):
        schedule.slot(datetime(2026, 11, 30))
    slots = list(schedule)
    assert slots[1] == (second_slot - timedelta(minutes=10), 2.0, schedule.rules[1])


def test_reaction_schedule_empty_values():
    schedule = ReactionSchedule.from_schedule(make_schedule([], []))
    assert schedule.rules == []
    assert len(schedule) == 0
    assert schedule.resolution == timedelta(hours=1)


@pytest.mark.parametrize("share", [0, 1, 0.5])
@pytest.mark.parametrize("band", [[(0.0, 7.4)], MODES_BAND, [(-7.4, -1.4), (1.4, 7.4)]])
@pytest.mark.parametrize("p", [-0.0074, -0.002, 0.0, 0.0014, 0.003, 0.0074])
def test_zero_residual_returns_the_plan(share, band, p):
    rule = ReactionRule(reference=0.0, react=share, band=band)
    if rule.contains(p):
        assert react(rule, p, 0.0) == p
        assert rule.react_to(p, 0.0) == p


def test_react_zero_returns_the_plan_whatever_the_residual():
    rule = ReactionRule(reference=0.0, react=0, band=[(1.0, 2.0)])
    for residual in (-10.0, -1.0, 0.0, 1.0, 10.0):
        assert react(rule, 1.5, residual) == 1.5
    # Even a plan outside the band is returned as is, since react 0 means "follow the plan".
    assert react(rule, 5.0, 1.0) == 5.0


def test_react_one_absorbs_the_residual():
    rule = ReactionRule(reference=0.0, react=1, band=[(0.0, 7.4)])
    assert react(rule, 3.0, 0.5) == 2.5
    assert react(rule, 3.0, -0.5) == 3.5


def test_intermediate_react_absorbs_a_share():
    rule = ReactionRule(reference=0.0, react=0.5, band=[(0.0, 7.4)])
    assert react(rule, 3.0, 1.0) == 2.5
    assert react(rule, 3.0, -1.0) == 3.5
    assert react(rule, 3.0, 100.0) == 0.0


@pytest.mark.parametrize("share", [-1, -0.001, 1.001, 2])
def test_parse_reaction_rejects_react_outside_the_unit_interval(share):
    reaction = [{"reference": 0, "react": share, "band": [[-7.4, 7.4]]}]
    with pytest.raises(InvalidReactionError, match=r"share in \[0, 1\]"):
        parse_reaction(make_schedule([1.0], reaction))


@pytest.mark.parametrize("share", [0, 0.5, 1])
def test_parse_reaction_accepts_react_within_the_unit_interval(share):
    reaction = [{"reference": 0, "react": share, "band": [[-7.4, 7.4]]}]
    (rule,) = parse_reaction(make_schedule([1.0], reaction))
    assert rule.react == share


def test_clipping_to_band_edges():
    rule = ReactionRule(reference=0.0, react=1, band=[(0.0, 7.4)])
    assert react(rule, 3.0, 10.0) == 0.0
    assert react(rule, 3.0, -10.0) == 7.4
    assert react(rule, 3.0, -4.4) == 7.4


def test_null_band_sides_are_rejected():
    """A device's power capacity always bounds its band, so a null side is a contract violation, not an open end."""
    schedule = {
        "values": [1.0],
        "reaction": [{"reference": 0.0, "react": 1, "band": [[None, 7.4]]}],
    }
    with pytest.raises(InvalidReactionError, match="Expected a number"):
        parse_reaction(schedule)


def test_a_value_in_a_hole_is_never_returned():
    rule = ReactionRule(reference=0.0, react=1, band=MODES_BAND)
    p = 0.003
    for step in range(-2000, 2001):
        residual = step / 100_000
        setpoint = react(rule, p, residual)
        assert rule.contains(setpoint), (residual, setpoint)
        continuous = p - residual
        if rule.contains(continuous):
            assert setpoint == continuous


def test_hole_resolves_to_the_edge_on_the_side_of_the_current_mode():
    rule = ReactionRule(reference=0.0, react=1, band=MODES_BAND)
    # p = 0.003 lies in (0.0014, 0.0074): a raw value in the hole below rounds up to 0.0014, nearer to 0 or not.
    assert react(rule, 0.003, 0.0025) == 0.0014  # raw 0.0005, nearer to 0
    assert react(rule, 0.003, 0.0021) == 0.0014  # raw 0.0009, nearer to 0.0014
    # A raw value in the next hole down rounds to that hole's upper edge, idle.
    assert react(rule, 0.003, 0.0035) == 0.0  # raw -0.0005
    # Past the outermost edges, the outermost edge.
    assert react(rule, 0.003, 0.1) == -0.0074
    assert react(rule, 0.003, -0.1) == 0.0074
    # From the discharging side the same holes round the other way.
    assert react(rule, -0.003, -0.0025) == -0.0014  # raw -0.0005
    assert react(rule, -0.003, -0.0035) == 0.0  # raw 0.0005


def test_the_documented_operation_modes_examples():
    band = [(-7.4, -1.4), (0.0, 0.0), (1.4, 7.4)]
    rule = ReactionRule(reference=-0.4, react=1, band=band)
    assert rule.react_to(p=2.0, residual=1.2) == 1.4  # raw 0.8
    assert rule.react_to(p=2.0, residual=1.4) == 1.4  # raw 0.6, no longer 0.0
    assert (
        rule.react_to(p=2.0, residual=2.5) == 0.0
    )  # raw -0.5: the edge of that hole on the charging side
    assert clip_to_band(0.8, band, current=(0.0, 0.0)) == 0.0
    assert clip_to_band(-0.5, band, current=(-7.4, -1.4)) == -1.4
    assert rule.react_to(p=2.0, residual=1.2, current=(0.0, 0.0)) == 0.0
    # The worked example: idle or 1.4 to 7.4 kW, a forecast surplus of 0.5 kW, a plan of 1.4 kW and a reference of -1.4 kW.
    rule = ReactionRule(reference=-1.4, react=1, band=[(0.0, 0.0), (1.4, 7.4)])
    as_forecast = -0.5 - rule.reference
    assert (
        rule.react_to(p=1.4, residual=as_forecast) == 1.4
    )  # raw 0.5, in the hole: stays charging
    extra_sun = -1.5 - rule.reference
    assert rule.react_to(p=1.4, residual=extra_sun) == 1.5


def test_a_value_inside_another_range_passes_the_pure_function():
    band = [(-7.4, -1.4), (0.0, 0.0), (1.4, 7.4)]
    assert clip_to_band(-2.0, band, current=(1.4, 7.4)) == -2.0
    assert react(ReactionRule(0.0, 1, band), 2.0, 4.0) == -2.0


def test_without_a_current_range_the_nearest_edge_wins_and_ties_go_towards_the_plan():
    band = [(0.0, 0.0), (0.0014, 0.0074)]
    assert clip_to_band(0.0005, band) == 0.0
    assert clip_to_band(0.0009, band) == 0.0014
    # 0.0007 is equally far from 0 and from 0.0014.
    assert clip_to_band(0.0007, band, towards=0.003) == 0.0014
    assert clip_to_band(0.0007, band, towards=0.0) == 0.0
    assert clip_to_band(0.0007, band) == 0.0
    assert clip_to_band(0.0007, band, towards=0.0007) == 0.0
    assert clip_to_band(0.0, [(-5.0, -1.0), (1.0, 5.0)], towards=2.0) == 1.0
    assert clip_to_band(0.0, [(-5.0, -1.0), (1.0, 5.0)], towards=-2.0) == -1.0
    # A plan outside every range leaves react() without a current range: the nearest edge, ties towards the plan.
    assert react(ReactionRule(0.0, 1, band), 0.0009, 0.0) == 0.0014


def test_clip_to_band_ignores_range_order():
    band = [(1.4, 7.4), (0.0, 0.0), (-7.4, -1.4)]
    assert clip_to_band(0.0005, band, towards=3.0) == 0.0
    assert clip_to_band(0.0005, band, current=(1.4, 7.4)) == 1.4
    assert clip_to_band(100.0, band) == 7.4
    assert clip_to_band(3.0, band) == 3.0


def test_empty_band_leaves_the_value():
    assert clip_to_band(5.0, []) == 5.0
    assert react(ReactionRule(0.0, 1, []), 3.0, 1.0) == 2.0


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


def test_residuals_without_departures_equal_the_deviation():
    plans = {"a": 1.0, "b": 2.0}
    assert residuals(0.3, ["a", "b"], plans, dict(plans)) == {"a": 0.3, "b": 0.3}
    assert residuals(0.3, [], plans, dict(plans)) == {}


def test_residuals_missing_realized_power_is_an_error():
    with pytest.raises(KeyError):
        residuals(0.0, ["a"], {"a": 1.0}, {})


def test_convert_reaction_units_moves_reference_and_band_edges():
    reaction = [
        {"reference": 0.4, "react": 1, "band": [[-7.4, 7.4], [8, 10]]},
        {"reference": -0.1, "react": 0, "band": []},
    ]
    converted = convert_reaction_units(reaction, lambda xs: [x * 1000 for x in xs])
    assert converted == [
        {"reference": 400.0, "react": 1, "band": [[-7400.0, 7400.0], [8000, 10000]]},
        {"reference": -100.0, "react": 0, "band": []},
    ]
    # The served entries are left alone.
    assert reaction[0]["reference"] == 0.4
    assert reaction[0]["band"] == [[-7.4, 7.4], [8, 10]]


T0 = datetime(2026, 11, 30, 18, 0, tzinfo=timezone.utc)
THREE_MODES = [(-7.4, -1.4), (0.0, 0.0), (1.4, 7.4)]
FIXTURE_BANDS = [
    MODES_BAND,
    THREE_MODES,
    [(0.0, 0.0), (1.4, 7.4)],
    [(1.4, 7.4)],
    [(0.0, 0.0)],
    [(-7.4, 7.4)],
    [(-3.5, -1.4), (0.0, 0.0), (1.4, 7.4)],
]


def _feed(policy: ModePolicy, values: list[float], start: int = 0) -> list[float]:
    return [
        policy.observe(T0 + timedelta(seconds=start + i), v)
        for i, v in enumerate(values)
    ]


@pytest.mark.parametrize("band", FIXTURE_BANDS)
def test_the_plan_is_the_setpoint_at_zero_residual_in_every_mode(band):
    rule = ReactionRule(reference=0.4, react=1, band=band)
    policy = ModePolicy()
    for low, high in band:
        for p in (low, (low + high) / 2, high):
            assert react(rule, p, 0.0) == p
            assert rule.react_to(p, 0.0) == p
            policy.start_slot(p, band)
            assert policy.observe(T0, p) == p


def test_the_default_policy_holds_the_plan_mode_for_the_slot():
    policy = ModePolicy()
    policy.start_slot(2.0, THREE_MODES)
    assert policy.current == (1.4, 7.4)
    # Every raw value outside the charging mode, in a hole, on idle or beyond the band, clips to the mode's nearer edge.
    assert _feed(policy, [0.8, 0.6, -0.5, 0.0, 3.0, 9.0]) == [
        1.4,
        1.4,
        1.4,
        1.4,
        3.0,
        7.4,
    ]
    # Inside the discharging mode, however long: still the charging mode's edge.
    assert _feed(policy, [-2.0] * 600 + [-10.0], start=6) == [1.4] * 601
    assert policy.current == (1.4, 7.4) and policy.switches == 0


def test_while_holding_a_raw_value_inside_another_range_clips_to_the_current_edge():
    policy = ModePolicy(dwell=timedelta(hours=1))
    policy.start_slot(2.0, THREE_MODES)
    assert _feed(policy, [-2.0, -10.0, 0.0, 0.5]) == [1.4, 1.4, 1.4, 1.4]
    policy.start_slot(-3.0, THREE_MODES)
    assert _feed(policy, [2.0, 10.0, 0.0, -0.5], start=4) == [-1.4, -1.4, -1.4, -1.4]
    policy.start_slot(0.0, THREE_MODES)
    assert _feed(policy, [2.0, -2.0, 0.7], start=8) == [0.0, 0.0, 0.0]
    assert policy.switches == 0


def test_a_short_excursion_into_another_mode_does_not_switch():
    policy = ModePolicy(dwell=timedelta(seconds=5))
    policy.start_slot(2.0, THREE_MODES)
    assert _feed(policy, [-2.0, 2.0, 2.0]) == [1.4, 2.0, 2.0]
    assert _feed(policy, [-2.0] * 4 + [2.0], start=3) == [1.4] * 4 + [2.0]
    assert policy.current == (1.4, 7.4) and policy.switches == 0


def test_a_lasting_excursion_switches_after_the_dwell_and_back_the_same_way():
    policy = ModePolicy(dwell=timedelta(seconds=5))
    policy.start_slot(2.0, THREE_MODES)
    # Five seconds after the first sample inside the discharging mode, the policy switches.
    assert _feed(policy, [-2.0] * 7) == [1.4] * 5 + [-2.0, -2.0]
    assert policy.current == (-7.4, -1.4) and policy.switches == 1
    # The plan's mode is now the other mode: a raw value in it is held at this mode's edge until the dwell has passed again.
    assert _feed(policy, [2.0] * 7, start=7) == [-1.4] * 5 + [2.0, 2.0]
    assert policy.current == (1.4, 7.4) and policy.switches == 2
    # A new slot starts in the plan's mode again, candidate forgotten.
    policy.start_slot(-3.0, THREE_MODES)
    assert policy.current == (-7.4, -1.4)


def test_the_margin_keeps_a_value_just_inside_another_mode_from_counting():
    two_modes = [(-7.4, -1.4), (1.4, 7.4)]
    policy = ModePolicy(dwell=timedelta(seconds=2), margin=0.2)
    policy.start_slot(2.0, two_modes)
    # -1.5 is 0.1 past the hole into the discharging mode: not enough.
    assert _feed(policy, [-1.5] * 5) == [1.4] * 5
    assert policy.switches == 0
    # -1.7 is 0.3 past the hole: enough, and the dwell runs from the first qualifying sample.
    assert _feed(policy, [-1.7] * 4, start=5) == [1.4, 1.4, -1.7, -1.7]
    assert policy.switches == 1
    # With idle in between, the same -1.5 counts for idle, which has no margin to be past.
    policy = ModePolicy(dwell=timedelta(seconds=2), margin=0.2)
    policy.start_slot(2.0, THREE_MODES)
    assert _feed(policy, [-1.5] * 5) == [1.4, 1.4, 0.0, 0.0, 0.0]
    assert policy.current == (0.0, 0.0) and policy.switches == 1


def test_idle_is_reached_only_through_a_switch_and_never_with_the_default_policy():
    policy = ModePolicy()
    policy.start_slot(2.0, THREE_MODES)
    assert _feed(policy, [0.0] * 10 + [-0.5]) == [1.4] * 11
    assert policy.switches == 0
    policy = ModePolicy(dwell=timedelta(seconds=1))
    policy.start_slot(2.0, THREE_MODES)
    assert _feed(policy, [0.0] * 3) == [1.4, 0.0, 0.0]
    assert policy.current == (0.0, 0.0) and policy.switches == 1
    # Back out of idle takes the dwell too.
    assert _feed(policy, [3.0] * 3, start=3) == [0.0, 3.0, 3.0]
    assert policy.current == (1.4, 7.4) and policy.switches == 2


def test_a_raw_value_inside_the_current_mode_asks_for_nothing():
    policy = ModePolicy(dwell=timedelta(seconds=1))
    policy.start_slot(-3.0, THREE_MODES)
    assert _feed(policy, [1.9] * 3) == [-1.4, 1.9, 1.9]
    assert policy.current == (1.4, 7.4) and policy.switches == 1
    # Charging now, with idle on the far side of the hole below: a raw value inside charging never counts for idle.
    assert _feed(policy, [1.9] * 100, start=3) == [1.9] * 100
    assert policy.current == (1.4, 7.4) and policy.switches == 1


def test_a_raw_value_beyond_idle_counts_for_idle_unless_a_wider_mode_takes_it():
    policy = ModePolicy(dwell=timedelta(seconds=1))
    policy.start_slot(2.0, THREE_MODES)
    # In the hole beyond idle: idle it is.
    assert _feed(policy, [-0.5] * 3) == [1.4, 0.0, 0.0]
    assert policy.current == (0.0, 0.0)
    policy.start_slot(2.0, THREE_MODES)
    # Inside the discharging mode: that mode, not idle.
    assert _feed(policy, [-2.0] * 3, start=3) == [1.4, -2.0, -2.0]
    assert policy.current == (-7.4, -1.4)


def test_the_policy_without_a_band_or_a_plan_in_the_band_falls_back():
    policy = ModePolicy()
    policy.start_slot(5.0, [])
    assert policy.observe(T0, 123.0) == 123.0
    policy.start_slot(0.7, THREE_MODES)  # a plan in a hole: no current mode
    assert policy.current is None
    assert policy.observe(T0, 0.6) == 0.0  # the nearest edge
    assert policy.observe(T0, 0.9) == 1.4


def test_policy_samples_out_of_order_are_refused():
    policy = ModePolicy(dwell=timedelta(seconds=5))
    policy.start_slot(2.0, THREE_MODES)
    policy.observe(T0 + timedelta(seconds=1), -2.0)
    with pytest.raises(ValueError):
        policy.observe(T0, -2.0)
