"""Reaction rules served with a schedule, the pure functions that evaluate them, and the controller-side helpers.

FlexMeasures can serve, next to a schedule's ``values``, a ``reaction`` array with one rule per slot:
a ``reference`` (the uncontrolled power the plan is balanced against), a ``react`` share in [0, 1] and a ``band`` (a list of ranges),
all in the schedule's unit (see FlexMeasures/flexmeasures#2894 and FlexMeasures/flexmeasures-client#297).
A home controller evaluates the rule locally at meter speed:

    setpoint = clip(p - react * residual, band)

where ``p`` is the slot's schedule value and ``residual`` is the deviation handed to this device:
what the uncontrolled part of the meter shows against the reference, ``(meter − Σ device power) − reference``,
the deviation from the balance point the plan rests on.
Signs are those of the schedule values: consumption positive, so import at the connection is positive.

The rule functions (:func:`parse_reaction`, :func:`react`, :func:`clip_to_band`, :func:`residuals`) are pure: no timers, no state, no I/O.
Four helpers hold the little state a live controller needs between samples, and nothing more:
:class:`ExitGate` the current excursion outside the band, :class:`DriftGate` the energy absorbed since the last plan,
:class:`Smoother` the window of recent samples, and :class:`ModePolicy` the operation mode the device is in.
None of them does I/O.
Deadbands on commands and the devices' own settle times belong to the device drivers in the controller.
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
    "DriftGate",
    "TargetWindow",
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

    :param reference: The uncontrolled power the plan is balanced against, in the schedule's unit:
                      usually the forecast, and where the operation modes make the plan miss the kink, the balance point.
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

    Every plan value must lie in its own band, within half a watt (a planner rounds its output),
    since the rule rests on the plan being feasible; an array where one does not is refused as a whole.
    A slot with an empty band has no band to lie in and is not checked.

    :param schedule: The dictionary that ``get_schedule`` returns, with ``values`` and possibly ``reaction``.
    :returns:        One rule per value, or ``None`` when the schedule carries no ``reaction``.
    :raises InvalidReactionError: When ``reaction`` is not a list with exactly one valid entry per value,
                                  or when a plan value lies outside its band.
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
    rules = [_parse_rule(entry, index) for index, entry in enumerate(reaction)]
    tolerance = _plan_tolerance(schedule.get("unit"))
    for index, (value, rule) in enumerate(zip(values, rules)):
        if rule.band and not any(
            low - tolerance <= value <= high + tolerance for low, high in rule.band
        ):
            raise InvalidReactionError(
                f"Reaction entry {index}: the plan value {value} lies outside its band {rule.band}."
            )
    return rules


PLAN_TOLERANCE_W = 0.5
"""How far a plan value may lie outside its band and still count as inside, in watts: the planner's rounding."""


