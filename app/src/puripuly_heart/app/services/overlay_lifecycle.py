from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from typing import TYPE_CHECKING

from puripuly_heart.config.settings import OVERLAY_TARGET_DESKTOP
from puripuly_heart.adapters.overlay.bridge import OverlayBridge
from puripuly_heart.core.overlay.diagnostics import OverlayDiagnosticsRecorder
from puripuly_heart.core.overlay.presenter import OverlayPresenter
from puripuly_heart.core.overlay.process import OverlayProcessManager

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings

logger = logging.getLogger(__name__)

OVERLAY_STARTUP_TIMEOUT_MS = 3000
OVERLAY_SHUTDOWN_GRACE_S = 0.05
DESKTOP_INTERACTION_MODE_EDIT = "edit"
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


class OverlayLifecycleMixin:
    # State machine: off → starting → connected/failed → stopping → off

    async def _refresh_overlay_runtime_dependencies(self) -> None:
        if self.settings is None or self.hub is None:
            return

        await self._refresh_peer_stt_runtime()
        self._sync_effective_hub_flags(self.settings)
        self._refresh_overlay_peer_consumers()

    async def set_overlay_enabled(self, enabled: bool) -> None:
        if self.settings is None:
            return

        self.log_basic(f"[Overlay] Toggle request: enabled={enabled}")
        self.log_detailed(
            "[Overlay] Toggle detail: "
            f"current_state={self.overlay_state} "
            f"has_bridge={self._overlay_bridge is not None} "
            f"has_manager={self._overlay_manager is not None}"
        )
        self.settings.ui.overlay_enabled = bool(enabled)
        if not enabled:
            self.settings.ui.peer_translation_enabled = False
            self._last_peer_translation_enabled = False
            self._last_peer_translation_activation_requested = False
        self._refresh_overlay_peer_consumers()

        if enabled:
            await self._begin_overlay_start()
            self.save_settings()
            return

        await self._shutdown_overlay_runtime(preserve_failure_reason=True)
        self.save_settings()

    def on_overlay_start_failed(self, failure_reason: str | None) -> None:
        previous_state = self.overlay_state
        self.overlay_state = "failed"
        self.failure_reason = self._normalize_overlay_failure_reason(failure_reason)
        self.auto_restart_scheduled = False
        self._log_overlay_state_transition(previous_state, self.overlay_state)
        self._sync_effective_hub_flags()
        self._notify_overlay_state()

    def on_overlay_runtime_disconnected(self) -> None:
        self.on_overlay_start_failed("runtime_disconnected")

    def on_overlay_runtime_crashed(self) -> None:
        self.on_overlay_start_failed("runtime_crashed")

    async def _begin_overlay_start(self) -> None:
        if self._overlay_lock is None:
            self._overlay_lock = asyncio.Lock()

        async with self._overlay_lock:
            if self.overlay_state in {"starting", "connected"}:
                return

            await self._teardown_overlay_runtime(preserve_presenter_state=True)
            self._active_overlay_target = self._overlay_target_for_settings(self.settings)
            previous_state = self.overlay_state
            self.overlay_state = "starting"
            self.auto_restart_scheduled = False
            self._log_overlay_state_transition(previous_state, self.overlay_state)
            self._notify_overlay_state()
            self._overlay_start_task = asyncio.create_task(self._run_overlay_start())

    async def _run_overlay_start(self) -> None:
        current_task = asyncio.current_task()
        try:
            if self.settings is None or self.hub is None:
                self._active_overlay_target = None
                self.on_overlay_start_failed("unknown")
                return

            presenter = self._overlay_presenter
            overlay_instance_id = f"overlay-{secrets.token_hex(8)}"
            from puripuly_heart.config.paths import user_config_dir
            diagnostics = OverlayDiagnosticsRecorder(overlay_instance_id=overlay_instance_id, diagnostics_dir=user_config_dir() / "diagnostics" / "overlay")
            overlay_target = self._active_overlay_target or self._overlay_target_for_settings(
                self.settings
            )
            self._active_overlay_target = overlay_target
            peer_presentation_refresh_burst = overlay_target != OVERLAY_TARGET_DESKTOP
            self_presentation_refresh_burst = overlay_target != OVERLAY_TARGET_DESKTOP
            self.log_detailed(
                "[Overlay][Start] "
                f"target={overlay_target} "
                f"overlay_instance_id={overlay_instance_id} "
                f"logging_mode={self.runtime_logging_mode} "
                f"peer_presentation_refresh_burst={peer_presentation_refresh_burst} "
                f"self_presentation_refresh_burst={self_presentation_refresh_burst}"
            )

            if presenter is None:
                presenter = OverlayPresenter(
                    calibration=self.overlay_calibration.copy(),
                    clock=self.clock,
                    runtime_log_detailed=self.log_detailed,
                    show_translation=self.settings.overlay.show_translation,
                    show_peer_original=self.settings.overlay.show_peer_original,
                    peer_presentation_refresh_burst=peer_presentation_refresh_burst,
                    self_presentation_refresh_burst=self_presentation_refresh_burst,
                )
                self._overlay_presenter = presenter
            else:
                # Reuse existing presenter — update callback (may change between sessions)
                presenter.runtime_log_detailed = self.log_detailed
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
                runtime_logging_mode=self.runtime_logging_mode,
                desktop_runtime_controls_enabled=overlay_target == OVERLAY_TARGET_DESKTOP,
            )
            if overlay_target == OVERLAY_TARGET_DESKTOP:
                initial_desktop_controls = self._build_initial_desktop_runtime_controls(
                    self.settings
                )
                initial_interaction_control = initial_desktop_controls[-1]
                self._set_desktop_overlay_interaction_mode(initial_interaction_control.get("mode"))
                for payload in initial_desktop_controls:
                    self._track_desktop_apply_window_bounds_control(payload)
                bridge.set_initial_desktop_runtime_controls(initial_desktop_controls)
            await bridge.start()
            presenter.attach_bridge(bridge)
            latest_snapshot = presenter.snapshot()
            if bridge.snapshot() != latest_snapshot:
                await bridge.replace_snapshot(latest_snapshot)
            self._overlay_bridge = bridge
            self.hub.overlay_sink = presenter

            renderer_events: asyncio.Queue[dict[str, object]] | None = None
            if overlay_target == OVERLAY_TARGET_DESKTOP:
                renderer_events = asyncio.Queue(maxsize=64)
                self._desktop_renderer_events = renderer_events
                self._desktop_renderer_events_task = asyncio.create_task(
                    self._consume_desktop_renderer_events(renderer_events)
                )

            from puripuly_heart.app.wiring import get_or_create_job_handle

            manager = OverlayProcessManager(
                process_runner=self._overlay_process_runner_for_target(overlay_target),
                bridge_url=bridge.url,
                bridge_messages=bridge.messages,
                session_token=bridge.session_token,
                locale=self.settings.ui.locale,
                startup_timeout_ms=OVERLAY_STARTUP_TIMEOUT_MS,
                renderer_events=renderer_events,
                overlay_instance_id=overlay_instance_id,
                logging_mode=self.runtime_logging_mode,
                diagnostics=diagnostics,
                job_handle=get_or_create_job_handle(),
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
            self.runtime_logging.emit_persisted(_error_detail, level=logging.ERROR)
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

    async def _shutdown_overlay_runtime(self, *, preserve_failure_reason: bool) -> None:
        if self._overlay_lock is None:
            self._overlay_lock = asyncio.Lock()

        self.log_basic("[Overlay] Shutdown requested")
        self.log_detailed(
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

            previous_state = self.overlay_state
            self.overlay_state = "stopping"
            self.auto_restart_scheduled = False
            self._log_overlay_state_transition(previous_state, self.overlay_state)
            self._notify_overlay_state()

            if self._overlay_manager is not None:
                self._overlay_manager.mark_shutdown_requested()
            await self._emit_overlay_shutdown()
            await self._teardown_overlay_runtime(preserve_presenter_state=False)
            previous_state = self.overlay_state
            self.overlay_state = "off"
            if not preserve_failure_reason:
                self.failure_reason = None
            self._log_overlay_state_transition(previous_state, self.overlay_state)
            self._sync_effective_hub_flags()
            await self._refresh_overlay_runtime_dependencies()
            self._notify_overlay_state()

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
        if (
            presenter is not None
            and self.hub is not None
            and getattr(self.hub, "overlay_sink", None) is presenter
        ):
            if preserve_presenter_state:
                self.hub.overlay_sink = presenter
            else:
                self.hub.overlay_sink = None
                with contextlib.suppress(Exception):
                    await self.hub.reset_overlay_preview()
        if not preserve_presenter_state and presenter is not None:
            presenter.reset_scene()
            self._overlay_presenter = None

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
        previous_state = self.overlay_state
        self.overlay_state = "connected"
        self.failure_reason = None
        self.auto_restart_scheduled = False
        self._log_overlay_state_transition(previous_state, self.overlay_state)
        self._sync_effective_hub_flags()
        self._notify_overlay_state()

    def _normalize_overlay_failure_reason(self, failure_reason: str | None) -> str:
        if isinstance(failure_reason, str) and failure_reason in _OVERLAY_FAILURE_REASONS:
            return failure_reason
        return "unknown"

    def _notify_overlay_state(self) -> None:
        bridge = self._ui_event_bridge
        if bridge is not None:
            bridge.report_overlay_state(self.overlay_state, failure_reason=self.failure_reason)

    def _log_overlay_state_transition(self, previous_state: str, next_state: str) -> None:
        manager = self._overlay_manager
        transition_message = f"[Overlay] State transition: {previous_state} -> {next_state}"
        if self.failure_reason is not None:
            transition_message = f"{transition_message} failure_reason={self.failure_reason}"
        self.log_basic(transition_message)
        self.log_detailed(
            "[Overlay] State detail: "
            f"presenter_attached={self._overlay_presenter is not None} "
            f"bridge_attached={self._overlay_bridge is not None} "
            f"manager_state={manager.state if manager is not None else None}"
        )
