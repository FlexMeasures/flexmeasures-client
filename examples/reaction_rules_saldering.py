"""A day after saldering: a plain schedule against reaction rules.

Until the end of 2026, Dutch households net their exported energy against their imported energy
(``saldering``), so a kilowatt-hour is worth the same whichever way it crosses the meter,
and a schedule that is a little off costs nothing.
From 2027 an exported kilowatt-hour earns the feed-in price, which is far below the import price
and negative in some hours. A schedule is then only as good as the forecast it was built on,
and the forecast of a household is wrong within minutes: a cloud, extra sun, the oven.

This example simulates one such day for a bidirectional charger, offline and deterministically:

- a forecast of everything behind the meter that is not the charger (household load minus PV),
  and the actual, which differs from the forecast by a cloud, a sunny spell and an oven;
- the plan a scheduler would make under the forecast, holding the connection at zero where that pays;
- the reaction rules FlexMeasures would serve with that plan (reference, react share and band per slot);
- what the connection does when the charger follows the plan blindly, and when it reacts.

The rules are evaluated with the real client code (``flexmeasures_client.reaction.react``),
so the pictures show what the client would do; only the plan and the rules are made up here,
the way the planner would make them.

Run it with ``python examples/reaction_rules_saldering.py --out <dir>`` to write two pictures
(``reaction-rules-day.png`` and ``reaction-rules-cost.png``) and print the cost table.
Plotting needs matplotlib (``uv sync --group examples``); the numbers do not.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from flexmeasures_client.reaction import ReactionRule, react

SLOT_MINUTES = 5
SLOTS = 24 * 60 // SLOT_MINUTES
SLOT_HOURS = SLOT_MINUTES / 60
CAPACITY_KW = 7.4
MIN_POWER_KW = 1.4  # a charger that delivers nothing between 0 and 1.4 kW
BAND = [(-CAPACITY_KW, -MIN_POWER_KW), (0.0, 0.0), (MIN_POWER_KW, CAPACITY_KW)]
NIGHT_CHARGE_KW = 2.5

# Colours: two series (plain schedule, reaction rules) and neutral ink for forecasts and plans.
PLAIN = "#eb6834"
REACTION = "#2a78d6"
NEUTRAL = "#52514e"
BAND_FILL = "#eda100"
SURFACE = "#fcfcfb"


def hours() -> list[float]:
    """The start of every slot, in hours since midnight."""
    return [slot * SLOT_HOURS for slot in range(SLOTS)]


def import_price(hour: float) -> float:
    """A retail import price in EUR/kWh: cheap at night, dear in the evening peak."""
    if hour < 6:
        return 0.20
    if hour < 9:
        return 0.28
    if hour < 17:
        return 0.23
    if hour < 21:
        return 0.38
    return 0.24


def feed_in_price(hour: float) -> float:
    """A feed-in price in EUR/kWh after saldering: low, and negative around noon."""
    return -0.03 if 12 <= hour < 15 else 0.06


def _bell(hour: float, centre: float, width: float) -> float:
    return math.exp(-0.5 * ((hour - centre) / width) ** 2)


def household_forecast(hour: float) -> float:
    """Forecast household load in kW: a base, breakfast, and cooking in the evening."""
    return (
        0.35
        + 1.2 * _bell(hour, 7.5, 0.5)
        + 2.0 * _bell(hour, 18.5, 0.7)
        + (0.3 if 19 <= hour < 23 else 0.0)
    )


def pv_forecast(hour: float) -> float:
    """Forecast PV output in kW: a bell between 7:00 and 19:00 peaking at 4.5 kW."""
    if not 7 < hour < 19:
        return 0.0
    return 4.5 * math.sin(math.pi * (hour - 7) / 12) ** 1.5


def uncontrolled_forecast(hour: float) -> float:
    """What the plan assumes for everything that is not the charger: load minus PV, import positive."""
    return household_forecast(hour) - pv_forecast(hour)


def surprise(hour: float, rng: random.Random) -> float:
    """How much the house differs from its forecast: a cloud, a sunny spell, the oven, and a little noise."""
    delta = rng.uniform(-0.15, 0.15)
    if 10.5 <= hour < 11.25:  # a cloud takes 60 % of the PV
        delta += 0.6 * pv_forecast(hour)
    if 12.67 <= hour < 14.17:  # a sunny spell, 1.5 kW more than forecast
        delta -= 1.5
    if 17.67 <= hour < 18.5:  # the oven
        delta += 2.8
    return delta


def plan(hour: float, reference: float) -> float:
    """The charger's plan under the forecast, in kW (charging positive).

    Cheap night hours charge at a steady rate. Where the house is forecast to export at least the
    charger's minimum power while export earns less than import costs, the plan absorbs that surplus,
    holding the connection at zero. In the expensive evening the car covers the house the same way.
    Elsewhere the charger idles.
    """
    if 1 <= hour < 5:
        return NIGHT_CHARGE_KW
    if reference <= -MIN_POWER_KW and feed_in_price(hour) < import_price(hour):
        return min(-reference, CAPACITY_KW)
    if 17 <= hour < 21 and reference >= MIN_POWER_KW:
        return -min(reference, CAPACITY_KW)
    return 0.0


def rule_for(hour: float, reference: float, p: float) -> ReactionRule:
    """The reaction rule FlexMeasures would serve for the slot, from the slot's cost structure.

    React where a surprise changes what is optimal: where the plan holds the connection near zero and
    export earns less than import, and wherever export costs money. Follow the plan elsewhere.
    """
    planned_meter = reference + p
    near_zero = abs(planned_meter) < 0.05
    asymmetric = feed_in_price(hour) < import_price(hour)
    share = 1.0 if (near_zero and asymmetric) or feed_in_price(hour) < 0 else 0.0
    return ReactionRule(reference=reference, react=share, band=BAND)


@dataclass
class Day:
    """Every per-slot series of the simulated day, in kW, plus the per-slot rules."""

    hour: list[float]
    forecast: list[float]
    actual: list[float]
    plan: list[float]
    rules: list[ReactionRule]
    plain_charger: list[float]
    reaction_charger: list[float]

    @property
    def planned_meter(self) -> list[float]:
        return [f + p for f, p in zip(self.forecast, self.plan)]

    @property
    def plain_meter(self) -> list[float]:
        return [a + d for a, d in zip(self.actual, self.plain_charger)]

    @property
    def reaction_meter(self) -> list[float]:
        return [a + d for a, d in zip(self.actual, self.reaction_charger)]


def simulate(seed: int = 7) -> Day:
    """Simulate the day: the plan follows the forecast, the reaction follows the actual."""
    rng = random.Random(seed)
    hour = hours()
    forecast = [uncontrolled_forecast(h) for h in hour]
    actual = [f + surprise(h, rng) for h, f in zip(hour, forecast)]
    plans = [plan(h, f) for h, f in zip(hour, forecast)]
    rules = [rule_for(h, f, p) for h, f, p in zip(hour, forecast, plans)]
    # The surprise is the uncontrolled part beyond the forecast: with one device that reacts at once,
    # (meter − charger) − reference is exactly actual − forecast.
    reaction_charger = [
        react(rule, p, a - f) for rule, p, a, f in zip(rules, plans, actual, forecast)
    ]
    return Day(hour, forecast, actual, plans, rules, plans, reaction_charger)


def cost(hour: list[float], meter: list[float], saldering: bool = False) -> float:
    """The day's cost in EUR: import at the import price, export at the feed-in price (or netted under saldering)."""
    total = 0.0
    for h, m in zip(hour, meter):
        energy = m * SLOT_HOURS
        price = import_price(h) if (energy > 0 or saldering) else feed_in_price(h)
        total += energy * price
    return total


