from __future__ import annotations

from typing import Protocol


class RealtimeLogSink(Protocol):
    def append_log(self, line: str) -> None: ...
