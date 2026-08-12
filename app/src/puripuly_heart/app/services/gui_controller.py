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
import logging
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import flet as ft

from puripuly_heart.app.wiring import (
    build_peer_stt_provider_signature,
    create_llm_provider,
    create_fallback_llm_provider,
    create_osc_sink,
    create_peer_stt_backend,
    create_secret_store,
    create_stt_backend,
    resolve_peer_stt_config,
)
from puripuly_heart.app.headless_mic import run_audio_vad_loop
from puripuly_heart.config.audio_host_api import normalize_input_host_api
from puripuly_heart.config.paths import default_models_dir, default_vad_model_path
from puripuly_heart.config.settings import (
    DESKTOP_FLET_MIN_HEIGHT,
    DESKTOP_FLET_MIN_WIDTH,
    DESKTOP_FLET_SIZE_PRESETS,
    OVERLAY_TARGET_DESKTOP,
    OVERLAY_TARGET_STEAMVR,
    AppSettings,
    LLMProviderName,
    STTProviderName,
    load_settings,
    new_settings_for_first_run,
    save_settings,
)
from puripuly_heart.config.vad_defaults import DEFAULT_STABLE_VAD_HANGOVER_MS
from puripuly_heart.core.runtime.local_qwen_lifecycle import LOCAL_QWEN_IDLE_RELEASE_SECONDS
from puripuly_heart.core.audio.desktop_pipeline import DesktopPeerPipeline
from puripuly_heart.core.audio.desktop_source import DesktopLoopbackAudioSource
from puripuly_heart.core.audio.diagnostics import compute_audio_frame_metrics
from puripuly_heart.core.audio.gate import VrcMicAudioGate
from puripuly_heart.core.audio.source import (
    AudioSource,
    MicrophoneTestRouteObservation,
    SelfMicCaptureChannelDecision,
    SoundDeviceAudioSource,
    determine_self_mic_capture_channels,
    observe_microphone_test_route,
    resolve_sounddevice_input_device,
)
from puripuly_heart.ports.model_discovery import ModelDiscovery
from puripuly_heart.ports.ui import ClipboardWatcherRuntime
from puripuly_heart.ports.osc import OscSink
from puripuly_heart.core.clipboard.watcher import create_clipboard_watcher
from puripuly_heart.core.clock import SystemClock
from puripuly_heart.core.verification.api_key_verifier import ApiKeyVerifier
from puripuly_heart.core.services.provider_manager import ProviderManager
from puripuly_heart.core.services.pipeline_lifecycle import PipelineLifecycleManager
from puripuly_heart.core.services.toggle_coordinator import ToggleCoordinator
from puripuly_heart.core.stt.local_stt_manager import LOCAL_STT_PROVIDERS, LocalSTTManager
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
from puripuly_heart.core.overlay.process import OverlayProcessManager
from puripuly_heart.core.runtime.peer_channel import PeerChannelRuntime, PeerRuntimeConfig
from puripuly_heart.core.runtime_logging import SessionLoggingMode, SessionRuntimeLoggingService
from puripuly_heart.core.stt.controller import (
    FinalTranscriptSuppressedNotification,
    ManagedSTTProvider,
)
from puripuly_heart.core.vad.bundled import SILERO_VAD_VERSION, ensure_silero_vad_onnx
from puripuly_heart.core.vad.gating import VadGating, create_peer_vad_gating
from puripuly_heart.core.vad.silero import SileroVadOnnx
from puripuly_heart.app.services.ui_bridge import UIEventBridge
from puripuly_heart.domain.i18n import get_locale, set_locale, t
from puripuly_heart.domain.overlay_calibration import OverlayCalibration
from puripuly_heart.domain.overlay_contract import (
    OverlayPeerConsumerContract,
    build_overlay_peer_consumer_contract,
)
from puripuly_heart.app.services.overlay_manager import DESKTOP_INTERACTION_MODE_EDIT, OverlayManagerMixin
from puripuly_heart.app.services.clipboard_manager import ClipboardManagerMixin
from puripuly_heart.app.services.mic_test_manager import MicTestManagerMixin
from puripuly_heart.app.services.peer_flags import PeerFlagsMixin
from puripuly_heart.app.services.overlay_lifecycle import OverlayLifecycleMixin
from puripuly_heart.app.services.calibration_manager import CalibrationManagerMixin
from puripuly_heart.app.services.provider_signatures import ProviderSignaturesMixin
from puripuly_heart.app.services.diagnostics_manager import DiagnosticsManagerMixin
from puripuly_heart.app.services.peer_runtime_manager import PeerRuntimeManagerMixin
from puripuly_heart.app.services.settings_manager import SettingsManagerMixin