def kwh(hour: list[float], meter: list[float], where) -> float:
    """Energy in kWh over the slots where ``where(hour, meter)`` holds, as a magnitude."""
    return sum(abs(m) * SLOT_HOURS for h, m in zip(hour, meter) if where(h, m))


def summary(day: Day) -> dict[str, float]:
    """The numbers the pictures and the printed table rest on."""
    exported_at_negative = lambda h, m: m < 0 and feed_in_price(h) < 0  # noqa: E731
    imported_at_peak = lambda h, m: m > 0 and 17 <= h < 21  # noqa: E731
    return {
        "cost_saldering_plain": cost(day.hour, day.plain_meter, saldering=True),
        "cost_planned": cost(day.hour, day.planned_meter),
        "cost_plain": cost(day.hour, day.plain_meter),
        "cost_reaction": cost(day.hour, day.reaction_meter),
        "kwh_negative_price_plain": kwh(
            day.hour, day.plain_meter, exported_at_negative
        ),
        "kwh_negative_price_reaction": kwh(
            day.hour, day.reaction_meter, exported_at_negative
        ),
        "kwh_peak_import_plain": kwh(day.hour, day.plain_meter, imported_at_peak),
        "kwh_peak_import_reaction": kwh(day.hour, day.reaction_meter, imported_at_peak),
    }


