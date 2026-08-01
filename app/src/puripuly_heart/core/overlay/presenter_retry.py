from __future__ import annotations


class PresenterRetryMixin:
    """Retry target tracking for python and native retry paths."""

    def _active_python_retry_targets(self) -> dict[str, str]:
        targets: dict[str, str] = {}
        if self.peer_presentation_refresh_burst:
            key = self._presentation_state.peer_presentation_refresh_target_key
            if key is not None:
                targets["peer"] = key
        if self.self_presentation_refresh_burst:
            key = self._presentation_state.self_presentation_refresh_target_key
            if key is not None:
                targets["self"] = key
        return targets

    def _active_native_retry_targets(self) -> dict[str, str]:
        targets: dict[str, str] = {}
        if self._native_quiet_tail_peer_target is not None:
            targets["peer"] = self._native_quiet_tail_peer_target
        if self._native_quiet_tail_self_target is not None:
            targets["self"] = self._native_quiet_tail_self_target
        return targets

    def _synchronize_native_retry_targets(self, targets: dict[str, str]) -> None:
        self._native_quiet_tail_peer_target = targets.get("peer")
        self._native_quiet_tail_self_target = targets.get("self")
