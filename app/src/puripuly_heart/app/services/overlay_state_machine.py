"""Pure state machine for overlay lifecycle.

Validates and records state transitions. No I/O, no asyncio.
Testable with simple data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_OVERLAY_FAILURE_REASONS = frozenset(
    {
        "missing_executable",
        "spawn_failed",
        "manifest_invalid",
        "contract_mismatch",
        "bridge_auth_failed",
        "startup_timeout",
        "stale_overlay_build",
        "vendored_openvr_dll_missing",
        "packaged_openvr_dll_missing",
        "openvr_dll_hash_mismatch",
        "steamvr_not_installed",
        "steamvr_not_running",
        "hmd_not_found",
        "openvr_init_failed",
        "renderer_init_failed",
        "runtime_disconnected",
        "window_configuration_failed",
        "runtime_control_invalid",
        "runtime_crashed",
        "unknown",
    }
)

# Valid transitions: (current_state, event) -> next_state
_VALID_TRANSITIONS: dict[tuple[str, str], str] = {
    ("off", "start"): "starting",
    ("starting", "connected"): "connected",
    ("starting", "failed"): "failed",
    ("starting", "stop"): "stopping",
    ("connected", "disconnect"): "failed",
    ("connected", "crash"): "failed",
    ("connected", "stop"): "stopping",
    ("stopping", "done"): "off",
    ("failed", "start"): "starting",
    ("failed", "stop"): "off",
}


@dataclass
class OverlayStateMachine:
    """Pure state machine for overlay lifecycle.

    Tracks current state, failure reason, and auto-restart flag.
    All transitions are validated — invalid transitions raise ValueError.
    """

    _state: str = field(default="off")
    _failure_reason: str | None = field(default=None)
    _auto_restart_scheduled: bool = field(default=False)

    @property
    def state(self) -> str:
        return self._state

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    @failure_reason.setter
    def failure_reason(self, value: str | None) -> None:
        self._failure_reason = value

    @property
    def auto_restart_scheduled(self) -> bool:
        return self._auto_restart_scheduled

    @auto_restart_scheduled.setter
    def auto_restart_scheduled(self, value: bool) -> None:
        self._auto_restart_scheduled = value

    def transition(self, event: str, *, failure_reason: str | None = None) -> str:
        """Validate and execute a state transition.

        Returns the previous state.
        Raises ValueError if the transition is invalid.
        """
        key = (self._state, event)
        next_state = _VALID_TRANSITIONS.get(key)
        if next_state is None:
            raise ValueError(
                f"Invalid overlay transition: {self._state} + {event}"
            )
        prev = self._state
        self._state = next_state
        self._auto_restart_scheduled = False

        # Set failure reason for failure transitions
        if next_state == "failed":
            self._failure_reason = self._normalize_failure_reason(failure_reason)
        elif next_state == "off":
            # Preserve failure reason only if explicitly requested
            pass
        elif next_state == "connected":
            self._failure_reason = None

        return prev

    def clear_failure_reason(self) -> None:
        """Clear the failure reason (e.g., when preserve_failure_reason=False)."""
        self._failure_reason = None

    @staticmethod
    def _normalize_failure_reason(reason: str | None) -> str:
        if isinstance(reason, str) and reason in _OVERLAY_FAILURE_REASONS:
            return reason
        return "unknown"

    def is_startable(self) -> bool:
        """Check if the overlay can be started from current state."""
        return self._state in {"off", "failed"}

    def is_stoppable(self) -> bool:
        """Check if the overlay can be stopped from current state."""
        return self._state in {"connected", "starting", "failed"}
