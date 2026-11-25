"""A band exit, second by second: the rule acts, the dwell waits, one retrigger fires.

The companion of ``reaction_rules_saldering.py`` at the other timescale: eight minutes at the meter's
sampling rate, around a surprise the charger cannot absorb within its band. A car that must keep energy
for the morning has a narrow discharge band, so when the oven adds 4.5 kW the charger discharges to its
band edge and 3 kW are left over. That residual is what asks for a new plan:

- the rule acts first, on every meter sample, and keeps acting while the dwell runs;
- the dwell is the charger's settle time plus one sample, so a residual only counts as a band exit
  once the device has had the chance to land;
- then one retrigger fires, with fresh meter data posted, and nothing else fires while the request
  is in flight, even when the surprise flaps;
- the new plan and its rules arrive a couple of minutes later and replace the installed ones;
  the band moves with the plan and the residual closes.

The setpoints come from the real client code (``flexmeasures_client.reaction.react``); the surprise,
the plan, the settle time and the planner's turnaround are scripted. Timing values are placeholders.

Run it with ``python examples/reaction_rules_retrigger.py --out <dir>`` to write
``reaction-rules-retrigger.png``; plotting needs matplotlib (``uv sync --group examples``).
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from flexmeasures_client.reaction import ReactionRule, react

SECONDS = 8 * 60
SAMPLE_S = 1  # a DSMR 5 meter reports every second
SETTLE_S = (
    20  # the charger's settle time for an adjustment while running (a placeholder)
)
DWELL_S = SETTLE_S + SAMPLE_S
TURNAROUND_S = 150  # from the retrigger to the new plan's arrival (a placeholder)
TOLERANCE_KW = 0.05

MIN_POWER_KW = 1.4
SURPRISE_AT_S = 120
SURPRISE_KW = 4.5
FLAP_AT_S, FLAP_FOR_S, FLAP_KW = 200, 15, -1.0

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
    """The house beyond its forecast: the oven, with a short flap while the request is in flight."""
    if second < SURPRISE_AT_S:
        return 0.0
    delta = SURPRISE_KW
    if FLAP_AT_S <= second < FLAP_AT_S + FLAP_FOR_S:
        delta += FLAP_KW
    return delta


@dataclass
class Trace:
    """Every per-sample series of the eight minutes, in kW, plus the events in seconds."""

    second: list[int] = field(default_factory=list)
    surprise: list[float] = field(default_factory=list)
    plan: list[float] = field(default_factory=list)
    band_low: list[float] = field(default_factory=list)
    commanded: list[float] = field(default_factory=list)
    realized: list[float] = field(default_factory=list)
    residual: list[float] = field(default_factory=list)
    band_exit_at: int | None = None
    retrigger_at: int | None = None
    new_plan_at: int | None = None
    retriggers: int = 0


def simulate() -> Trace:
    """Walk the eight minutes sample by sample, the way the controller's loop does."""
    trace = Trace()
    rule, plan = OLD_RULE, OLD_PLAN_KW
    realized = plan
    exited_since: int | None = None
    in_flight_until: int | None = None
    for second in range(0, SECONDS, SAMPLE_S):
        # The new plan and its rules replace the installed ones when they arrive.
        if in_flight_until is not None and second == in_flight_until:
            rule, plan = NEW_RULE, NEW_PLAN_KW
            trace.new_plan_at = second
            in_flight_until = None
            exited_since = None
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
        if outside and exited_since is None:
            exited_since = second
            if trace.band_exit_at is None:
                trace.band_exit_at = second
        if not outside:
            exited_since = None
        # One retrigger per exit, after the dwell, and at most one request in flight.
        if (
            outside
            and exited_since is not None
            and second - exited_since >= DWELL_S
            and in_flight_until is None
            and trace.retrigger_at is None
        ):
            trace.retrigger_at = second
            trace.retriggers += 1
            in_flight_until = second + TURNAROUND_S
        trace.second.append(second)
        trace.surprise.append(delta)
        trace.plan.append(plan)
        trace.band_low.append(rule.band[0][0])
        trace.commanded.append(commanded)
        trace.realized.append(realized)
        trace.residual.append(residual)
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
    ax.set_xticks(range(0, SECONDS + 1, 60))
    ax.set_xticklabels([f"{m}" for m in range(0, SECONDS // 60 + 1)])


def _event_line(ax, second: int | None, color: str) -> None:
    if second is not None:
        ax.axvline(second, color=color, linewidth=1, linestyle=(0, (2, 2)), zorder=1)


def plot(trace: Trace, path: Path) -> None:
    """Three panels: the surprise, the charger against its band, and the residual with its events."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_house, ax_charger, ax_residual) = plt.subplots(
        3,
        1,
        figsize=(11, 9),
        sharex=True,
        facecolor=SURFACE,
        gridspec_kw={"hspace": 0.5, "height_ratios": [1, 1.3, 1.3]},
    )
    exit_at, retrigger_at, new_plan_at = (
        trace.band_exit_at,
        trace.retrigger_at,
        trace.new_plan_at,
    )

    # Panel 1: the surprise.
    _style(
        ax_house,
        "The surprise: the house beyond its forecast",
        "kW, extra import positive",
    )
    ax_house.plot(trace.second, trace.surprise, color=NEUTRAL, linewidth=1.8)
    ax_house.text(
        SURPRISE_AT_S + 8,
        SURPRISE_KW + 0.35,
        "the oven: +4.5 kW",
        color=NEUTRAL,
        fontsize=9,
    )
    ax_house.text(
        FLAP_AT_S + FLAP_FOR_S + 6,
        SURPRISE_KW + FLAP_KW - 0.1,
        "a flap",
        color=NEUTRAL,
        fontsize=9,
        va="top",
    )
    ax_house.set_ylim(-0.6, 6.2)

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
        8,
        OLD_PLAN_KW - 0.35,
        "discharge band of the installed plan:\nthe car keeps energy for the morning",
        color="#8a5d00",
        fontsize=8.5,
        va="top",
    )
    if new_plan_at is not None:
        ax_charger.text(
            new_plan_at + 10,
            NEW_RULE.band[0][0] + 0.25,
            "new plan, wider band",
            color="#8a5d00",
            fontsize=8.5,
            va="bottom",
        )
    ax_charger.set_ylim(-7.6, 1.2)
    ax_charger.legend(loc="upper right", frameon=False, fontsize=9, ncol=3)
    for second in (exit_at, retrigger_at, new_plan_at):
        _event_line(ax_charger, second, NEUTRAL)

    # Panel 3: the residual and the events.
    _style(ax_residual, "The residual: what no device absorbed", "kW")
    ax_residual.plot(
        trace.second,
        trace.residual,
        color=RESIDUAL,
        linewidth=1.8,
        label="residual after the chain",
    )
    if exit_at is not None and retrigger_at is not None:
        ax_residual.axvspan(exit_at, retrigger_at, color=NEUTRAL, alpha=0.12, lw=0)
        ax_residual.text(
            exit_at - 5,
            3.35,
            "dwell: settle time\n+ one sample",
            ha="right",
            va="bottom",
            fontsize=8.5,
            color=NEUTRAL,
        )
        ax_residual.annotate(
            "band exit",
            xy=(exit_at, 0.1),
            xytext=(exit_at - 8, 0.9),
            ha="right",
            fontsize=9,
            color=NEUTRAL,
            arrowprops={"arrowstyle": "-", "color": NEUTRAL, "lw": 0.8},
        )
    if retrigger_at is not None and new_plan_at is not None:
        ax_residual.axvspan(retrigger_at, new_plan_at, color=REACTION, alpha=0.07, lw=0)
        ax_residual.text(
            (retrigger_at + new_plan_at) / 2,
            4.95,
            "request in flight: no second retrigger, even when the surprise flaps",
            ha="center",
            fontsize=8.5,
            color=REACTION,
        )
        ax_residual.annotate(
            "retrigger: one request,\nfresh meter data posted",
            xy=(retrigger_at, 3.05),
            xytext=(retrigger_at + 8, 3.5),
            ha="left",
            va="bottom",
            fontsize=9,
            color="#0b0b0b",
            arrowprops={"arrowstyle": "->", "color": "#0b0b0b", "lw": 0.9},
        )
        ax_residual.annotate(
            "new plan and rules arrive:\nthe residual closes",
            xy=(new_plan_at, 0.05),
            xytext=(new_plan_at + 12, 1.6),
            ha="left",
            fontsize=9,
            color="#0b0b0b",
            arrowprops={"arrowstyle": "->", "color": "#0b0b0b", "lw": 0.9},
        )
        ax_residual.text(
            retrigger_at + 8,
            0.3,
            "the rule keeps acting meanwhile, clipped to the band",
            ha="left",
            color=NEUTRAL,
            fontsize=8.5,
            va="bottom",
        )
    ax_residual.set_ylim(-0.6, 5.5)
    ax_residual.set_xlabel("minutes", color=NEUTRAL, fontsize=9)
    for second in (exit_at, retrigger_at, new_plan_at):
        _event_line(ax_residual, second, NEUTRAL)

    fig.suptitle(
        "A band exit, second by second: the rule acts, the dwell waits, one retrigger fires",
        x=0.02,
        ha="left",
        fontsize=13,
        color="#0b0b0b",
    )
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


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
        f"Band exit at {trace.band_exit_at} s, retrigger at {trace.retrigger_at} s "
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
