"""Overlay state management mixin for TranslatorApp.

Extracted from app.py during mixin-decomposition (Phase 2A).
Handles overlay state sync, peer contract, desktop overlay handlers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class AppOverlayMixin:
    """Overlay state sync, peer contract, desktop overlay handlers.

    Attributes used (defined on TranslatorApp):
        overlay_state: str
        overlay_failure_reason: str | None
        overlay_peer_contract: object | None
    """

    # AI: NO IMPORTS — this mixin imports nothing from other modules at runtime.
    # All dependencies come through self.* attributes defined on TranslatorApp.
    # This is intentional: overlay mixin is pure orchestration, no domain logic.
    # If you need to import something here, consider whether it belongs in controller instead.

    # AI: STATE CASCADE — this method is called by event_bridge.py via getattr(self.app, ...).
    # After updating overlay_state and overlay_failure_reason, it triggers TWO sync paths:
    # 1. _sync_settings_overlay_runtime_state → updates SettingsView overlay section
    # 2. refresh_overlay_peer_contract → rebuilds peer contract for both views
    # BOTH must run. If you add early returns, ensure contract stays in sync.
    def on_overlay_state_changed(
        self,
        *,
        state: str,
        failure_reason: str | None = None,
    ) -> None:
        previous_state = getattr(self, "overlay_state", "unknown")
        self._log_basic(f"[Overlay] State changed: {previous_state} -> {state}")
        self.overlay_state = state
        self.overlay_failure_reason = failure_reason
        self._log_detailed(
            f"[Overlay] State detail: overlay_state={state} failure_reason={failure_reason}"
        )
        self._sync_settings_overlay_runtime_state()
        self.refresh_overlay_peer_contract()

    # AI: PARAMETER DISCARD — interaction_mode and captions_locked are received but not used.
    # The method only triggers _sync_settings_overlay_runtime_state which re-reads
    # desktop_locked directly from controller.desktop_overlay_captions_locked.
    # The parameters exist for the CALLER's interface (overlay_manager.py), not for this method.
    # Don't "fix" by removing parameters — it would break the caller's keyword call.
    def on_desktop_overlay_state_changed(
        self,
        *,
        interaction_mode: str | None = None,
        captions_locked: bool | None = None,
    ) -> None:
        _ = (interaction_mode, captions_locked)
        self._sync_settings_overlay_runtime_state()

    # AI: DUAL VIEW PROPAGATION — peer contract must be set on BOTH view_settings and view_dashboard.
    # view_settings uses it for overlay preview configuration.
    # view_dashboard uses it for peer translation status display.
    # If contract is None, propagation is skipped (no views updated).
    # Called from: on_overlay_state_changed + apply_locale (app.py).
    def refresh_overlay_peer_contract(self) -> None:
        controller = getattr(self, "controller", None)
        build_contract = getattr(controller, "build_overlay_peer_consumer_contract", None)
        if not callable(build_contract):
            return
        contract = build_contract()
        self.overlay_peer_contract = contract
        if contract is None:
            return
        view_settings = getattr(self, "view_settings", None)
        set_settings_contract = getattr(view_settings, "set_overlay_peer_contract", None)
        if callable(set_settings_contract):
            set_settings_contract(contract)
        view_dashboard = getattr(self, "view_dashboard", None)
        set_dashboard_contract = getattr(view_dashboard, "set_overlay_peer_contract", None)
        if callable(set_dashboard_contract):
            set_dashboard_contract(contract)

    # AI: STATE AGGREGATION — gathers overlay state from 3 sources:
    # 1. self.overlay_state (set by on_overlay_state_changed)
    # 2. self.overlay_failure_reason (set by on_overlay_state_changed)
    # 3. controller.settings.overlay.target + controller.desktop_overlay_captions_locked
    # These are NOT always in sync — overlay_state changes before settings update.
    # This method bridges the gap for the settings UI.
    def _sync_settings_overlay_runtime_state(self) -> None:
        view_settings = getattr(self, "view_settings", None)
        set_state = getattr(view_settings, "set_overlay_runtime_state", None)
        if not callable(set_state):
            return
        controller = getattr(self, "controller", None)
        settings = getattr(controller, "settings", None)
        overlay_target = None
        if settings is not None:
            overlay_target = getattr(settings.overlay, "target", None)
        desktop_locked = False
        if controller is not None:
            desktop_locked = bool(getattr(controller, "desktop_overlay_captions_locked", False))
        set_state(
            self.overlay_state,
            failure_reason=self.overlay_failure_reason,
            overlay_target=overlay_target,
            desktop_captions_locked=desktop_locked,
        )

    # AI: ASYNC PATTERN — all _on_desktop_overlay_* methods use page.run_task (not mutation queue)
    # because overlay operations are independent of settings mutations.
    # Each calls _refresh_settings_desktop_overlay_state AFTER the async operation.
    # If the controller call raises, the UI refresh does NOT run (no try/finally).
    # This is a known limitation — stale UI state on error.
    def _on_desktop_overlay_lock_change(self, locked: bool) -> None:
        async def _task():
            await self.controller.set_desktop_overlay_captions_locked(bool(locked))
            self._refresh_settings_desktop_overlay_state()

        self.page.run_task(_task)

    def _on_desktop_overlay_size_change(self, size_preset: str) -> None:
        async def _task():
            await self.controller.set_desktop_overlay_size_preset(size_preset)
            self._refresh_settings_desktop_overlay_state()

        self.page.run_task(_task)

    def _on_desktop_overlay_recovery_action(self, action: str) -> None:
        if action not in {"retry", "reopen"}:
            return

        async def _task():
            await self.controller.set_overlay_enabled(True)

        self.page.run_task(_task)

    def _on_desktop_overlay_position_reset(self) -> None:
        async def _task():
            await self.controller.reset_desktop_overlay_position()
            self._refresh_settings_desktop_overlay_state()

        self.page.run_task(_task)

    # AI: TWO-STEP SYNC — first syncs settings object to view_settings (sync_desktop_overlay_settings),
    # then syncs runtime overlay state (set_overlay_runtime_state). Order matters:
    # settings sync must come first because runtime state sync reads overlay_target from settings.
    def _refresh_settings_desktop_overlay_state(self) -> None:
        controller = getattr(self, "controller", None)
        settings = getattr(controller, "settings", None)
        view_settings = getattr(self, "view_settings", None)
        sync_settings = getattr(view_settings, "sync_desktop_overlay_settings", None)
        if settings is not None and callable(sync_settings):
            sync_settings(settings)
        self._sync_settings_overlay_runtime_state()
