"""A band exit, second by second: the rule acts, the gate waits, one retrigger fires.

The companion of ``reaction_rules_saldering.py`` at the other timescale: seven minutes at the meter's
sampling rate, around two surprises. A car that must keep energy for the morning has a narrow discharge
band. First the kettle adds 3 kW for two minutes: the charger discharges to its band edge, 1.5 kW are
left over, and that is too little energy to be worth a new plan, so nothing fires. Then the oven adds
4.5 kW and stays: 3 kW are left over, the exit gate's dwell and energy thresholds are both met, and one
retrigger fires:

- the rule acts first, on every meter sample, and keeps acting while the gate waits;
- the gate (``flexmeasures_client.reaction.ExitGate``) needs the residual outside the band for at
  least the dwell, the charger's settle time plus one sample, and a minimum energy past the band,
  so a kettle passes and an oven does not;
- then one retrigger fires, with fresh meter data posted, and nothing else fires while the request
  is in flight, even when the surprise wobbles because the oven's element cycles;
- the new plan and its rules arrive half a minute later and replace the installed ones;
  the band moves with the plan and the residual closes.

The setpoints come from the real client code (``flexmeasures_client.reaction.react``) and the gate is
the real one; the surprises, the plan, the settle time and the planner's turnaround are scripted.
Timing values are placeholders, except the round trip, which takes 20 to 30 s in practice.

Run it with ``python examples/reaction_rules_retrigger.py --out <dir>`` to write
``reaction-rules-retrigger.png``; plotting needs matplotlib (``uv sync --group examples``).
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flexmeasures_client.reaction import ExitGate, ReactionRule, react

SECONDS = 7 * 60
SAMPLE_S = 1  # a DSMR 5 meter reports every second
SETTLE_S = (
    20  # the charger's settle time for an adjustment while running (a placeholder)
)
DWELL_S = SETTLE_S + SAMPLE_S
MIN_ENERGY_KWH = (
    0.1  # energy past the band before a retrigger: 100 Wh, a few cents at any tariff
)
TURNAROUND_S = 30  # from the retrigger to the new plan's arrival: a scheduling round trip takes 20 to 30 s
TOLERANCE_KW = 0.05
T0 = datetime(2026, 12, 10, 18, 0, tzinfo=timezone.utc)

MIN_POWER_KW = 1.4
KETTLE_AT_S, KETTLE_FOR_S, KETTLE_KW = 40, 120, 3.0
OVEN_AT_S, OVEN_KW = 210, 4.5
CYCLE_AT_S, CYCLE_FOR_S, CYCLE_KW = (
    340,
    8,
    -1.0,
)  # the oven's heating element cycles off for a moment

# The installed plan: the car covers the house in the expensive evening, but must keep energy for the morning,
# so its discharge band is narrow. The new plan trades that energy for cheap night charging and discharges harder.
OLD_PLAN_KW = -2.0
OLD_RULE = ReactionRule(
    reference=2.0,
    react=1,
    band=[(-3.5, -MIN_POWER_KW), (0.0, 0.0), (MIN_POWER_KW, 7.4)],
)
NEW_PLAN_KW = -5.0
NEW_RULE = ReactionRule(
    reference=6.5,
    react=1,
    band=[(-6.5, -MIN_POWER_KW), (0.0, 0.0), (MIN_POWER_KW, 7.4)],
)

REACTION = "#2a78d6"
NEUTRAL = "#52514e"
RESIDUAL = "#e34948"
BAND_FILL = "#eda100"
SURFACE = "#fcfcfb"


def surprise_at(second: int) -> float:
    """The house beyond its forecast: a kettle, then the oven, whose element cycles off for a moment."""
    delta = 0.0
    if KETTLE_AT_S <= second < KETTLE_AT_S + KETTLE_FOR_S:
        delta += KETTLE_KW
    if second >= OVEN_AT_S:
        delta += OVEN_KW
    if CYCLE_AT_S <= second < CYCLE_AT_S + CYCLE_FOR_S:
        delta += CYCLE_KW
    return delta


@dataclass
class Trace:
    """Every per-sample series of the seven minutes, in kW, plus the events in seconds."""

    second: list[int] = field(default_factory=list)
    surprise: list[float] = field(default_factory=list)
    plan: list[float] = field(default_factory=list)
    band_low: list[float] = field(default_factory=list)
    commanded: list[float] = field(default_factory=list)
    realized: list[float] = field(default_factory=list)
    residual: list[float] = field(default_factory=list)
    energy_outside: list[float] = field(default_factory=list)
    exits_at: list[int] = field(default_factory=list)
    retrigger_at: int | None = None
    new_plan_at: int | None = None
    retriggers: int = 0


def simulate() -> Trace:
    """Walk the seven minutes sample by sample, the way the controller's loop does."""
    trace = Trace()
    gate = ExitGate(
        dwell=timedelta(seconds=DWELL_S),
        min_energy=MIN_ENERGY_KWH,
        tolerance=TOLERANCE_KW,
    )
    rule, plan = OLD_RULE, OLD_PLAN_KW
    realized = plan
    plan_due: int | None = None
    was_outside = False
    for second in range(0, SECONDS, SAMPLE_S):
        # The new plan and its rules replace the installed ones when they arrive.
        if plan_due is not None and second == plan_due:
            rule, plan = NEW_RULE, NEW_PLAN_KW
            trace.new_plan_at = second
            plan_due = None
            gate.plan_arrived()
        delta = surprise_at(second)
        # The surprise is on the uncontrolled part, so it is the same whatever the charger does;
        # the new plan was made knowing the house, so against its reference the surprise is gone.
        surprise = (
            delta
            if rule is OLD_RULE
            else delta - (NEW_RULE.reference - OLD_RULE.reference)
        )
        commanded = react(rule, plan, surprise)
        # The charger lands on a command within its settle time (first-order lag).
        realized += (commanded - realized) * (1 - math.exp(-SAMPLE_S / (SETTLE_S / 3)))
        # What the chain leaves over, counting the device at its commanded power while it settles.
        residual = surprise + (commanded - plan)
        outside = abs(residual) > TOLERANCE_KW
        if outside and not was_outside:
            trace.exits_at.append(second)
        was_outside = outside
        if gate.observe(T0 + timedelta(seconds=second), residual):
            gate.request_sent()
            trace.retrigger_at = second
            trace.retriggers += 1
            plan_due = second + TURNAROUND_S
        trace.second.append(second)
        trace.surprise.append(delta)
        trace.plan.append(plan)
        trace.band_low.append(rule.band[0][0])
        trace.commanded.append(commanded)
        trace.realized.append(realized)
        trace.residual.append(residual)
        trace.energy_outside.append(gate.energy_outside * 1000)  # Wh
    return trace


