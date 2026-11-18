"""Clocks that tell an S2 CEM and its handlers what time it is.

A CEM that runs live uses the wall clock.
A CEM that steers a co-simulation uses the simulated time instead.
The resource manager sends that time along with its messages (see the CEM's accept_simulation_metadata).
This way, the CEM records and plans at simulated moments, not at the moments the code actually ran.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current moment, timezone-aware."""
        ...


class WallClock:
    """A clock that returns the current wall-clock time, in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimulatedClock:
    """A clock that returns the simulated time it was last set to.

    The clock stands still between settings, just as simulated time stands still between the messages that carry it.
    Until it is first set, it returns the wall-clock time.
    This suits a resource manager that does not wrap its first messages.
    A resource manager that wraps every message from its handshake on (as the co-simulation's does) sets the clock with its first message.
    Background work, such as planning a schedule, reads the clock at the moment it runs.
    By then, a later message may have set it to a later time.
    """

    def __init__(self, start: datetime | None = None) -> None:
        self._now: datetime | None = None
        if start is not None:
            self.set(start)

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError(
                f"A simulated time has to say which time zone it is in, and {moment.isoformat()} does not."
            )
        self._now = moment

    @property
    def is_set(self) -> bool:
        return self._now is not None

    def now(self) -> datetime:
        if self._now is None:
            return datetime.now(timezone.utc)
        return self._now