def print_summary(numbers: dict[str, float]) -> None:
    rows = [
        ("Under saldering, plain schedule", numbers["cost_saldering_plain"]),
        (
            "After saldering, the plan as the scheduler expected it",
            numbers["cost_planned"],
        ),
        ("After saldering, plain schedule on the real day", numbers["cost_plain"]),
        ("After saldering, reaction rules on the real day", numbers["cost_reaction"]),
    ]
    width = max(len(label) for label, _ in rows)
    print("Cost of the day")
    for label, value in rows:
        print(f"  {label:<{width}}  {value:6.2f} EUR")
    print(
        f"Exported at a negative price: {numbers['kwh_negative_price_plain']:.1f} kWh (plain) "
        f"against {numbers['kwh_negative_price_reaction']:.1f} kWh (reaction)"
    )
    print(
        f"Imported in the evening peak:  {numbers['kwh_peak_import_plain']:.1f} kWh (plain) "
        f"against {numbers['kwh_peak_import_reaction']:.1f} kWh (reaction)"
    )


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
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 3)])


def _shade_negative_price(ax, hour: list[float]) -> None:
    starts = [h for h in hour if feed_in_price(h) < 0]
    if starts:
        ax.axvspan(
            min(starts),
            max(starts) + SLOT_HOURS,
            color="#eda100",
            alpha=0.10,
            lw=0,
            zorder=0,
        )