def _style(ax, title: str, ylabel: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=11, color="#0b0b0b", pad=8)
    ax.set_ylabel(ylabel, color=NEUTRAL, fontsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#d6d6d2")
    ax.tick_params(colors=NEUTRAL, labelsize=8.5)
    ax.axhline(0, color="#d6d6d2", linewidth=1, zorder=0)
    ax.set_xlim(0, SECONDS)
    ax.set_xticks(range(0, SECONDS + 1, 30))
    ax.set_xticklabels([f"{t // 60}:{t % 60:02d}" for t in range(0, SECONDS + 1, 30)])


def _event_line(ax, second: int | None, color: str) -> None:
    if second is not None:
        ax.axvline(second, color=color, linewidth=1, linestyle=(0, (2, 2)), zorder=1)


def _segments(seconds: list[int], values: list[float]) -> list[tuple[int, int, float]]:
    """Runs of equal values as (start, end, value), for drawing a band that changes with the plan."""
    runs: list[tuple[int, int, float]] = []
    start, current = seconds[0], values[0]
    for second, value in zip(seconds[1:], values[1:]):
        if value != current:
            runs.append((start, second, current))
            start, current = second, value
    runs.append((start, seconds[-1] + SAMPLE_S, current))
    return runs


def plot(trace: Trace, path: Path) -> None:
    """Four panels: the surprise, the charger against its band, the residual, and the energy gate."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_house, ax_charger, ax_residual, ax_gate) = plt.subplots(
        4,
        1,
        figsize=(11, 11.5),
        sharex=True,
        facecolor=SURFACE,
        gridspec_kw={"hspace": 0.55, "height_ratios": [1, 1.3, 1.1, 1]},
    )
    retrigger_at, new_plan_at = trace.retrigger_at, trace.new_plan_at
    kettle_exit = trace.exits_at[0] if trace.exits_at else None
    oven_exit = trace.exits_at[1] if len(trace.exits_at) > 1 else None

    # Panel 1: the surprise.
    _style(
        ax_house,
        "The surprise: the house beyond its forecast",
        "kW, extra import positive",
    )
    ax_house.plot(trace.second, trace.surprise, color=NEUTRAL, linewidth=1.8)
    ax_house.text(
        KETTLE_AT_S + 4,
        KETTLE_KW + 0.35,
        "the kettle: +3 kW for two minutes",
        color=NEUTRAL,
        fontsize=9,
    )
    ax_house.text(
        OVEN_AT_S + 4,
        OVEN_KW + 0.35,
        "the oven: +4.5 kW, and it stays",
        color=NEUTRAL,
        fontsize=9,
    )
    ax_house.text(
        CYCLE_AT_S + CYCLE_FOR_S + 3,
        OVEN_KW + CYCLE_KW - 0.1,
        "the oven's element cycles off",
        color=NEUTRAL,
        fontsize=9,
        va="top",
    )
    ax_house.set_ylim(-0.6, 6.4)

    # Panel 2: the charger against its band.
    _style(
        ax_charger,
        "The charger: the rule acts at once, the band stops it, the new plan widens the band",
        "kW, charging positive",
    )
    for start, end, low in _segments(trace.second, trace.band_low):
        ax_charger.axhspan(
            low,
            -MIN_POWER_KW,
            xmin=start / SECONDS,
            xmax=end / SECONDS,
            color=BAND_FILL,
            alpha=0.12,
            lw=0,
            zorder=0,
        )
    ax_charger.plot(
        trace.second,
        trace.plan,
        color=NEUTRAL,
        linewidth=3.4,
        linestyle=(0, (3, 2)),
        alpha=0.55,
        label="plan p",
    )
    ax_charger.plot(
        trace.second,
        trace.commanded,
        color=REACTION,
        linewidth=1.6,
        label="setpoint from the rule",
    )
    ax_charger.plot(
        trace.second,
        trace.realized,
        color=REACTION,
        linewidth=1.2,
        linestyle=(0, (1, 2)),
        label="realized (settles in 20 s)",
    )
    ax_charger.text(
        KETTLE_AT_S + 40,
        OLD_RULE.band[0][0] - 0.3,
        "discharge band of the installed plan:\nthe car keeps energy for the morning",
        color="#8a5d00",
        fontsize=8.5,
        va="top",
    )
    if new_plan_at is not None:
        ax_charger.text(
            new_plan_at + 5,
            NEW_RULE.band[0][0] + 0.25,
            "new plan, wider band",
            color="#8a5d00",
            fontsize=8.5,
            va="bottom",
        )
    ax_charger.set_ylim(-7.6, 1.2)
    ax_charger.legend(loc="upper right", frameon=False, fontsize=9, ncol=3)

    # Panel 3: the residual.
    _style(ax_residual, "The residual: what no device absorbed", "kW")
    ax_residual.plot(trace.second, trace.residual, color=RESIDUAL, linewidth=1.8)
    if kettle_exit is not None:
        ax_residual.text(
            kettle_exit + 4,
            1.75,
            "1.5 kW left over for two minutes: the gate lets it pass",
            color=NEUTRAL,
            fontsize=8.5,
            va="bottom",
        )
    if oven_exit is not None and retrigger_at is not None:
        ax_residual.axvspan(oven_exit, retrigger_at, color=NEUTRAL, alpha=0.10, lw=0)
        ax_residual.text(
            oven_exit + 4,
            3.3,
            "3 kW left over: the gate counts",
            color=NEUTRAL,
            fontsize=8.5,
            va="bottom",
        )
    if retrigger_at is not None and new_plan_at is not None:
        ax_residual.axvspan(retrigger_at, new_plan_at, color=REACTION, alpha=0.07, lw=0)
        ax_residual.annotate(
            "retrigger: one request,\nfresh meter data posted",
            xy=(retrigger_at, 3.05),
            xytext=(retrigger_at - 6, 4.1),
            ha="right",
            va="bottom",
            fontsize=9,
            color="#0b0b0b",
            arrowprops={"arrowstyle": "->", "color": "#0b0b0b", "lw": 0.9},
        )
        ax_residual.text(
            (retrigger_at + new_plan_at) / 2,
            0.35,
            "in flight:\nno second\nrequest",
            ha="center",
            va="bottom",
            fontsize=8,
            color=REACTION,
        )
        ax_residual.annotate(
            "new plan and rules arrive:\nthe residual closes",
            xy=(new_plan_at, 0.05),
            xytext=(new_plan_at + 6, 1.8),
            ha="left",
            fontsize=9,
            color="#0b0b0b",
            arrowprops={"arrowstyle": "->", "color": "#0b0b0b", "lw": 0.9},
        )
    ax_residual.set_ylim(-0.6, 5.6)

    # Panel 4: the energy gate.
    _style(
        ax_gate,
        "The exit gate: energy past the band since the exit, and when it is worth a new plan",
        "Wh",
    )
    ax_gate.axhline(
        MIN_ENERGY_KWH * 1000, color=RESIDUAL, linewidth=1, linestyle=(0, (4, 3))
    )
    ax_gate.text(
        4,
        MIN_ENERGY_KWH * 1000 + 4,
        f"threshold: {MIN_ENERGY_KWH * 1000:.0f} Wh past the band",
        color=RESIDUAL,
        fontsize=8.5,
        va="bottom",
    )
    ax_gate.fill_between(
        trace.second, 0, trace.energy_outside, color=NEUTRAL, alpha=0.18, lw=0
    )
    ax_gate.plot(trace.second, trace.energy_outside, color=NEUTRAL, linewidth=1.6)
    if kettle_exit is not None:
        ax_gate.text(
            kettle_exit + 4,
            62,
            "the kettle: 50 Wh, then back\ninside the band: the count resets",
            color=NEUTRAL,
            fontsize=8.5,
            va="bottom",
        )
    if oven_exit is not None and retrigger_at is not None:
        ax_gate.axvspan(oven_exit, oven_exit + DWELL_S, color=NEUTRAL, alpha=0.10, lw=0)
        ax_gate.text(
            oven_exit - 4,
            60,
            "dwell: settle time\n+ one sample",
            ha="right",
            va="bottom",
            fontsize=8.5,
            color=NEUTRAL,
        )
        ax_gate.annotate(
            "100 Wh reached: retrigger",
            xy=(retrigger_at, MIN_ENERGY_KWH * 1000),
            xytext=(retrigger_at - 6, 140),
            ha="right",
            fontsize=9,
            color="#0b0b0b",
            arrowprops={"arrowstyle": "->", "color": "#0b0b0b", "lw": 0.9},
        )
    ax_gate.set_ylim(0, 175)
    ax_gate.set_xlabel("minutes:seconds", color=NEUTRAL, fontsize=9)
    for ax in (ax_charger, ax_residual, ax_gate):
        for second in (kettle_exit, oven_exit, retrigger_at, new_plan_at):
            _event_line(ax, second, NEUTRAL)

    fig.suptitle(
        "A band exit, second by second: the rule acts, the gate lets the kettle pass, the oven fires one retrigger",
        x=0.02,
        ha="left",
        fontsize=13,
        color="#0b0b0b",
    )
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out", type=Path, default=Path("."), help="directory for the picture"
    )
    parser.add_argument("--no-plot", action="store_true", help="only print the events")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    trace = simulate()
    print(
        f"Band exits at {trace.exits_at} s, retrigger at {trace.retrigger_at} s "
        f"({trace.retriggers} request), new plan at {trace.new_plan_at} s; "
        f"residual at the end: {trace.residual[-1]:.2f} kW"
    )
    if args.no_plot:
        return 0
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print(
            "matplotlib is not installed; install the 'examples' dependency group to draw the picture.",
            file=sys.stderr,
        )
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    plot(trace, args.out / "reaction-rules-retrigger.png")
    print(f"Picture written to {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
