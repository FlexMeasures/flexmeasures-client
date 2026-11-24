"""Trigger a schedule with reaction rules, parse them and walk a day of slots with a scripted surprise.

The script opts in to reaction rules with ``react: true`` on the device's flex-model,
fetches the schedule, parses its ``reaction`` array
and prints the setpoint the device would get in every slot, given a scripted surprise at the meter.
See FlexMeasures/flexmeasures-client#297 for the contract.

It doubles as a smoke test against a FlexMeasures server that serves rules, through its exit code:

- 0: the server served rules and every setpoint respected them;
- 1: the trigger or the fetch failed, or a setpoint broke an invariant;
- 2: the server served the schedule without rules.

Usage::

    python examples/reaction_rules.py --host localhost:5000 --no-ssl \\
        --email user@example.com --password secret \\
        --sensor 8 --price-sensor 7

Every option can be given through the environment instead, as ``FLEXMEASURES_HOST``, ``FLEXMEASURES_EMAIL``,
``FLEXMEASURES_PASSWORD``, ``FLEXMEASURES_SENSOR`` and ``FLEXMEASURES_PRICE_SENSOR``.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from flexmeasures_client import FlexMeasuresClient
from flexmeasures_client.reaction import InvalidReactionError, ReactionSchedule, react

NO_RULES = 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the connection, the sensors and the scheduling window from the command line or the environment."""
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
    parser.add_argument("--unit", default="kW")
    parser.add_argument(
        "--power-capacity", default="7.4 kW", help="the device's power capacity"
    )
    parser.add_argument("--soc-at-start", type=float, default=20.0)
    parser.add_argument("--soc-min", type=float, default=10.0)
    parser.add_argument("--soc-max", type=float, default=60.0)
    args = parser.parse_args(argv)
    for name in ("email", "password", "sensor"):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required")
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


def format_edge(edge: float | None, unbounded: str) -> str:
    """Return a band edge for printing, naming an unbounded side."""
    return unbounded if edge is None else f"{edge:g}"


def walk(schedule: ReactionSchedule, surprise: list[float]) -> list[str]:
    """Print the setpoint per slot, and return the invariants that were broken.

    With one reacting device, the residual handed to it is the surprise itself.
    """
    assert schedule.rules is not None
    problems = []
    print(
        f"{'slot start':<25} {'plan':>8} {'reference':>10} {'react':>5} {'surprise':>9} {'setpoint':>9}  band [{schedule.unit}]"
    )
    for index, (slot_start, p, rule) in enumerate(schedule):
        assert rule is not None
        delta = surprise[index]
        setpoint = react(rule, p, delta)
        band = ", ".join(
            f"[{format_edge(low, '-inf')}, {format_edge(high, 'inf')}]"
            for low, high in rule.band
        )
        print(
            f"{slot_start.isoformat():<25} {p:>8.3f} {rule.reference:>10.3f} {rule.react:>5.2g} {delta:>9.3f} {setpoint:>9.3f}  {band}"
        )
        if react(rule, p, 0.0) != p:
            problems.append(f"slot {index}: a zero surprise does not return the plan")
        if rule.band and not rule.contains(setpoint):
            problems.append(f"slot {index}: setpoint {setpoint} is outside the band")
        if rule.react != 0 and rule.band and not rule.contains(p):
            problems.append(f"slot {index}: the plan {p} is outside its own band")
    return problems


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
    problems = walk(schedule, scripted_surprise(len(schedule)))
    if problems:
        print("\n".join(problems), file=sys.stderr)
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
