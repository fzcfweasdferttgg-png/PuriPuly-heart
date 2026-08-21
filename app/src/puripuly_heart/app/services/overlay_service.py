"""OverlayService — merged overlay lifecycle + desktop overlay management.

Combines OverlayLifecycleMixin (state machine, start/stop, bridge/presenter
lifecycle) and OverlayManagerMixin (desktop window bounds, interaction mode,
runtime controls) into a single service that OWNS all overlay state.

Flet-free: no ft.Page dependency — uses asyncio.create_task() directly.
All external dependencies (settings, hub, logging, etc.) are injected as
callbacks at construction time.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import math
import secrets
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from puripuly_heart.config.settings import (
    DESKTOP_FLET_MIN_HEIGHT,
    DESKTOP_FLET_MIN_WIDTH,
    DESKTOP_FLET_SIZE_PRESETS,
    OVERLAY_TARGET_DESKTOP,
    OVERLAY_TARGET_STEAMVR,
    AppSettings,
)
from puripuly_heart.adapters.overlay.bridge import OverlayBridge
from puripuly_heart.core.overlay.diagnostics import OverlayDiagnosticsRecorder
from puripuly_heart.core.overlay.presenter import OverlayPresenter
from puripuly_heart.app.services.overlay_process import (
    DefaultOverlayProcessRunner,
    DesktopFletOverlayRunner,
    OverlayProcessManager,
    OverlayProcessRunner,
)
from puripuly_heart.app.services.overlay_state_machine import OverlayStateMachine

if TYPE_CHECKING:
    from puripuly_heart.core.clock import SystemClock

logger = logging.getLogger(__name__)

# --- Module-level constants (from both mixins) ---

OVERLAY_STARTUP_TIMEOUT_MS = 3000
OVERLAY_SHUTDOWN_GRACE_S = 0.05
DESKTOP_BOUNDS_PERSIST_DEBOUNCE_S = 0.05
DESKTOP_INTERACTION_MODE_EDIT = "edit"
DESKTOP_INTERACTION_MODE_PASS_THROUGH = "pass_through"
DESKTOP_INTERACTION_MODES = frozenset(
    {DESKTOP_INTERACTION_MODE_EDIT, DESKTOP_INTERACTION_MODE_PASS_THROUGH}
)


@dataclass
class OverlayService:
    """Unified overlay lifecycle + desktop overlay management service.

    Owns all overlay state (bridge, presenter, manager, state machine,
    desktop bounds, interaction mode). External dependencies are injected
    as callbacks.
    """

    # --- Callbacks (injected at construction) ---

    _settings_provider: Callable[[], AppSettings | None]
    _hub_provider: Callable[[], object | None]  # Pipeline | None
    _clock: object  # SystemClock
    _runtime_logging_mode_provider: Callable[[], str]
    _log_basic: Callable[[str], None]
    _log_detailed: Callable[[str, int, BaseException | None], bool]
    _save_settings: Callable[[], None]
    _apply_settings: Callable[[AppSettings], Awaitable[None]]
    _sync_effective_hub_flags: Callable[[AppSettings | None], None]
    _refresh_overlay_peer_consumers: Callable[[], None]
    _refresh_peer_stt_runtime: Callable[[], Awaitable[None]]
    _ui_event_bridge_provider: Callable[[], object | None]
    _calibration_service_provider: Callable[[], object | None]
    _overlay_calibration_provider: Callable[[], object]
    _runtime_logging_provider: Callable[[], object]  # SessionRuntimeLoggingService
    _signature_detector_provider: Callable[[], object]  # SignatureChangeDetector

    # --- Owned state ---

    _overlay_state_machine: OverlayStateMachine = field(
        default_factory=OverlayStateMachine,
    )
    _overlay_bridge: OverlayBridge | None = None
    _overlay_presenter: OverlayPresenter | None = None
    _overlay_manager: OverlayProcessManager | None = None
    _overlay_start_task: asyncio.Task[None] | None = None
    _overlay_monitor_task: asyncio.Task[None] | None = None
    _overlay_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _active_overlay_target: str | None = field(init=False, default=None)
    _desktop_renderer_events: asyncio.Queue[dict[str, object]] | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _desktop_renderer_events_task: asyncio.Task[None] | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _desktop_bounds_persist_task: asyncio.Task[None] | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _pending_desktop_bounds: dict[str, int | float] | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _desktop_suppressed_bounds_signatures: set[tuple[float, float, float, float]] = field(
        init=False,
        default_factory=set,
        repr=False,
    )
    desktop_overlay_interaction_mode: str = field(
        init=False,
        default=DESKTOP_INTERACTION_MODE_EDIT,
    )

    # --- Convenience accessors ---

    @property
    def _settings(self) -> AppSettings | None:
        return self._settings_provider()

    @property
    def _hub(self) -> object | None:
        return self._hub_provider()

    @property
    def _sm(self) -> OverlayStateMachine:
        return self._overlay_state_machine

    @property
    def _runtime_logging(self) -> object:
        return self._runtime_logging_provider()

    @property
    def _ui_event_bridge(self) -> object | None:
        return self._ui_event_bridge_provider()

    @property
    def _calibration_service(self) -> object | None:
        return self._calibration_service_provider()

    @property
    def _overlay_calibration(self) -> object:
        return self._overlay_calibration_provider()

    @property
    def _signature_detector(self) -> object:
        return self._signature_detector_provider()

    @property
    def _runtime_logging_mode(self) -> str:
        return self._runtime_logging_mode_provider()

    # --- Logging helpers ---

    def _emit_log_basic(self, message: str, *, level: int = logging.INFO) -> None:
        self._log_basic(message)

    def _emit_log_detailed(
        self,
        message: str,
        *,
        level: int = logging.INFO,
        exception: BaseException | None = None,
    ) -> bool:
        return self._log_detailed(message, level, exception)

    # =====================================================================
    # PUBLIC API — state properties (from OverlayLifecycleMixin)
    # =====================================================================

    @property
    def overlay_state(self) -> str:
        return self._sm.state

    @overlay_state.setter
    def overlay_state(self, value: str) -> None:
        # Direct state assignment — used during initialization only.
        self._sm._state = value

    @property
    def failure_reason(self) -> str | None:
        return self._sm.failure_reason

    @failure_reason.setter
    def failure_reason(self, value: str | None) -> None:
        self._sm.failure_reason = value

    @property
    def auto_restart_scheduled(self) -> bool:
        return self._sm.auto_restart_scheduled

    @auto_restart_scheduled.setter
    def auto_restart_scheduled(self, value: bool) -> None:
        self._sm.auto_restart_scheduled = value

    # =====================================================================
    # PUBLIC API — desktop overlay (from OverlayManagerMixin)
    # =====================================================================

    @property
    def desktop_overlay_captions_locked(self) -> bool:
        return self.desktop_overlay_interaction_mode == DESKTOP_INTERACTION_MODE_PASS_THROUGH

    def overlay_target_for_settings(self, settings: AppSettings | None = None) -> str:
        resolved_settings = settings or self._settings
        if resolved_settings is None:
            return OVERLAY_TARGET_STEAMVR
        return self._normalized_overlay_target(resolved_settings.overlay.target)

    def overlay_runtime_is_active(self) -> bool:
        start_task = self._overlay_start_task
        return bool(
            self.overlay_state in {"starting", "connected"}
            or self._overlay_bridge is not None
            or self._overlay_manager is not None
            or (start_task is not None and not start_task.done())
        )

    def previous_overlay_target_for_apply(self) -> str:
        if self.overlay_runtime_is_active() and self._active_overlay_target is not None:
            return self._active_overlay_target
        return self.overlay_target_for_settings(self._settings)

    def build_initial_desktop_runtime_controls(
        self,
        settings: AppSettings,
    ) -> list[dict[str, object]]:
        desktop_settings = copy.deepcopy(settings.overlay.desktop_flet)
        desktop_settings.validate()
        bounds = self._desktop_launch_bounds_for_current_launch(desktop_settings)
        visual = desktop_settings.visual
        interaction_mode = DESKTOP_INTERACTION_MODE_EDIT
        self._emit_log_detailed(
            "[DesktopOverlay][Launch] "
            f"target=desktop locked={desktop_settings.locked} "
            f"interaction_mode={interaction_mode} "
            f"size_preset={desktop_settings.size_preset} "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']} "
            f"text_scale={visual.text_scale} "
            f"background_alpha={visual.background_alpha} "
            f"outline_width={visual.outline_width}"
        )
        return [
            {
                "command": "apply_window_bounds",
                "x": bounds["x"],
                "y": bounds["y"],
                "width": bounds["width"],
                "height": bounds["height"],
            },
            {
                "command": "apply_visual_config",
                "text_scale": visual.text_scale,
                "background_alpha": visual.background_alpha,
                "outline_width": visual.outline_width,
            },
            {"command": "set_interaction_mode", "mode": interaction_mode},
        ]

    async def set_desktop_overlay_captions_locked(self, locked: bool) -> None:
        if self._settings is None:
            return
        if self.overlay_state != "connected":
            return
        if self._active_overlay_target != OVERLAY_TARGET_DESKTOP or self._overlay_bridge is None:
            return

        mode = DESKTOP_INTERACTION_MODE_PASS_THROUGH if locked else DESKTOP_INTERACTION_MODE_EDIT
        if not await self._broadcast_desktop_runtime_control(
            {
                "command": "set_interaction_mode",
                "mode": mode,
            }
        ):
            return
        self._set_desktop_overlay_interaction_mode(mode)

    async def set_desktop_overlay_size_preset(self, size_preset: str) -> None:
        if self._settings is None:
            return
        normalized_size_preset = (
            size_preset if size_preset in DESKTOP_FLET_SIZE_PRESETS else "medium"
        )
        if self._settings.overlay.desktop_flet.size_preset == normalized_size_preset:
            return
        updated = copy.deepcopy(self._settings)
        updated.overlay.desktop_flet.size_preset = normalized_size_preset
        await self._apply_settings(updated)

    async def reset_desktop_overlay_position(self) -> None:
        await self._handle_desktop_overlay_reset_requested()

    async def broadcast_desktop_runtime_control_payloads(
        self,
        payloads: list[dict[str, object]],
    ) -> None:
        for payload in payloads:
            if payload.get("command") == "apply_window_bounds":
                bounds = self._desktop_bounds_from_payload(payload)
                if bounds is not None:
                    await self._broadcast_desktop_window_bounds_control(bounds)
                continue
            await self._broadcast_desktop_runtime_control(payload)

    def prepare_desktop_runtime_settings_update(
        self,
        previous_settings: AppSettings | None,
        next_settings: AppSettings,
    ) -> list[dict[str, object]]:
        if previous_settings is None:
            return []
        previous_desktop = copy.deepcopy(previous_settings.overlay.desktop_flet)
        previous_desktop.validate()
        next_desktop = next_settings.overlay.desktop_flet
        next_desktop.validate()

        if not self._desktop_runtime_is_running_for_settings_update(next_settings):
            return []

        controls: list[dict[str, object]] = []
        if previous_desktop.size_preset != next_desktop.size_preset:
            self._drain_pending_desktop_user_bounds_events()
            bounds = self._desktop_center_preserving_bounds_for_size_preset_change(
                previous_desktop_settings=previous_desktop,
                next_size_preset=next_desktop.size_preset,
            )
            if previous_desktop.position.x is not None and previous_desktop.position.y is not None:
                next_desktop.position.x = bounds["x"]
                next_desktop.position.y = bounds["y"]
                next_desktop.position.validate()
            controls.append({"command": "apply_window_bounds", **bounds})

        previous_visual = previous_desktop.visual
        next_visual = next_desktop.visual
        if (
            previous_visual.text_scale != next_visual.text_scale
            or previous_visual.background_alpha != next_visual.background_alpha
            or previous_visual.outline_width != next_visual.outline_width
        ):
            controls.append(
                {
                    "command": "apply_visual_config",
                    "text_scale": next_visual.text_scale,
                    "background_alpha": next_visual.background_alpha,
                    "outline_width": next_visual.outline_width,
                }
            )
        return controls

    def sync_desktop_overlay_interaction_mode_from_settings(
        self,
        settings: AppSettings,
    ) -> None:
        if self.overlay_target_for_settings(settings) != OVERLAY_TARGET_DESKTOP:
            return
        if (
            self._active_overlay_target == OVERLAY_TARGET_DESKTOP
            and self._overlay_bridge is not None
        ):
            return
        self._set_desktop_overlay_interaction_mode(DESKTOP_INTERACTION_MODE_EDIT)

    async def emit_overlay_runtime_logging_mode_update(self) -> None:
        bridge = self._overlay_bridge
        if bridge is None:
            return
        await bridge.broadcast_runtime_control(logging_mode=self._runtime_logging_mode)

    def schedule_overlay_runtime_logging_mode_update(self) -> None:
        bridge = self._overlay_bridge
        if bridge is None:
            return

        try:
            asyncio.get_running_loop().create_task(
                self.emit_overlay_runtime_logging_mode_update()
            )
        except RuntimeError:
            self._emit_log_detailed(
                "[Overlay] Skipping logging mode update; no running loop available",
                level=logging.WARNING,
            )

    async def cancel_desktop_renderer_event_task(self) -> None:
        await self._cancel_desktop_renderer_event_task()

    async def cancel_desktop_bounds_persistence(self) -> None:
        await self._cancel_desktop_bounds_persistence()

    # =====================================================================
    # PUBLIC API — lifecycle (from OverlayLifecycleMixin)
    # =====================================================================

    async def set_overlay_enabled(self, enabled: bool) -> None:
        if self._settings is None:
            return

        self._emit_log_basic(f"[Overlay] Toggle request: enabled={enabled}")
        self._emit_log_detailed(
            "[Overlay] Toggle detail: "
            f"current_state={self.overlay_state} "
            f"has_bridge={self._overlay_bridge is not None} "
            f"has_manager={self._overlay_manager is not None}"
        )
        self._settings.ui.overlay_enabled = bool(enabled)
        if not enabled:
            self._settings.ui.peer_translation_enabled = False
            self._signature_detector.last_peer_translation_enabled = False
            self._signature_detector.last_peer_translation_activation_requested = False
        self._refresh_overlay_peer_consumers()

        if enabled:
            await self.begin_overlay_start()
            self._save_settings()
            return

        await self.shutdown_overlay_runtime(preserve_failure_reason=True)
        self._save_settings()

    def on_overlay_start_failed(self, failure_reason: str | None) -> None:
        previous_state = self._sm.transition("failed", failure_reason=failure_reason)
        self._log_overlay_state_transition(previous_state, self.overlay_state)
        self._sync_effective_hub_flags(self._settings)
        self._notify_overlay_state()

    def on_overlay_runtime_disconnected(self) -> None:
        self.on_overlay_start_failed("runtime_disconnected")

    def on_overlay_runtime_crashed(self) -> None:
        self.on_overlay_start_failed("runtime_crashed")

    async def begin_overlay_start(self) -> None:
        async with self._overlay_lock:
            if not self._sm.is_startable():
                return

            await self._teardown_overlay_runtime(preserve_presenter_state=True)
            self._active_overlay_target = self.overlay_target_for_settings(self._settings)
            previous_state = self._sm.transition("start")
            self._log_overlay_state_transition(previous_state, self.overlay_state)
            self._notify_overlay_state()
            self._overlay_start_task = asyncio.create_task(self._run_overlay_start())

    async def shutdown_overlay_runtime(self, *, preserve_failure_reason: bool) -> None:
        self._emit_log_basic("[Overlay] Shutdown requested")
        self._emit_log_detailed(
            "[Overlay] Shutdown detail: "
            f"preserve_failure_reason={preserve_failure_reason} "
            f"state={self.overlay_state} "
            f"has_bridge={self._overlay_bridge is not None} "
            f"has_manager={self._overlay_manager is not None} "
            f"presenter_attached={self._overlay_presenter is not None}"
        )
        async with self._overlay_lock:
            has_runtime = (
                self._overlay_bridge is not None
                or self._overlay_manager is not None
                or (self._overlay_start_task is not None and not self._overlay_start_task.done())
            )
            if not has_runtime and self.overlay_state == "off":
                return

            previous_state = self._sm.transition("stop")
            self._log_overlay_state_transition(previous_state, self.overlay_state)
            self._notify_overlay_state()

            if self._overlay_manager is not None:
                self._overlay_manager.mark_shutdown_requested()
            await self._emit_overlay_shutdown()
            await self._teardown_overlay_runtime(preserve_presenter_state=False)
            previous_state = self._sm.transition("done")
            if not preserve_failure_reason:
                self._sm.clear_failure_reason()
            self._log_overlay_state_transition(previous_state, self.overlay_state)
            self._sync_effective_hub_flags(self._settings)
            await self._refresh_overlay_runtime_dependencies()
            self._notify_overlay_state()

    async def refresh_overlay_runtime_dependencies(self) -> None:
        await self._refresh_overlay_runtime_dependencies()

    # =====================================================================
    # PRIVATE — lifecycle internals (from OverlayLifecycleMixin)
    # =====================================================================

    async def _refresh_overlay_runtime_dependencies(self) -> None:
        if self._settings is None or self._hub is None:
            return

        await self._refresh_peer_stt_runtime()
        self._sync_effective_hub_flags(self._settings)
        self._refresh_overlay_peer_consumers()

    async def _run_overlay_start(self) -> None:
        current_task = asyncio.current_task()
        try:
            if self._settings is None or self._hub is None:
                self._active_overlay_target = None
                self.on_overlay_start_failed("unknown")
                return

            presenter = self._overlay_presenter
            overlay_instance_id = f"overlay-{secrets.token_hex(8)}"
            from puripuly_heart.config.paths import user_config_dir
            diagnostics = OverlayDiagnosticsRecorder(
                overlay_instance_id=overlay_instance_id,
                diagnostics_dir=user_config_dir() / "diagnostics" / "overlay",
            )
            overlay_target = self._active_overlay_target or self.overlay_target_for_settings(
                self._settings
            )
            self._active_overlay_target = overlay_target
            peer_presentation_refresh_burst = overlay_target != OVERLAY_TARGET_DESKTOP
            self_presentation_refresh_burst = overlay_target != OVERLAY_TARGET_DESKTOP
            self._emit_log_detailed(
                "[Overlay][Start] "
                f"target={overlay_target} "
                f"overlay_instance_id={overlay_instance_id} "
                f"logging_mode={self._runtime_logging_mode} "
                f"peer_presentation_refresh_burst={peer_presentation_refresh_burst} "
                f"self_presentation_refresh_burst={self_presentation_refresh_burst}"
            )

            if presenter is None:
                presenter = OverlayPresenter(
                    calibration=self._overlay_calibration.copy(),
                    clock=self._clock,
                    runtime_log_detailed=self._emit_log_detailed,
                    show_translation=self._settings.overlay.show_translation,
                    show_peer_original=self._settings.overlay.show_peer_original,
                    peer_presentation_refresh_burst=peer_presentation_refresh_burst,
                    self_presentation_refresh_burst=self_presentation_refresh_burst,
                )
                self._overlay_presenter = presenter
                if self._calibration_service is not None:
                    self._calibration_service.set_overlay_presenter(presenter)
            else:
                # Reuse existing presenter — update callback (may change between sessions)
                presenter.runtime_log_detailed = self._emit_log_detailed
                await presenter.update_peer_presentation_refresh_burst(
                    peer_presentation_refresh_burst
                )
                await presenter.update_self_presentation_refresh_burst(
                    self_presentation_refresh_burst
                )
            bridge = OverlayBridge(
                session_token=secrets.token_urlsafe(16),
                initial_snapshot=presenter.snapshot(),
                overlay_instance_id=overlay_instance_id,
                runtime_logging_mode=self._runtime_logging_mode,
                desktop_runtime_controls_enabled=overlay_target == OVERLAY_TARGET_DESKTOP,
            )
            if overlay_target == OVERLAY_TARGET_DESKTOP:
                initial_desktop_controls = self.build_initial_desktop_runtime_controls(
                    self._settings
                )
                initial_interaction_control = initial_desktop_controls[-1]
                self._set_desktop_overlay_interaction_mode(
                    initial_interaction_control.get("mode")
                )
                for payload in initial_desktop_controls:
                    self._track_desktop_apply_window_bounds_control(payload)
                bridge.set_initial_desktop_runtime_controls(initial_desktop_controls)
            await bridge.start()
            presenter.attach_bridge(bridge)
            latest_snapshot = presenter.snapshot()
            if bridge.snapshot() != latest_snapshot:
                await bridge.replace_snapshot(latest_snapshot)
            self._overlay_bridge = bridge
            hub = self._hub
            if hub is not None:
                hub.overlay_sink = presenter

            renderer_events: asyncio.Queue[dict[str, object]] | None = None
            if overlay_target == OVERLAY_TARGET_DESKTOP:
                renderer_events = asyncio.Queue(maxsize=64)
                self._desktop_renderer_events = renderer_events
                self._desktop_renderer_events_task = asyncio.create_task(
                    self._consume_desktop_renderer_events(renderer_events)
                )

            from puripuly_heart.app.wiring import get_or_create_job_handle
            from puripuly_heart.adapters.overlay.infrastructure import OverlayInfrastructure

            manager = OverlayProcessManager(
                process_runner=self._overlay_process_runner_for_target(overlay_target),
                bridge_url=bridge.url,
                bridge_messages=bridge.messages,
                session_token=bridge.session_token,
                locale=self._settings.ui.locale,
                startup_timeout_ms=OVERLAY_STARTUP_TIMEOUT_MS,
                renderer_events=renderer_events,
                overlay_instance_id=overlay_instance_id,
                logging_mode=self._runtime_logging_mode,
                diagnostics=diagnostics,
                job_handle=get_or_create_job_handle(),
                infrastructure=OverlayInfrastructure(),
            )
            self._overlay_manager = manager
            await manager.start()
            if self._overlay_manager is not manager:
                return

            if manager.state != "connected":
                await self._handle_overlay_start_failure(manager.failure_reason)
                return

            self._mark_overlay_connected()
            await self._refresh_overlay_runtime_dependencies()
            monitor_task = getattr(manager, "_monitor_task", None)
            if monitor_task is not None:
                self._overlay_monitor_task = asyncio.create_task(
                    self._watch_overlay_runtime(manager, monitor_task)
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            import traceback as _traceback

            _error_detail = (
                f"[Overlay] Failed to start overlay runtime\n"
                f"{_traceback.format_exception(type(exc), exc, exc.__traceback__)}"
            )
            self._runtime_logging.emit_persisted(_error_detail, level=logging.ERROR)
            logger.error("[Overlay] Failed to start overlay runtime: %s", exc, exc_info=True)
            await self._handle_overlay_start_failure("unknown")
        finally:
            if self._overlay_start_task is current_task:
                self._overlay_start_task = None

    async def _watch_overlay_runtime(
        self,
        manager: OverlayProcessManager,
        monitor_task: asyncio.Task[None],
    ) -> None:
        current_task = asyncio.current_task()
        try:
            await monitor_task
            if self._overlay_manager is not manager:
                return
            if manager.state != "failed":
                return

            reason = self._normalize_overlay_failure_reason(manager.failure_reason)
            if reason == "runtime_disconnected":
                self.on_overlay_runtime_disconnected()
            elif reason == "runtime_crashed":
                self.on_overlay_runtime_crashed()
            else:
                self.on_overlay_start_failed(reason)
            await self._teardown_overlay_runtime(preserve_presenter_state=True)
            await self._refresh_overlay_runtime_dependencies()
        except asyncio.CancelledError:
            raise
        finally:
            if self._overlay_monitor_task is current_task:
                self._overlay_monitor_task = None

    async def _handle_overlay_start_failure(self, failure_reason: str | None) -> None:
        self.on_overlay_start_failed(failure_reason)
        await self._teardown_overlay_runtime(preserve_presenter_state=True)
        await self._refresh_overlay_runtime_dependencies()

    async def _emit_overlay_shutdown(self) -> None:
        presenter = self._overlay_presenter
        if presenter is None:
            return
        with contextlib.suppress(Exception):
            await presenter.broadcast_shutdown()
            await asyncio.sleep(OVERLAY_SHUTDOWN_GRACE_S)

    async def _teardown_overlay_runtime(self, *, preserve_presenter_state: bool) -> None:
        current_task = asyncio.current_task()

        start_task = self._overlay_start_task
        if start_task is not None and start_task is not current_task and not start_task.done():
            start_task.cancel()
            await asyncio.gather(start_task, return_exceptions=True)
        if start_task is not None and start_task.done():
            self._overlay_start_task = None

        monitor_task = self._overlay_monitor_task
        if (
            monitor_task is not None
            and monitor_task is not current_task
            and not monitor_task.done()
        ):
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        if monitor_task is not None and monitor_task.done():
            self._overlay_monitor_task = None

        await self._cancel_desktop_renderer_event_task()
        await self._cancel_desktop_bounds_persistence()

        presenter = self._overlay_presenter
        if not preserve_presenter_state and presenter is not None:
            with contextlib.suppress(Exception):
                await presenter.clear_for_runtime_detach()
        if presenter is not None:
            presenter.detach_bridge()
        hub = self._hub
        if (
            presenter is not None
            and hub is not None
            and getattr(hub, "overlay_sink", None) is presenter
        ):
            if preserve_presenter_state:
                hub.overlay_sink = presenter
            else:
                hub.overlay_sink = None
                with contextlib.suppress(Exception):
                    await hub.reset_overlay_preview()
        if not preserve_presenter_state and presenter is not None:
            presenter.reset_scene()
            self._overlay_presenter = None
            if self._calibration_service is not None:
                self._calibration_service.set_overlay_presenter(None)

        manager = self._overlay_manager
        self._overlay_manager = None
        if manager is not None:
            with contextlib.suppress(Exception):
                await manager.stop()

        bridge = self._overlay_bridge
        self._overlay_bridge = None
        if bridge is not None:
            with contextlib.suppress(Exception):
                await bridge.stop()
        self._active_overlay_target = None
        self._desktop_suppressed_bounds_signatures.clear()
        if not preserve_presenter_state:
            self._set_desktop_overlay_interaction_mode(DESKTOP_INTERACTION_MODE_EDIT)

    def _mark_overlay_connected(self) -> None:
        previous_state = self._sm.transition("connected")
        self._log_overlay_state_transition(previous_state, self.overlay_state)
        self._sync_effective_hub_flags(self._settings)
        self._notify_overlay_state()

    @staticmethod
    def _normalize_overlay_failure_reason(failure_reason: str | None) -> str:
        return OverlayStateMachine._normalize_failure_reason(failure_reason)

    def _notify_overlay_state(self) -> None:
        bridge = self._ui_event_bridge
        if bridge is not None:
            bridge.report_overlay_state(self.overlay_state, failure_reason=self.failure_reason)

    def _log_overlay_state_transition(self, previous_state: str, next_state: str) -> None:
        manager = self._overlay_manager
        transition_message = f"[Overlay] State transition: {previous_state} -> {next_state}"
        if self.failure_reason is not None:
            transition_message = f"{transition_message} failure_reason={self.failure_reason}"
        self._emit_log_basic(transition_message)
        self._emit_log_detailed(
            "[Overlay] State detail: "
            f"presenter_attached={self._overlay_presenter is not None} "
            f"bridge_attached={self._overlay_bridge is not None} "
            f"manager_state={manager.state if manager is not None else None}"
        )

    # =====================================================================
    # PRIVATE — desktop overlay internals (from OverlayManagerMixin)
    # =====================================================================

    @staticmethod
    def _normalized_overlay_target(value: object) -> str:
        if value == OVERLAY_TARGET_DESKTOP:
            return OVERLAY_TARGET_DESKTOP
        return OVERLAY_TARGET_STEAMVR

    def _overlay_process_runner_for_target(self, target: str) -> OverlayProcessRunner:
        if target == OVERLAY_TARGET_DESKTOP:
            return DesktopFletOverlayRunner()
        return DefaultOverlayProcessRunner()

    @staticmethod
    def _desktop_dimensions_for_size_preset(size_preset: object) -> tuple[int, int]:
        if isinstance(size_preset, str) and size_preset in DESKTOP_FLET_SIZE_PRESETS:
            return DESKTOP_FLET_SIZE_PRESETS[size_preset]
        return DESKTOP_FLET_SIZE_PRESETS["medium"]

    def _desktop_launch_bounds_for_current_launch(
        self,
        desktop_settings: object,
    ) -> dict[str, int | float]:
        position = getattr(desktop_settings, "position", None)
        x = getattr(position, "x", None)
        y = getattr(position, "y", None)
        width, height = self._desktop_dimensions_for_size_preset(
            getattr(desktop_settings, "size_preset", None)
        )
        if self._is_finite_non_bool_number(x) and self._is_finite_non_bool_number(y):
            return {"x": x, "y": y, "width": width, "height": height}  # type: ignore[dict-item]
        return self._desktop_centered_bounds_for_dimensions(width=width, height=height)

    def _desktop_centered_bounds_for_dimensions(
        self,
        *,
        width: int | float,
        height: int | float,
    ) -> dict[str, int | float]:
        work_area = self._desktop_work_area_for_current_launch()
        if work_area is None:
            return {"x": 0, "y": 0, "width": width, "height": height}
        left, top, work_width, work_height = work_area
        if not (
            self._is_finite_non_bool_number(left)
            and self._is_finite_non_bool_number(top)
            and self._is_finite_non_bool_number(work_width)
            and self._is_finite_non_bool_number(work_height)
            and work_width > 0
            and work_height > 0
        ):
            return {"x": 0, "y": 0, "width": width, "height": height}

        return {
            "x": left + ((work_width - width) / 2),
            "y": top + ((work_height - height) / 2),
            "width": width,
            "height": height,
        }

    @staticmethod
    def _is_finite_non_bool_number(value: object) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )

    @staticmethod
    def _desktop_bounds_signature(
        bounds: dict[str, int | float],
    ) -> tuple[float, float, float, float]:
        return (
            float(bounds["x"]),
            float(bounds["y"]),
            float(bounds["width"]),
            float(bounds["height"]),
        )

    def _desktop_bounds_from_payload(
        self,
        payload: dict[object, object],
    ) -> dict[str, int | float] | None:
        x = payload.get("x")
        y = payload.get("y")
        width = payload.get("width")
        height = payload.get("height")
        if not (
            self._is_finite_non_bool_number(x)
            and self._is_finite_non_bool_number(y)
            and self._is_finite_non_bool_number(width)
            and self._is_finite_non_bool_number(height)
        ):
            return None
        if width < DESKTOP_FLET_MIN_WIDTH or height < DESKTOP_FLET_MIN_HEIGHT:  # type: ignore[operator]
            return None
        return {
            "x": x,  # type: ignore[dict-item]
            "y": y,  # type: ignore[dict-item]
            "width": width,  # type: ignore[dict-item]
            "height": height,  # type: ignore[dict-item]
        }

    def _is_valid_desktop_window_bounds_event_payload(
        self,
        payload: dict[object, object],
    ) -> bool:
        source = payload.get("source")
        persist = payload.get("persist")
        if source not in {"user", "reset", "programmatic", "launch_repair"}:
            return False
        expected_persist = source in {"user", "reset"}
        return bool(
            payload.get("event") == "window_bounds_changed"
            and isinstance(persist, bool)
            and persist is expected_persist
            and self._desktop_bounds_from_payload(payload) is not None
        )

    def _track_desktop_apply_window_bounds_control(self, payload: dict[str, object]) -> None:
        if payload.get("command") != "apply_window_bounds":
            return
        bounds = self._desktop_bounds_from_payload(payload)
        if bounds is None:
            return
        self._desktop_suppressed_bounds_signatures.add(self._desktop_bounds_signature(bounds))

    def _consume_suppressed_desktop_bounds(self, bounds: dict[str, int | float]) -> bool:
        signature = self._desktop_bounds_signature(bounds)
        if signature not in self._desktop_suppressed_bounds_signatures:
            return False
        self._desktop_suppressed_bounds_signatures.discard(signature)
        return True

    def _discard_suppressed_desktop_bounds(self, bounds: dict[str, int | float]) -> None:
        self._desktop_suppressed_bounds_signatures.discard(self._desktop_bounds_signature(bounds))

    @staticmethod
    def _is_desktop_user_window_bounds_event(event: object) -> bool:
        if not isinstance(event, dict):
            return False
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return False
        return bool(
            payload.get("event") == "window_bounds_changed"
            and payload.get("source") == "user"
            and payload.get("persist") is True
        )

    def _drain_pending_desktop_user_bounds_events(self) -> None:
        queue = self._desktop_renderer_events
        if queue is None:
            return
        retained: list[dict[str, object]] = []
        dropped = 0
        while True:
            try:
                event = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if self._is_desktop_user_window_bounds_event(event):
                dropped += 1
                continue
            retained.append(event)
        for event in retained:
            queue.put_nowait(event)
        if dropped:
            self._emit_log_detailed(
                f"[DesktopOverlay][Bounds] drained_pending_user_bounds count={dropped}"
            )

    def _set_desktop_overlay_interaction_mode(self, mode: object) -> bool:
        if not isinstance(mode, str) or mode not in DESKTOP_INTERACTION_MODES:
            return False
        previous_mode = self.desktop_overlay_interaction_mode
        self.desktop_overlay_interaction_mode = mode
        if previous_mode != mode:
            self._notify_desktop_overlay_interaction_mode()
        return True

    def _notify_desktop_overlay_interaction_mode(self) -> None:
        # Notify via callback if registered (set by GuiController)
        handler = getattr(self, "_on_desktop_overlay_state_changed", None)
        if callable(handler):
            handler(
                interaction_mode=self.desktop_overlay_interaction_mode,
                captions_locked=self.desktop_overlay_captions_locked,
            )

    async def _broadcast_desktop_runtime_control(self, payload: dict[str, object]) -> bool:
        if self._active_overlay_target != OVERLAY_TARGET_DESKTOP:
            return False
        bridge = self._overlay_bridge
        if bridge is None:
            return False
        broadcast = getattr(bridge, "broadcast_desktop_runtime_control", None)
        if not callable(broadcast):
            return False
        try:
            await broadcast(payload)
        except Exception as exc:
            self._emit_log_detailed(
                "[Overlay] Failed to send desktop runtime control",
                level=logging.WARNING,
                exception=exc,
            )
            return False
        return True

    async def _broadcast_desktop_window_bounds_control(
        self,
        bounds: dict[str, int | float],
    ) -> None:
        payload: dict[str, object] = {
            "command": "apply_window_bounds",
            "x": bounds["x"],
            "y": bounds["y"],
            "width": bounds["width"],
            "height": bounds["height"],
        }
        if await self._broadcast_desktop_runtime_control(payload):
            self._track_desktop_apply_window_bounds_control(payload)

    async def _consume_desktop_renderer_events(
        self,
        queue: asyncio.Queue[dict[str, object]],
    ) -> None:
        try:
            while True:
                event = await queue.get()
                try:
                    await self._handle_desktop_renderer_event(event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._emit_log_detailed(
                        "[Overlay] Ignoring desktop renderer event after controller error",
                        level=logging.WARNING,
                        exception=exc,
                    )
        except asyncio.CancelledError:
            raise

    async def _handle_desktop_renderer_event(self, event: object) -> None:
        if self._active_overlay_target != OVERLAY_TARGET_DESKTOP:
            return
        if not isinstance(event, dict):
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        event_type = payload.get("event")
        if event_type == "window_bounds_changed":
            await self._handle_desktop_window_bounds_changed(payload)
            return
        if event_type == "reset_to_bottom_center_requested":
            await self._handle_desktop_overlay_reset_requested()
            return
        if event_type == "interaction_mode_changed":
            self._set_desktop_overlay_interaction_mode(payload.get("mode"))

    async def _handle_desktop_window_bounds_changed(
        self,
        payload: dict[object, object],
    ) -> None:
        if not self._is_valid_desktop_window_bounds_event_payload(payload):
            self._emit_log_detailed(
                "[DesktopOverlay][Bounds] ignored reason=invalid_payload "
                f"keys={sorted(str(key) for key in payload)} "
                f"source={payload.get('source')} persist={payload.get('persist')}"
            )
            return
        bounds = self._desktop_bounds_from_payload(payload)
        if bounds is None:
            self._emit_log_detailed(
                "[DesktopOverlay][Bounds] ignored reason=invalid_bounds "
                f"source={payload.get('source')} persist={payload.get('persist')}"
            )
            return
        source = payload.get("source")
        interaction_mode = self.desktop_overlay_interaction_mode
        self._emit_log_detailed(
            "[DesktopOverlay][Bounds] received "
            f"source={source} persist={payload.get('persist')} "
            f"interaction_mode={interaction_mode} "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']}"
        )
        if source in {"programmatic", "launch_repair"}:
            self._emit_log_detailed(
                "[DesktopOverlay][Bounds] ignored reason=programmatic_source "
                f"source={source} x={bounds['x']} y={bounds['y']} "
                f"width={bounds['width']} height={bounds['height']}"
            )
            self._discard_suppressed_desktop_bounds(bounds)
            return
        if source == "reset":
            self._emit_log_detailed(
                "[DesktopOverlay][Bounds] reset_requested "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            await self._handle_desktop_overlay_reset_requested(bounds=bounds)
            return
        if source == "user" and interaction_mode != DESKTOP_INTERACTION_MODE_EDIT:
            self._emit_log_detailed(
                "[DesktopOverlay][Bounds] ignored reason=locked_interaction_mode "
                f"interaction_mode={interaction_mode} x={bounds['x']} y={bounds['y']} "
                f"width={bounds['width']} height={bounds['height']}"
            )
            return
        if self._consume_suppressed_desktop_bounds(bounds):
            self._emit_log_detailed(
                "[DesktopOverlay][Bounds] ignored reason=suppressed_signature "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            return
        self._schedule_desktop_bounds_persistence(bounds)
        self._emit_log_detailed(
            "[DesktopOverlay][Bounds] scheduled_persist "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']}"
        )

    def _schedule_desktop_bounds_persistence(
        self,
        bounds: dict[str, int | float],
    ) -> None:
        self._pending_desktop_bounds = dict(bounds)
        task = self._desktop_bounds_persist_task
        if task is not None and not task.done():
            task.cancel()
        self._desktop_bounds_persist_task = asyncio.create_task(
            self._persist_desktop_bounds_after_debounce()
        )

    async def _persist_desktop_bounds_after_debounce(self) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(DESKTOP_BOUNDS_PERSIST_DEBOUNCE_S)
            bounds = self._pending_desktop_bounds
            self._pending_desktop_bounds = None
            if bounds is None:
                return
            self._persist_desktop_bounds(bounds)
        except asyncio.CancelledError:
            raise
        finally:
            if self._desktop_bounds_persist_task is current_task:
                self._desktop_bounds_persist_task = None

    def _persist_desktop_bounds(self, bounds: dict[str, int | float]) -> None:
        if self._settings is None or self._active_overlay_target != OVERLAY_TARGET_DESKTOP:
            return
        if self._desktop_bounds_from_payload({"event": "window_bounds_changed", **bounds}) is None:
            return
        desktop_settings = self._settings.overlay.desktop_flet
        desktop_settings.position.x = bounds["x"]
        desktop_settings.position.y = bounds["y"]
        desktop_settings.position.validate()
        self._emit_log_detailed(
            "[DesktopOverlay][Bounds] persisted "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']} size_preset={desktop_settings.size_preset}"
        )
        self._save_settings()

    async def _handle_desktop_overlay_reset_requested(
        self,
        *,
        bounds: dict[str, int | float] | None = None,
    ) -> None:
        if self._settings is None:
            return
        configured_for_desktop = (
            self.overlay_target_for_settings(self._settings) == OVERLAY_TARGET_DESKTOP
        )
        desktop_renderer_active = bool(
            self._active_overlay_target == OVERLAY_TARGET_DESKTOP
            and self._overlay_bridge is not None
        )
        if not configured_for_desktop and not desktop_renderer_active:
            return
        await self._cancel_desktop_bounds_persistence()
        self._drain_pending_desktop_user_bounds_events()
        _ = bounds
        desktop_settings = self._settings.overlay.desktop_flet
        desktop_settings.position.x = None
        desktop_settings.position.y = None
        desktop_settings.locked = False
        desktop_settings.validate()
        self._set_desktop_overlay_interaction_mode(DESKTOP_INTERACTION_MODE_EDIT)
        self._save_settings()
        if not desktop_renderer_active:
            return
        await self._broadcast_desktop_runtime_control(
            {
                "command": "set_interaction_mode",
                "mode": DESKTOP_INTERACTION_MODE_EDIT,
            }
        )
        await self._broadcast_desktop_window_bounds_control(
            self._desktop_center_bounds_for_current_preset()
        )

    def _desktop_center_bounds_for_current_preset(self) -> dict[str, int | float]:
        assert self._settings is not None
        width, height = self._desktop_dimensions_for_size_preset(
            self._settings.overlay.desktop_flet.size_preset
        )
        return self._desktop_centered_bounds_for_dimensions(width=width, height=height)

    def _desktop_work_area_for_current_launch(
        self,
    ) -> tuple[int | float, int | float, int | float, int | float] | None:
        _ = self
        if sys.platform != "win32":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            # SPI_GETWORKAREA returns the primary monitor work area excluding taskbars.
            if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return None
            return (
                rect.left,
                rect.top,
                rect.right - rect.left,
                rect.bottom - rect.top,
            )
        except Exception:
            return None

    async def _cancel_desktop_renderer_event_task(self) -> None:
        current_task = asyncio.current_task()
        task = self._desktop_renderer_events_task
        self._desktop_renderer_events_task = None
        self._desktop_renderer_events = None
        if task is not None and task is not current_task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _cancel_desktop_bounds_persistence(self) -> None:
        current_task = asyncio.current_task()
        task = self._desktop_bounds_persist_task
        self._desktop_bounds_persist_task = None
        self._pending_desktop_bounds = None
        if task is not None and task is not current_task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _discard_pending_desktop_bounds_persistence(self) -> None:
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        task = self._desktop_bounds_persist_task
        self._desktop_bounds_persist_task = None
        self._pending_desktop_bounds = None
        if task is not None and task is not current_task and not task.done():
            task.cancel()

    def _desktop_runtime_is_running_for_settings_update(
        self,
        settings: AppSettings,
    ) -> bool:
        return bool(
            settings.ui.overlay_enabled
            and self._active_overlay_target == OVERLAY_TARGET_DESKTOP
            and self._overlay_bridge is not None
        )

    def _desktop_center_preserving_bounds_for_size_preset_change(
        self,
        *,
        previous_desktop_settings: object,
        next_size_preset: object,
    ) -> dict[str, int | float]:
        previous_bounds = self._desktop_launch_bounds_for_current_launch(previous_desktop_settings)
        next_width, next_height = self._desktop_dimensions_for_size_preset(next_size_preset)
        old_center_x = previous_bounds["x"] + (previous_bounds["width"] / 2)
        old_center_y = previous_bounds["y"] + (previous_bounds["height"] / 2)
        return {
            "x": old_center_x - (next_width / 2),
            "y": old_center_y - (next_height / 2),
            "width": next_width,
            "height": next_height,
        }
