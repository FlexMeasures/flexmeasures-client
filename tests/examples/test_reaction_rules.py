"""Run the reaction rules probe against a stand-in client."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flexmeasures_client.reaction import ReactionSchedule, react

MODULE_PATH = Path(__file__).parents[2] / "examples" / "reaction_rules.py"
module_spec = importlib.util.spec_from_file_location("reaction_rules", MODULE_PATH)
assert module_spec is not None
assert module_spec.loader is not None
example = importlib.util.module_from_spec(module_spec)
sys.modules[module_spec.name] = example
module_spec.loader.exec_module(example)

START = datetime(2026, 11, 30, 0, 0, tzinfo=timezone(timedelta(hours=1)))
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
            {"reference": 0.4, "react": 1, "band": [[-7.4, 7.4]]},
            {"reference": 0.4, "react": 1, "band": [[-7.4, 7.4]]},
            {"reference": 0.4, "react": 0, "band": []},
            {"reference": 0.4, "react": 1, "band": [[1.4, 7.4]]},
        ]
    return schedule


def write_csv(path: Path, rows: list[tuple[datetime, float]]) -> Path:
    path.write_text(
        "time,deviation\n"
        + "".join(f"{when.isoformat()},{value}\n" for when, value in rows)
    )
    return path


def every(seconds: float, start: float, end: float, value: float):
    """Samples of one value every so many seconds from start to end (seconds after the schedule start)."""
    return [
        (START + timedelta(seconds=s), value)
        for s in range(int(start), int(end), int(seconds))
    ]


def recorded_series() -> list[tuple[datetime, float]]:
    """Every 10 s: 5 kW too much for five minutes (one exit, one retrigger), then for one minute (an exit, no retrigger).

    In the first slot the plan is 4.4 kW with a band down to 1.4 kW, so 3 kW of a surprise are absorbed and 2 kW are left over:
    100 Wh past the band take 180 s, while one minute of it is 33 Wh.
    """
    quiet = 0.0
    return (
        every(10, 0, 60, quiet)
        + every(10, 60, 360, 5.0)
        + every(10, 360, 1200, quiet)
        + every(10, 1200, 1260, 5.0)
        + every(10, 1260, 1800, quiet)
    )


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
    slot_table = out.split("\n\n")[0]
    assert sum(line.startswith("2026-11-30T") for line in slot_table.splitlines()) == 8
    assert "absorb the deviation" in out
    assert "Samples: 7200 walked, 0 outside the schedule skipped." in out
    assert "mode switches: 0," in out


def test_defaults_give_the_same_walk_as_the_per_slot_evaluation():
    """With the scripted surprise, constant within a slot, every slot ends where react() puts it."""
    schedule = ReactionSchedule.from_schedule(served_schedule(with_rules=True))
    assert schedule.rules is not None
    knobs = example.Knobs()
    assert (knobs.window, knobs.median, knobs.min_energy, knobs.tolerance) == (
        5.0,
        3,
        0.1,
        0.05,
    )
    result = example.walk(
        schedule, example.scripted_samples(schedule, example.DEFAULT_SAMPLE_S), knobs
    )
    expected = [
        react(rule, p, surprise)
        for p, rule, surprise in zip(
            schedule.values, schedule.rules, example.scripted_deviation(len(schedule))
        )
    ]
    assert result.slot_setpoint == pytest.approx(expected)
    assert result.slot_deviation == pytest.approx(
        example.scripted_deviation(len(schedule))
    )
    assert result.problems == []
    assert result.samples == 7200 and result.skipped == 0


def test_the_knobs_default_to_the_classes_defaults():
    args = example.parse_args(ARGS)
    assert args.window == example.Smoother().window.total_seconds()
    assert args.median == example.Smoother().median_samples
    assert args.min_energy == example.ExitGate(dwell=timedelta(0)).min_energy
    assert args.tolerance == example.ExitGate(dwell=timedelta(0)).tolerance
    assert (args.dwell, args.turnaround, args.sample) == (21.0, 30.0, 1.0)
    assert args.mode_dwell is None and args.mode_margin == 0.0
    assert args.drift == example.DriftGate().threshold == 0.5
    assert args.drift_lead is None and args.targets == []
    knobs = example.Knobs.from_args(args)
    assert (knobs.drift, knobs.drift_lead, knobs.targets) == (0.5, None, [])


def test_targets_need_an_offset():
    args = example.parse_args(
        ARGS + ["--drift-lead", "15", "--target-at", "2026-11-30T01:00:00+01:00"]
    )
    assert args.drift_lead == 15.0
    assert args.targets == [START + timedelta(hours=1)]
    with pytest.raises(SystemExit):
        example.parse_args(ARGS + ["--target-at", "2026-11-30T01:00:00"])


@pytest.mark.asyncio
async def test_a_sunny_spell_absorbed_long_enough_retriggers_on_drift(capsys):
    """The scripted sunny spell is absorbed in slots 4 and 5 (01:00 to 01:30), 2 kW above the plan, inside the band.

    No band exit fires, but the drift reaches 0.5 kWh during the spell; with a threshold of 2 kWh, it never does.
    """
    schedule = ReactionSchedule.from_schedule(served_schedule(with_rules=True))
    samples = list(example.scripted_samples(schedule, example.DEFAULT_SAMPLE_S))
    result = example.walk(schedule, samples, example.Knobs())
    assert result.retriggers == []
    (retrigger,) = result.drift_retriggers
    assert START + timedelta(hours=1) <= retrigger.at < START + timedelta(minutes=90)
    assert retrigger.drift == pytest.approx(0.5, abs=0.01)
    assert (
        example.walk(schedule, samples, example.Knobs(drift=2.0)).drift_retriggers == []
    )
    client = StandInClient(served_schedule(with_rules=True))
    assert await example.run(client, example.parse_args(ARGS)) == 0
    out = capsys.readouterr().out
    assert "retriggers: 0, drift retriggers: 1," in out
    assert f"{retrigger.at.isoformat():<25} {retrigger.drift:>11.3f}" in out


def test_a_target_within_the_lead_retriggers_on_small_drift():
    """The cooking step is absorbed in slot 3 (00:45 to 01:00), 1.5 kW below the plan; a target at 01:00 with a 15-minute lead fires on the first 50 Wh of it."""
    schedule = ReactionSchedule.from_schedule(served_schedule(with_rules=True))
    samples = list(example.scripted_samples(schedule, example.DEFAULT_SAMPLE_S))
    target = START + timedelta(hours=1)
    knobs = example.Knobs(drift=2.0, drift_lead=15.0, targets=[target])
    (retrigger,) = example.walk(schedule, samples, knobs).drift_retriggers
    assert START + timedelta(minutes=45) <= retrigger.at < target
    assert retrigger.drift == pytest.approx(-0.05, abs=0.001)


@pytest.mark.asyncio
async def test_a_recorded_series_gives_the_expected_exits_and_retriggers(
    tmp_path, capsys
):
    # The older names still work: a surprise column, read through --surprise-csv.
    path = tmp_path / "surprise.csv"
    path.write_text(
        "time,surprise\n"
        + "".join(f"{when.isoformat()},{value}\n" for when, value in recorded_series())
    )
    client = StandInClient(served_schedule(with_rules=True))
    args = example.parse_args(
        ARGS + ["--surprise-csv", str(path), "--surprise-unit", "kW"]
    )
    assert await example.run(client, args) == 0
    out = capsys.readouterr().out
    assert "Replaying 180 recorded samples" in out
    assert "Band exits: 2, retriggers: 1," in out
    assert "Samples: 180 walked, 0 outside the schedule skipped." in out


def test_a_recorded_series_in_watts_walks_the_same(tmp_path):
    schedule = ReactionSchedule.from_schedule(served_schedule(with_rules=True))
    in_kw = example.samples_from_csv(
        write_csv(tmp_path / "kw.csv", recorded_series()), "kW", "kW"
    )
    in_w = example.samples_from_csv(
        write_csv(
            tmp_path / "w.csv",
            [(when, value * 1000) for when, value in recorded_series()],
        ),
        "W",
        "kW",
    )
    assert in_w == in_kw
    knobs = example.Knobs()
    result = example.walk(schedule, in_w, knobs)
    assert len(result.exits) == 2 and len(result.retriggers) == 1
    # The retrigger comes 100 Wh after the exit, at 2 kW left over: three minutes,
    # or one sample more when the 10 s steps sum to a hair under the threshold.
    first = result.exits[0]
    assert first.retrigger_at is not None
    assert (
        timedelta(seconds=180)
        <= first.retrigger_at - first.start
        <= timedelta(seconds=190)
    )
    assert first.end == START + timedelta(seconds=370)
    assert result.exits[1].retrigger_at is None
    assert first.energy == pytest.approx(2.0 * 290 / 3600, rel=1e-6)


def test_a_higher_min_energy_suppresses_the_retrigger(tmp_path):
    schedule = ReactionSchedule.from_schedule(served_schedule(with_rules=True))
    samples = example.samples_from_csv(
        write_csv(tmp_path / "surprise.csv", recorded_series()), "kW", "kW"
    )
    at_default = example.walk(schedule, samples, example.Knobs())
    assert len(at_default.retriggers) == 1
    raised = example.walk(schedule, samples, example.Knobs(min_energy=1.0))
    assert len(raised.retriggers) == 0
    assert len(raised.exits) == len(at_default.exits) == 2


def test_the_window_smooths_a_one_sample_spike_away(tmp_path):
    """A 4 kW spike for one second pushes the setpoint past the band unsmoothed; a long window spreads it thin enough to absorb."""
    schedule = ReactionSchedule.from_schedule(served_schedule(with_rules=True))
    spike = (
        every(1, 0, 30, 0.0)
        + [(START + timedelta(seconds=30), 4.0)]
        + every(1, 31, 90, 0.0)
    )
    samples = example.samples_from_csv(
        write_csv(tmp_path / "spike.csv", spike), "kW", "kW"
    )
    unsmoothed = example.walk(schedule, samples, example.Knobs(median=1, window=1))
    assert len(unsmoothed.exits) == 1
    smoothed = example.walk(schedule, samples, example.Knobs(median=1, window=60))
    assert smoothed.exits == []


@pytest.mark.asyncio
async def test_samples_outside_the_schedule_are_skipped_and_counted(tmp_path, capsys):
    rows = (
        [(START - timedelta(hours=1), 0.0)]
        + every(60, 0, 7200, 0.0)
        + [(START + timedelta(hours=2), 0.0), (START + timedelta(hours=3), 0.0)]
    )
    path = write_csv(tmp_path / "surprise.csv", rows)
    client = StandInClient(served_schedule(with_rules=True))
    assert (
        await example.run(
            client, example.parse_args(ARGS + ["--deviation-csv", str(path)])
        )
        == 0
    )
    assert (
        "Samples: 120 walked, 3 outside the schedule skipped."
        in capsys.readouterr().out
    )


@pytest.mark.asyncio
async def test_a_recording_without_offsets_is_refused(tmp_path, capsys):
    path = tmp_path / "naive.csv"
    path.write_text("time,deviation\n2026-11-30T00:00:00,1.0\n")
    client = StandInClient(served_schedule(with_rules=True))
    assert (
        await example.run(
            client, example.parse_args(ARGS + ["--deviation-csv", str(path)])
        )
        == 1
    )
    assert "needs an offset" in capsys.readouterr().err


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
    assert "lies outside its band" in capsys.readouterr().err


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


def test_scripted_deviation_has_a_cooking_step_and_a_sunny_spell():
    deviation = example.scripted_deviation(100)
    assert len(deviation) == 100
    assert deviation[35] > 1.0
    assert deviation[55] < -1.0
    assert abs(deviation[0]) < 0.2


def excursion_into_charging() -> list[tuple[datetime, float]]:
    """In the fourth slot (plan -1.4 kW, three modes), 3.3 kW less import than forecast for two minutes, every second.

    The raw setpoint is 1.9 kW, inside the charging mode, while the plan discharges.
    (3.3 rather than a multiple of 0.7, so that the smoother's ramp does not pass exactly through idle.)
    """
    slot = 45 * 60
    return (
        every(1, slot, slot + 30, 0.0)
        + every(1, slot + 30, slot + 150, -3.3)
        + every(1, slot + 150, slot + 240, 0.0)
    )


def test_the_default_mode_policy_holds_the_plan_mode_and_leaves_a_residual(tmp_path):
    schedule = ReactionSchedule.from_schedule(served_schedule(with_rules=True))
    samples = example.samples_from_csv(
        write_csv(tmp_path / "charging.csv", excursion_into_charging()), "kW", "kW"
    )
    held = example.walk(schedule, samples, example.Knobs())
    assert held.mode_switches == 0
    assert held.slot_setpoint[3] == -1.4
    (exit_,) = held.exits
    # The policy holds the discharging mode's edge, -1.4 kW: nothing of the 3.3 kW is absorbed for the whole excursion.
    assert exit_.end is not None and exit_.end - exit_.start >= timedelta(seconds=110)
    assert exit_.energy == pytest.approx(
        3.3 * (exit_.end - exit_.start).total_seconds() / 3600, rel=0.05
    )


def test_a_mode_dwell_lets_the_device_switch_and_closes_the_residual(tmp_path):
    schedule = ReactionSchedule.from_schedule(served_schedule(with_rules=True))
    samples = example.samples_from_csv(
        write_csv(tmp_path / "charging.csv", excursion_into_charging()), "kW", "kW"
    )
    switched = example.walk(schedule, samples, example.Knobs(mode_dwell=30.0))
    # Into charging after 30 s, and back into discharging 30 s after the excursion ends.
    assert switched.mode_switches == 2
    assert switched.slot_setpoint[3] == -1.4
    first = switched.exits[0]
    assert first.end is not None and first.end - first.start <= timedelta(seconds=40)
    # With a margin wider than the excursion reaches into charging, the car goes idle instead,
    # and stays there: the way back into discharging is behind the same margin.
    idled = example.walk(
        schedule, samples, example.Knobs(mode_dwell=30.0, mode_margin=1.0)
    )
    assert idled.mode_switches == 1
    assert idled.slot_setpoint[3] == 0.0
