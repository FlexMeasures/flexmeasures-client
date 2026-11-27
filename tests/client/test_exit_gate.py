"""The exit gate: a short peak passes, a lasting one fires once, nothing fires while a request is in flight."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from flexmeasures_client.reaction import ExitGate

T0 = datetime(2026, 12, 10, 18, 0, tzinfo=timezone.utc)
SAMPLE = timedelta(seconds=1)


def _feed(gate: ExitGate, residuals: list[float], start: datetime = T0) -> list[int]:
    """Feed one residual per second; return the seconds at which the gate fired."""
    fired = []
    for second, residual in enumerate(residuals):
        if gate.observe(start + second * SAMPLE, residual):
            fired.append(second)
            gate.request_sent()
    return fired


def test_a_short_peak_passes_the_dwell_but_not_the_energy_gate():
    gate = ExitGate(dwell=timedelta(seconds=21), min_energy=0.1)
    # A kettle: 1.5 kW left over for two minutes is 50 Wh, under the 100 Wh gate.
    assert _feed(gate, [1.5] * 120 + [0.0] * 10) == []
    assert gate.exited_at is None and gate.energy_outside == 0.0


def test_a_lasting_residual_fires_once_when_the_energy_is_reached():
    gate = ExitGate(dwell=timedelta(seconds=21), min_energy=0.1)
    # 3 kW left over reaches 100 Wh after 120 s; the dwell of 21 s is long past.
    assert _feed(gate, [3.0] * 300) == [120]


def test_the_dwell_alone_is_enough_without_an_energy_gate():
    gate = ExitGate(dwell=timedelta(seconds=21), min_energy=0.0)
    assert _feed(gate, [3.0] * 60) == [21]


def test_nothing_fires_while_a_request_is_in_flight_and_the_plan_resets_the_gate():
    gate = ExitGate(dwell=timedelta(seconds=21), min_energy=0.0)
    fired = _feed(gate, [3.0] * 60)
    assert fired == [21]
    assert gate.in_flight
    # The residual wobbles and persists: still nothing.
    assert _feed(gate, [2.0, 3.0] * 30, start=T0 + 60 * SAMPLE) == []
    gate.plan_arrived()
    assert not gate.in_flight and gate.exited_at is None and gate.energy_outside == 0.0


def test_a_return_inside_the_band_resets_the_exit():
    gate = ExitGate(dwell=timedelta(seconds=21), min_energy=0.05)
    # 40 s outside (33 Wh), back inside, then 40 s outside again: each excursion starts from zero.
    assert _feed(gate, [3.0] * 40 + [0.0] * 5 + [3.0] * 40) == []


def test_residuals_within_the_tolerance_count_as_inside():
    gate = ExitGate(dwell=timedelta(seconds=1), min_energy=0.0, tolerance=0.05)
    assert _feed(gate, [0.04] * 10) == []
    assert _feed(gate, [-0.06] * 10, start=T0 + 10 * SAMPLE) == [
        1
    ]  # one second after the exit


def test_samples_out_of_order_are_refused():
    gate = ExitGate(dwell=timedelta(seconds=1))
    gate.observe(T0 + SAMPLE, 0.0)
    with pytest.raises(ValueError):
        gate.observe(T0, 0.0)


def test_the_reason_says_band_exit_and_the_plan_clears_it():
    gate = ExitGate(dwell=timedelta(seconds=21), min_energy=0.1)
    assert gate.reason is None
    # An oven: 3 kW left over reaches 100 Wh after 120 s.
    assert _feed(gate, [3.0] * 200) == [120]
    assert gate.reason == "band exit"
    gate.plan_arrived()
    assert gate.reason is None


def test_outside_for_is_the_duration_of_the_current_excursion():
    gate = ExitGate(dwell=timedelta(seconds=21))
    assert gate.outside_for == timedelta(0)
    _feed(gate, [0.0] * 10 + [1.0] * 31)
    assert gate.outside_for == timedelta(seconds=30)
    gate.observe(T0 + timedelta(seconds=41), 0.0)
    assert gate.outside_for == timedelta(0)
