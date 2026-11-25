"""The saldering example: a plain schedule against reaction rules on one simulated day.

The numbers are deterministic (seeded), so the tests pin the story the pictures tell:
reacting keeps the connection closer to the plan, exports nothing at a negative price,
and costs less than following the plan blindly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "examples" / "reaction_rules_saldering.py"
)
module_spec = importlib.util.spec_from_file_location(
    "reaction_rules_saldering", EXAMPLE
)
assert module_spec is not None
assert module_spec.loader is not None
example = importlib.util.module_from_spec(module_spec)
# Register the module before executing it: its dataclass resolves annotations through sys.modules.
sys.modules[module_spec.name] = example
module_spec.loader.exec_module(example)


def test_reacting_beats_following_the_plan_blindly():
    day = example.simulate()
    numbers = example.summary(day)
    assert numbers["cost_reaction"] < numbers["cost_plain"]
    assert numbers["kwh_negative_price_reaction"] < numbers["kwh_negative_price_plain"]
    assert numbers["kwh_peak_import_reaction"] < numbers["kwh_peak_import_plain"]


def test_reacting_keeps_the_connection_closer_to_the_plan_where_it_reacts():
    day = example.simulate()
    reacting = [index for index, rule in enumerate(day.rules) if rule.react > 0]
    assert reacting, "the day should have slots that react"
    plain = sum(abs(day.plain_meter[i] - day.planned_meter[i]) for i in reacting)
    reaction = sum(abs(day.reaction_meter[i] - day.planned_meter[i]) for i in reacting)
    assert reaction < plain


def test_setpoints_stay_inside_the_band():
    day = example.simulate()
    assert all(
        rule.contains(setpoint)
        for rule, setpoint in zip(day.rules, day.reaction_charger)
    )


def test_without_a_surprise_the_reaction_is_the_plan():
    day = example.simulate()
    for rule, p in zip(day.rules, day.plan):
        assert example.react(rule, p, 0.0) == p


def test_the_numbers_need_no_plotting_library(capsys):
    assert example.main(["--no-plot"]) == 0
    assert "Cost of the day" in capsys.readouterr().out


def test_the_pictures_are_written(tmp_path):
    pytest.importorskip("matplotlib")
    assert example.main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / "reaction-rules-day.png").is_file()
    assert (tmp_path / "reaction-rules-cost.png").is_file()
