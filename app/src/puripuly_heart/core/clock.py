"""Clock abstraction — monotonic time for latency tracking.

SystemClock uses time.monotonic (not wall clock) to avoid NTP jumps.
FakeClock is for testing — manual time control via advance().
Re-exports Clock Protocol from ports.clock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from puripuly_heart.ports.clock import Clock

__all__ = ["Clock", "SystemClock", "FakeClock"]


class SystemClock(Clock):
    def now(self) -> float:
        return time.monotonic()


@dataclass(slots=True)
class FakeClock(Clock):
    _now: float = 0.0

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("seconds must be >= 0")
        self._now += seconds
