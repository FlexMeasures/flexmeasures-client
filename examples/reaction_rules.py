"""Trigger a schedule with reaction rules, parse them and walk a day of samples through the local loop.

The script opts in to reaction rules with ``react: true`` on the device's flex-model,
fetches the schedule, parses its ``reaction`` array and walks the day sample by sample,
the way a home controller's loop does, with the real pieces of ``flexmeasures_client.reaction``:
the surprise at the meter goes through the :class:`Smoother`, the rule gives the setpoint,
rounded to the device's operation modes by the :class:`ModePolicy` (which holds the plan's mode unless ``--mode-dwell`` lets it switch),
what the device could not absorb is the residual, and the :class:`ExitGate` says when a retrigger is due.
A retrigger in this probe only marks the moment: no new schedule is requested,
and after ``--turnaround`` seconds the gate is told that the plan arrived and the same rules continue.
See FlexMeasures/flexmeasures-client#297 for the contract.

The surprise is scripted by default (a cooking step, a sunny spell and a wobble, one sample per ``--sample`` seconds),
or replayed from a recording with ``--surprise-csv``: a CSV with a header and two columns,
``time`` (ISO 8601 with an offset) and ``surprise`` (in ``--surprise-unit``), walked in time order.
The loop's knobs are options, with the defaults of the classes.

It prints the setpoint per slot, every band exit with the energy past the band, and a summary,
and doubles as a smoke test against a FlexMeasures server that serves rules, through its exit code:

- 0: the server served rules and every setpoint respected them;
- 1: the trigger or the fetch failed, a setpoint broke an invariant, or the recording could not be read;
- 2: the server served the schedule without rules.

Usage::

    python examples/reaction_rules.py --host localhost:5000 --no-ssl \\
        --email user@example.com --password secret \\
        --sensor 8 --price-sensor 7 \\
        --surprise-csv surprise.csv --window 5 --median 3 --dwell 21 --min-energy 0.1

Every connection option can be given through the environment instead, as ``FLEXMEASURES_HOST``, ``FLEXMEASURES_EMAIL``,
``FLEXMEASURES_PASSWORD``, ``FLEXMEASURES_SENSOR`` and ``FLEXMEASURES_PRICE_SENSOR``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import os
import sys
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd

from flexmeasures_client import FlexMeasuresClient
from flexmeasures_client.client import convert_units
from flexmeasures_client.reaction import (
    ExitGate,
    InvalidReactionError,
    ModePolicy,
    ReactionSchedule,
    Smoother,
    react,
)

NO_RULES = 2

_GATE_DEFAULTS = {f.name: f.default for f in fields(ExitGate)}
DEFAULT_WINDOW_S = Smoother().window.total_seconds()
DEFAULT_MEDIAN = Smoother().median_samples
# The gate has no default dwell, as the controller measures it: a 20 s settle time plus one sample, as in the retrigger example.
DEFAULT_DWELL_S = 21.0
DEFAULT_MIN_ENERGY = _GATE_DEFAULTS["min_energy"]
DEFAULT_TOLERANCE = _GATE_DEFAULTS["tolerance"]
DEFAULT_TURNAROUND_S = 30.0
DEFAULT_SAMPLE_S = 1.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the connection, the sensors, the scheduling window, the surprise and the loop's knobs."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    env = os.environ.get
    parser.add_argument("--host", default=env("FLEXMEASURES_HOST", "localhost:5000"))
    parser.add_argument("--no-ssl", action="store_true", help="connect over http")
    parser.add_argument("--email", default=env("FLEXMEASURES_EMAIL"))
    parser.add_argument("--password", default=env("FLEXMEASURES_PASSWORD"))
    parser.add_argument(
        "--sensor",
        type=int,
        default=env("FLEXMEASURES_SENSOR"),
        help="the power sensor of the device to schedule",
    )
    parser.add_argument(
        "--price-sensor",
        type=int,
        default=env("FLEXMEASURES_PRICE_SENSOR"),
        help="the consumption price sensor, optional",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="start of the scheduling window, default: the coming midnight (UTC)",
    )
    parser.add_argument("--duration", default="P1D")
    parser.add_argument("--unit", default="kW", help="the unit of the schedule")
    parser.add_argument(
        "--power-capacity", default="7.4 kW", help="the device's power capacity"
    )
    parser.add_argument("--soc-at-start", type=float, default=20.0)
    parser.add_argument("--soc-min", type=float, default=10.0)
    parser.add_argument("--soc-max", type=float, default=60.0)

    surprise = parser.add_argument_group("the surprise")
    surprise.add_argument(
        "--surprise-csv",
        type=Path,
        default=None,
        help="replay a recorded surprise: a CSV with a header and the columns time (ISO 8601 with offset) and surprise",
    )
    surprise.add_argument(
        "--surprise-unit",
        default="kW",
        help="the unit of the recorded surprise, converted to the schedule's unit (default: kW)",
    )
    surprise.add_argument(
        "--sample",
        type=float,
        default=DEFAULT_SAMPLE_S,
        help=f"seconds between the samples of the scripted surprise (default: {DEFAULT_SAMPLE_S:g})",
    )

    knobs = parser.add_argument_group("the loop's knobs")
    knobs.add_argument(
        "--window",
        type=float,
        default=DEFAULT_WINDOW_S,
        help=f"the smoother's mean, in seconds (default: {DEFAULT_WINDOW_S:g})",
    )
    knobs.add_argument(
        "--median",
        type=int,
        default=DEFAULT_MEDIAN,
        help=f"how many of the latest samples the smoother's median spans, 1 disables it (default: {DEFAULT_MEDIAN})",
    )
    knobs.add_argument(
        "--dwell",
        type=float,
        default=DEFAULT_DWELL_S,
        help=f"the gate's dwell, in seconds: the slowest device's settle time plus one sample (default: {DEFAULT_DWELL_S:g})",
    )
    knobs.add_argument(
        "--min-energy",
        type=float,
        default=DEFAULT_MIN_ENERGY,
        help=f"energy past the band before a retrigger, in the schedule's unit times hours, kWh for kW (default: {DEFAULT_MIN_ENERGY:g})",
    )
    knobs.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"residuals within this magnitude count as inside the band, in the schedule's unit (default: {DEFAULT_TOLERANCE:g})",
    )
    knobs.add_argument(
        "--mode-dwell",
        type=float,
        default=None,
        help="seconds a raw setpoint must lie in another operation mode before the device switches to it (default: never within a slot)",
    )
    knobs.add_argument(
        "--mode-margin",
        type=float,
        default=0.0,
        help="how far past the hole a raw setpoint must lie in another mode to count, in the schedule's unit (default: 0)",
    )
    knobs.add_argument(
        "--turnaround",
        type=float,
        default=DEFAULT_TURNAROUND_S,
        help=f"seconds from a retrigger to the plan's arrival, after which the gate is reset (default: {DEFAULT_TURNAROUND_S:g})",
    )

    args = parser.parse_args(argv)
    for name in ("email", "password", "sensor"):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required")
    for name in ("sample", "window", "dwell", "turnaround"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name} must be positive")
    if args.mode_dwell is not None and args.mode_dwell <= 0:
        parser.error("--mode-dwell must be positive")
    if args.mode_margin < 0:
        parser.error("--mode-margin must not be negative")
    if args.median < 1:
        parser.error("--median must be at least 1")
    if args.start is None:
        tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
        args.start = datetime.combine(tomorrow, datetime.min.time(), timezone.utc)
    return args


def flex_fields(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the flex-model that opts in to reaction rules for one device, and the flex-context."""
    flex_model = {
        "soc-unit": "kWh",
        "soc-at-start": args.soc_at_start,
        "soc-min": args.soc_min,
        "soc-max": args.soc_max,
        "power-capacity": args.power_capacity,
        "react": True,
    }
    flex_context: dict[str, Any] = {}
    if args.price_sensor is not None:
        flex_context["consumption-price"] = {"sensor": args.price_sensor}
    return flex_model, flex_context


