"""Reaction rules served with a schedule, and the pure function that evaluates them.

FlexMeasures can serve, next to a schedule's ``values``, a ``reaction`` array with one rule per slot:
a ``reference`` (the uncontrolled power the plan assumed), a ``react`` share in [0, 1] and a ``band`` (a list of ranges),
all in the schedule's unit (see FlexMeasures/flexmeasures#2894 and FlexMeasures/flexmeasures-client#297).
A home controller evaluates the rule locally at meter speed:

    setpoint = clip(p - react * residual, band)

where ``p`` is the slot's schedule value and ``residual`` is the surprise handed to this device,
the surprise being what the meter shows beyond the plan: ``(meter − Σ device power) − reference``.
Sign convention: meter import positive, device consumption positive.

Everything here is pure: no timers, no state, no I/O.
Rounding with hysteresis, deadbands on commands and dwell times belong to the device drivers in the controller.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator, Mapping, Sequence
from dataclasses import dataclass
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
]

Range = tuple[float | None, float | None]
"""One allowed range of a band, as ``(low, high)``, with ``None`` for an unbounded side."""

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
                      as ``(low, high)`` tuples with ``None`` for an unbounded side.
                      A band with more than one range has holes, for devices with operation modes.
    """

    reference: float
    react: float
    band: list[Range]

    def react_to(self, p: float, residual: float) -> float:
        """Return the setpoint for this slot, given the plan value and the residual handed to the device.

        See :func:`react`.
        """
        return react(self, p, residual)

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

    def react_at(self, when: datetime, residual: float) -> float:
        """Return the setpoint at the given moment, following the plan where the schedule has no rules.

        :raises IndexError: When the moment falls outside the schedule.
        """
        index = self.slot(when)
        p = self.values[index]
        if self.rules is None:
            return p
        return react(self.rules[index], p, residual)


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
        low = None if item[0] is None else _number(item[0], f"{where}: low")
        high = None if item[1] is None else _number(item[1], f"{where}: high")
        if low is not None and high is not None and low > high:
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
    for low, high in band:
        if (low is None or low <= value) and (high is None or value <= high):
            return True
    return False


def clip_to_band(
    value: float, band: Sequence[Range], towards: float | None = None
) -> float:
    """Clip a value to a band, which is a list of allowed ranges.

    A value inside one of the ranges is returned as is.
    A value in a hole between ranges, or beyond the outermost edge, becomes the nearest edge.
    When two edges are equally near, the edge on the side of ``towards`` wins, which callers set to the plan value;
    without ``towards``, or with it equally far from both, the lower edge wins.
    ``None`` sides are unbounded.
    An empty band leaves no room at all, so the value is returned as is.

    :param value:   The value to clip.
    :param band:    The allowed ranges.
    :param towards: Which side to prefer on a tie.
    :returns:       The nearest allowed value.
    """
    if not band or _contains(band, value):
        return value
    nearest: list[float] = []
    nearest_distance: float | None = None
    for low, high in band:
        edge: float | None
        if low is not None and value < low:
            edge = low
        elif high is not None and value > high:
            edge = high
        else:
            # The value is inside this range, or the range is unbounded on the side where the value lies.
            continue
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


def react(rule: ReactionRule, p: float, residual: float) -> float:
    """Return the setpoint for one device and slot: ``clip(p - react * residual, band)``.

    Pure and stateless.
    With react 0 the plan value is returned as is.
    Sign convention: meter import positive, device consumption positive.

    :param rule:     The slot's reaction rule.
    :param p:        The slot's schedule value, in the rule's unit.
    :param residual: The surprise handed to this device, in the rule's unit; see :func:`residuals`.
                     With one reacting device, it is the surprise itself: ``(meter − Σ device power) − reference``.
    :returns:        The setpoint, in the rule's unit.
    """
    if rule.react == 0:
        return p
    return clip_to_band(p - rule.react * residual, rule.band, towards=p)


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
                None if low is None else convert([low])[0],
                None if high is None else convert([high])[0],
            ]
            for low, high in entry["band"]
        ]
        converted.append(new_entry)
    return converted
