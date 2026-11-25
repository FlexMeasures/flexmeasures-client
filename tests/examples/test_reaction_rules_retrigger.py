"""The band-exit example: the rule acts, the dwell waits, one retrigger fires, the new plan closes the residual."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "examples" / "reaction_rules_retrigger.py"
)
module_spec = importlib.util.spec_from_file_location(
    "reaction_rules_retrigger", EXAMPLE
)
assert module_spec is not None
assert module_spec.loader is not None
example = importlib.util.module_from_spec(module_spec)
sys.modules[module_spec.name] = example
module_spec.loader.exec_module(example)


def test_the_rule_acts_at_once_and_the_band_stops_it():
    trace = example.simulate()
    at_surprise = trace.second.index(example.SURPRISE_AT_S)
    assert trace.commanded[at_surprise - 1] == example.OLD_PLAN_KW
    assert (
        trace.commanded[at_surprise] == example.OLD_RULE.band[0][0]
    )  # the discharge edge
    assert trace.residual[at_surprise] == pytest.approx(
        example.SURPRISE_KW - (example.OLD_PLAN_KW - example.OLD_RULE.band[0][0])
    )


def test_exactly_one_retrigger_after_the_dwell():
    trace = example.simulate()
    assert trace.retriggers == 1
    assert trace.band_exit_at == example.SURPRISE_AT_S
    assert trace.retrigger_at == example.SURPRISE_AT_S + example.DWELL_S


def test_the_cycling_oven_during_the_flight_fires_nothing():
    trace = example.simulate()
    assert trace.retrigger_at < example.CYCLE_AT_S < trace.new_plan_at
    assert trace.retriggers == 1


def test_the_new_plan_closes_the_residual():
    trace = example.simulate()
    assert trace.new_plan_at == trace.retrigger_at + example.TURNAROUND_S
    after = trace.second.index(trace.new_plan_at)
    assert all(abs(r) <= example.TOLERANCE_KW for r in trace.residual[after:])
    assert all(c == example.NEW_PLAN_KW for c in trace.commanded[after:])


def test_the_events_print_without_a_plotting_library(capsys):
    assert example.main(["--no-plot"]) == 0
    assert "retrigger at" in capsys.readouterr().out


def test_the_picture_is_written(tmp_path):
    pytest.importorskip("matplotlib")
    assert example.main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / "reaction-rules-retrigger.png").is_file()