def scripted_surprise(slots: int, amplitude: float = 1.0) -> list[float]:
    """Return a surprise on the uncontrolled part of the meter per slot, in the schedule's unit.

    The household behaves as forecast at first, then someone cooks (more import than forecast),
    then the sun comes out (less import than forecast), with a small wobble throughout.
    """
    surprise = []
    for index in range(slots):
        fraction = index / max(slots, 1)
        value = 0.1 * amplitude * math.sin(2 * math.pi * 4 * fraction)
        if 0.3 <= fraction < 0.4:
            value += 1.5 * amplitude
        if 0.5 <= fraction < 0.65:
            value -= 2.0 * amplitude
        surprise.append(value)
    return surprise


def scripted_samples(
    schedule: ReactionSchedule, sample_seconds: float
) -> Iterator[tuple[datetime, float]]:
    """Yield the scripted surprise once per sample over the schedule, constant within a slot."""
    per_slot = scripted_surprise(len(schedule))
    step = timedelta(seconds=sample_seconds)
    when = schedule.start
    while when < schedule.end:
        yield when, per_slot[schedule.slot(when)]
        when += step


def samples_from_csv(
    path: Path, surprise_unit: str, schedule_unit: str
) -> list[tuple[datetime, float]]:
    """Read a recorded surprise series and return it in the schedule's unit, in time order.

    :raises ValueError: When the file lacks the columns, a time has no offset, or a value is not a number.
    """
    samples = []
    with path.open(newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None or not {"time", "surprise"} <= set(
            reader.fieldnames
        ):
            raise ValueError(
                f"{path} needs a header with the columns time and surprise, got {reader.fieldnames}."
            )
        for number, row in enumerate(reader, start=2):
            when = pd.Timestamp(row["time"]).to_pydatetime()
            if when.tzinfo is None:
                raise ValueError(f"{path} line {number}: the time needs an offset.")
            try:
                value = float(row["surprise"])
            except ValueError as error:
                raise ValueError(
                    f"{path} line {number}: surprise {row['surprise']!r} is not a number."
                ) from error
            samples.append((when, value))
    samples.sort(key=lambda sample: sample[0])
    values = convert_units(
        [value for _, value in samples], surprise_unit, schedule_unit
    )
    return [(when, value) for (when, _), value in zip(samples, values)]


@dataclass
class Knobs:
    """The settings of the local loop, as the options give them."""

    window: float = DEFAULT_WINDOW_S
    median: int = DEFAULT_MEDIAN
    dwell: float = DEFAULT_DWELL_S
    min_energy: float = DEFAULT_MIN_ENERGY
    tolerance: float = DEFAULT_TOLERANCE
    turnaround: float = DEFAULT_TURNAROUND_S
    mode_dwell: float | None = None
    mode_margin: float = 0.0

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Knobs:
        return cls(
            window=args.window,
            median=args.median,
            dwell=args.dwell,
            min_energy=args.min_energy,
            tolerance=args.tolerance,
            turnaround=args.turnaround,
            mode_dwell=args.mode_dwell,
            mode_margin=args.mode_margin,
        )


@dataclass
class Exit:
    """One excursion of the residual outside the band."""

    start: datetime
    end: datetime | None = None
    energy: float = 0.0
    retrigger_at: datetime | None = None


@dataclass
class Walk:
    """What the walk through the samples found."""

    slot_surprise: list[float | None]
    slot_setpoint: list[float | None]
    exits: list[Exit] = field(default_factory=list)
    retriggers: list[datetime] = field(default_factory=list)
    samples: int = 0
    skipped: int = 0
    mode_switches: int = 0
    problems: list[str] = field(default_factory=list)


def walk(
    schedule: ReactionSchedule,
    samples: Iterable[tuple[datetime, float]],
    knobs: Knobs,
) -> Walk:
    """Walk the samples through the smoother, the rule and the gate; keep the per-slot and per-exit results.

    With one reacting device, the residual is the surprise plus what the setpoint moved away from the plan.
    The mode policy starts every slot in the mode that contains the plan and rounds the rule's answer relative to it.
    A retrigger only marks the moment and tells the gate a request went out;
    after the turnaround the gate is told the plan arrived, and the same rules continue.
    """
    assert schedule.rules is not None
    result = Walk(
        slot_surprise=[None] * len(schedule), slot_setpoint=[None] * len(schedule)
    )
    for index, (p, rule) in enumerate(zip(schedule.values, schedule.rules)):
        if react(rule, p, 0.0) != p:
            result.problems.append(
                f"slot {index}: a zero surprise does not return the plan"
            )
        if rule.react != 0 and rule.band and not rule.contains(p):
            result.problems.append(
                f"slot {index}: the plan {p} is outside its own band"
            )
    smoother = Smoother(
        window=timedelta(seconds=knobs.window), median_samples=knobs.median
    )
    gate = ExitGate(
        dwell=timedelta(seconds=knobs.dwell),
        min_energy=knobs.min_energy,
        tolerance=knobs.tolerance,
    )
    policy = ModePolicy(
        dwell=(
            None if knobs.mode_dwell is None else timedelta(seconds=knobs.mode_dwell)
        ),
        margin=knobs.mode_margin,
    )
    slot_index: int | None = None
    plan_due: datetime | None = None
    current: Exit | None = None
    last_seen: datetime | None = None
    outside_reported: set[int] = set()
    for when, raw in samples:
        try:
            index = schedule.slot(when)
        except IndexError:
            result.skipped += 1
            continue
        result.samples += 1
        if plan_due is not None and when >= plan_due:
            gate.plan_arrived()
            plan_due = None
        p, rule = schedule.values[index], schedule.rules[index]
        if index != slot_index:
            policy.start_slot(p, rule.band)
            slot_index = index
        surprise = smoother.update(when, raw)
        setpoint = (
            p if rule.react == 0 else policy.observe(when, p - rule.react * surprise)
        )
        if rule.band and not rule.contains(setpoint) and index not in outside_reported:
            outside_reported.add(index)
            result.problems.append(
                f"slot {index}: setpoint {setpoint} is outside the band"
            )
        residual = surprise + (setpoint - p)
        outside = abs(residual) > knobs.tolerance
        if outside:
            if current is None:
                current = Exit(start=when)
                result.exits.append(current)
            elif last_seen is not None:
                current.energy += (
                    abs(residual) * (when - last_seen).total_seconds() / 3600
                )
        elif current is not None:
            current.end = when
            current = None
        if gate.observe(when, residual):
            gate.request_sent()
            result.retriggers.append(when)
            if current is not None:
                current.retrigger_at = when
            plan_due = when + timedelta(seconds=knobs.turnaround)
        result.slot_surprise[index] = surprise
        result.slot_setpoint[index] = setpoint
        last_seen = when
    result.mode_switches = policy.switches
    return result


def _number(value: float | None, width: int, digits: int = 3) -> str:
    return f"{'-':>{width}}" if value is None else f"{value:>{width}.{digits}f}"


def print_slots(schedule: ReactionSchedule, result: Walk) -> None:
    """Print the plan, the rule and the last sample's surprise and setpoint per slot."""
    assert schedule.rules is not None
    print(
        f"{'slot start':<25} {'plan':>8} {'reference':>10} {'react':>5} {'surprise':>9} {'setpoint':>9}  band [{schedule.unit}]"
    )
    for index, (slot_start, p, rule) in enumerate(schedule):
        assert rule is not None
        band = ", ".join(f"[{low:g}, {high:g}]" for low, high in rule.band)
        print(
            f"{slot_start.isoformat():<25} {p:>8.3f} {rule.reference:>10.3f} {rule.react:>5.2g}"
            f" {_number(result.slot_surprise[index], 9)} {_number(result.slot_setpoint[index], 9)}  {band}"
        )


def print_summary(result: Walk, unit: str, knobs: Knobs) -> None:
    """Print every band exit with its energy past the band, and the counts."""
    if result.exits:
        print()
        print(
            f"{'exit':<25} {'end':<25} {'duration':>10} {'energy':>10} {'retrigger':<25}"
        )
        for exit_ in result.exits:
            end = exit_.end.isoformat() if exit_.end is not None else "(still out)"
            duration = str(exit_.end - exit_.start) if exit_.end is not None else "-"
            retrigger = exit_.retrigger_at.isoformat() if exit_.retrigger_at else "-"
            print(
                f"{exit_.start.isoformat():<25} {end:<25} {duration:>10} {exit_.energy:>10.3f} {retrigger:<25}"
            )
    energy = sum(exit_.energy for exit_ in result.exits)
    print()
    print(
        f"Samples: {result.samples} walked, {result.skipped} outside the schedule skipped."
        f" Band exits: {len(result.exits)}, retriggers: {len(result.retriggers)},"
        f" mode switches: {result.mode_switches},"
        f" energy past the band: {energy:.3f} {unit}h."
    )
    print(
        f"Knobs: window {knobs.window:g} s, median {knobs.median}, dwell {knobs.dwell:g} s,"
        f" min-energy {knobs.min_energy:g} {unit}h, tolerance {knobs.tolerance:g} {unit}, turnaround {knobs.turnaround:g} s,"
        f" mode-dwell {'hold' if knobs.mode_dwell is None else f'{knobs.mode_dwell:g} s'}, mode-margin {knobs.mode_margin:g} {unit}."
    )


async def run(client: FlexMeasuresClient, args: argparse.Namespace) -> int:
    """Trigger, fetch, parse and walk; return the exit code."""
    flex_model, flex_context = flex_fields(args)
    try:
        raw = await client.trigger_and_get_schedule(
            sensor_id=args.sensor,
            start=args.start,
            duration=args.duration,
            flex_model=flex_model,
            flex_context=flex_context,
            unit=args.unit,
        )
    except (ValueError, ConnectionError) as error:
        print(f"Scheduling failed: {error}", file=sys.stderr)
        return 1
    if not isinstance(raw, dict):
        print(f"Expected one schedule, but got {type(raw).__name__}", file=sys.stderr)
        return 1
    try:
        schedule = ReactionSchedule.from_schedule(raw)
    except InvalidReactionError as error:
        print(f"The schedule's reaction is invalid: {error}", file=sys.stderr)
        return 1
    print(
        f"Schedule of {len(schedule)} slots of {schedule.resolution} from {schedule.start.isoformat()} in {schedule.unit}."
    )
    if not schedule.has_rules:
        print(
            "The server returned NO reaction rules with this schedule (no 'reaction' array)."
            " The server may not serve rules yet, or the trigger did not opt in.",
            file=sys.stderr,
        )
        return NO_RULES
    samples: Iterable[tuple[datetime, float]]
    if args.surprise_csv is not None:
        try:
            samples = samples_from_csv(
                args.surprise_csv, args.surprise_unit, schedule.unit
            )
        except (OSError, ValueError, NotImplementedError) as error:
            print(f"The recorded surprise could not be read: {error}", file=sys.stderr)
            return 1
        print(f"Replaying {len(samples)} recorded samples from {args.surprise_csv}.")
    else:
        samples = scripted_samples(schedule, args.sample)
    knobs = Knobs.from_args(args)
    result = walk(schedule, samples, knobs)
    print_slots(schedule, result)
    print_summary(result, schedule.unit, knobs)
    if result.problems:
        print("\n".join(result.problems), file=sys.stderr)
        return 1
    reacting = sum(1 for rule in schedule.rules if rule.react != 0)
    print(
        f"All setpoints respect the rules; {reacting} of {len(schedule)} slots absorb the surprise."
    )
    return 0


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = FlexMeasuresClient(
        host=args.host,
        ssl=not args.no_ssl,
        email=args.email,
        password=args.password,
    )
    try:
        return await run(client, args)
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
