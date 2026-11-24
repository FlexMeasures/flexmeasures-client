"""Run the reaction rules example against a stand-in client."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[2] / "examples" / "reaction_rules.py"
module_spec = importlib.util.spec_from_file_location("reaction_rules", MODULE_PATH)
assert module_spec is not None
assert module_spec.loader is not None
example = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(example)

ARGS = [
    "--email",
    "user@example.com",
    "--password",
    "secret",
    "--sensor",
    "8",
    "--price-sensor",
    "7",
    "--start",
    "2026-11-30T00:00:00+01:00",
    "--duration",
    "PT2H",
]


class StandInClient:
    def __init__(self, schedule: dict):
        self.schedule = schedule
        self.calls: list[dict] = []
        self.closed = False

    async def trigger_and_get_schedule(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.schedule

    async def close(self) -> None:
        self.closed = True


def served_schedule(with_rules: bool) -> dict:
    schedule = {
        "values": [4.4, 0.0, 7.4, -1.4, 2.0, 2.0, 0.0, 1.4],
        "start": "2026-11-30T00:00:00+01:00",
        "duration": "PT2H",
        "unit": "kW",
    }
    if with_rules:
        schedule["reaction"] = [
            {"reference": 0.4, "react": 1, "band": [[1.4, 7.4]]},
            {"reference": 0.4, "react": 0, "band": [[0, 0]]},
            {"reference": 0.4, "react": 1, "band": [[-7.4, -1.4], [0, 0], [1.4, 7.4]]},
            {"reference": 0.4, "react": 1, "band": [[-7.4, -1.4], [0, 0], [1.4, 7.4]]},
            {"reference": 0.4, "react": 1, "band": [[None, None]]},
            {"reference": 0.4, "react": 1, "band": [[None, 7.4]]},
            {"reference": 0.4, "react": 0, "band": []},
            {"reference": 0.4, "react": 1, "band": [[1.4, None]]},
        ]
    return schedule


@pytest.mark.asyncio
async def test_example_opts_in_and_walks_the_rules(capsys):
    client = StandInClient(served_schedule(with_rules=True))
    args = example.parse_args(ARGS)
    assert await example.run(client, args) == 0
    (call,) = client.calls
    assert call["sensor_id"] == 8
    assert call["unit"] == "kW"
    assert call["flex_context"] == {"consumption-price": {"sensor": 7}}
    assert call["flex_model"]["react"] is True
    assert call["flex_model"]["power-capacity"] == "7.4 kW"
    out = capsys.readouterr().out
    assert "8 slots" in out
    assert sum(line.startswith("2026-11-30T") for line in out.splitlines()) == 8
    assert "absorb the surprise" in out


@pytest.mark.asyncio
async def test_example_says_when_the_server_served_no_rules(capsys):
    client = StandInClient(served_schedule(with_rules=False))
    assert await example.run(client, example.parse_args(ARGS)) == example.NO_RULES
    assert "NO reaction rules" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_example_fails_on_an_invalid_reaction(capsys):
    schedule = served_schedule(with_rules=True)
    schedule["reaction"] = schedule["reaction"][:3]
    assert await example.run(StandInClient(schedule), example.parse_args(ARGS)) == 1
    assert "invalid" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_example_fails_when_a_plan_breaks_its_own_band(capsys):
    schedule = served_schedule(with_rules=True)
    schedule["values"][0] = 100.0
    assert await example.run(StandInClient(schedule), example.parse_args(ARGS)) == 1
    assert "outside its own band" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_example_main_closes_the_client(monkeypatch):
    created = []

    def make_client(**kwargs):
        client = StandInClient(served_schedule(with_rules=True))
        client.connection = kwargs
        created.append(client)
        return client

    monkeypatch.setattr(example, "FlexMeasuresClient", make_client)
    assert await example.main(ARGS + ["--host", "fm.example", "--no-ssl"]) == 0
    (client,) = created
    assert client.closed
    assert client.connection == {
        "host": "fm.example",
        "ssl": False,
        "email": "user@example.com",
        "password": "secret",
    }


def test_scripted_surprise_has_a_cooking_step_and_a_sunny_spell():
    surprise = example.scripted_surprise(100)
    assert len(surprise) == 100
    assert surprise[35] > 1.0
    assert surprise[55] < -1.0
    assert abs(surprise[0]) < 0.2
