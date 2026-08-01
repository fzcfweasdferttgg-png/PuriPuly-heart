from __future__ import annotations

from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Return monotonic seconds."""
