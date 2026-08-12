"""Overlay diagnostics recorder — bounded child-process line storage.

Stores overlay child-process stdout/stderr lines in bounded deques
(max 100 per stream).  Used at failure time to report how many lines
were captured before the overlay process crashed.

Called by overlay/process.py (record_child_line + len reads) and
ui/overlay_lifecycle.py (instantiation).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from puripuly_heart.config.paths import default_overlay_diagnostics_dir

_CHILD_LINE_LIMIT = 100


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


@dataclass(slots=True)
class OverlayDiagnosticsRecorder:
    overlay_instance_id: str
    diagnostics_dir: Path

    # child_stdout_lines/child_stderr_lines only ever read via len() — never iterated.
    # Bounded deque exists for failure diagnostics, not replay.
    child_stdout_lines: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=_CHILD_LINE_LIMIT)
    )
    child_stderr_lines: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=_CHILD_LINE_LIMIT)
    )

    _sequence: int = field(init=False, default=0)

    def record_child_line(self, stream: str, line: str) -> dict[str, Any]:
        # stream parameter must be exactly 'stderr' or 'stdout' — drives deque selection.
        # No validation; caller responsibility (process.py).
        target = self.child_stderr_lines if stream == "stderr" else self.child_stdout_lines
        return self._append(
            target, category="child_line", event="child_line", stream=stream, line=line
        )

    def _append(
        self,
        target: deque[dict[str, Any]],
        *,
        category: str,
        event: str,
        **fields: Any,
    ) -> dict[str, Any]:
        payload = self._event(category=category, event=event, **fields)
        target.append(payload)
        return payload

    def _event(self, *, category: str, event: str, **fields: Any) -> dict[str, Any]:
        self._sequence += 1
        payload: dict[str, Any] = {
            "sequence": self._sequence,
            "recorded_at": time.time(),
            "overlay_instance_id": self.overlay_instance_id,
            "category": category,
            "event": event,
        }
        payload.update({key: _json_safe(value) for key, value in fields.items()})
        return payload
