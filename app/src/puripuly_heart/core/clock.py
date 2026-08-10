"""Clock abstraction — monotonic time for latency tracking.

SystemClock uses time.monotonic (not wall clock) to avoid NTP jumps.
Re-exports Clock Protocol from ports.clock.
"""

from __future__ import annotations

import time

from puripuly_heart.ports.clock import Clock

__all__ = ["Clock", "SystemClock"]


class SystemClock(Clock):
    def now(self) -> float:
        return time.monotonic()