def _plan_tolerance(unit: object) -> float:
    """Return the plan tolerance in the schedule's unit, half a watt, or 0.0005 schedule units for a unit that is not a power unit."""
    per_watt = {"W": 1.0, "kW": 1e-3, "MW": 1e-6}
    if isinstance(unit, str) and unit in per_watt:
        return PLAN_TOLERANCE_W * per_watt[unit]
    return 0.0005


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
    :param residual: The deviation handed to this device, in the rule's unit; see :func:`residuals`.
                     With one reacting device, it is the deviation from the balance point itself: ``(meter − Σ device power) − reference``.
    :param current:  The range the device is in now, by default the one containing ``p``.
    :returns:        The setpoint, in the rule's unit.
    """
    if rule.react == 0:
        return p
    if current is None:
        current = _range_containing(rule.band, p)
    return clip_to_band(p - rule.react * residual, rule.band, current, towards=p)


def residuals(
    deviation: float,
    chain: Sequence[Device],
    plans: Mapping[Device, float],
    realized: Mapping[Device, float],
) -> dict[Device, float]:
    """Walk the devices in the order in which they react, and return the residual each device in the chain is given.

    Devices react in series.
    The residual for device ``i`` is the deviation at the meter plus every departure from plan the chain did not cause,
    plus the departures of the devices before ``i`` in the chain:

        r_i = Δ + Σ_{j outside the chain}(d_j − p_j) + Σ_{j before i}(d_j − p_j)

    The chain is the devices with a nonzero react share for the slot, in the order in which they react;
    pass only those, as a device with react 0 sits outside the chain for the slot.

    :param deviation: Δ, the deviation of the uncontrolled part of the meter from the reference: ``(m − Σ d) − reference``.
    :param chain:    The devices that react, in order.
    :param plans:    The plan value per controlled device, chain or not.
    :param realized: The realized power per controlled device, chain or not.
    :returns:        The residual per device in the chain, in the chain's order.
    :raises KeyError: When a device in the chain or in ``plans`` lacks a plan or a realized power.
    """
    departures = {device: realized[device] - plan for device, plan in plans.items()}
    outside = sum(
        departure for device, departure in departures.items() if device not in chain
    )
    result: dict[Device, float] = {}
    before = 0.0
    for device in chain:
        result[device] = deviation + outside + before
        before += departures[device]
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


TargetWindow = tuple[datetime, datetime]
"""A target as the window ``(start, end)`` within which it falls; a target at one moment ``t`` is ``(t, t)``."""


def _as_window(target: datetime | Sequence[datetime]) -> TargetWindow:
    """Return a target, given as a moment or as a ``(start, end)`` pair, as a window.

    :raises ValueError: When the window ends before it starts, or a moment is not timezone-aware.
    """
    if isinstance(target, datetime):
        start = end = target
    else:
        start, end = target
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Targets must be timezone-aware.")
    if end < start:
        raise ValueError(
            f"The target window ends ({end.isoformat()}) before it starts ({start.isoformat()})."
        )
    return (start, end)


@dataclass
class DriftGate:
    """Decides when the energy a device absorbed inside its band has moved it far enough from its plan to ask for a new one.

    The :class:`ExitGate` only sees the residual past the band; energy the device absorbs inside its band never reaches it.
    Yet absorbing a deviation moves the device's state of charge away from the plan, and nothing in the rules gives it back:
    off the price kink the band is one-sided, so a device that absorbed a surplus is not asked to give it back later,
    and the drift ratchets in one direction.
    The two gates answer two different questions, and so stay separate:
    the exit gate asks whether the rule could not cope, this gate asks whether the device has left its plan.
    The server's contract (FlexMeasures/flexmeasures#2894 and FlexMeasures/flexmeasures#2905) asks a controller that follows the rules to retrigger after absorbing,
    once the state of charge has drifted by a set amount from the plan, and in any case before a target.

    The drift is the integral of realised minus planned power since the last plan, in kWh,
    positive when the device took in more energy than planned.
    A retrigger is due when the drift reaches ``threshold`` in either direction,
    or when a target lies within ``lead`` of the sample and the drift reaches ``min_drift_before_target``;
    each target fires at most once, and any retrigger within the lead of a target counts for that target,
    since the plan it brings already steers towards the target.

    A target is a moment or a window, such as a car's departure that may come any time between 07:00 and 08:00.
    The lead counts towards the window's start, and a window stays due until its end:
    it is past only once its end has passed, so a target whose window has started still fires once on some drift.
    While the sample lies inside a target window, from its start to its end,
    the drift is held to ``threshold_in_window`` instead of ``threshold`` when that is set,
    since a device that may be asked to deliver at any moment has less room to drift.
    The threshold is best chosen relative to the room the device has left before its next target:
    0.5 kWh is a first default, a few minutes of charging that a car can easily make up for.

    After :meth:`request_sent` nothing fires until :meth:`plan_arrived`,
    which resets the drift to zero, because the new plan starts from the measured state of charge.
    The drift keeps integrating while a request is in flight.

    :param threshold:               The drift that is worth a new plan, in kWh.
    :param lead:                    How long before a target to fire when there is drift; ``None`` fires on the threshold alone.
    :param min_drift_before_target: The drift needed to fire for a target, in kWh, so that a device on plan does not retrigger.
    :param targets:                 The device's targets, each a timezone-aware datetime or a ``(start, end)`` window of two;
                                    they are stored as windows, a moment ``t`` as ``(t, t)``.
    :param threshold_in_window:     The drift that is worth a new plan while inside a target window, in kWh;
                                    ``None`` keeps ``threshold``.
    """

    threshold: float = 0.5
    lead: timedelta | None = None
    min_drift_before_target: float = 0.05
    targets: list[datetime | TargetWindow] = field(default_factory=list)
    threshold_in_window: float | None = None
    drift: float = 0.0
    in_flight: bool = False
    _last_seen: datetime | None = None
    _targets_done: set[TargetWindow] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.targets = list(self.windows)

    @property
    def windows(self) -> list[TargetWindow]:
        """Return the targets as ``(start, end)`` windows."""
        return [_as_window(target) for target in self.targets]

    def observe(self, when: datetime, realized: float, planned: float) -> bool:
        """Feed one sample; return True when a retrigger is due now.

        The first sample only sets the clock;
        each later one adds ``(realized - planned)`` times the hours since the previous sample to the drift.
        Powers are in kW, so that the drift is in kWh: the caller converts from the schedule's unit.

        :param when:     The sample's time, timezone-aware; samples must be fed in order.
        :param realized: The power the device took, in kW, consumption positive (the commanded setpoint will do).
        :param planned:  The plan value for the moment, in kW.
        :raises ValueError: When the sample comes before the previous one.
        """
        if self._last_seen is not None and when < self._last_seen:
            raise ValueError("samples must be observed in order")
        if self._last_seen is not None:
            hours = (when - self._last_seen).total_seconds() / 3600
            self.drift += (realized - planned) * hours
        self._last_seen = when
        if self.in_flight:
            return False
        due_targets = self._targets_within_lead(when)
        if abs(self.drift) >= self._threshold_at(when) or (
            due_targets and abs(self.drift) >= self.min_drift_before_target
        ):
            self._targets_done.update(due_targets)
            return True
        return False

    def _in_window(self, when: datetime) -> bool:
        """Return whether the moment lies inside one of the target windows, ends included."""
        return any(start <= when <= end for start, end in self.windows)

    def _threshold_at(self, when: datetime) -> float:
        """Return the drift threshold that holds at the moment: the in-window one inside a target window, when set."""
        if self.threshold_in_window is not None and self._in_window(when):
            return self.threshold_in_window
        return self.threshold

    def _targets_within_lead(self, when: datetime) -> list[TargetWindow]:
        """Return the targets not fired for yet whose window starts by ``when + lead`` and has not ended by ``when``."""
        if self.lead is None:
            return []
        return [
            (start, end)
            for start, end in self.windows
            if (start, end) not in self._targets_done
            and when <= end
            and start <= when + self.lead
        ]

    def request_sent(self) -> None:
        """A scheduling request went out: nothing fires until the plan arrives."""
        self.in_flight = True

    def plan_arrived(self) -> None:
        """A new plan is installed, starting from the measured state of charge: the drift starts afresh."""
        self.in_flight = False
        self.drift = 0.0


@dataclass
class Smoother:
    """Smooths a per-sample series, such as the deviation at the meter, over a short rolling window.

    A meter samples once a second and a charger is polled every few seconds, so the instantaneous
    deviation carries their misalignment during a ramp, and a one-second spike (a kettle's inrush,
    a switching transient) would move the setpoint for one sample and back. Two stages remove both:
    a median over the last ``median_samples`` kills spikes, and a mean over ``window`` seconds of
    those medians takes out the jitter. The price is lag of about half the window.

    Smooth the deviation, computed per sample against the slot's reference, and feed the smoothed
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
    """Keeps the range (operation mode) a device is in, and holds setpoints to it.

    A device with operation modes has a band with holes. Which mode the device is in is state,
    so it lives here next to :class:`ExitGate` and :class:`Smoother`: call :meth:`start_slot` when a slot begins,
    which puts the device in the mode that contains the plan value, and :meth:`observe` for every sample.

    Switching modes is the slow, wearing transition on a real charger (a contactor reversing the direction of the current,
    or stopping and starting it), so by default the policy never switches within a slot: it holds the plan's mode,
    and every raw value outside that mode, in a hole, inside another mode or beyond the band, is clipped to the mode's nearer edge
    (1.4 kW for a charging car asked to idle or to discharge). Crossing to idle is a mode change like any other,
    and happens only through a switch.

    With a ``dwell``, the policy switches once the raw value has lain inside another mode, at least ``margin`` past the hole,
    for at least that long; switching back obeys the same dwell and margin.
    A raw value counts as inside a single-value mode such as idle (``[0, 0]``) when it lies on that value or beyond it,
    seen from the current mode, and the margin does not apply to it, since there is nothing to be past;
    a raw value that also lies inside a wider mode past the margin counts for that wider mode.

    A lasting deviation the policy will not follow leaves a residual that the device does not absorb;
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
        target = self._mode_asked_for(raw) if self.dwell is not None else None
        if target is None:
            self._candidate = None
            self._candidate_since = None
        elif self._candidate != target:
            self._candidate, self._candidate_since = target, when
        elif when - self._candidate_since >= self.dwell:  # type: ignore[operator]
            self.current = target
            self.switches += 1
            self._candidate = None
            self._candidate_since = None
        low, high = self.current
        return min(max(raw, low), high)

    def _mode_asked_for(self, raw: float) -> Range | None:
        """Return the mode other than the current one that the raw value counts as inside, if any.

        A wider mode counts when the raw value lies inside it, at least the margin past the hole towards the current mode.
        A single-value mode counts when the raw value lies on it or beyond it, seen from the current mode;
        of several, the one nearest to the raw value.
        """
        assert self.current is not None
        low_c, high_c = self.current
        if low_c <= raw <= high_c:
            return None
        below = raw < low_c
        for low, high in self._band:
            if (low, high) == self.current or low == high or not low <= raw <= high:
                continue
            margin = min(self.margin, high - low)
            if (below and raw <= high - margin) or (not below and raw >= low + margin):
                return (low, high)
        points = [
            (low, high)
            for low, high in self._band
            if low == high
            and ((below and raw <= low < low_c) or (not below and raw >= low > high_c))
        ]
        if not points:
            return None
        return min(points, key=lambda point: abs(point[0] - raw))
