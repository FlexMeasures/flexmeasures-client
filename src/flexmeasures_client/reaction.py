"""Reaction rules served with a schedule, and the pure function that evaluates them.

FlexMeasures can serve, next to a schedule's ``values``, a ``reaction`` array with one rule per slot:
a ``reference`` (the uncontrolled power the plan assumed), a ``react`` share in [0, 1] and a ``band`` (a list of ranges),
all in the schedule's unit (see FlexMeasures/flexmeasures#2894 and FlexMeasures/flexmeasures-client#297).
A home controller evaluates the rule locally at meter speed:

    setpoint = clip(p - react * residual, band)

where ``p`` is the slot's schedule value and ``residual`` is the surprise handed to this device,
the surprise being what the meter shows beyond the plan: ``(meter − Σ device power) − reference``.
Signs are those of the schedule values: consumption positive, so import at the connection is positive.

Everything here is pure: no timers, no state, no I/O.
Rounding with hysteresis, deadbands on commands and dwell times belong to the device drivers in the controller.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TypeVar

import pandas as pd

__all__ = [
    "Range",
    "ReactionRule",
    "ReactionSchedule",
    "InvalidReactionError",
    "parse_reaction",
    "clip_to_band",
    "react",
    "residuals",
    "convert_reaction_units",
    "ExitGate",
    "Smoother",
    "ModePolicy",
]

Range = tuple[float, float]
"""One allowed range of a band, as ``(low, high)``; both sides are finite numbers."""

Device = TypeVar("Device", bound=Hashable)


class InvalidReactionError(ValueError):
    """Raised when a schedule's ``reaction`` array does not match the contract."""


@dataclass(frozen=True)
class ReactionRule:
    """The reaction rule for one device and one schedule slot.

    :param reference: The uncontrolled power the plan assumed for the slot, in the schedule's unit.
    :param react:     The share of the residual the device absorbs, in [0, 1]: 0 follows the plan, 1 absorbs it one to one.
                      The server serves 0 or 1 for now; intermediate shares are reserved for a later version.
    :param band:      The ranges within which deviating keeps the device's own constraints satisfiable,
                      as ``(low, high)`` tuples of finite numbers (a device's power capacity always bounds it).
                      A band with more than one range has holes, for devices with operation modes.
    """

    reference: float
    react: float
    band: list[Range]

    def react_to(
        self, p: float, residual: float, current: Range | None = None
    ) -> float:
        """Return the setpoint for this slot, given the plan value and the residual handed to the device.

        See :func:`react`.
        """
        return react(self, p, residual, current)

    def contains(self, value: float) -> bool:
        """Return whether the value lies within one of the band's ranges."""
        return _contains(self.band, value)


