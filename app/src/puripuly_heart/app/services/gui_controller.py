"""GuiController — application orchestrator for the PuriPuly GUI.

Owns the pipeline, overlay, STT, peer, diagnostics, and clipboard lifecycles.
Does NOT own UI layout — that stays in ui/views/.  Every public method is
either a lifecycle operation (start/stop) or a settings mutation (dispatched
via SettingsCommandExecutor).

Threading: asyncio event loop runs in a background thread (daemon).  All flet
UI mutations must run on the main thread via self.page.update().  Audio
callbacks (vad_cb, vad_peer_cb) run on the audio thread — they only set
events or call asyncio.run_coroutine_threadsafe() to hand off to the event
loop thread.

Hex violations in core/ (provider_manager, pipeline_lifecycle) are resolved
by passing callables from app.wiring at construction time, not by importing
app.wiring from core/.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from puripuly_heart.app.wiring import (
    create_llm_provider,
    create_fallback_llm_provider,
    create_osc_sink,
    create_peer_stt_backend,
    create_secret_store,
    create_stt_backend,
)
from puripuly_heart.app.headless_mic import run_audio_vad_loop
from puripuly_heart.config.paths import default_models_dir, default_vad_model_path
from puripuly_heart.adapters.storage.settings_persistence import save_settings
from puripuly_heart.config.settings import (
    DESKTOP_FLET_MIN_HEIGHT,
    DESKTOP_FLET_MIN_WIDTH,
    DESKTOP_FLET_SIZE_PRESETS,
    OVERLAY_TARGET_DESKTOP,
    OVERLAY_TARGET_STEAMVR,
    AppSettings,
    LLMProviderName,
    STTProviderName,
)
from puripuly_heart.config.vad_defaults import DEFAULT_STABLE_VAD_HANGOVER_MS
from puripuly_heart.core.runtime.local_qwen_lifecycle import LOCAL_QWEN_IDLE_RELEASE_SECONDS
from puripuly_heart.core.audio.gate import VrcMicAudioGate
from puripuly_heart.core.audio.source import (
    SoundDeviceAudioSource,
    determine_self_mic_capture_channels,
)
from puripuly_heart.ports.model_discovery import ModelDiscovery
from puripuly_heart.ports.osc import OscSink
from puripuly_heart.core.clock import SystemClock
from puripuly_heart.core.verification.api_key_verifier import ApiKeyVerifier
from puripuly_heart.app.services.provider_manager import ProviderManager
from puripuly_heart.app.services.pipeline_lifecycle import PipelineLifecycleManager
from puripuly_heart.app.services.toggle_coordinator import ToggleCoordinator
from puripuly_heart.app.services.local_stt_manager import LOCAL_STT_PROVIDERS, LocalSTTManager
from puripuly_heart.core.pipeline.pipeline import Pipeline
from puripuly_heart.adapters.overlay.sink import OverlayEventAdapter
from puripuly_heart.config.prompts import render_dual_translation_prompt_template, render_translation_prompt_template, warm_prompt_cache
from puripuly_heart.core.osc.receiver import (
    VRC_OSC_RECEIVER_HOST,
    VRC_OSC_RECEIVER_PORT,
    VrcMicState,
    VrcOscReceiver,
)
from puripuly_heart.adapters.overlay.bridge import OverlayBridge
from puripuly_heart.core.overlay.presenter import OverlayPresenter
from puripuly_heart.app.services.overlay_process import OverlayProcessManager
from puripuly_heart.core.runtime.peer_channel import PeerChannelRuntime
from puripuly_heart.core.runtime_logging import SessionLoggingMode, SessionRuntimeLoggingService
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.core.vad.bundled import ensure_silero_vad_onnx
from puripuly_heart.app.services.ui_bridge import UIEventBridge
from puripuly_heart.domain.i18n import get_locale, set_locale, t
from puripuly_heart.domain.overlay_calibration import OverlayCalibration
from puripuly_heart.domain.overlay_contract import (
    OverlayPeerConsumerContract,
    build_overlay_peer_consumer_contract,
)
from puripuly_heart.app.services.overlay_service import OverlayService, DESKTOP_INTERACTION_MODE_EDIT
from puripuly_heart.app.services.clipboard_service import ClipboardService
from puripuly_heart.app.services.mic_test_service import MicTestService
from puripuly_heart.app.services.peer_toggle_coordinator import PeerToggleCoordinator as PeerToggleCoordinatorSvc
from puripuly_heart.app.services.calibration_service import CalibrationService
from puripuly_heart.app.services.diagnostics_service_runtime import DiagnosticsService
from puripuly_heart.app.services.peer_runtime_service import PeerRuntimeService, build_peer_runtime_config
from puripuly_heart.app.services.settings_service import SettingsService
from puripuly_heart.app.services.signature_detector import SignatureChangeDetector

logger = logging.getLogger(__name__)

# Hardcoded STT session reset deadline (not configurable via settings)
STT_RESET_DEADLINE_S = 300.0


@dataclass(slots=True)
class BaseGuiController:
    page: object
    app: object
    config_path: Path

    settings: AppSettings | None = None
    clock: SystemClock = SystemClock()
    _settings_service: SettingsService | None = None

    _pipeline_manager: PipelineLifecycleManager | None = None
    model_discovery: ModelDiscovery | None = None
    _api_key_verifier: ApiKeyVerifier | None = None
    _provider_manager: ProviderManager | None = None
    _peer_runtime: PeerChannelRuntime | None = None
    _peer_runtime_service: PeerRuntimeService | None = None
    _diagnostics_service: DiagnosticsService | None = None

    _bridge_task: asyncio.Task[None] | None = None
    _mic_test_service: MicTestService | None = None
    _toggle_coordinator: ToggleCoordinator | None = None
    _peer_toggle_coordinator: PeerToggleCoordinatorSvc | None = None
    _signature_detector: SignatureChangeDetector = field(
        default_factory=SignatureChangeDetector,
    )
    _overlay_service: OverlayService | None = None
    _ui_event_bridge: UIEventBridge | None = None
    _clipboard_service: ClipboardService | None = None
    _local_stt_manager: LocalSTTManager | None = field(init=False, default=None)
    _runtime_logging: SessionRuntimeLoggingService | None = field(init=False, default=None)
    _mutation_queue: list = field(init=False, default_factory=list)
    _mutation_worker_active: bool = field(init=False, default=False)

    _calibration_service: CalibrationService | None = None
    _async_loop: object | None = field(init=False, default=None)
    log_handler_factory: Callable[[Any], logging.Handler] | None = field(default=None)

    @property
    def overlay_calibration(self) -> OverlayCalibration:
        if self._calibration_service is not None:
            return self._calibration_service.overlay_calibration
        return OverlayCalibration()

    @overlay_calibration.setter
    def overlay_calibration(self, value: OverlayCalibration) -> None:
        if self._calibration_service is not None:
            self._calibration_service.overlay_calibration = value

    # --- OverlayService property delegations ---

    @property
    def overlay_state(self) -> str:
        if self._overlay_service is not None:
            return self._overlay_service.overlay_state
        return "off"

    @overlay_state.setter
    def overlay_state(self, value: str) -> None:
        if self._overlay_service is not None:
            self._overlay_service.overlay_state = value

    @property
    def failure_reason(self) -> str | None:
        if self._overlay_service is not None:
            return self._overlay_service.failure_reason
        return None

    @failure_reason.setter
    def failure_reason(self, value: str | None) -> None:
        if self._overlay_service is not None:
            self._overlay_service.failure_reason = value

    @property
    def auto_restart_scheduled(self) -> bool:
        if self._overlay_service is not None:
            return self._overlay_service.auto_restart_scheduled
        return False

    @auto_restart_scheduled.setter
    def auto_restart_scheduled(self, value: bool) -> None:
        if self._overlay_service is not None:
            self._overlay_service.auto_restart_scheduled = value

    @property
    def desktop_overlay_captions_locked(self) -> bool:
        if self._overlay_service is not None:
            return self._overlay_service.desktop_overlay_captions_locked
        return False

    @property
    def desktop_overlay_interaction_mode(self) -> str:
        if self._overlay_service is not None:
            return self._overlay_service.desktop_overlay_interaction_mode
        return DESKTOP_INTERACTION_MODE_EDIT

    @property
    def _overlay_bridge(self) -> OverlayBridge | None:
        if self._overlay_service is not None:
            return self._overlay_service._overlay_bridge
        return None

    @property
    def _overlay_presenter(self) -> OverlayPresenter | None:
        if self._overlay_service is not None:
            return self._overlay_service._overlay_presenter
        return None

    @property
    def _overlay_manager(self) -> OverlayProcessManager | None:
        if self._overlay_service is not None:
            return self._overlay_service._overlay_manager
        return None

    @property
    def effective_context_mode(self) -> str:
        if self.settings is None:
            return "local"
        if self._effective_integrated_context_enabled_for(self.settings):
            return "integrated"
        return "local"

    # Backward-compatible properties delegating to PipelineLifecycleManager
    @property
    def hub(self) -> Pipeline | None:
        return self._pipeline_manager.hub if self._pipeline_manager else None

    @hub.setter
    def hub(self, value: Pipeline | None) -> None:
        if self._pipeline_manager is not None:
            self._pipeline_manager.hub = value

    @property
    def sender(self) -> object | None:
        return self._pipeline_manager.sender if self._pipeline_manager else None

    @property
    def osc(self) -> OscSink | None:
        return self._pipeline_manager.osc if self._pipeline_manager else None

    @property
    def _last_vrc_mic_sync_enabled(self) -> bool | None:
        return self._pipeline_manager._last_vrc_mic_sync_enabled if self._pipeline_manager else None

    @property
    def _last_mic_loop_close_exception(self) -> BaseException | None:
        return self._pipeline_manager._last_mic_loop_close_exception if self._pipeline_manager else None

    @property
    def receiver(self) -> object | None:
        return self._pipeline_manager.receiver if self._pipeline_manager else None

    @property
    def vrc_mic_state(self) -> VrcMicState | None:
        return self._pipeline_manager.vrc_mic_state if self._pipeline_manager else None

    @property
    def vrc_mic_audio_gate(self) -> VrcMicAudioGate | None:
        return self._pipeline_manager.vrc_mic_audio_gate if self._pipeline_manager else None

    @property
    def _stt_desired(self) -> bool:
        return self._toggle_coordinator.stt_desired if self._toggle_coordinator else False

    @_stt_desired.setter
    def _stt_desired(self, value: bool) -> None:
        if self._toggle_coordinator:
            self._toggle_coordinator.stt_desired = value

    @property
    def _is_stopping(self) -> bool:
        return self._toggle_coordinator._is_stopping if self._toggle_coordinator else False

    @_is_stopping.setter
    def _is_stopping(self, value: bool) -> None:
        if self._toggle_coordinator:
            self._toggle_coordinator._is_stopping = value

    @property
    def _mic_task(self) -> object | None:
        return self._pipeline_manager._mic_task if self._pipeline_manager else None

    @property
    def _audio_source(self) -> object | None:
        return self._pipeline_manager._audio_source if self._pipeline_manager else None

    @property
    def _local_stt_pending_enable_after_install(self) -> bool:
        if self._local_stt_manager is not None:
            return self._local_stt_manager._pending_enable_after_install
        return False

    # --- DiagnosticsService delegation ---

    @property
    def debug_capture_fault_profile(self) -> str:
        return self._diagnostics_service.debug_capture_fault_profile

    @property
    def debug_stt_fault_profile(self) -> str:
        return self._diagnostics_service.debug_stt_fault_profile

    def _debug_audio_fault_allowed(self) -> bool:
        return self._diagnostics_service.debug_audio_fault_allowed()

    def _detailed_audio_diag_enabled(self) -> bool:
        return self._diagnostics_service.detailed_audio_diag_enabled()

    async def _on_self_terminal_failure(self, exc: Exception) -> None:
        await self._diagnostics_service.on_self_terminal_failure(exc)

    def _on_final_transcript_suppressed(self, notification) -> None:
        self._diagnostics_service.on_final_transcript_suppressed(notification)

    def _wrap_diagnostic_audio_source(self, source, *, channel_label: str):
        return self._diagnostics_service.wrap_diagnostic_audio_source(source, channel_label=channel_label)

    def _schedule_audio_environment_snapshot(self) -> None:
        run_task = self._get_page_run_task()
        self._diagnostics_service.schedule_audio_environment_snapshot(page_run_task=run_task)

    async def _log_audio_environment_snapshot_async(self) -> None:
        await self._diagnostics_service._log_audio_environment_snapshot_async()

    def cycle_debug_capture_fault_profile(self) -> str:
        return self._diagnostics_service.cycle_debug_capture_fault_profile()

    def cycle_debug_stt_fault_profile(self) -> str:
        return self._diagnostics_service.cycle_debug_stt_fault_profile()

    def clear_debug_audio_fault_profiles(self) -> None:
        self._diagnostics_service.clear_debug_audio_fault_profiles()

    async def start(self) -> None:
        self._settings_service = SettingsService(
            _config_path=self.config_path,
            _hub_provider=lambda: self.hub,
            _signature_detector_provider=lambda: self._signature_detector,
            _log_basic=lambda msg: self.log_basic(msg),
            _log_detailed=lambda msg: self.log_detailed(msg),
            _log_error=lambda msg: self._log_error(msg),
            _mic_test_audio_signature=lambda s: MicTestService._microphone_test_audio_settings_signature(s),
            _peer_activation_requested=lambda s: self._peer_translation_activation_requested_for(s),
            _build_peer_runtime_config=lambda s: build_peer_runtime_config(s),
        )
        self.settings = self._settings_service.load_or_init_settings(self.config_path)
        self.settings.ui.overlay_enabled = False
        self.settings.ui.peer_translation_enabled = False
        self._calibration_service = CalibrationService(
            _save_settings=lambda: self.save_settings(),
            _log_detailed=lambda msg: self.log_detailed(msg),
        )
        self._calibration_service.sync_from_settings(self.settings)

        # DiagnosticsService — owns audio diagnostics state
        self._diagnostics_service = DiagnosticsService(
            is_debug_ui_preview=lambda: getattr(self.app, "debug_ui_preview", False),
            show_hallucination_dialog=getattr(self.app, "show_local_qwen_hallucination_dialog", None),
            set_stt_desired=lambda v: setattr(self._toggle_coordinator, "stt_desired", v) if self._toggle_coordinator else None,
            set_dash_stt_enabled=lambda v: (dash := getattr(self.app, "view_dashboard", None)) and dash.set_stt_enabled(v),
            log_basic=lambda msg: self.log_basic(msg),
            log_detailed=lambda msg, exc=None: self.log_detailed(msg, exception=exc),
            log_error=lambda msg: self._log_error(msg),
            runtime_logging=self.runtime_logging,
        )

        set_locale(self.settings.ui.locale)
        self._sync_ui_from_settings()
        with contextlib.suppress(Exception):
            apply_locale = getattr(self.app, "apply_locale", None)
            if callable(apply_locale):
                apply_locale()

        runtime_logging = self.runtime_logging
        runtime_logging.set_mode(SessionLoggingMode.BASIC)

        # Attach realtime sink to LogsView for GUI log display
        logs_view = getattr(self.app, "view_logs", None)
        if logs_view is not None:
            runtime_logging.attach_realtime_sink(logs_view)

        # PipelineLifecycleManager — core service for pipeline lifecycle
        if self._pipeline_manager is None:
            self._pipeline_manager = PipelineLifecycleManager(
                config_path=self.config_path,
                clock=self.clock,
                on_error=self._log_error,
                diagnostic_wrapper=self._wrap_diagnostic_audio_source,
                is_detailed_diag_enabled=lambda: self._detailed_audio_diag_enabled,
                run_audio_vad_loop=run_audio_vad_loop,
                default_vad_hangover_ms=DEFAULT_STABLE_VAD_HANGOVER_MS,
                vad_model_path_factory=default_vad_model_path,
                render_prompt_template=render_translation_prompt_template,
                render_dual_prompt_template=render_dual_translation_prompt_template,
                warm_prompt_cache_fn=warm_prompt_cache,
            )

        await self._init_pipeline()
        self._refresh_local_stt_runtime_state()

        assert self.hub is not None

        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            # Set needs_key flags based on saved verification status & key existence
            # STT: no network providers — never needs a key
            dash.stt_needs_key = False

            # LLM: check current provider's verification status
            llm_provider = self.settings.provider.llm.value
            if self._llm_provider_requires_secret(self.settings.provider.llm):
                llm_verified = self.settings.api_key_verified.is_verified(llm_provider)
                dash.translation_needs_key = (self.hub.llm is None) or (not llm_verified)
            else:
                dash.translation_needs_key = False

            # Set initial enabled states (all start as off/gray)
            dash.set_translation_enabled(False)
            dash.set_stt_enabled(False)
            self.hub.translation_enabled = False

        await self.hub.start(auto_flush_osc=True)

        bridge = UIEventBridge(
            app=self.app,
            event_queue=self.hub.ui_events,
            runtime_logging=runtime_logging,
        )
        self._ui_event_bridge = bridge
        self._bridge_task = asyncio.create_task(bridge.run())

        # ClipboardService — clipboard watcher + manual-typing state machine
        import inspect as _inspect

        async def _submit_text_and_wait(text: str, source: str) -> None:
            if self.hub is None:
                return
            utterance_id = await self.hub.submit_text(text, source=source)
            runtime = getattr(self.hub, "self_runtime", None)
            tasks = getattr(runtime, "translation_tasks", None)
            task = tasks.get(utterance_id) if isinstance(tasks, dict) else None
            if isinstance(task, asyncio.Task):
                await asyncio.gather(task, return_exceptions=True)
            elif _inspect.isawaitable(task):
                await task

        def _set_osc_typing(reason: str, active: bool) -> None:
            osc = self.osc
            if osc is None:
                return
            set_reason = getattr(osc, "set_typing_reason", None)
            if callable(set_reason):
                set_reason(reason, active)
                return
            osc.send_typing(active)

        self._clipboard_service = ClipboardService(
            _submit_text_and_wait=_submit_text_and_wait,
            _set_osc_typing=_set_osc_typing,
            _clock=self.clock,
            _log_error=lambda msg: self._log_error(msg),
            _settings_provider=lambda: self.settings,
            _page_run_task=self._get_page_run_task(),
        )

        await self._sync_clipboard_watcher()


    async def stop(self) -> None:
        self._is_stopping = True
        self._cancel_stt_idle_release()
        await self._stop_clipboard_watcher()
        await self._cancel_local_stt_download()
        await self.stop_microphone_test()
        await self.set_stt_enabled(False)
        await self._configure_vrc_mic_receiver(enabled=False)
        await self._reset_manual_typing_state()
        await self.shutdown_overlay_runtime(preserve_failure_reason=True)
        if self._peer_runtime is not None:
            with contextlib.suppress(Exception):
                await self._peer_runtime.close()
            self._peer_runtime = None

        if self._bridge_task:
            self._bridge_task.cancel()
            await asyncio.gather(self._bridge_task, return_exceptions=True)
            self._bridge_task = None
        self._ui_event_bridge = None

        if self.hub is not None:
            with contextlib.suppress(Exception):
                await self.hub.stop()

        if self._runtime_logging is not None:
            with contextlib.suppress(Exception):
                self._runtime_logging.close()
            self._runtime_logging = None

    # --- ClipboardService delegation ---

    async def _sync_clipboard_watcher(self) -> None:
        if self._clipboard_service is not None:
            await self._clipboard_service.sync_clipboard_watcher()

    async def _stop_clipboard_watcher(self) -> None:
        if self._clipboard_service is not None:
            await self._clipboard_service.stop_clipboard_watcher()

    def note_manual_input_activity(self, has_text: bool) -> None:
        if self._clipboard_service is not None:
            self._clipboard_service.note_manual_input_activity(has_text)

    async def submit_text(self, text: str) -> None:
        if self._clipboard_service is not None:
            await self._clipboard_service.submit_text(text)

    async def _reset_manual_typing_state(self) -> None:
        if self._clipboard_service is not None:
            await self._clipboard_service.reset_manual_typing_state()


    async def set_translation_enabled(self, enabled: bool) -> bool:
        if self._toggle_coordinator is None:
            return False
        result = await self._toggle_coordinator.set_translation_enabled(enabled)
        # UI side-effects
        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            dash.set_translation_enabled(result)
        return result

    async def set_stt_enabled(self, enabled: bool) -> None:
        if self._toggle_coordinator is None:
            return
        await self._toggle_coordinator.set_stt_enabled(enabled)

    def _show_short_stt_message(self, message_key: str) -> None:
        self._show_short_message(message_key)

    def _show_short_message(self, message_key: str, **message_kwargs: object) -> None:
        message = t(message_key, **message_kwargs)
        notifier = getattr(self.app, "_show_notification", None)
        if callable(notifier):
            with contextlib.suppress(Exception):
                notifier(message, level="warning")
                return
        self._log_error(message)

    def _refresh_local_stt_runtime_state(self) -> None:
        if self._local_stt_manager is not None:
            self._local_stt_manager.refresh_runtime_state()

    def _current_local_stt_runtime_status(self) -> str:
        if self._local_stt_manager is not None:
            return self._local_stt_manager.current_status()
        return "ready"

    def _peer_local_stt_requested(self, settings: AppSettings | None = None) -> bool:
        if self._local_stt_manager is None:
            return False
        resolved_settings = settings or self.settings
        if resolved_settings is None:
            return False
        return self._local_stt_manager.peer_local_stt_requested(resolved_settings)

    def _reset_local_stt_pending_enable_after_install(self) -> None:
        if self._local_stt_manager is not None:
            self._local_stt_manager.reset_pending_enable()

    def _reset_local_stt_pending_peer_enable_after_install(self) -> None:
        if self._local_stt_manager is not None:
            self._local_stt_manager.reset_pending_peer_enable()

    def _clear_local_stt_pending_enable_if_provider_switched_away(self) -> None:
        if self._local_stt_manager is not None and self.settings is not None:
            self._local_stt_manager.clear_pending_if_provider_switched_away(self.settings)

    def _sync_local_stt_notice(self) -> None:
        dash = getattr(self.app, "view_dashboard", None)
        if dash is None or self.settings is None:
            return
        status = self._current_local_stt_runtime_status()
        should_show = status == "downloading" or (
            (
                self.settings.provider.stt in LOCAL_STT_PROVIDERS
                or self._peer_local_stt_requested(self.settings)
            )
            and status != "ready"
        )
        download_percent = self._local_stt_manager.download_percent if self._local_stt_manager is not None else None
        with contextlib.suppress(Exception):
            dash.set_local_stt_notice(
                status if should_show else None,
                percent=download_percent if status == "downloading" else None,
            )

    def _handle_local_stt_unavailable(
        self,
        status: str,
        *,
        resume_self: bool,
        resume_peer: bool,
    ) -> bool:
        if self._local_stt_manager is None:
            return False
        self._local_stt_manager.handle_unavailable(
            status,
            resume_self=resume_self,
            resume_peer=resume_peer,
            settings=self.settings,
        )
        if resume_self:
            self._stt_desired = False
        dash = getattr(self.app, "view_dashboard", None)
        if resume_self and dash is not None:
            dash.set_stt_enabled(False)
            dash.set_stt_needs_key(False)
        self._show_short_stt_message("local_stt.not_installed")
        return False

    async def _ensure_local_stt_ready(self) -> bool:
        if self._local_stt_manager is None:
            return True
        if self.settings is None or self.settings.provider.stt not in LOCAL_STT_PROVIDERS:
            return True
        result = await self._local_stt_manager.ensure_ready(self.settings)
        if not result:
            self._stt_desired = False
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_stt_enabled(False)
                # snackbar + needs_key ТОЛЬКО при missing/invalid, НЕ при warmup failure
                stt_status = self._local_stt_manager.runtime_status
                if stt_status in ("missing", "invalid"):
                    dash.set_stt_needs_key(False)
                    self._show_short_stt_message("local_stt.not_installed")
        return result

    async def _ensure_peer_local_stt_ready(self) -> bool:
        if self._local_stt_manager is None:
            return True
        if self.settings is None:
            return True
        result = await self._local_stt_manager.ensure_peer_ready(self.settings)
        if not result:
            self._show_short_stt_message("local_stt.not_installed")
        return result

    async def _cancel_local_stt_download(self) -> None:
        if self._local_stt_manager is not None:
            await self._local_stt_manager.cancel_download()

    async def _ensure_stt_switch(self) -> None:
        if self._toggle_coordinator is not None:
            await self._toggle_coordinator._ensure_stt_switch()

    async def _replace_runtime_stt_provider(self) -> None:
        if self._toggle_coordinator is not None:
            await self._toggle_coordinator._replace_runtime_stt_provider()

    async def _run_stt_switch(self) -> None:
        if self._toggle_coordinator is not None:
            await self._toggle_coordinator._run_stt_switch()

    def _cancel_stt_idle_release(self) -> None:
        if self._toggle_coordinator is not None:
            self._toggle_coordinator._cancel_stt_idle_release()

    async def _stt_idle_release_after(self, delay: float) -> None:
        if self._toggle_coordinator is not None:
            await self._toggle_coordinator._stt_idle_release_after(delay)


    async def verify_api_key(self, provider: str, key: str, base_url: str | None = None) -> tuple[bool, str]:
        """Verify API key using ApiKeyVerifier service."""
        if self._api_key_verifier is None:
            return False, "Model discovery not initialized"

        def _resolve_default_base_url(p: str) -> str | None:
            if p == "openai_compatible":
                return self.settings.provider.openai_compatible.base_url
            if p == "local_llm":
                return self.settings.provider.local_llm.base_url
            return None

        result = await self._api_key_verifier.verify(
            provider, key, base_url,
            default_base_url_resolver=_resolve_default_base_url,
        )
        if result.success:
            self.log_basic(f"[VerifyKey] provider={provider} result=OK")
        else:
            self._log_error(f"[VerifyKey] provider={provider} result={result.error_message}")
        return result.success, result.error_message

    def _on_provider_manager_llm_rebuilt(self, llm: object | None) -> None:
        if self.settings is None:
            return
        dash = getattr(self.app, "view_dashboard", None)
        # Stop translation if provider changed while translation was active
        if self.hub is not None and self.hub.translation_enabled:
            self.hub.translation_enabled = False
            if dash is not None:
                dash.set_translation_enabled(False)
        if dash is not None:
            dash.translation_needs_key = (
                (llm is None)
                and self._llm_provider_requires_secret(self.settings.provider.llm)
            )

    def _on_provider_manager_stt_rebuilt(self, stt: object | None) -> None:
        self._sync_effective_hub_flags(self.settings)
        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            dash.set_stt_needs_key(False)
            if stt is None:
                dash.set_stt_enabled(False)
                self._stt_desired = False

    async def apply_providers(
        self,
        settings: AppSettings | None = None,
        *,
        force_rebuild_llm: bool = False,
    ) -> None:
        if self._is_stopping:
            return
        next_settings = (
            self.settings
            if settings is None
            else self.merge_settings_tab_apply_with_current_languages(settings)
        )
        if next_settings is None:
            return

        prev_settings = self.settings
        prev_self_provider_signature = self._signature_detector.last_self_stt_provider_signature
        prev_peer_provider_signature = self._signature_detector.last_peer_stt_provider_signature
        prev_llm_provider_signature = self._signature_detector.last_llm_provider_signature

        if prev_settings is not None:
            if prev_self_provider_signature is None:
                prev_self_provider_signature = self._build_self_stt_provider_signature(
                    prev_settings
                )
            if prev_peer_provider_signature is None:
                prev_peer_provider_signature = self._build_peer_stt_provider_signature(
                    prev_settings
                )
            if prev_llm_provider_signature is None:
                prev_llm_provider_signature = self._build_llm_provider_signature(prev_settings)

        next_self_provider_signature = self._build_self_stt_provider_signature(next_settings)
        next_peer_provider_signature = self._build_peer_stt_provider_signature(next_settings)
        next_llm_provider_signature = self._build_llm_provider_signature(next_settings)

        should_rebuild_llm = force_rebuild_llm or (
            prev_llm_provider_signature is None
            or next_llm_provider_signature != prev_llm_provider_signature
        )
        should_refresh_peer = (
            prev_peer_provider_signature is None
            or next_peer_provider_signature != prev_peer_provider_signature
        )
        should_refresh_self_stt = (
            prev_self_provider_signature is None
            or next_self_provider_signature != prev_self_provider_signature
        )

        self.settings = next_settings
        if self._provider_manager is not None:
            self._provider_manager.settings = next_settings
        logger.info(
            "[Settings] apply_providers: stt_quant=%s peer_stt_quant=%s llm=%s",
            self.settings.provider.stt_quant,
            self.settings.provider.peer_stt_quant,
            self.settings.provider.llm.value,
        )
        self.save_settings()

        # Update command executor's settings reference after apply
        _se_view = getattr(self.app, "view_settings", None)
        if _se_view is not None and getattr(_se_view, "_command_executor", None) is not None:
            _se_view._command_executor.update_settings(self.settings)
        self._clear_local_stt_pending_enable_if_provider_switched_away()
        self._sync_local_stt_notice()

        if self.hub is not None:
            self.hub.source_language = next_settings.languages.source_language
            self.hub.target_language = next_settings.languages.target_language
            self.hub.second_target_language = next_settings.languages.second_target_language
            self.hub.peer_source_language = next_settings.languages.peer_source_language
            self.hub.peer_target_language = next_settings.languages.peer_target_language
            self.hub.system_prompt = next_settings.system_prompt
            self.hub.low_latency_mode = next_settings.stt.low_latency_mode
            self.hub.low_latency_spec_retry_max = next_settings.stt.low_latency_spec_retry_max
            self.hub.hangover_s = (
                next_settings.stt.low_latency_vad_hangover_ms / 1000.0
                if next_settings.stt.low_latency_mode
                else DEFAULT_STABLE_VAD_HANGOVER_MS / 1000.0
            )
            self.hub.peer_hangover_s = next_settings.desktop_audio.vad_hangover_ms / 1000.0
            self.hub.chatbox_include_source = next_settings.osc.chatbox_include_source
            if self.hub.output_dispatcher is not None:
                self.hub.output_dispatcher.chatbox_include_source = next_settings.osc.chatbox_include_source
            self._sync_effective_hub_flags(next_settings)

        if should_rebuild_llm:
            await self._rebuild_llm_provider()

        if should_refresh_peer:
            await self._refresh_peer_stt_runtime()
            self._sync_effective_hub_flags(next_settings)
            self._refresh_overlay_peer_consumers()

        if should_refresh_self_stt:
            if self._stt_desired:
                await self._replace_runtime_stt_provider()
            else:
                await self._rebuild_stt_provider()

        self._sync_signature_caches(next_settings)


    async def _rebuild_llm_provider(self) -> None:
        """Rebuild only the LLM provider without tearing down the entire pipeline."""
        if self._provider_manager is not None:
            await self._provider_manager.rebuild_llm_provider()

    async def _rebuild_stt_provider(self) -> None:
        """Rebuild only the STT provider so later enable uses current settings."""
        if self._provider_manager is not None:
            await self._provider_manager.rebuild_stt_provider()


    async def _init_pipeline(self) -> None:
        assert self.settings is not None
        self._sync_signature_caches(self.settings)
        secrets = create_secret_store(config_path=self.config_path)

        from puripuly_heart.app.wiring import create_model_discovery
        if self.model_discovery is None:
            self.model_discovery = create_model_discovery()
        if self._api_key_verifier is None:
            self._api_key_verifier = ApiKeyVerifier(model_discovery=self.model_discovery)

        settings_view = getattr(self.app, "view_settings", None)

        # Create command executor for settings sections
        from puripuly_heart.config.settings import materialize_translation_settings
        from puripuly_heart.core.services.settings_command_executor import SettingsCommandExecutor
        if settings_view is not None and getattr(settings_view, "_command_executor", None) is None:
            draft_svc = getattr(settings_view, "_draft_service", None)
            if draft_svc is not None:
                settings_view._command_executor = SettingsCommandExecutor(
                    settings=self.settings,
                    draft_service=draft_svc,
                    materialize_fn=materialize_translation_settings,
                )

        # Create providers in controller (wiring layer)
        llm = None
        with contextlib.suppress(Exception):
            llm = create_llm_provider(
                self.settings,
                secrets=secrets,
                runtime_logging=self.runtime_logging,
            )

        fallback_llm = None
        with contextlib.suppress(Exception):
            fallback_llm = create_fallback_llm_provider(
                self.settings,
                secrets=secrets,
                runtime_logging=self.runtime_logging,
            )

        stt = None
        if self.settings.provider.stt != STTProviderName.NONE:
            try:
                backend = create_stt_backend(
                    self.settings,
                    secrets=secrets,
                    diagnostics_enabled=self._detailed_audio_diag_enabled,
                )
                stt = ManagedSTTProvider(
                    backend=backend,
                    sample_rate_hz=self.settings.audio.internal_sample_rate_hz,
                    stt_provider_name=self.settings.provider.stt,
                    clock=self.clock,
                    reset_deadline_s=STT_RESET_DEADLINE_S,
                    drain_timeout_s=self.settings.stt.drain_timeout_s,
                    bridging_ms=self.settings.audio.ring_buffer_ms,
                    on_terminal_failure=self._on_self_terminal_failure,
                    on_final_transcript_suppressed=self._on_final_transcript_suppressed,
                    runtime_logging=self.runtime_logging,
                    stt_input_fault_profile_provider=lambda: (
                        self._diagnostics_service.debug_stt_fault_profile if self._debug_audio_fault_allowed() else "none"
                    ),
                )
            except Exception as exc:
                self._log_error(f"STT backend not available: {exc}")

        osc, sender = create_osc_sink(
            self.settings, clock=self.clock, runtime_logging=self.runtime_logging,
        )

        overlay_adapter = OverlayEventAdapter(clock=self.clock)

        # Delegate pipeline creation to PipelineLifecycleManager
        hub = await self._pipeline_manager.init_pipeline(
            self.settings,
            self.runtime_logging,
            llm_provider=llm,
            fallback_llm=fallback_llm,
            stt_provider=stt,
            overlay_adapter=overlay_adapter,
            osc=osc,
            sender=sender,
        )

        # ProviderManager — core service for LLM/STT rebuild
        self._provider_manager = ProviderManager(
            hub=hub,
            settings=self.settings,
            config_path=self.config_path,
            clock=self.clock,
            runtime_logging=self.runtime_logging,
            on_llm_rebuilt=self._on_provider_manager_llm_rebuilt,
            on_stt_rebuilt=self._on_provider_manager_stt_rebuilt,
            on_error=self._log_error,
            debug_stt_fault_profile_provider=lambda: (
                self._diagnostics_service.debug_stt_fault_profile if self._debug_audio_fault_allowed() else "none"
            ),
            detailed_audio_diag_enabled_provider=lambda: self._detailed_audio_diag_enabled,
            on_terminal_failure=self._on_self_terminal_failure,
            on_final_transcript_suppressed=self._on_final_transcript_suppressed,
            create_secret_store=create_secret_store,
            create_llm_provider=create_llm_provider,
            create_fallback_llm_provider=create_fallback_llm_provider,
            create_stt_backend=create_stt_backend,
        )

        # LocalSTTManager — core service for local STT lifecycle
        self._local_stt_manager = LocalSTTManager(
            hub=hub,
            config_path=self.config_path,
            models_dir=default_models_dir(),
            peer_stt_backend_factory=lambda: create_peer_stt_backend(
                self.settings,
                secrets=create_secret_store(config_path=self.config_path),
                diagnostics_enabled=self._detailed_audio_diag_enabled,
            ),
            peer_translation_requested_resolver=self._peer_translation_activation_requested_for,
            on_status_change=lambda status: None,
            on_notice_update=lambda: self._sync_local_stt_notice(),
            on_error=self._log_error,
            on_stt_toggle_update=None,
        )

        # ToggleCoordinator — core service for toggle state machine
        self._toggle_coordinator = ToggleCoordinator(
            hub=hub,
            settings=self.settings,
            local_stt_manager=self._local_stt_manager,
            on_error=self._log_error,
            log_basic=self.log_basic,
            log_detailed=self.log_detailed,
            start_mic_loop=self._pipeline_manager.start_mic_loop,
            stop_mic_loop=self._pipeline_manager.stop_mic_loop,
            rebuild_stt_provider=self._rebuild_stt_provider,
        )

        # OverlayService — unified overlay lifecycle + desktop overlay management
        self._overlay_service = OverlayService(
            _settings_provider=lambda: self.settings,
            _hub_provider=lambda: self.hub,
            _clock=self.clock,
            _runtime_logging_mode_provider=lambda: self.runtime_logging_mode,
            _log_basic=lambda msg: self.log_basic(msg),
            _log_detailed=lambda msg, level=logging.INFO, exc=None: self.log_detailed(msg, level=level, exception=exc),
            _save_settings=lambda: self.save_settings(),
            _apply_settings=lambda s: self.apply_settings(s),
            _sync_effective_hub_flags=lambda s: self._sync_effective_hub_flags(s),
            _refresh_overlay_peer_consumers=lambda: self._refresh_overlay_peer_consumers(),
            _refresh_peer_stt_runtime=lambda: self._refresh_peer_stt_runtime(),
            _ui_event_bridge_provider=lambda: self._ui_event_bridge,
            _calibration_service_provider=lambda: self._calibration_service,
            _overlay_calibration_provider=lambda: self.overlay_calibration,
            _runtime_logging_provider=lambda: self.runtime_logging,
            _signature_detector_provider=lambda: self._signature_detector,
        )

        # PeerToggleCoordinator — peer translation flag computation and toggle orchestration
        self._peer_toggle_coordinator = PeerToggleCoordinatorSvc(
            _settings_provider=lambda: self.settings,
            _hub_provider=lambda: self.hub,
            _overlay_state_provider=lambda: self.overlay_state,
            _overlay_bridge_provider=lambda: self._overlay_bridge,
            _failure_reason_provider=lambda: self.failure_reason,
            _signature_detector_provider=lambda: self._signature_detector,
            _log_basic=lambda msg: self.log_basic(msg),
            _log_detailed=lambda msg: self.log_detailed(msg),
            _ensure_peer_local_stt_ready=self._ensure_peer_local_stt_ready,
            _begin_overlay_start=self.begin_overlay_start,
            _refresh_overlay_runtime_dependencies=self.refresh_overlay_runtime_dependencies,
            _save_settings=lambda: self.save_settings(),
            _enqueue_peer_translation_disclosure=self._enqueue_peer_translation_disclosure,
            _clear_local_stt_pending_enable=lambda: self._clear_local_stt_pending_enable_if_provider_switched_away(),
            _sync_local_stt_notice=lambda: self._sync_local_stt_notice(),
            _refresh_overlay_peer_contract=lambda: self.refresh_overlay_peer_contract(),
        )

        from puripuly_heart.config.paths import default_vad_model_path as _default_vad_model_path
        self._peer_runtime = PeerChannelRuntime(
            hub=hub,
            clock=self.clock,
            stt_factory=self._create_peer_stt_provider_from_runtime_config,
            source_factory=self._create_peer_audio_source_from_runtime_config,
            vad_factory=self._create_peer_vad_from_runtime_config,
            vad_model_resolver=lambda: ensure_silero_vad_onnx(target_path=_default_vad_model_path()),
            run_audio_loop=self._run_peer_audio_vad_loop,
        )
        self._peer_runtime_service = PeerRuntimeService(
            _peer_runtime=self._peer_runtime,
            _config_path=self.config_path,
            _clock=self.clock,
            _runtime_logging=self.runtime_logging,
            _signature_detector=self._signature_detector,
            _log_basic=lambda msg: self.log_basic(msg),
            _log_detailed=lambda msg: self.log_detailed(msg),
            _detailed_audio_diag_enabled=lambda: self._detailed_audio_diag_enabled(),
            _debug_audio_fault_allowed=lambda: self._debug_audio_fault_allowed(),
            _debug_stt_fault_profile_provider=lambda: self._diagnostics_service.debug_stt_fault_profile if self._diagnostics_service else "none",
            _on_final_transcript_suppressed=self._on_final_transcript_suppressed,
            _wrap_diagnostic_audio_source=self._wrap_diagnostic_audio_source,
            _ensure_peer_local_stt_ready=self._ensure_peer_local_stt_ready,
            _sync_effective_hub_flags=lambda s: self._sync_effective_hub_flags(s),
            _peer_runtime_should_be_active=lambda s: self._peer_runtime_should_be_active(s),
            _settings_provider=lambda: self.settings,
            _hub_provider=lambda: self.hub,
        )

        # MicTestService — microphone test session lifecycle
        self._mic_test_service = MicTestService(
            _settings_provider=lambda: self.settings,
            _clock=self.clock,
            _log_basic=lambda msg: self.log_basic(msg),
            _log_error=lambda msg: self._log_error(msg),
            _set_stt_enabled=lambda enabled: self.set_stt_enabled(enabled),
            _is_stt_active_or_desired=lambda: (
                self._stt_desired
                or self._local_stt_pending_enable_after_install
                or self._mic_task is not None
                or self._audio_source is not None
            ),
            _last_mic_loop_close_exception_provider=lambda: self._last_mic_loop_close_exception,
            _signature_detector_provider=lambda: self._signature_detector,
        )

        self._signature_detector.last_peer_translation_enabled = self.settings.ui.peer_translation_enabled
        await self._pipeline_manager.configure_vrc_receiver(
            enabled=self.settings.osc.vrc_mic_intercept,
            settings=self.settings,
        )

    # --- PeerRuntimeService delegation ---

    def _build_peer_runtime_config(self, settings):
        return build_peer_runtime_config(settings)

    def _enqueue_peer_translation_disclosure(self) -> None:
        if self._peer_runtime_service is not None:
            self._peer_runtime_service.enqueue_peer_translation_disclosure()

    def _create_peer_stt_provider_from_runtime_config(self, config, on_terminal_failure):
        return self._peer_runtime_service.create_peer_stt_provider_from_runtime_config(config, on_terminal_failure)

    def _create_peer_audio_source_from_runtime_config(self, config):
        return self._peer_runtime_service.create_peer_audio_source_from_runtime_config(config)

    def _create_peer_vad_from_runtime_config(self, config, model_path):
        return self._peer_runtime_service.create_peer_vad_from_runtime_config(config, model_path)

    async def _run_peer_audio_vad_loop(self, **kwargs):
        await self._peer_runtime_service.run_peer_audio_vad_loop(**kwargs)

    async def _refresh_peer_stt_runtime(self) -> None:
        if self._peer_runtime_service is not None:
            await self._peer_runtime_service.refresh_peer_stt_runtime()

    # --- OverlayService delegation ---

    async def set_overlay_enabled(self, enabled: bool) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.set_overlay_enabled(enabled)

    def on_overlay_start_failed(self, failure_reason: str | None) -> None:
        if self._overlay_service is not None:
            self._overlay_service.on_overlay_start_failed(failure_reason)

    def on_overlay_runtime_disconnected(self) -> None:
        if self._overlay_service is not None:
            self._overlay_service.on_overlay_runtime_disconnected()

    def on_overlay_runtime_crashed(self) -> None:
        if self._overlay_service is not None:
            self._overlay_service.on_overlay_runtime_crashed()

    async def begin_overlay_start(self) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.begin_overlay_start()

    async def shutdown_overlay_runtime(self, *, preserve_failure_reason: bool) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.shutdown_overlay_runtime(
                preserve_failure_reason=preserve_failure_reason,
            )

    async def refresh_overlay_runtime_dependencies(self) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.refresh_overlay_runtime_dependencies()

    def overlay_target_for_settings(self, settings: AppSettings | None = None) -> str:
        if self._overlay_service is not None:
            return self._overlay_service.overlay_target_for_settings(settings)
        return OVERLAY_TARGET_STEAMVR

    def overlay_runtime_is_active(self) -> bool:
        if self._overlay_service is not None:
            return self._overlay_service.overlay_runtime_is_active()
        return False

    def previous_overlay_target_for_apply(self) -> str:
        if self._overlay_service is not None:
            return self._overlay_service.previous_overlay_target_for_apply()
        return self.overlay_target_for_settings(self.settings)

    def build_initial_desktop_runtime_controls(
        self,
        settings: AppSettings,
    ) -> list[dict[str, object]]:
        if self._overlay_service is not None:
            return self._overlay_service.build_initial_desktop_runtime_controls(settings)
        return []

    async def set_desktop_overlay_captions_locked(self, locked: bool) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.set_desktop_overlay_captions_locked(locked)

    async def set_desktop_overlay_size_preset(self, size_preset: str) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.set_desktop_overlay_size_preset(size_preset)

    async def reset_desktop_overlay_position(self) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.reset_desktop_overlay_position()

    async def broadcast_desktop_runtime_control_payloads(
        self,
        payloads: list[dict[str, object]],
    ) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.broadcast_desktop_runtime_control_payloads(payloads)

    def prepare_desktop_runtime_settings_update(
        self,
        previous_settings: AppSettings | None,
        next_settings: AppSettings,
    ) -> list[dict[str, object]]:
        if self._overlay_service is not None:
            return self._overlay_service.prepare_desktop_runtime_settings_update(
                previous_settings, next_settings,
            )
        return []

    def sync_desktop_overlay_interaction_mode_from_settings(
        self,
        settings: AppSettings,
    ) -> None:
        if self._overlay_service is not None:
            self._overlay_service.sync_desktop_overlay_interaction_mode_from_settings(settings)

    async def emit_overlay_runtime_logging_mode_update(self) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.emit_overlay_runtime_logging_mode_update()

    def schedule_overlay_runtime_logging_mode_update(self) -> None:
        if self._overlay_service is not None:
            self._overlay_service.schedule_overlay_runtime_logging_mode_update()

    async def cancel_desktop_renderer_event_task(self) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.cancel_desktop_renderer_event_task()

    async def cancel_desktop_bounds_persistence(self) -> None:
        if self._overlay_service is not None:
            await self._overlay_service.cancel_desktop_bounds_persistence()

    # --- MicTestService delegation ---

    @property
    def microphone_test_meter_level(self) -> float:
        if self._mic_test_service is not None:
            return self._mic_test_service.microphone_test_meter_level
        return 0.0

    @property
    def microphone_test_active(self) -> bool:
        if self._mic_test_service is not None:
            return self._mic_test_service.microphone_test_active
        return False

    async def start_microphone_test(self, *, meter_callback=None, level_log_interval_s=1.0) -> bool:
        if self._mic_test_service is not None:
            return await self._mic_test_service.start_microphone_test(
                meter_callback=meter_callback,
                level_log_interval_s=level_log_interval_s,
            )
        return False

    async def stop_microphone_test(self) -> None:
        if self._mic_test_service is not None:
            await self._mic_test_service.stop_microphone_test()

    async def stop_microphone_test_for_audio_settings_change(self) -> None:
        if self._mic_test_service is not None:
            await self._mic_test_service.stop_microphone_test_for_audio_settings_change()

    async def run_microphone_test_capture(self, *, meter_callback=None, level_log_interval_s=1.0) -> None:
        if self._mic_test_service is not None:
            await self._mic_test_service.run_microphone_test_capture(
                meter_callback=meter_callback,
                level_log_interval_s=level_log_interval_s,
            )

    @staticmethod
    def _microphone_test_audio_settings_signature(settings):
        return MicTestService._microphone_test_audio_settings_signature(settings)

    # --- PeerToggleCoordinator delegation ---

    @property
    def effective_peer_translation_enabled(self) -> bool:
        if self._peer_toggle_coordinator is not None:
            return self._peer_toggle_coordinator.effective_peer_translation_enabled
        return False

    def _effective_peer_translation_enabled_for(self, settings):
        if self._peer_toggle_coordinator is None:
            return False
        return self._peer_toggle_coordinator._effective_peer_translation_enabled_for(settings)

    def _peer_translation_eula_accepted_for(self, settings):
        if self._peer_toggle_coordinator is None:
            return False
        return self._peer_toggle_coordinator._peer_translation_eula_accepted_for(settings)

    def _peer_translation_activation_requested_for(self, settings):
        if self._peer_toggle_coordinator is None:
            return False
        return self._peer_toggle_coordinator._peer_translation_activation_requested_for(settings)

    def _effective_peer_overlay_enabled_for(self, settings):
        if self._peer_toggle_coordinator is None:
            return False
        return self._peer_toggle_coordinator._effective_peer_overlay_enabled_for(settings)

    def _effective_integrated_context_enabled_for(self, settings):
        if self._peer_toggle_coordinator is None:
            return False
        return self._peer_toggle_coordinator._effective_integrated_context_enabled_for(settings)

    def _sync_effective_hub_flags(self, settings=None):
        if self._peer_toggle_coordinator is not None:
            self._peer_toggle_coordinator.sync_effective_hub_flags(settings)

    def build_overlay_peer_consumer_contract(self):
        if self._peer_toggle_coordinator is not None:
            return self._peer_toggle_coordinator.build_overlay_peer_consumer_contract()
        return None

    def _refresh_overlay_peer_consumers(self):
        if self._peer_toggle_coordinator is not None:
            self._peer_toggle_coordinator.refresh_overlay_peer_consumers()

    def _peer_runtime_should_be_active(self, settings):
        if self._peer_toggle_coordinator is not None:
            return self._peer_toggle_coordinator.peer_runtime_should_be_active(settings)
        return False

    async def set_peer_translation_enabled(self, enabled: bool) -> None:
        if self._peer_toggle_coordinator is not None:
            await self._peer_toggle_coordinator.set_peer_translation_enabled(enabled)

    async def _start_mic_loop(self) -> None:
        """Start microphone capture — delegates to PipelineLifecycleManager."""
        if self._pipeline_manager is None:
            return
        assert self.settings is not None
        await self._pipeline_manager.start_mic_loop(self.settings)

    async def _stop_mic_loop(self) -> None:
        """Stop microphone capture — delegates to PipelineLifecycleManager."""
        if self._pipeline_manager is not None:
            await self._pipeline_manager.stop_mic_loop()

    async def _configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        """Enable/disable VRC OSC receiver — delegates to PipelineLifecycleManager."""
        if self._pipeline_manager is not None:
            await self._pipeline_manager.configure_vrc_receiver(
                enabled=enabled,
                settings=self.settings,
            )

    def _stop_vrc_mic_receiver(self) -> None:
        if self._pipeline_manager is not None:
            self._pipeline_manager._stop_vrc_receiver()

    @property
    def runtime_logging(self) -> SessionRuntimeLoggingService:
        if self._runtime_logging is None:
            from puripuly_heart.config.paths import user_config_dir
            self._runtime_logging = SessionRuntimeLoggingService(ui_handler_factory=self.log_handler_factory, log_dir=user_config_dir())
        logs_view = getattr(self.app, "view_logs", None)
        if logs_view is not None:
            self._runtime_logging.attach_realtime_sink(logs_view)
        return self._runtime_logging

    def attach_view_logs_if_ready(self) -> None:
        """Retroactively attach the realtime log sink when the popup view is created after runtime_logging.

        Call this after setting ``app.view_logs`` to connect the GUI log
        display to the logging pipeline.
        """
        if self._runtime_logging is not None:
            logs_view = getattr(self.app, "view_logs", None)
            if logs_view is not None:
                self._runtime_logging.attach_realtime_sink(logs_view)

    @property
    def runtime_logging_mode(self) -> str:
        return self.runtime_logging.mode.value

    def set_runtime_logging_mode(self, mode: SessionLoggingMode | str) -> None:
        previous_mode = self.runtime_logging.mode
        self.runtime_logging.set_mode(mode)
        if (
            previous_mode is not SessionLoggingMode.DETAILED
            and self.runtime_logging.mode is SessionLoggingMode.DETAILED
        ):
            self._schedule_audio_environment_snapshot()
        manager = self._overlay_manager
        if manager is not None:
            set_logging_mode = getattr(manager, "set_logging_mode", None)
            if callable(set_logging_mode):
                set_logging_mode(self.runtime_logging.mode)
        self.schedule_overlay_runtime_logging_mode_update()

    def log_basic(self, message: str, *, level: int = logging.INFO) -> None:
        try:
            self.runtime_logging.emit_basic(message, level=level)
            return
        except Exception:
            logger.log(level, message)

    def log_detailed(
        self,
        message: str,
        *,
        level: int = logging.INFO,
        exception: BaseException | None = None,
    ) -> bool:
        rendered_message = message
        exc_info = None
        if exception is not None:
            exc_info = (type(exception), exception, exception.__traceback__)
            rendered_message = (
                f"{message}\n{''.join(traceback.format_exception(*exc_info)).rstrip()}"
            )
        try:
            return self.runtime_logging.emit_detailed(rendered_message, level=level)
        except Exception:
            logger.log(level, message, exc_info=exc_info)
            return True

    def log_detailed_lazy(
        self,
        build_message: Callable[[], str],
        *,
        level: int = logging.INFO,
        exception: BaseException | None = None,
    ) -> bool:
        exc_info = None
        if exception is not None:
            exc_info = (type(exception), exception, exception.__traceback__)

        def render_message() -> str:
            rendered_message = build_message()
            if exc_info is None:
                return rendered_message
            return f"{rendered_message}\n{''.join(traceback.format_exception(*exc_info)).rstrip()}"

        try:
            return self.runtime_logging.emit_detailed_lazy(render_message, level=level)
        except Exception:
            logger.log(level, build_message(), exc_info=exc_info)
            return True

    # Sequential FIFO queue for settings changes.
    # Prevents race conditions when user rapidly toggles settings.
    def _get_page_run_task(self) -> Callable | None:
        """Return the GUI framework's async-task scheduler, or None.

        Override in subclasses to provide framework-specific scheduling.
        Base implementation falls back to getattr(self.page, "run_task", None).
        """
        return getattr(self.page, "run_task", None)

    def _run_page_task(self, task) -> None:
        """Safely run an async task on the page's event loop.

        Uses _get_page_run_task() virtual hook for framework-specific scheduling.
        """
        run_task = self._get_page_run_task()
        if callable(run_task):
            run_task(task)

    def _queue_mutation(self, task_factory) -> None:
        self._mutation_queue.append(task_factory)
        if self._mutation_worker_active:
            return
        self._mutation_worker_active = True

        async def _worker():
            try:
                while self._mutation_queue:
                    next_task = self._mutation_queue.pop(0)
                    try:
                        await next_task()
                    except Exception:
                        logger.exception("[GuiController] Mutation task failed")
            finally:
                self._mutation_worker_active = False

        self._run_page_task(_worker())

    def apply_settings_with_sync(self, settings) -> None:
        """Apply settings and sync mic test dialog."""
        async def _task():
            await self.apply_settings(settings)
            sync = getattr(self.app, "_sync_microphone_test_dialog_if_inactive", None)
            if callable(sync):
                sync()
        self._queue_mutation(_task)

    def apply_prompt_settings(self, settings) -> None:
        """Apply prompt settings with language merge."""
        async def _task():
            merged = self.merge_settings_tab_apply_with_current_languages(settings)
            await self.apply_settings(merged)
        self._queue_mutation(_task)

    def apply_pending_providers(self) -> None:
        """Apply pending provider settings from Settings view."""
        pending_settings = None
        view_settings = getattr(self.app, "view_settings", None)
        consume = getattr(view_settings, "consume_provider_apply_settings", None)
        if callable(consume) and getattr(view_settings, "has_provider_changes", False):
            pending_settings = consume()
            view_settings.has_provider_changes = False

        async def _task():
            if pending_settings is None:
                await self.apply_providers()
            else:
                await self.apply_providers(pending_settings)
        self._queue_mutation(_task)

    def rebuild_local_llm_if_needed(self) -> None:
        async def _task():
            settings = self.settings
            if settings is None or settings.provider.llm != LLMProviderName.LOCAL_LLM:
                return
            await self.apply_providers(force_rebuild_llm=True)
        self._queue_mutation(_task)

    def _api_key_field_matches_current(self, provider: str, key: str) -> bool:
        """Check if API key field still has the same value as when verification was initiated."""
        field_name_map = {
            "openai_compatible": "_openai_compatible_key",
            "backup_openai_compatible": "_fallback_api_key",
        }
        field_name = field_name_map.get(provider)
        if field_name is None:
            return True
        view_settings = getattr(self.app, "view_settings", None)
        field = getattr(view_settings, field_name, None) if view_settings else None
        if field is None:
            return True
        current_key = getattr(field, "value", None)
        if current_key is None:
            return True
        return current_key == key

    async def verify_and_persist_api_key(
        self, provider: str, key: str, *, base_url: str | None = None
    ) -> tuple[bool, str]:
        logger.info("[VerifyKey] provider=%s key_len=%d base_url=%s", provider, len(key), base_url or "(from settings)")
        success, msg = await self.verify_api_key(provider, key, base_url=base_url)
        logger.info("[VerifyKey] provider=%s success=%s msg=%s", provider, success, msg)

        if not self._api_key_field_matches_current(provider, key):
            logger.info("[VerifyKey] field changed since request, discarding result")
            return success, msg

        self.settings.api_key_verified.set_verified(provider, success)
        save_settings(self.config_path, self.settings)
        logger.info("[VerifyKey] saved api_key_verified.%s=%s", provider, success)

        view_dashboard = getattr(self.app, "view_dashboard", None)
        if view_dashboard:
            if provider in ("openai_compatible", "local_llm"):
                view_dashboard.set_translation_needs_key(not success, update_ui=False)

        return success, msg

    def clear_secret_verification(self, key: str) -> None:
        logger.info("[VerifyKey] secret_cleared key=%s", key)
        field_map = {
            "openai_compatible_api_key": "openai_compatible",
            "local_llm_api_key": "local_llm",
            "backup_api_key": "backup_openai_compatible",
            "fallback_local_llm_api_key": "fallback_local_llm",
        }
        verified_key = field_map.get(key)
        if verified_key is not None:
            self.settings.api_key_verified.set_verified(verified_key, False)
            save_settings(self.config_path, self.settings)
            view_dashboard = getattr(self.app, "view_dashboard", None)
            if view_dashboard and key in ("openai_compatible_api_key", "local_llm_api_key"):
                view_dashboard.set_translation_needs_key(True, update_ui=False)

    def load_secrets(self, config_path: Path) -> dict[str, str]:
        store = create_secret_store(config_path=config_path)
        return {
            "openai_compatible_api_key": store.get("openai_compatible_api_key") or "",
            "backup_api_key": store.get("backup_api_key") or "",
            "fallback_local_llm_api_key": store.get("fallback_local_llm_api_key") or "",
            "local_llm_api_key": store.get("local_llm_api_key") or "",
        }

    def write_secret(self, key: str, value: str, config_path: Path) -> bool:
        try:
            store = create_secret_store(config_path=config_path)
            if value:
                store.set(key, value)
            else:
                store.delete(key)
            return True
        except Exception:
            logger.warning("[Secrets] Failed to write key=%s", key, exc_info=True)
            return False

    def fetch_models(self, base_url: str, api_key: str) -> list[str]:
        if self.model_discovery is None:
            logger.error("[FetchModels] model_discovery not initialized")
            return []
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self.model_discovery.fetch_models(base_url, api_key),
                )
                return future.result(timeout=30)
        return asyncio.run(self.model_discovery.fetch_models(base_url, api_key))

    def test_connection(self, base_url: str, api_key: str) -> tuple[int, str]:
        if self.model_discovery is None:
            logger.error("[TestConnection] model_discovery not initialized")
            return 0, "model_discovery not initialized"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self.model_discovery.test_connection(base_url, api_key),
                )
                return future.result(timeout=30)
        return asyncio.run(self.model_discovery.test_connection(base_url, api_key))

    def auto_apply_pending_on_leave(self) -> None:
        view_settings = getattr(self.app, "view_settings", None)
        if view_settings is None:
            return
        if getattr(view_settings, "has_provider_changes", False):
            consume = getattr(view_settings, "consume_provider_apply_settings", None)
            if consume is None:
                return
            pending = consume()
            if pending is not None:
                logger.info(
                    "[Settings] auto_apply: pending stt_quant=%s peer_stt_quant=%s llm=%s",
                    pending.provider.stt_quant,
                    pending.provider.peer_stt_quant,
                    pending.provider.llm.value,
                )
                view_settings.has_provider_changes = False
                merged = self.merge_settings_tab_apply_with_current_languages(pending)
                logger.info(
                    "[Settings] auto_apply: merged stt_quant=%s peer_stt_quant=%s llm=%s",
                    merged.provider.stt_quant,
                    merged.provider.peer_stt_quant,
                    merged.provider.llm.value,
                )
                self.settings = merged
                self.save_settings()

                async def _task():
                    await self.apply_providers(merged)
                self._queue_mutation(_task)
        elif getattr(view_settings, "has_pending_prompt_changes", False):
            pending = view_settings.consume_prompt_apply_settings()
            if pending is not None:
                async def _task():
                    merged = self.merge_settings_tab_apply_with_current_languages(pending)
                    await self.apply_settings(merged)
                self._queue_mutation(_task)

    def on_overlay_state_changed(self, *, state: str, failure_reason: str | None = None) -> None:
        previous_state = self.overlay_state
        self.log_basic(f"[Overlay] State changed: {previous_state} -> {state}")
        self.overlay_state = state
        self.failure_reason = failure_reason
        self._sync_settings_overlay_runtime_state()
        self.refresh_overlay_peer_contract()

    def on_desktop_overlay_state_changed(self, *, interaction_mode: str | None = None, captions_locked: bool | None = None) -> None:
        _ = (interaction_mode, captions_locked)
        self._sync_settings_overlay_runtime_state()

    def refresh_overlay_peer_contract(self) -> None:
        """Rebuild overlay/peer contract and propagate to views."""
        contract = self.build_overlay_peer_consumer_contract()
        self.app.overlay_peer_contract = contract
        if contract is None:
            return
        view_settings = getattr(self.app, "view_settings", None)
        set_contract = getattr(view_settings, "set_overlay_peer_contract", None)
        if callable(set_contract):
            set_contract(contract)
        view_dashboard = getattr(self.app, "view_dashboard", None)
        set_dashboard_contract = getattr(view_dashboard, "set_overlay_peer_contract", None)
        if callable(set_dashboard_contract):
            set_dashboard_contract(contract)

    def _sync_settings_overlay_runtime_state(self) -> None:
        """Sync overlay runtime state to settings view."""
        view_settings = getattr(self.app, "view_settings", None)
        set_state = getattr(view_settings, "set_overlay_runtime_state", None)
        if not callable(set_state):
            return
        overlay_target = None
        if self.settings is not None:
            overlay_target = getattr(self.settings.overlay, "target", None)
        desktop_locked = self.desktop_overlay_captions_locked
        set_state(
            self.overlay_state,
            failure_reason=self.failure_reason,
            overlay_target=overlay_target,
            desktop_captions_locked=desktop_locked,
        )

    def _on_desktop_overlay_lock_change_async(self, locked: bool) -> None:
        async def _task():
            await self.set_desktop_overlay_captions_locked(bool(locked))
            self._refresh_desktop_overlay_state()
        self._run_page_task(_task)

    def _on_desktop_overlay_size_change_async(self, size_preset: str) -> None:
        async def _task():
            await self.set_desktop_overlay_size_preset(size_preset)
            self._refresh_desktop_overlay_state()
        self._run_page_task(_task)

    def _on_desktop_overlay_recovery_action(self, action: str) -> None:
        if action not in {"retry", "reopen"}:
            return
        async def _task():
            await self.set_overlay_enabled(True)
        self._run_page_task(_task)

    def _on_desktop_overlay_position_reset_async(self) -> None:
        async def _task():
            await self.reset_desktop_overlay_position()
            self._refresh_desktop_overlay_state()
        self._run_page_task(_task)

    def _refresh_desktop_overlay_state(self) -> None:
        view_settings = getattr(self.app, "view_settings", None)
        sync_settings = getattr(view_settings, "sync_desktop_overlay_settings", None)
        if self.settings is not None and callable(sync_settings):
            sync_settings(self.settings)
        self._sync_settings_overlay_runtime_state()

    def _on_translation_toggle_async(self, enabled: bool) -> None:
        async def _task():
            await self.set_translation_enabled(enabled)
        self._run_page_task(_task)

    def _on_stt_toggle_async(self, enabled: bool) -> None:
        self._consume_pending_provider_settings_if_needed()
        async def _task():
            await self.set_stt_enabled(enabled)
        self._run_page_task(_task)

    def _on_overlay_toggle_async(self, enabled: bool) -> None:
        async def _task():
            await self.set_overlay_enabled(enabled)
        self._run_page_task(_task)

    def _on_peer_translation_toggle_async(self, enabled: bool) -> None:
        """Toggle peer translation with EULA gate."""
        if enabled and not getattr(self.settings.ui, "peer_translation_eula_accepted", False):
            view_dashboard = getattr(self.app, "view_dashboard", None)
            if view_dashboard:
                view_dashboard.show_peer_eula_dialog(
                    on_accept=self._accept_peer_translation_eula_and_enable_async
                )
            return
        async def _task():
            self._consume_pending_provider_settings_if_needed()
            await self.set_peer_translation_enabled(enabled)
        self._run_page_task(_task)

    def _accept_peer_translation_eula_and_enable_async(self) -> None:
        self.settings.ui.peer_translation_eula_accepted = True
        save_settings(self.config_path, self.settings)
        async def _task():
            await self.set_peer_translation_enabled(True)
        self._run_page_task(_task)

    def _on_language_change_async(
        self,
        source_code: str,
        target_code: str,
        peer_source_code: str = "",
        peer_target_code: str = "",
        second_target_code: str = "",
    ) -> None:
        """Handle language change with STT compatibility check."""
        from puripuly_heart.domain.language import get_stt_compatibility_warning
        warning = get_stt_compatibility_warning(
            source_code, self.settings.provider.stt.value if self.settings else ""
        )
        if warning:
            notifier = getattr(self.app, "_show_notification", None)
            if callable(notifier):
                with contextlib.suppress(Exception):
                    notifier(warning, level="warning")
        async def _task():
            await self.on_dashboard_language_change(
                source_code=source_code, target_code=target_code,
                peer_source_code=peer_source_code,
                peer_target_code=peer_target_code,
                second_target_code=second_target_code,
            )
        self._run_page_task(_task)

    def _on_manual_submit_async(self, _source, text: str) -> None:
        async def _task():
            await self.submit_text(text)
        self._run_page_task(_task)

    async def submit_manual_text(self, text: str) -> None:
        """Public API for manual text submission from popup view."""
        await self.submit_text(text)

    def _on_manual_input_activity_async(self, has_text: bool) -> None:
        self.note_manual_input_activity(has_text)

    def _consume_pending_provider_settings_if_needed(self) -> None:
        """Consume pending provider settings before toggle."""
        view_settings = getattr(self.app, "view_settings", None)
        if view_settings is None or not getattr(view_settings, "has_provider_changes", False):
            return
        pending = view_settings.consume_provider_apply_settings()
        if pending is not None:
            view_settings.has_provider_changes = False
            merged = self.merge_settings_tab_apply_with_current_languages(pending)
            self.settings = merged
            self.save_settings()

    def _on_start_microphone_test_async(self) -> None:
        async def _task():
            await self.start_microphone_test()
        self._run_page_task(_task)

    def _on_stop_microphone_test_async(self) -> None:
        async def _task():
            await self.stop_microphone_test()
        self._run_page_task(_task)

    # --- CalibrationService delegation (replaces CalibrationManagerMixin) ---

    def begin_overlay_calibration(self) -> OverlayCalibration:
        return self._calibration_service.begin_overlay_calibration()

    def set_overlay_calibration_field(
        self, field_name: str, value: object
    ) -> OverlayCalibration:
        return self._calibration_service.set_overlay_calibration_field(
            field_name, value
        )

    def apply_overlay_calibration(self) -> OverlayCalibration:
        return self._calibration_service.apply_overlay_calibration(self.settings)

    def cancel_overlay_calibration(self) -> OverlayCalibration:
        return self._calibration_service.cancel_overlay_calibration()

    def _sync_overlay_calibration_cache(
        self, settings: AppSettings | None = None
    ) -> None:
        if self._calibration_service is not None:
            self._calibration_service.sync_from_settings(settings or self.settings)

    async def _emit_overlay_calibration_update(self) -> None:
        if self._calibration_service is not None:
            await self._calibration_service._emit_calibration_update()

    def _schedule_overlay_calibration_emit(self) -> None:
        if self._calibration_service is not None:
            self._calibration_service.set_overlay_presenter(self._overlay_presenter)
            self._calibration_service._schedule_calibration_emit()

    # --- SettingsService delegation (extracted from SettingsManagerMixin) ---

    def _llm_provider_requires_secret(self, provider):
        return self._settings_service.llm_provider_requires_secret(provider)

    def _selected_stt_provider(self):
        return self._settings_service.selected_stt_provider(self.settings)

    def _build_self_stt_runtime_signature(self, settings):
        return self._settings_service.build_self_stt_runtime_signature(settings)

    def _build_self_stt_provider_signature(self, settings):
        return self._settings_service.build_self_stt_provider_signature(settings)

    def _build_peer_stt_runtime_signature(self, settings):
        return self._settings_service.build_peer_stt_runtime_signature(settings)

    def _build_peer_stt_provider_signature(self, settings):
        return self._settings_service.build_peer_stt_provider_signature(settings)

    def _build_llm_provider_signature(self, settings):
        return self._settings_service.build_llm_provider_signature(settings)

    def _sync_signature_caches(self, settings):
        if self._settings_service is not None:
            self._settings_service.sync_signature_caches(settings)

    def _copy_provider_prompt_apply_fields(self, source, target):
        self._settings_service.copy_provider_prompt_apply_fields(source, target)

    def merge_settings_tab_apply_with_current_languages(self, pending):
        return self._settings_service.merge_settings_tab_apply_with_current_languages(self.settings, pending)

    def _load_or_init_settings(self, path):
        return self._settings_service.load_or_init_settings(path)

    def save_settings(self) -> None:
        if self._settings_service is not None and self.settings is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(
                    None,
                    self._settings_service.save_settings_to_disk,
                    self.config_path,
                    self.settings,
                )
            except RuntimeError:
                # No running loop — safe to call synchronously
                self._settings_service.save_settings_to_disk(self.config_path, self.settings)

    # --- Settings UI sync (stays in GuiController — accesses self.app views) ---

    def _sync_ui_from_settings(self) -> None:
        settings = self.settings
        if settings is None:
            return
        with contextlib.suppress(Exception):
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_languages_from_codes(
                    settings.languages.source_language,
                    settings.languages.target_language,
                    settings.languages.peer_source_language,
                    settings.languages.peer_target_language,
                    settings.languages.second_target_language,
                )
                dash.set_recent_languages(
                    settings.languages.recent_source_languages,
                    settings.languages.recent_target_languages,
                )
                dash.on_recent_languages_change = self._on_recent_languages_change
        # Settings window is NOT updated here — it manages its own state.
        # Only the dashboard (Control popup) is synced.
        self._refresh_overlay_peer_consumers()

    def _on_recent_languages_change(self, source: list[str], target: list[str]) -> None:
        if self.settings is None:
            return
        self.settings.languages.recent_source_languages = list(source)
        self.settings.languages.recent_target_languages = list(target)
        self.save_settings()

    async def on_dashboard_language_change(
        self,
        *,
        source_code: str,
        target_code: str,
        peer_source_code: str = "",
        peer_target_code: str = "",
        second_target_code: str = "",
    ) -> None:
        if self.settings is None:
            return
        updated = copy.deepcopy(self.settings)
        updated.languages.source_language = source_code
        updated.languages.target_language = target_code
        updated.languages.second_target_language = second_target_code
        updated.languages.peer_source_language = peer_source_code
        updated.languages.peer_target_language = peer_target_code
        await self.apply_settings(updated)

    async def apply_settings(self, settings: AppSettings) -> None:
        # Phase 0: Pre-diff checks (mic test audio change)
        prev_microphone_test_audio_signature = (
            self._signature_detector.last_microphone_test_audio_settings_signature
            or self._microphone_test_audio_settings_signature(self.settings)
        )
        next_microphone_test_audio_signature = self._microphone_test_audio_settings_signature(
            settings
        )
        if (
            prev_microphone_test_audio_signature is not None
            and prev_microphone_test_audio_signature != next_microphone_test_audio_signature
        ):
            await self.stop_microphone_test_for_audio_settings_change()

        prev_locale = get_locale()
        prev_overlay_enabled = (
            self.settings.ui.overlay_enabled if self.settings is not None else False
        )
        previous_settings_for_desktop = (
            copy.deepcopy(self.settings) if self.settings is not None else None
        )
        prev_overlay_target = self.previous_overlay_target_for_apply()
        next_overlay_target = self.overlay_target_for_settings(settings)
        if (
            prev_overlay_target != next_overlay_target
            and prev_overlay_enabled
            and settings.ui.overlay_enabled
            and self.overlay_runtime_is_active()
        ):
            self.log_basic(
                "[Overlay] Target changed while running; stopping current overlay before switch"
            )
            settings = copy.deepcopy(settings)
            settings.ui.overlay_enabled = False
        desktop_runtime_controls = self.prepare_desktop_runtime_settings_update(
            previous_settings_for_desktop,
            settings,
        )

        # Phase 1: Compute diff (pure function — no side effects)
        prev_peer_translation_enabled = (
            self._signature_detector.last_peer_translation_enabled
            if self._signature_detector.last_peer_translation_enabled is not None
            else (self.settings.ui.peer_translation_enabled if self.settings is not None else False)
        )
        prev_peer_activation_requested = (
            self._signature_detector.last_peer_translation_activation_requested
            if self._signature_detector.last_peer_translation_activation_requested is not None
            else (
                self._peer_translation_activation_requested_for(self.settings)
                if self.settings is not None
                else False
            )
        )
        prev_self_signature = (
            self._signature_detector.last_self_stt_runtime_signature
            or self._signature_detector.last_stt_runtime_signature
        )
        prev_peer_signature = self._signature_detector.last_peer_stt_runtime_signature

        diff = self._settings_service.compute_diff(
            prev_settings=self.settings,
            next_settings=settings,
            hub_source_lang=self.hub.source_language if self.hub else None,
            hub_target_lang=self.hub.target_language if self.hub else None,
            hub_peer_source_lang=(
                getattr(self.hub, "peer_source_language", None) if self.hub else None
            ),
            hub_peer_target_lang=(
                getattr(self.hub, "peer_target_language", None) if self.hub else None
            ),
            hub_low_latency=self.hub.low_latency_mode if self.hub else None,
            hub_second_target_lang=(
                getattr(self.hub, "second_target_language", "") if self.hub else ""
            ),
            prev_self_signature=prev_self_signature,
            prev_peer_signature=prev_peer_signature,
            prev_peer_enabled=prev_peer_translation_enabled,
            prev_peer_activation=prev_peer_activation_requested,
            next_self_signature=self._build_self_stt_runtime_signature(settings),
            next_peer_signature=self._build_peer_stt_runtime_signature(settings),
            next_peer_activation=self._peer_translation_activation_requested_for(settings),
            prev_overlay_target=prev_overlay_target,
            next_overlay_target=next_overlay_target,
            prev_overlay_enabled=prev_overlay_enabled,
            prev_vrc_mic_sync=self._last_vrc_mic_sync_enabled,
            prev_locale=prev_locale,
        )

        # Phase 2: Apply mutations (update state)
        if diff.source_language_changed or diff.target_language_changed:
            presenter = self._overlay_presenter
            self.log_basic(
                "[Settings] Applying languages: "
                f"source={self.hub.source_language if self.hub else None}->{settings.languages.source_language} "
                f"target={self.hub.target_language if self.hub else None}->{settings.languages.target_language}"
            )
            self.log_detailed(
                "[Settings] Language apply detail: "
                f"overlay_state={self.overlay_state} "
                f"presenter_attached={presenter is not None} "
                f"bridge_attached={self._overlay_bridge is not None} "
                "overlay_sink_matches_presenter="
                f"{self.hub is not None and presenter is not None and getattr(self.hub, 'overlay_sink', None) is presenter}"
            )

        self.settings = settings
        self._signature_detector.last_microphone_test_audio_settings_signature = next_microphone_test_audio_signature
        self._sync_overlay_calibration_cache(settings)
        self.sync_desktop_overlay_interaction_mode_from_settings(settings)
        self.save_settings()
        await self.broadcast_desktop_runtime_control_payloads(desktop_runtime_controls)
        await self._sync_clipboard_watcher()
        self._refresh_local_stt_runtime_state()
        self._clear_local_stt_pending_enable_if_provider_switched_away()

        # Phase 3: Dispatch side effects based on diff
        if diff.low_latency_changed:
            self.log_detailed(
                "[Settings] Low latency detail: "
                f"mode changing to {settings.stt.low_latency_mode} rebuilding_llm_provider=True"
            )
            await self._rebuild_llm_provider()

        if diff.llm_provider_changed:
            self.log_basic(
                f"[Settings] LLM provider changed: rebuilding"
            )
            await self._rebuild_llm_provider()

        if self.hub is not None:
            self.hub.source_language = settings.languages.source_language
            self.hub.target_language = settings.languages.target_language
            self.hub.second_target_language = settings.languages.second_target_language
            if self.hub.translation_service is not None:
                ts = self.hub.translation_service
                ts.source_language = settings.languages.source_language
                ts.target_language = settings.languages.target_language
                ts.second_target_language = settings.languages.second_target_language
                ts.peer_source_language = settings.languages.peer_source_language
                ts.peer_target_language = settings.languages.peer_target_language
                ts.system_prompt = settings.system_prompt
            self.hub.peer_source_language = settings.languages.peer_source_language
            self.hub.peer_target_language = settings.languages.peer_target_language
            self.hub.system_prompt = settings.system_prompt
            self.hub.low_latency_mode = settings.stt.low_latency_mode
            self.hub.low_latency_spec_retry_max = settings.stt.low_latency_spec_retry_max
            self.hub.hangover_s = (
                settings.stt.low_latency_vad_hangover_ms / 1000.0
                if settings.stt.low_latency_mode
                else DEFAULT_STABLE_VAD_HANGOVER_MS / 1000.0
            )
            self.hub.peer_hangover_s = settings.desktop_audio.vad_hangover_ms / 1000.0
            self.hub.chatbox_include_source = settings.osc.chatbox_include_source
            if self.hub.output_dispatcher is not None:
                self.hub.output_dispatcher.chatbox_include_source = settings.osc.chatbox_include_source
            self._sync_effective_hub_flags(settings)

            async def _clear_language_runtime_state(channel: str) -> None:
                try:
                    await self.hub.clear_language_runtime_state(channel=channel)
                except Exception as exc:
                    self._log_error(f"Failed to clear language runtime state for {channel}: {exc}")

            if diff.source_language_changed or diff.target_language_changed or diff.second_target_language_changed:
                await _clear_language_runtime_state("self")
            if diff.effective_peer_source_changed or diff.effective_peer_target_changed or diff.second_target_language_changed:
                await _clear_language_runtime_state("peer")

        presenter = self._overlay_presenter
        if presenter is not None:
            await presenter.update_display_preferences(
                show_translation=settings.overlay.show_translation,
                show_peer_original=settings.overlay.show_peer_original,
            )

        if diff.overlay_enabled_changed:
            await self.set_overlay_enabled(settings.ui.overlay_enabled)

        if diff.vrc_mic_sync_changed:
            if self.vrc_mic_audio_gate is not None:
                self.vrc_mic_audio_gate.set_enabled(settings.osc.vrc_mic_intercept)
            self.log_detailed(f"[Settings] VRC mic sync enabled: {settings.osc.vrc_mic_intercept}")
            await self._configure_vrc_mic_receiver(enabled=settings.osc.vrc_mic_intercept)

        self._sync_signature_caches(settings)

        if diff.source_language_changed or diff.target_language_changed:
            self.log_detailed(
                "[Settings] Language runtime impact: "
                f"should_restart_stt={diff.should_restart_stt} "
                f"should_refresh_peer={diff.should_refresh_peer} "
                f"prev_overlay_enabled={prev_overlay_enabled} "
                f"next_overlay_enabled={settings.ui.overlay_enabled}"
            )

        if diff.should_refresh_peer and self.hub is not None:
            await self._refresh_peer_stt_runtime()
            self._sync_effective_hub_flags(settings)
            self._refresh_overlay_peer_consumers()

        if diff.should_restart_stt:
            await self._replace_runtime_stt_provider()

        # Settings window is NOT updated here — it manages its own state.
        # Language changes are synced at the data level only.

        if diff.locale_changed:
            set_locale(settings.ui.locale)
            apply_locale = getattr(self.app, "apply_locale", None)
            if callable(apply_locale):
                try:
                    apply_locale()
                except Exception as exc:
                    self._log_error(f"Failed to apply locale: {exc}")

        self._refresh_overlay_peer_consumers()

    def _log_error(self, message: str) -> None:
        self.log_basic(message, level=logging.ERROR)
        notifier = getattr(self.app, "_show_notification", None)
        if callable(notifier):
            with contextlib.suppress(Exception):
                notifier(message, level="error")


# ─── Framework-specific controllers ──────────────────────────────────────


@dataclass(slots=True)
class FletGuiController(BaseGuiController):
    """Flet-specific controller. Requires a non-None page (ft.Page)."""

    def __post_init__(self) -> None:
        if self.page is None:
            raise TypeError("FletGuiController requires a non-None page")

    def _get_page_run_task(self) -> Callable | None:
        """Return Flet's page.run_task for async scheduling."""
        return getattr(self.page, "run_task", None)


@dataclass(slots=True)
class TkinterGuiController(BaseGuiController):
    """Tkinter-specific controller. Uses asyncio loop instead of Flet page."""

    def __post_init__(self) -> None:
        self.page = None

    def _get_page_run_task(self) -> Callable | None:
        """Return a callable that schedules coroutines on the stored event loop."""
        loop = self._async_loop
        if loop is None or not loop.is_running():
            return None

        def _schedule(task: object) -> None:
            asyncio.run_coroutine_threadsafe(task, loop)

        return _schedule


# Backward compatibility alias — existing imports continue to work
GuiController = FletGuiController