logger = logging.getLogger(__name__)

# Hardcoded STT session reset deadline (not configurable via settings)
STT_RESET_DEADLINE_S = 300.0


@dataclass(slots=True)
class GuiController(
    OverlayManagerMixin,
    ClipboardManagerMixin,
    MicTestManagerMixin,
    PeerFlagsMixin,
    OverlayLifecycleMixin,
    CalibrationManagerMixin,
    ProviderSignaturesMixin,
    DiagnosticsManagerMixin,
    PeerRuntimeManagerMixin,
    SettingsManagerMixin,
):
    page: ft.Page
    app: object
    config_path: Path

    settings: AppSettings | None = None
    clock: SystemClock = SystemClock()

    _pipeline_manager: PipelineLifecycleManager | None = None
    model_discovery: ModelDiscovery | None = None
    _api_key_verifier: ApiKeyVerifier | None = None
    _provider_manager: ProviderManager | None = None
    _peer_runtime: PeerChannelRuntime | None = None

    _bridge_task: asyncio.Task[None] | None = None
    _microphone_test_meter_level: float = field(init=False, default=0.0)
    _microphone_test_task: asyncio.Task[None] | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _microphone_test_lifecycle_lock: asyncio.Lock | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _debug_capture_fault_profile: str = field(init=False, default="none")
    _debug_stt_fault_profile: str = field(init=False, default="none")
    _toggle_coordinator: ToggleCoordinator | None = None
    _last_stt_runtime_signature: tuple[object, ...] | None = None
    _last_self_stt_runtime_signature: tuple[object, ...] | None = None
    _last_peer_stt_runtime_signature: tuple[object, ...] | None = None
    _last_peer_stt_desired_active: bool | None = None
    _last_self_stt_provider_signature: tuple[object, ...] | None = None
    _last_peer_stt_provider_signature: tuple[object, ...] | None = None
    _last_llm_provider_signature: tuple[object, ...] | None = None
    _last_microphone_test_audio_settings_signature: tuple[object, ...] | None = None
    _last_peer_translation_enabled: bool | None = None
    _last_peer_translation_activation_requested: bool | None = None
    _ui_event_bridge: UIEventBridge | None = None
    _clipboard_watcher: ClipboardWatcherRuntime | None = field(init=False, default=None)
    _clipboard_loop: asyncio.AbstractEventLoop | None = field(init=False, default=None)
    _clipboard_watcher_lock: asyncio.Lock | None = field(init=False, default=None)
    _manual_typing_active: bool = field(init=False, default=False)
    _manual_typing_last_activity_at: float = field(init=False, default=0.0)
    _manual_typing_idle_task: object | None = field(init=False, default=None, repr=False)
    _manual_submit_typing_generation: int = field(init=False, default=0)
    _manual_submit_typing_reasons: set[str] = field(init=False, default_factory=set)
    _local_stt_manager: LocalSTTManager | None = field(init=False, default=None)
    _overlay_bridge: OverlayBridge | None = None
    _overlay_presenter: OverlayPresenter | None = None
    _overlay_manager: OverlayProcessManager | None = None
    _overlay_start_task: asyncio.Task[None] | None = None
    _overlay_monitor_task: asyncio.Task[None] | None = None
    _overlay_lock: asyncio.Lock | None = None
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
    _runtime_logging: SessionRuntimeLoggingService | None = field(init=False, default=None)
    _local_qwen_hallucination_detection_count: int = field(init=False, default=0)
    _local_qwen_hallucination_modal_shown: bool = field(init=False, default=False)

    overlay_state: str = "off"
    failure_reason: str | None = None
    auto_restart_scheduled: bool = False
    desktop_overlay_interaction_mode: str = field(
        init=False,
        default=DESKTOP_INTERACTION_MODE_EDIT,
    )
    overlay_calibration: OverlayCalibration = field(default_factory=OverlayCalibration)
    _overlay_calibration_draft: OverlayCalibration | None = None
    log_handler_factory: Callable[[Any], logging.Handler] | None = field(default=None)


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

    async def start(self) -> None:
        self.settings = self._load_or_init_settings(self.config_path)
        self.settings.ui.overlay_enabled = False
        self.settings.ui.peer_translation_enabled = False
        self._sync_overlay_calibration_cache(self.settings)
        self._overlay_calibration_draft = None
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
        await self._shutdown_overlay_runtime(preserve_failure_reason=True)
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
        show_snackbar = getattr(self.app, "_show_snackbar", None)
        if callable(show_snackbar):
            with contextlib.suppress(Exception):
                show_snackbar(message, ft.Colors.ORANGE_700)
                return
        opener = getattr(self.page, "open", None)
        if callable(opener):
            with contextlib.suppress(Exception):
                opener(
                    ft.SnackBar(
                        ft.Text(message, color=ft.Colors.WHITE),
                        bgcolor=ft.Colors.ORANGE_700,
                        duration=4000,
                        behavior=ft.SnackBarBehavior.FLOATING,
                        margin=ft.margin.only(bottom=90),
                        padding=20,
                    )
                )
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
        prev_self_provider_signature = self._last_self_stt_provider_signature
        prev_peer_provider_signature = self._last_peer_stt_provider_signature
        prev_llm_provider_signature = self._last_llm_provider_signature

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
        if settings_view is not None:
            if settings_view._command_executor is None:
                draft_svc = settings_view._draft_service
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
                    self._debug_stt_fault_profile if self._debug_audio_fault_allowed() else "none"
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
                self._debug_stt_fault_profile if self._debug_audio_fault_allowed() else "none"
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
        self._last_peer_translation_enabled = self.settings.ui.peer_translation_enabled
        await self._pipeline_manager.configure_vrc_receiver(
            enabled=self.settings.osc.vrc_mic_intercept,
            settings=self.settings,
        )


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
        self._schedule_overlay_runtime_logging_mode_update()


    def _schedule_overlay_runtime_logging_mode_update(self) -> None:
        bridge = self._overlay_bridge
        if bridge is None:
            return

        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):
            try:
                run_task(self._emit_overlay_runtime_logging_mode_update)
                return
            except Exception as exc:
                self.log_detailed(
                    "[Overlay] Failed to schedule logging mode update via page.run_task",
                    level=logging.WARNING,
                    exception=exc,
                )
                return

        try:
            asyncio.get_running_loop().create_task(self._emit_overlay_runtime_logging_mode_update())
        except RuntimeError:
            self.log_detailed(
                "[Overlay] Skipping logging mode update; no running loop and page.run_task unavailable",
                level=logging.WARNING,
            )

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
    def _queue_mutation(self, task_factory) -> None:
        queue = getattr(self, "_mutation_queue", None)
        if queue is None:
            queue = []
            self._mutation_queue = queue
        queue.append(task_factory)
        if getattr(self, "_mutation_worker_active", False):
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

        self.page.run_task(_worker)

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
        return asyncio.run(self.model_discovery.fetch_models(base_url, api_key))

    def test_connection(self, base_url: str, api_key: str) -> tuple[int, str]:
        if self.model_discovery is None:
            logger.error("[TestConnection] model_discovery not initialized")
            return 0, "model_discovery not initialized"
        return asyncio.run(self.model_discovery.test_connection(base_url, api_key))

    def auto_apply_pending_on_leave(self) -> None:
        view_settings = getattr(self.app, "view_settings", None)
        if view_settings is None:
            return
        if view_settings.has_provider_changes:
            pending = view_settings.consume_provider_apply_settings()
            if pending is not None:
                view_settings.has_provider_changes = False
                merged = self.merge_settings_tab_apply_with_current_languages(pending)
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
        previous_state = getattr(self, "_overlay_state", "unknown")
        self.log_basic(f"[Overlay] State changed: {previous_state} -> {state}")
        self._overlay_state = state
        self._overlay_failure_reason = failure_reason
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
        desktop_locked = bool(getattr(self, "desktop_overlay_captions_locked", False))
        set_state(
            getattr(self, "_overlay_state", "off"),
            failure_reason=getattr(self, "_overlay_failure_reason", None),
            overlay_target=overlay_target,
            desktop_captions_locked=desktop_locked,
        )

    def _on_desktop_overlay_lock_change_async(self, locked: bool) -> None:
        async def _task():
            await self.set_desktop_overlay_captions_locked(bool(locked))
            self._refresh_desktop_overlay_state()
        self.page.run_task(_task)

    def _on_desktop_overlay_size_change_async(self, size_preset: str) -> None:
        async def _task():
            await self.set_desktop_overlay_size_preset(size_preset)
            self._refresh_desktop_overlay_state()
        self.page.run_task(_task)

    def _on_desktop_overlay_recovery_action(self, action: str) -> None:
        if action not in {"retry", "reopen"}:
            return
        async def _task():
            await self.set_overlay_enabled(True)
        self.page.run_task(_task)

    def _on_desktop_overlay_position_reset_async(self) -> None:
        async def _task():
            await self.reset_desktop_overlay_position()
            self._refresh_desktop_overlay_state()
        self.page.run_task(_task)

    def _refresh_desktop_overlay_state(self) -> None:
        view_settings = getattr(self.app, "view_settings", None)
        sync_settings = getattr(view_settings, "sync_desktop_overlay_settings", None)
        if self.settings is not None and callable(sync_settings):
            sync_settings(self.settings)
        self._sync_settings_overlay_runtime_state()

    def _on_translation_toggle_async(self, enabled: bool) -> None:
        async def _task():
            await self.set_translation_enabled(enabled)
            view_dashboard = getattr(self.app, "view_dashboard", None)
            if view_dashboard:
                view_dashboard.set_translation_enabled(enabled)
        self.page.run_task(_task)

    def _on_stt_toggle_async(self, enabled: bool) -> None:
        self._consume_pending_provider_settings_if_needed()
        async def _task():
            await self.set_stt_enabled(enabled)
        self.page.run_task(_task)

    def _on_overlay_toggle_async(self, enabled: bool) -> None:
        async def _task():
            await self.set_overlay_enabled(enabled)
        self.page.run_task(_task)

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
        self.page.run_task(_task)

    def _accept_peer_translation_eula_and_enable_async(self) -> None:
        self.settings.ui.peer_translation_eula_accepted = True
        save_settings(self.config_path, self.settings)
        async def _task():
            await self.set_peer_translation_enabled(True)
        self.page.run_task(_task)

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
            view_dashboard = getattr(self.app, "view_dashboard", None)
            if view_dashboard:
                show_snackbar = getattr(view_dashboard, "show_snackbar", None)
                if callable(show_snackbar):
                    show_snackbar(warning)
        self.on_dashboard_language_change(
            source_code, target_code,
            peer_source_code=peer_source_code,
            peer_target_code=peer_target_code,
            second_target_code=second_target_code,
        )

    def _on_manual_submit_async(self, _source, text: str) -> None:
        async def _task():
            await self.submit_text(text)
        self.page.run_task(_task)

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
        self.start_microphone_test()

    def _on_stop_microphone_test_async(self) -> None:
        self.stop_microphone_test()

    def _log_error(self, message: str) -> None:
        self.log_basic(message, level=logging.ERROR)
