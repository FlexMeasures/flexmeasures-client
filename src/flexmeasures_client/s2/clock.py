"""The time an S2 CEM and its handlers go by.

A CEM that runs live goes by the wall clock.
One that steers a co-simulation goes by the time the simulation says it is,
which a resource manager sends along with its messages (see CEM's accept_simulation_metadata),
so that what the CEM records and plans is placed at simulated moments rather than at the moment it ran.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current moment, timezone-aware."""
        ...


class WallClock:
    """The time it is."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimulatedClock:
    """The time a simulation says it is, as last set.

    It stands still between settings, as a simulation's time does between the messages that carry it.
    Until it is first set it goes by the wall clock, which is the time the handshake of each connection runs on,
    since a resource manager sends the simulated time only with the messages that follow it.
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