def plot_day(day: Day, path: Path) -> None:
    """Three panels: the house against its forecast, the connection, and the charger."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_house, ax_meter, ax_charger) = plt.subplots(
        3,
        1,
        figsize=(11, 9.5),
        sharex=True,
        facecolor=SURFACE,
        gridspec_kw={"hspace": 0.5},
    )

    # Panel 1: the uncontrolled part of the house, forecast against actual.
    _style(
        ax_house,
        "Everything behind the meter except the charger: forecast and actual",
        "kW, import positive",
    )
    ax_house.plot(
        day.hour,
        day.forecast,
        color=NEUTRAL,
        linewidth=1.6,
        linestyle=(0, (4, 3)),
        label="forecast",
    )
    ax_house.plot(day.hour, day.actual, color=NEUTRAL, linewidth=1.8, label="actual")
    for text, x, y, dy in (
        ("a cloud", 10.9, -1.0, 2.4),
        ("extra sun", 13.4, -5.7, -1.6),
        ("the oven", 18.1, 5.0, 1.0),
    ):
        ax_house.annotate(
            text,
            xy=(x, y),
            xytext=(x, y + dy),
            ha="center",
            va="center",
            fontsize=9,
            color=NEUTRAL,
            arrowprops={"arrowstyle": "-", "color": NEUTRAL, "lw": 0.8, "shrinkB": 3},
        )
    ax_house.set_ylim(-8.2, 7.2)
    ax_house.legend(loc="upper left", frameon=False, fontsize=9)

    # Panel 2: the connection.
    _style(ax_meter, "Net power at the connection", "kW, import positive")
    _shade_negative_price(ax_meter, day.hour)
    ax_meter.plot(
        day.hour,
        day.planned_meter,
        color=NEUTRAL,
        linewidth=1.6,
        linestyle=(0, (4, 3)),
        label="as planned",
    )
    ax_meter.plot(
        day.hour, day.plain_meter, color=PLAIN, linewidth=1.8, label="plain schedule"
    )
    ax_meter.plot(
        day.hour,
        day.reaction_meter,
        color=REACTION,
        linewidth=1.8,
        label="with reaction rules",
    )
    ax_meter.text(
        13.5,
        -2.15,
        "exported at a negative price",
        color=PLAIN,
        fontsize=9,
        ha="center",
        va="top",
    )
    ax_meter.text(
        18.75,
        3.7,
        "imported at the peak price",
        color=PLAIN,
        fontsize=9,
        ha="left",
        va="center",
    )
    ax_meter.text(
        12.05,
        4.4,
        "feed-in price below zero",
        color="#8a5d00",
        fontsize=8.5,
        ha="left",
        va="top",
    )
    ax_meter.set_ylim(-3.0, 4.6)
    ax_meter.legend(loc="upper left", frameon=False, fontsize=9, ncol=3)

    # Panel 3: the charger.
    _style(ax_charger, "The charger: plan and reaction", "kW, charging positive")
    for low, high in (BAND[0], BAND[2]):
        ax_charger.axhspan(low, high, color=BAND_FILL, alpha=0.10, lw=0, zorder=0)
    ax_charger.text(
        0.15,
        CAPACITY_KW - 0.3,
        "band: the charger's own limits",
        color="#8a5d00",
        fontsize=8.5,
        va="top",
    )
    ax_charger.text(0.15, 0.25, "dead zone", color="#8a5d00", fontsize=8.5, va="bottom")
    ax_charger.plot(
        day.hour,
        day.plan,
        color=NEUTRAL,
        linewidth=1.6,
        linestyle=(0, (4, 3)),
        label="plan p",
    )
    ax_charger.plot(
        day.hour,
        day.reaction_charger,
        color=REACTION,
        linewidth=1.8,
        label="setpoint with reaction rules",
    )
    reacting = [h for h, rule in zip(day.hour, day.rules) if rule.react > 0]
    ax_charger.scatter(
        reacting,
        [-CAPACITY_KW - 0.6] * len(reacting),
        s=4,
        color=REACTION,
        marker="s",
        lw=0,
    )
    ax_charger.text(
        0.15,
        -CAPACITY_KW - 0.45,
        "react = 1 in these slots",
        color=REACTION,
        fontsize=8.5,
        va="bottom",
    )
    ax_charger.set_ylim(-CAPACITY_KW - 1.2, CAPACITY_KW + 0.4)
    ax_charger.legend(loc="upper right", frameon=False, fontsize=9)
    ax_charger.set_xlabel("time of day", color=NEUTRAL, fontsize=9)

    fig.suptitle(
        "One day after saldering: a plain schedule follows its plan, reaction rules follow the house",
        x=0.02,
        ha="left",
        fontsize=13,
        color="#0b0b0b",
    )
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def plot_cost(numbers: dict[str, float], path: Path) -> None:
    """One bar per way of running the day, in EUR."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [
        "Under saldering, plain schedule",
        "After saldering, the plan as expected",
        "After saldering, plain schedule",
        "After saldering, reaction rules",
    ]
    values = [
        numbers["cost_saldering_plain"],
        numbers["cost_planned"],
        numbers["cost_plain"],
        numbers["cost_reaction"],
    ]
    colors = [NEUTRAL, NEUTRAL, PLAIN, REACTION]
    fig, ax = plt.subplots(figsize=(11, 3.6), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    positions = list(range(len(labels)))[::-1]
    ax.barh(positions, values, color=colors, height=0.55)
    for pos, value in zip(positions, values):
        ax.text(
            value + 0.08,
            pos,
            f"{value:.2f} EUR",
            va="center",
            fontsize=9.5,
            color="#0b0b0b",
        )
    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=10, color="#0b0b0b")
    ax.set_xlim(0, max(values) * 1.25)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#d6d6d2")
    ax.tick_params(axis="x", colors=NEUTRAL, labelsize=8.5)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("cost of the day, EUR", color=NEUTRAL, fontsize=9)
    ax.set_title(
        "What the same day costs", loc="left", fontsize=12, color="#0b0b0b", pad=10
    )
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out", type=Path, default=Path("."), help="directory for the two pictures"
    )
    parser.add_argument(
        "--seed", type=int, default=7, help="seed of the small noise on the house"
    )
    parser.add_argument("--no-plot", action="store_true", help="only print the numbers")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    day = simulate(args.seed)
    numbers = summary(day)
    print_summary(numbers)
    if args.no_plot:
        return 0
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print(
            "matplotlib is not installed; install the 'examples' dependency group to draw the pictures.",
            file=sys.stderr,
        )
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    plot_day(day, args.out / "reaction-rules-day.png")
    plot_cost(numbers, args.out / "reaction-rules-cost.png")
    print(f"Pictures written to {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