@dataclass(frozen=True)
class ReactionSchedule:
    """A schedule paired with its reaction rules, as served by FlexMeasures.

    Build one with :meth:`from_schedule` from the dictionary that ``get_schedule`` returns.
    ``rules`` is ``None`` for a schedule served without rules, in which case every slot follows the plan.

    :param start:      Start of the first slot.
    :param resolution: Length of each slot.
    :param unit:       The unit of the values, references and band edges.
    :param values:     The planned power per slot.
    :param rules:      The reaction rule per slot, or ``None`` when the schedule has none.
    :param sensor:     The sensor the schedule is for, when the schedule names it.
    """

    start: datetime
    resolution: timedelta
    unit: str
    values: list[float]
    rules: list[ReactionRule] | None = None
    sensor: int | None = None

    @classmethod
    def from_schedule(cls, schedule: Mapping) -> ReactionSchedule:
        """Parse a schedule dictionary, with or without a ``reaction`` array.

        :raises InvalidReactionError: When the ``reaction`` array is present but invalid.
        """
        values = [float(v) for v in schedule["values"]]
        duration = pd.Timedelta(schedule["duration"]).to_pytimedelta()
        # An empty schedule has no slots to divide the duration over, so the duration is kept as the resolution.
        resolution = duration / len(values) if values else duration
        return cls(
            start=pd.Timestamp(schedule["start"]).to_pydatetime(),
            resolution=resolution,
            unit=schedule["unit"],
            values=values,
            rules=parse_reaction(schedule),
            sensor=schedule.get("sensor"),
        )

    @property
    def has_rules(self) -> bool:
        """Return whether the server served reaction rules with this schedule."""
        return self.rules is not None

    @property
    def end(self) -> datetime:
        """Return the end of the last slot."""
        return self.start + self.resolution * len(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __iter__(self) -> Iterator[tuple[datetime, float, ReactionRule | None]]:
        """Yield ``(slot start, plan value, rule)`` per slot, with the rule ``None`` for a schedule without rules."""
        for index, value in enumerate(self.values):
            rule = self.rules[index] if self.rules is not None else None
            yield self.start + self.resolution * index, value, rule

    def slot(self, when: datetime) -> int:
        """Return the index of the slot that contains the given moment.

        :raises IndexError: When the moment falls outside the schedule.
        """
        if when.tzinfo is None or self.start.tzinfo is None:
            raise ValueError("Compare timezone-aware datetimes only.")
        if not self.start <= when < self.end:
            raise IndexError(f"{when.isoformat()} falls outside the schedule.")
        return int((when - self.start) // self.resolution)

    def react_at(
        self, when: datetime, residual: float, current: Range | None = None
    ) -> float:
        """Return the setpoint at the given moment, following the plan where the schedule has no rules.

        :param current: The range the device is in now; see :func:`react`.
        :raises IndexError: When the moment falls outside the schedule.
        """
        index = self.slot(when)
        p = self.values[index]
        if self.rules is None:
            return p
        return react(self.rules[index], p, residual, current)


def parse_reaction(schedule: Mapping) -> list[ReactionRule] | None:
    """Parse the ``reaction`` array of a schedule dictionary into one rule per slot.

    :param schedule: The dictionary that ``get_schedule`` returns, with ``values`` and possibly ``reaction``.
    :returns:        One rule per value, or ``None`` when the schedule carries no ``reaction``.
    :raises InvalidReactionError: When ``reaction`` is not a list with exactly one valid entry per value.
    """
    reaction = schedule.get("reaction")
    if reaction is None:
        return None
    if not isinstance(reaction, (list, tuple)):
        raise InvalidReactionError(
            f"Expected the schedule's reaction to be a list, but got {type(reaction).__name__}."
        )
    values = schedule.get("values")
    if values is None:
        raise InvalidReactionError("A schedule with a reaction needs values.")
    if len(reaction) != len(values):
        raise InvalidReactionError(
            f"The schedule's reaction has {len(reaction)} entries for {len(values)} values; expected one per value."
        )
    return [_parse_rule(entry, index) for index, entry in enumerate(reaction)]


def _parse_rule(entry: object, index: int) -> ReactionRule:
    """Parse one entry of the ``reaction`` array."""
    if not isinstance(entry, Mapping):
        raise InvalidReactionError(
            f"Reaction entry {index} should be an object, but got {type(entry).__name__}."
        )
    missing = [key for key in ("reference", "react", "band") if key not in entry]
    if missing:
        raise InvalidReactionError(
            f"Reaction entry {index} misses {', '.join(missing)}."
        )
    reference = _number(entry["reference"], f"reaction entry {index}: reference")
    react = _number(entry["react"], f"reaction entry {index}: react")
    if not 0 <= react <= 1:
        raise InvalidReactionError(
            f"Reaction entry {index}: react should be a share in [0, 1], but got {react}."
        )
    band = entry["band"]
    if not isinstance(band, (list, tuple)):
        raise InvalidReactionError(
            f"Reaction entry {index}: band should be a list of ranges, but got {type(band).__name__}."
        )
    ranges: list[Range] = []
    for position, item in enumerate(band):
        where = f"reaction entry {index}: band range {position}"
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise InvalidReactionError(f"{where} should be a [low, high] pair.")
        low = _number(item[0], f"{where}: low")
        high = _number(item[1], f"{where}: high")
        if low > high:
            raise InvalidReactionError(
                f"{where} has its low ({low}) above its high ({high})."
            )
        ranges.append((low, high))
    return ReactionRule(reference=reference, react=react, band=ranges)


def _number(value: object, where: str) -> float:
    """Return the value as a float, refusing anything that is not a plain number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidReactionError(f"Expected a number for {where}, but got {value!r}.")
    return float(value)


def _contains(band: Sequence[Range], value: float) -> bool:
    """Return whether the value lies within one of the band's ranges."""
    return _range_containing(band, value) is not None


def _range_containing(band: Sequence[Range], value: float) -> Range | None:
    """Return the range of the band that contains the value, or None."""
    for low, high in band:
        if low <= value <= high:
            return (low, high)
    return None


def clip_to_band(
    value: float,
    band: Sequence[Range],
    current: Range | None = None,
    towards: float | None = None,
) -> float:
    """Clip a value to a band, which is a list of allowed ranges.

    A value inside one of the ranges is returned as is.
    A value in a hole between ranges becomes the edge of that hole on the side of the range the device is in now, ``current``:
    the device stays on its side of the dead zone rather than crossing it for a value that happens to lie nearer to the far edge.
    A value beyond the outermost edge becomes that edge.
    Without ``current`` the nearest edge wins, and when two edges are equally near the one on the side of ``towards``,
    which callers set to the plan value; without ``towards``, or with it equally far from both, the lower edge wins.
    An empty band leaves no room at all, so the value is returned as is.

    :param value:   The value to clip.
    :param band:    The allowed ranges.
    :param current: The range the device is in now, as ``(low, high)``; see :class:`ModePolicy` for keeping track of it.
    :param towards: Which side to prefer on a tie, when no current range is given.
    :returns:       The allowed value the value is rounded to.
    """
    if not band or _contains(band, value):
        return value
    if current is not None:
        low_c, high_c = current
        if value < low_c:
            return min(low for low, _ in band if low > value)
        return max(high for _, high in band if high < value)
    nearest: list[float] = []
    nearest_distance: float | None = None
    for low, high in band:
        if value < low:
            edge = low
        elif value > high:
            edge = high
        else:
            continue  # the value is inside this range
        distance = abs(edge - value)
        if nearest_distance is None or distance < nearest_distance:
            nearest, nearest_distance = [edge], distance
        elif distance == nearest_distance:
            nearest.append(edge)
    if not nearest:
        return value
    if len(nearest) == 1:
        return nearest[0]
    if towards is not None:
        preferred = [edge for edge in nearest if (edge - value) * (towards - value) > 0]
        if preferred:
            return min(preferred)
    return min(nearest)


def react(
    rule: ReactionRule, p: float, residual: float, current: Range | None = None
) -> float:
    """Return the setpoint for one device and slot: ``clip(p - react * residual, band)``.

    Pure and stateless.
    With react 0 the plan value is returned as is.
    A continuous answer in a hole of the band becomes the edge of that hole on the side of ``current``,
    the range the device is in now, which at the start of a slot is the range that contains ``p``;
    that is the default, so a stateless caller keeps the plan's side of every dead zone.
    Signs are those of the schedule values: consumption positive, so import at the connection is positive.

    :param rule:     The slot's reaction rule.
    :param p:        The slot's schedule value, in the rule's unit.
    :param residual: The surprise handed to this device, in the rule's unit; see :func:`residuals`.
                     With one reacting device, it is the surprise itself: ``(meter − Σ device power) − reference``.
    :param current:  The range the device is in now, by default the one containing ``p``.
    :returns:        The setpoint, in the rule's unit.
    """
    if rule.react == 0:
        return p
    if current is None:
        current = _range_containing(rule.band, p)
    return clip_to_band(p - rule.react * residual, rule.band, current, towards=p)


def residuals(
    surprise: float,
    chain: Sequence[Device],
    plans: Mapping[Device, float],
    realized: Mapping[Device, float],
) -> dict[Device, float]:
    """Walk the devices in the order in which they react, and return the residual each device in the chain is given.

    Devices react in series.
    The residual for device ``i`` is the surprise plus every deviation from plan the chain did not cause,
    plus the deviations of the devices before ``i`` in the chain:

        r_i = Δ + Σ_{j outside the chain}(d_j − p_j) + Σ_{j before i}(d_j − p_j)

    The chain is the devices with a nonzero react share for the slot, in the order in which they react;
    pass only those, as a device with react 0 sits outside the chain for the slot.

    :param surprise: Δ, the surprise on the uncontrolled part of the meter: ``(m − Σ d) − reference``.
    :param chain:    The devices that react, in order.
    :param plans:    The plan value per controlled device, chain or not.
    :param realized: The realized power per controlled device, chain or not.
    :returns:        The residual per device in the chain, in the chain's order.
    :raises KeyError: When a device in the chain or in ``plans`` lacks a plan or a realized power.
    """
    deviations = {device: realized[device] - plan for device, plan in plans.items()}
    outside = sum(
        deviation for device, deviation in deviations.items() if device not in chain
    )
    result: dict[Device, float] = {}
    before = 0.0
    for device in chain:
        result[device] = surprise + outside + before
        before += deviations[device]
    return result


def convert_reaction_units(
    reaction: Sequence[Mapping], convert: Callable[[list[float]], list[float]]
) -> list[dict]:
    """Return the entries of a ``reaction`` array with reference and band edges converted.

    The same function converts the schedule's values,
    so that values, references and bands stay in one unit.

    :param reaction: The ``reaction`` array as served, one entry per slot.
    :param convert:  Converts a list of numbers from the served unit to the requested one.
    :returns:        New entries, with ``react`` and any other fields kept as they are.
    """
    converted = []
    for entry in reaction:
        new_entry = dict(entry)
        new_entry["reference"] = convert([entry["reference"]])[0]
        new_entry["band"] = [
            [
                convert([low])[0],
                convert([high])[0],
            ]
            for low, high in entry["band"]
        ]
        converted.append(new_entry)
    return converted


@dataclass
class ExitGate:
    """Decides when a residual left outside every band is worth a new plan.

    The reaction rule acts on every meter sample; this gate sits behind it and turns a residual
    that no device absorbed into at most one scheduling request per exit. It is the one piece of
    the loop that keeps state, so it lives here next to the pure functions, with its thresholds
    as plain parameters rather than as settings of the client: the controller that runs the loop
    constructs one gate per device chain and feeds it every sample.

    A retrigger is due when the residual has been outside the band, beyond ``tolerance``,
    for at least ``dwell`` (the slowest commanded device's settle time plus one sample,
    so a residual only counts once every device has had the chance to land) and the energy
    that went past the band meanwhile reaches ``min_energy``. The energy gate is what lets a
    kettle pass: a short peak costs a few cents, less than a scheduling run is worth,
    while an oven or a car that left still fires within minutes. Set ``min_energy`` to 0
    to fire on the dwell alone.

    After :meth:`request_sent` nothing fires until :meth:`plan_arrived` resets the gate,
    so at most one request is in flight, even when the residual wobbles. A residual back
    inside the band resets the exit; the next excursion starts counting from zero.

    :param dwell:      The settle time of the slowest commanded device plus one sample.
    :param min_energy: Energy past the band before a retrigger, in the residual's unit times hours
                       (kWh for a residual in kW).
    :param tolerance:  Residuals within this magnitude count as inside the band.
    """

    dwell: timedelta
    min_energy: float = 0.1
    tolerance: float = 0.05
    exited_at: datetime | None = None
    energy_outside: float = 0.0
    in_flight: bool = False
    _last_seen: datetime | None = None

    def observe(self, when: datetime, residual: float) -> bool:
        """Feed one meter sample; return True when a retrigger is due now.

        :param when:     The sample's time; samples must be fed in order.
        :param residual: What the chain left over, in the schedule's unit.
        """
        if self._last_seen is not None and when < self._last_seen:
            raise ValueError("samples must be observed in order")
        hours = (
            (when - self._last_seen).total_seconds() / 3600
            if self._last_seen is not None
            else 0.0
        )
        self._last_seen = when
        if abs(residual) <= self.tolerance:
            self.exited_at = None
            self.energy_outside = 0.0
            return False
        if self.exited_at is None:
            self.exited_at = when
            self.energy_outside = 0.0
        else:
            self.energy_outside += abs(residual) * hours
        if self.in_flight:
            return False
        return (
            when - self.exited_at >= self.dwell
            and self.energy_outside >= self.min_energy
        )

    def request_sent(self) -> None:
        """A scheduling request went out: nothing fires until the plan arrives."""
        self.in_flight = True

    def plan_arrived(self) -> None:
        """A new plan and its rules are installed: start afresh."""
        self.in_flight = False
        self.exited_at = None
        self.energy_outside = 0.0


@dataclass
class Smoother:
    """Smooths a per-sample series, such as the surprise, over a short rolling window.

    A meter samples once a second and a charger is polled every few seconds, so the instantaneous
    surprise carries their misalignment during a ramp, and a one-second spike (a kettle's inrush,
    a switching transient) would move the setpoint for one sample and back. Two stages remove both:
    a median over the last ``median_samples`` kills spikes, and a mean over ``window`` seconds of
    those medians takes out the jitter. The price is lag of about half the window.

    Smooth the surprise, computed per sample against the slot's reference, and feed the smoothed
    residual to the :class:`ExitGate`. Do not smooth what is about timing rather than noise:
    a fuse guard must act within one sample, and the hand-over between commanded and realized
    device power must not be delayed.

    :param window:         The length of the mean, as a duration; samples older than this are dropped.
    :param median_samples: How many of the latest samples the median spans; 1 disables it.
    """

    window: timedelta = timedelta(seconds=5)
    median_samples: int = 3
    _raw: list[tuple[datetime, float]] = field(default_factory=list)
    _medians: list[tuple[datetime, float]] = field(default_factory=list)

    def update(self, when: datetime, value: float) -> float:
        """Feed one sample in order; return the smoothed value as of ``when``."""
        if self._raw and when < self._raw[-1][0]:
            raise ValueError("samples must be fed in order")
        if self.median_samples < 1:
            raise ValueError("median_samples must be at least 1")
        self._raw.append((when, value))
        del self._raw[: -self.median_samples]
        median = (
            sorted(v for _, v in self._raw)[len(self._raw) // 2]
            if len(self._raw) % 2
            else (
                sum(
                    sorted(v for _, v in self._raw)[
                        len(self._raw) // 2 - 1 : len(self._raw) // 2 + 1
                    ]
                )
                / 2
            )
        )
        self._medians.append((when, median))
        cutoff = when - self.window
        self._medians = [(t, v) for t, v in self._medians if t > cutoff]
        return sum(v for _, v in self._medians) / len(self._medians)

    def reset(self) -> None:
        """Forget every sample, for example when a new plan changes the reference."""
        self._raw.clear()
        self._medians.clear()


@dataclass
class ModePolicy:
    """Keeps the range (operation mode) a device is in, and rounds setpoints relative to it.

    A device with operation modes has a band with holes, and a setpoint in a hole is rounded to the edge
    on the side of the mode the device is in (see :func:`clip_to_band`). Which mode that is, is state,
    so it lives here next to :class:`ExitGate` and :class:`Smoother`: call :meth:`start_slot` when a slot begins,
    which puts the device in the mode that contains the plan value, and :meth:`observe` for every sample.

    Switching modes is the slow, wearing transition on a real charger (a contactor reversing the direction of the current),
    so by default the policy never switches within a slot: it holds the plan's mode, and a value that lies in another
    mode is rounded back to the nearest edge on this side of it. With a ``dwell``, the policy switches once the raw value
    has lain inside another mode, at least ``margin`` past the hole, for at least that long; switching back obeys the same
    dwell and margin. A single-value mode (idle, ``[0, 0]``) is an edge of the holes on both of its sides and is reached
    without a switch, so it is never a mode to switch into.

    A lasting surprise the policy will not follow leaves a residual that the device does not absorb;
    that is what the :class:`ExitGate` is for, which is unchanged by this policy.

    :param dwell:  How long the raw value must lie in another mode before the policy switches to it; ``None`` never switches.
    :param margin: How far past the hole the raw value must lie to count, in the band's unit, clamped to the mode's width.
    """

    dwell: timedelta | None = None
    margin: float = 0.0
    current: Range | None = None
    switches: int = 0
    _band: list[Range] = field(default_factory=list)
    _candidate: Range | None = None
    _candidate_since: datetime | None = None

    def start_slot(self, p: float, band: Sequence[Range]) -> None:
        """Begin a slot: the device is in the mode that contains the plan value ``p``."""
        self._band = sorted(band)
        self.current = _range_containing(self._band, p)
        self._candidate = None
        self._candidate_since = None

    def observe(self, when: datetime, raw: float) -> float:
        """Feed the raw setpoint of one sample, ``p - react * residual``, and return the setpoint to command.

        :param when: The sample's time; samples must be fed in order.
        :param raw:  The continuous answer of the rule, before rounding to the band.
        """
        if self._candidate_since is not None and when < self._candidate_since:
            raise ValueError("samples must be observed in order")
        if not self._band:
            return raw
        if self.current is None:
            return clip_to_band(raw, self._band)
        other = _range_containing(self._band, raw)
        if (
            self.dwell is not None
            and other is not None
            and other != self.current
            and other[0] < other[1]
            and self._past_the_hole(raw, other)
        ):
            if self._candidate != other:
                self._candidate, self._candidate_since = other, when
            elif when - self._candidate_since >= self.dwell:  # type: ignore[operator]
                self.current = other
                self.switches += 1
                self._candidate = None
                self._candidate_since = None
        else:
            self._candidate = None
            self._candidate_since = None
        return self._hold(raw)

    def _past_the_hole(self, raw: float, other: Range) -> bool:
        """Return whether the raw value lies at least the margin into the other mode, counted from the hole towards the current mode."""
        assert self.current is not None
        low, high = other
        margin = min(self.margin, high - low)
        if high < self.current[0]:
            return raw <= high - margin
        return raw >= low + margin

    def _hold(self, raw: float) -> float:
        """Round the raw value relative to the current mode, without entering another mode.

        A raw value in a hole becomes the edge of the hole on this side, as :func:`clip_to_band` does.
        A raw value inside another mode, or beyond the band on that mode's side, is treated as standing at that mode's far border,
        so it becomes the next edge towards the current mode; a single-value mode counts as such an edge.
        """
        assert self.current is not None
        low_c, high_c = self.current
        if low_c <= raw <= high_c:
            return raw
        other = _range_containing(self._band, raw)
        if raw < low_c:
            if other is None and raw < self._band[0][0]:
                other = self._band[0]
            if other == self.current:
                return low_c
            if other is not None and other[0] == other[1]:
                return raw  # a single-value mode is an edge of the hole
            anchor = raw if other is None else other[1]
            return min(low for low, _ in self._band if low > anchor)
        if other is None and raw > self._band[-1][1]:
            other = self._band[-1]
        if other == self.current:
            return high_c
        if other is not None and other[0] == other[1]:
            return raw
        anchor = raw if other is None else other[0]
        return max(high for _, high in self._band if high < anchor)
