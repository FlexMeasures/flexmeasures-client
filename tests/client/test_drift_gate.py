"""The drift gate: absorbing inside the band moves the device off its plan, and enough of it, or a target near, asks for a new plan."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from flexmeasures_client.reaction import DriftGate

T0 = datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc)
SAMPLE = timedelta(seconds=1)
PLAN = 2.0  # kW


def _feed(
    gate: DriftGate, realized: list[float], start: datetime = T0, plan: float = PLAN
) -> list[int]:
    """Feed one realised power per second against a constant plan; return the seconds at which the gate fired."""
    fired = []
    for second, power in enumerate(realized):
        if gate.observe(start + second * SAMPLE, power, plan):
            fired.append(second)
            gate.request_sent()
    return fired


def test_a_short_absorption_below_the_threshold_fires_nothing():
    gate = DriftGate(threshold=0.5)
    # A sunny spell absorbed at 3 kW for five minutes is 0.25 kWh, half the threshold.
    assert _feed(gate, [PLAN] + [PLAN + 3.0] * 300 + [PLAN] * 60) == []
    assert gate.drift == pytest.approx(0.25)
    assert not gate.in_flight


def test_a_sustained_absorption_fires_once_when_the_drift_reaches_the_threshold():
    gate = DriftGate(threshold=0.5)
    # The first sample sets the clock; 3 kW more than planned reaches 0.5 kWh after 600 s.
    assert _feed(gate, [PLAN + 3.0] * 1200) == [600]
    assert gate.drift == pytest.approx(3.0 * 1199 / 3600)


def test_drift_in_opposite_directions_cancels():
    gate = DriftGate(threshold=0.5)
    # 0.4 kWh taken in, then 0.4 kWh held back, then 0.4 kWh taken in again: never 0.5 kWh away from the plan.
    absorbed = [PLAN + 3.0] * 480
    held_back = [PLAN - 3.0] * 480
    assert _feed(gate, [PLAN] + absorbed + held_back + absorbed) == []
    assert gate.drift == pytest.approx(0.4)


def test_nothing_fires_while_in_flight_and_the_plan_resets_the_drift():
    gate = DriftGate(threshold=0.5)
    assert _feed(gate, [PLAN + 3.0] * 1200) == [600]
    assert gate.in_flight
    # The drift keeps growing, but one request is in flight.
    assert _feed(gate, [PLAN + 3.0] * 600, start=T0 + 1200 * SAMPLE) == []
    assert gate.drift > 1.0
    gate.plan_arrived()
    assert not gate.in_flight and gate.drift == 0.0
    # Afresh, and the clock carries on: the first sample already counts one second, so 600 seconds end at the 599th.
    assert _feed(gate, [PLAN + 3.0] * 700, start=T0 + 1800 * SAMPLE) == [599]


def test_a_target_within_the_lead_fires_with_small_drift_but_not_without():
    target = T0 + timedelta(minutes=30)
    lead = timedelta(minutes=10)
    on_plan = DriftGate(lead=lead, targets=[target])
    assert _feed(on_plan, [PLAN] * 1800) == []
    drifted = DriftGate(lead=lead, targets=[target])
    # 0.6 kW more than planned for the first ten minutes is 0.1 kWh, past the minimum before a target, but far below the threshold.
    fired = _feed(drifted, [PLAN + 0.6] * 601 + [PLAN] * 1199)
    assert fired == [20 * 60]


def test_each_target_fires_at_most_once():
    targets = [T0 + timedelta(minutes=30), T0 + timedelta(minutes=60)]
    gate = DriftGate(lead=timedelta(minutes=10), targets=targets)
    assert _feed(gate, [PLAN + 0.6] * 601 + [PLAN] * 600) == [20 * 60]
    gate.plan_arrived()
    # Drifting again from 20 minutes on: 0.05 kWh after five more minutes, still within the lead of the first target,
    # which already fired; nothing until the lead of the second target begins at 50 minutes.
    start = T0 + timedelta(minutes=20, seconds=1)
    assert _feed(gate, [PLAN + 0.6] * 40 * 60, start=start) == [30 * 60 - 1]


def test_without_a_lead_targets_do_not_fire():
    gate = DriftGate(targets=[T0 + timedelta(minutes=5)])
    assert _feed(gate, [PLAN + 1.2] * 600) == []


def test_samples_out_of_order_are_refused():
    gate = DriftGate()
    gate.observe(T0, PLAN, PLAN)
    with pytest.raises(ValueError):
        gate.observe(T0 - SAMPLE, PLAN, PLAN)


def test_a_moment_is_stored_as_a_window_of_one_moment():
    moment = T0 + timedelta(minutes=30)
    window = (T0 + timedelta(minutes=40), T0 + timedelta(minutes=50))
    gate = DriftGate(targets=[moment, window])
    assert gate.targets == [(moment, moment), window]
    with pytest.raises(ValueError):
        DriftGate(targets=[(window[1], window[0])])


def test_the_lead_counts_towards_the_start_of_a_target_window():
    window = (T0 + timedelta(minutes=30), T0 + timedelta(minutes=60))
    gate = DriftGate(lead=timedelta(minutes=10), targets=[window])
    # 0.1 kWh of drift in the first ten minutes; the lead of the window's start begins at 20 minutes, not that of its end at 50.
    assert _feed(gate, [PLAN + 0.6] * 601 + [PLAN] * 2999) == [20 * 60]


def test_a_target_window_that_has_started_is_not_past():
    window = (T0 + timedelta(minutes=5), T0 + timedelta(minutes=60))
    gate = DriftGate(lead=timedelta(minutes=10), targets=[window])
    # On plan for ten minutes, inside the window; then 1.1 kW more than planned reaches the 0.05 kWh minimum after 164 s.
    assert _feed(gate, [PLAN] * 601 + [PLAN + 1.1] * 600) == [600 + 164]


def test_inside_a_target_window_the_tighter_threshold_fires():
    window = (T0 + timedelta(minutes=20), T0 + timedelta(minutes=30))
    gate = DriftGate(threshold=0.5, threshold_in_window=0.1, targets=[window])
    # 0.12 kWh of drift in the first ten minutes, outside the window: below the threshold of 0.5 kWh, so nothing fires until the window starts.
    assert _feed(gate, [PLAN + 0.7] * 601 + [PLAN] * 1199) == [20 * 60]


def test_outside_a_target_window_the_tighter_threshold_does_not_fire():
    window = (T0 + timedelta(minutes=1), T0 + timedelta(minutes=5))
    gate = DriftGate(threshold=0.5, threshold_in_window=0.1, targets=[window])
    # The same 0.12 kWh of drift, reached at ten minutes, after the window ended.
    assert _feed(gate, [PLAN + 0.7] * 601 + [PLAN] * 1199) == []
    assert gate.drift == pytest.approx(0.7 * 600 / 3600)


def test_the_reason_says_drift_and_the_plan_clears_it():
    gate = DriftGate(threshold=0.5)
    assert gate.reason is None
    assert _feed(gate, [PLAN + 3.0] * 1200) == [600]
    assert gate.reason == "drift"
    gate.plan_arrived()
    assert gate.reason is None


def test_the_reason_says_drift_before_target():
    gate = DriftGate(lead=timedelta(minutes=10), targets=[T0 + timedelta(minutes=30)])
    assert _feed(gate, [PLAN + 0.6] * 601 + [PLAN] * 1199) == [20 * 60]
    assert gate.reason == "drift before target"


def test_the_reason_says_drift_in_target_window():
    window = (T0 + timedelta(minutes=20), T0 + timedelta(minutes=30))
    gate = DriftGate(threshold=0.5, threshold_in_window=0.1, targets=[window])
    assert _feed(gate, [PLAN + 0.7] * 601 + [PLAN] * 1199) == [20 * 60]
    assert gate.reason == "drift in target window"
