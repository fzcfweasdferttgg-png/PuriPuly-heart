from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
import json
import logging
import math
import secrets
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import flet as ft
import numpy as np

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
from puripuly_heart.config.audio_host_api import normalize_input_host_api
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
from puripuly_heart.core.llm.provider import SemaphoreLLMProvider
from puripuly_heart.core.local_stt_assets import (
    LocalSTTInstallState,
    LocalSTTManifestInvalidError,
    LocalSTTModelMissingError,
    inspect_local_stt_install_state,
)
from puripuly_heart.core.pipeline.pipeline import Pipeline
from puripuly_heart.adapters.overlay.sink import OverlayEventAdapter
from puripuly_heart.config.prompts import render_dual_translation_prompt_template, render_translation_prompt_template
from puripuly_heart.core.osc.receiver import (
    VRC_OSC_RECEIVER_HOST,
    VRC_OSC_RECEIVER_PORT,
    VrcMicState,
    VrcOscReceiver,
)
from puripuly_heart.core.overlay.bridge import OverlayBridge
from puripuly_heart.core.overlay.presenter import OverlayPresenter
from puripuly_heart.core.overlay.process import (
    DefaultOverlayProcessRunner,
    DesktopFletOverlayRunner,
    OverlayProcessManager,
    OverlayProcessRunner,
)
from puripuly_heart.core.runtime.peer_channel import PeerChannelRuntime, PeerRuntimeConfig
from puripuly_heart.core.runtime_logging import SessionLoggingMode, SessionRuntimeLoggingService
from puripuly_heart.core.stt.controller import (
    FinalTranscriptSuppressedNotification,
    ManagedSTTProvider,
)
from puripuly_heart.core.vad.bundled import SILERO_VAD_VERSION, ensure_silero_vad_onnx
from puripuly_heart.core.vad.gating import VadGating, create_peer_vad_gating
from puripuly_heart.core.vad.silero import SileroVadOnnx
from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTError
from puripuly_heart.ui.event_bridge import UIEventBridge
from puripuly_heart.ui.i18n import get_locale, set_locale, t
from puripuly_heart.domain.overlay_calibration import OverlayCalibration
from puripuly_heart.ui.overlay_peer_contract import (
    OverlayPeerConsumerContract,
    build_overlay_peer_consumer_contract,
)
from puripuly_heart.ui.views.logs import FletLogHandler

from puripuly_heart.ui.overlay_manager import DESKTOP_INTERACTION_MODE_EDIT, OverlayManagerMixin
from puripuly_heart.ui.clipboard_manager import ClipboardManagerMixin
from puripuly_heart.ui.mic_test_manager import MicTestManagerMixin
from puripuly_heart.ui.peer_flags import PeerFlagsMixin
from puripuly_heart.ui.overlay_lifecycle import OverlayLifecycleMixin
from puripuly_heart.ui.calibration_manager import CalibrationManagerMixin
from puripuly_heart.ui.provider_signatures import ProviderSignaturesMixin
from puripuly_heart.ui.diagnostics_manager import DiagnosticsManagerMixin
from puripuly_heart.ui.peer_runtime_manager import PeerRuntimeManagerMixin
from puripuly_heart.ui.settings_manager import SettingsManagerMixin

logger = logging.getLogger(__name__)

# Hardcoded STT session reset deadline (not configurable via settings)
STT_RESET_DEADLINE_S = 300.0


@dataclass(slots=True)
class _HubVadSink:
    hub: Pipeline
    channel: str = "self"

    async def handle_vad_event(self, event) -> None:  # noqa: ANN001
        if self.channel == "peer":
            await self.hub.handle_peer_vad_event(event)
            return
        await self.hub.handle_vad_event(event)


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

    sender: object | None = None
    osc: OscSink | None = None
    hub: Pipeline | None = None
    model_discovery: ModelDiscovery | None = None
    _peer_runtime: PeerChannelRuntime | None = None
    receiver: VrcOscReceiver | None = None
    vrc_mic_state: VrcMicState | None = None
    vrc_mic_audio_gate: VrcMicAudioGate | None = None

    _bridge_task: asyncio.Task[None] | None = None
    _mic_task: asyncio.Task[None] | None = None
    _audio_source: AudioSource | None = None
    _last_mic_loop_close_exception: BaseException | None = field(
        init=False,
        default=None,
        repr=False,
    )
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
    _vad: VadGating | None = None
    _stt_desired: bool = False
    _is_stopping: bool = False
    _stt_switch_lock: asyncio.Lock | None = None
    _stt_switch_task: asyncio.Task[None] | None = None
    _stt_idle_release_task: asyncio.Task[None] | None = None
    _stt_restart_requested: bool = False
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
    _last_vrc_mic_sync_enabled: bool | None = None
    _vrc_receiver_lock: asyncio.Lock | None = None
    _ui_event_bridge: UIEventBridge | None = None
    _clipboard_watcher: ClipboardWatcherRuntime | None = field(init=False, default=None)
    _clipboard_loop: asyncio.AbstractEventLoop | None = field(init=False, default=None)
    _clipboard_watcher_lock: asyncio.Lock | None = field(init=False, default=None)
    _manual_typing_active: bool = field(init=False, default=False)
    _manual_typing_last_activity_at: float = field(init=False, default=0.0)
    _manual_typing_idle_task: object | None = field(init=False, default=None, repr=False)
    _manual_submit_typing_generation: int = field(init=False, default=0)
    _manual_submit_typing_reasons: set[str] = field(init=False, default_factory=set)
    _local_stt_install_state: LocalSTTInstallState = field(
        init=False,
        default_factory=lambda: LocalSTTInstallState(status="ready"),
    )
    _local_stt_runtime_status: str = field(init=False, default="ready")
    _local_stt_download_origin: str | None = field(init=False, default=None)
    _local_stt_download_percent: int | None = field(init=False, default=None)
    _local_stt_download_task: asyncio.Task[object] | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _local_stt_download_cancel_event: threading.Event | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _local_stt_pending_enable_after_install: bool = field(init=False, default=False)
    _local_stt_pending_peer_enable_after_install: bool = field(init=False, default=False)
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
    _translation_toggle_intent_enabled: bool = field(init=False, default=False)
    _translation_toggle_generation: int = field(init=False, default=0)
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


    @property
    def effective_context_mode(self) -> str:
        if self.settings is None:
            return "local"
        if self._effective_integrated_context_enabled_for(self.settings):
            return "integrated"
        return "local"


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
            self.hub = None

        if self.sender is not None:
            with contextlib.suppress(Exception):
                self.sender.close()
            self.sender = None
        self.osc = None
        if self._runtime_logging is not None:
            with contextlib.suppress(Exception):
                self._runtime_logging.close()
            self._runtime_logging = None


    async def set_translation_enabled(self, enabled: bool) -> bool:
        request_generation = self._record_translation_toggle_intent(enabled)
        if self.hub is None:
            return False
        self.log_basic(f"[Translation] Toggle request: enabled={enabled}")
        self.log_detailed(
            "[Translation] Toggle detail: "
            f"current_enabled={self.hub.translation_enabled} "
            f"llm_available={self.hub.llm is not None}"
        )
        if enabled and not self._translation_toggle_intent_matches(
            enabled=True,
            generation=request_generation,
        ):
            self.log_detailed(
                "[Translation] Skipping stale enable request after newer toggle intent"
            )
            return False
        if enabled and self.hub.llm is None:
            self.hub.translation_enabled = False
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_translation_enabled(False)
            self._log_error("Translation is ON but LLM provider is not configured.")
            return False

        # Log provider info when enabling
        if enabled and self.settings is not None:
            provider = self.settings.provider.llm.value
            self.log_basic(f"[Translation] Enabled with provider: {provider}")

        # Clear context history when toggling translation
        self.hub.clear_context()
        self.hub.translation_enabled = bool(enabled)
        if enabled and self.hub.llm is not None:
            llm = self.hub.llm
            if isinstance(llm, SemaphoreLLMProvider):
                llm = llm.inner
        return bool(self.hub.translation_enabled)

    async def set_stt_enabled(self, enabled: bool) -> None:
        self.log_basic(f"[STT] Toggle request: enabled={enabled}")
        self.log_detailed(
            "[STT] Toggle detail: "
            f"desired_before={self._stt_desired} overlay_state={self.overlay_state}"
        )
        self._stt_desired = bool(enabled)
        if not enabled:
            self._reset_local_stt_pending_enable_after_install()

        # Log provider info when enabling
        if enabled and self.settings is not None:
            provider = self.settings.provider.stt.value
            self.log_basic(f"[STT] Enabled with provider: {provider}")

        if (
            enabled
            and self.settings is not None
            and self.settings.provider.stt in self._LOCAL_STT_PROVIDERS
        ):
            current_status = self._current_local_stt_runtime_status()
            if current_status == "downloading":
                self._local_stt_pending_enable_after_install = True
                self._stt_desired = False
                dash = getattr(self.app, "view_dashboard", None)
                if dash is not None:
                    dash.set_stt_enabled(False)
                self._show_short_stt_message("local_stt.download_in_progress")
                return
            if current_status in ("missing", "invalid", "download_failed"):
                self._handle_local_stt_unavailable(
                    current_status,
                    resume_self=True,
                    resume_peer=self._peer_local_stt_requested(self.settings),
                )
                return

        # Mark promo eligible when user explicitly enables STT via button
        if enabled and self.hub is not None:
            self.hub.mark_promo_eligible()

        await self._ensure_stt_switch()

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
        if self.settings is None:
            return
        self._local_stt_install_state = LocalSTTInstallState(status="ready")
        if self._local_stt_runtime_status not in ("downloading", "download_failed"):
            self._local_stt_runtime_status = "ready"
        self._sync_local_stt_notice()

    def _current_local_stt_runtime_status(self) -> str:
        if self._local_stt_runtime_status in ("downloading", "download_failed"):
            return self._local_stt_runtime_status
        return self._local_stt_install_state.status

    def _peer_local_stt_requested(self, settings: AppSettings | None = None) -> bool:
        resolved_settings = settings or self.settings
        return bool(
            resolved_settings is not None
            and resolved_settings.provider.peer_stt in self._LOCAL_STT_PROVIDERS
            and self._peer_translation_activation_requested_for(resolved_settings)
        )

    def _reset_local_stt_pending_enable_after_install(self) -> None:
        self._local_stt_pending_enable_after_install = False

    def _reset_local_stt_pending_peer_enable_after_install(self) -> None:
        self._local_stt_pending_peer_enable_after_install = False

    def _clear_local_stt_pending_enable_if_provider_switched_away(self) -> None:
        if self.settings is None:
            return
        if self.settings.provider.stt not in self._LOCAL_STT_PROVIDERS:
            self._reset_local_stt_pending_enable_after_install()
        if not self._peer_local_stt_requested(self.settings):
            self._reset_local_stt_pending_peer_enable_after_install()

    def _sync_local_stt_notice(self) -> None:
        dash = getattr(self.app, "view_dashboard", None)
        if dash is None or self.settings is None:
            return
        status = self._current_local_stt_runtime_status()
        should_show = status == "downloading" or (
            (
                self.settings.provider.stt in self._LOCAL_STT_PROVIDERS
                or self._peer_local_stt_requested(self.settings)
            )
            and status != "ready"
        )
        with contextlib.suppress(Exception):
            dash.set_local_stt_notice(
                status if should_show else None,
                percent=self._local_stt_download_percent if status == "downloading" else None,
            )

    def _handle_local_stt_unavailable(
        self,
        status: str,
        *,
        resume_self: bool,
        resume_peer: bool,
    ) -> bool:
        if status in ("missing", "invalid"):
            self._local_stt_install_state = LocalSTTInstallState(status=status)
        if self._local_stt_runtime_status != "downloading":
            self._local_stt_runtime_status = status
            self._local_stt_download_percent = None
        if resume_self:
            self._local_stt_pending_enable_after_install = True
            self._stt_desired = False
        if resume_peer:
            self._local_stt_pending_peer_enable_after_install = True
        dash = getattr(self.app, "view_dashboard", None)
        if resume_self and dash is not None:
            dash.set_stt_enabled(False)
            dash.set_stt_needs_key(False)
        self._sync_local_stt_notice()
        self._show_short_stt_message("local_stt.not_installed")
        return False

    async def _ensure_local_stt_ready(self) -> bool:
        if self.settings is None or self.settings.provider.stt not in self._LOCAL_STT_PROVIDERS:
            return True
        if self.hub is None or self.hub.stt is None:
            self._stt_desired = False
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_stt_enabled(False)
                dash.set_stt_needs_key(False)
            self._show_short_stt_message("local_stt.not_installed")
            return False
        try:
            from puripuly_heart.core.local_stt_assets import (
                default_local_stt_model_dir,
                load_local_stt_asset_manifest,
                resolve_model_id,
            )
            from puripuly_heart.config.paths import default_models_dir
            model_id = resolve_model_id(self.settings.provider.stt.value, self.settings.provider.stt_quant)
            if model_id is not None:
                model_dir = default_local_stt_model_dir(model_id, data_dir=default_models_dir())
                manifest = load_local_stt_asset_manifest(model_id)
                install_state = inspect_local_stt_install_state(model_dir, manifest=manifest)
                if install_state.status != "ready":
                    raise LocalSTTModelMissingError(f"model status: {install_state.status}")
        except (LocalSTTModelMissingError, LocalSTTManifestInvalidError) as exc:
            self._log_error(f"Local STT model not available: {exc}")
            self._stt_desired = False
            self._local_stt_install_state = LocalSTTInstallState(status="missing")
            self._local_stt_runtime_status = "missing"
            self._sync_local_stt_notice()
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_stt_enabled(False)
            self._show_short_stt_message("local_stt.not_installed")
            return False
        try:
            if not await self.hub.stt.warmup():
                raise SubprocessSTTError("STT warmup returned False — session not opened")
            self._local_stt_install_state = LocalSTTInstallState(status="ready")
            if self._local_stt_runtime_status != "downloading":
                self._local_stt_runtime_status = "ready"
            self._sync_local_stt_notice()
            return True
        except (LocalSTTModelMissingError, LocalSTTManifestInvalidError, SubprocessSTTError) as exc:
            self._log_error(f"Local STT warmup failed: {exc}")
            self._stt_desired = False
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_stt_enabled(False)
            return False

    async def _ensure_peer_local_stt_ready(self) -> bool:
        if self.settings is None:
            return True
        try:
            from puripuly_heart.core.local_stt_assets import (
                default_local_stt_model_dir,
                load_local_stt_asset_manifest,
                resolve_model_id,
            )
            from puripuly_heart.config.paths import default_models_dir
            model_id = resolve_model_id(self.settings.provider.peer_stt.value, self.settings.provider.peer_stt_quant)
            if model_id is not None:
                model_dir = default_local_stt_model_dir(model_id, data_dir=default_models_dir())
                manifest = load_local_stt_asset_manifest(model_id)
                install_state = inspect_local_stt_install_state(model_dir, manifest=manifest)
                if install_state.status != "ready":
                    self._log_error(f"Peer local STT model not available: {install_state.status}")
                    self._show_short_stt_message("local_stt.not_installed")
                    return False
        except (LocalSTTModelMissingError, LocalSTTManifestInvalidError) as exc:
            self._log_error(f"Peer local STT model check failed: {exc}")
            self._show_short_stt_message("local_stt.not_installed")
            return False
        try:
            await self._probe_peer_local_stt_runtime_load()
            self._local_stt_install_state = LocalSTTInstallState(status="ready")
            if self._local_stt_runtime_status != "downloading":
                self._local_stt_runtime_status = "ready"
            self._sync_local_stt_notice()
            return True
        except (LocalSTTModelMissingError, LocalSTTManifestInvalidError, SubprocessSTTError) as exc:
            self._log_error(f"Peer local STT warmup failed: {exc}")
            self._show_short_stt_message("local_stt.not_installed")
            return False

    async def _probe_peer_local_stt_runtime_load(self) -> None:
        assert self.settings is not None
        secrets = create_secret_store(config_path=self.config_path)
        peer_backend = create_peer_stt_backend(
            self.settings,
            secrets=secrets,
            diagnostics_enabled=self._detailed_audio_diag_enabled,
        )
        session = None
        try:
            session = await peer_backend.open_session()
        finally:
            if session is not None:
                with contextlib.suppress(Exception):
                    await session.close()
            close_backend = getattr(peer_backend, "close", None)
            if callable(close_backend):
                with contextlib.suppress(Exception):
                    await close_backend()

    async def _cancel_local_stt_download(self) -> None:
        task = self._local_stt_download_task
        cancel_event = self._local_stt_download_cancel_event
        self._reset_local_stt_pending_enable_after_install()
        self._reset_local_stt_pending_peer_enable_after_install()
        if cancel_event is not None:
            cancel_event.set()
        if task is None:
            self._local_stt_download_cancel_event = None
            return
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._local_stt_download_task = None
        self._local_stt_download_cancel_event = None

    async def _ensure_stt_switch(self) -> None:
        if self._stt_switch_task is None or self._stt_switch_task.done():
            self._stt_switch_task = asyncio.create_task(self._run_stt_switch())
        await self._stt_switch_task

    async def _replace_runtime_stt_provider(self) -> None:
        self._cancel_stt_idle_release()
        self.log_detailed(
            "[STT] Replacing runtime provider detail: "
            f"desired={self._stt_desired} mic_task_active={self._mic_task is not None}"
        )
        self.log_basic(
            f"[Settings] STT provider replacement: "
            f"provider_type={self.settings.provider.stt_compute}"
        )
        if self._stt_switch_lock is None:
            self._stt_switch_lock = asyncio.Lock()
        async with self._stt_switch_lock:
            if self._mic_task is not None:
                await self._stop_mic_loop()
            self._stt_restart_requested = False
            await self._rebuild_stt_provider()
        # Allow GPU driver to release resources before starting new backend
        # Only needed when STT is actually desired (will be started)
        if self._stt_desired and self.settings is not None and self.settings.provider.stt_compute == "gpu":
            await asyncio.sleep(0.5)
        if self._stt_desired:
            await self._ensure_stt_switch()

    _LOCAL_CPU_PROVIDERS = frozenset({
        STTProviderName.LOCAL_QWEN,
        STTProviderName.LOCAL_QWEN_17B,
        STTProviderName.LOCAL_GIGAAM_RNNT,
        STTProviderName.LOCAL_PARAKEET_TDT,
        STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
        STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
        STTProviderName.LOCAL_QWEN3_ASR_GGUF,
        STTProviderName.LOCAL_QWEN_17B_GGUF,
    })

    async def _run_stt_switch(self) -> None:
        if self._stt_switch_lock is None:
            self._stt_switch_lock = asyncio.Lock()
        async with self._stt_switch_lock:
            while True:
                desired = self._stt_desired
                restart = self._stt_restart_requested
                self._stt_restart_requested = False

                if not desired:
                    await self._stop_mic_loop()
                    if self.hub is not None:
                        self._cancel_stt_idle_release()
                        if (
                            not self._is_stopping
                            and self.settings is not None
                            and self.settings.provider.stt in self._LOCAL_CPU_PROVIDERS
                        ):
                            self._stt_idle_release_task = asyncio.create_task(
                                self._stt_idle_release_after(LOCAL_QWEN_IDLE_RELEASE_SECONDS)
                            )
                        else:
                            with contextlib.suppress(Exception):
                                await self.hub.stt.close()
                else:
                    self._cancel_stt_idle_release()
                    if self.hub is None:
                        self.log_detailed(
                            "[STT] Enable requested before hub is ready",
                            level=logging.WARNING,
                        )
                        break
                    if restart:
                        await self._stop_mic_loop()
                        with contextlib.suppress(Exception):
                            await self.hub.stt.close()
                    if not await self._ensure_local_stt_ready():
                        break
                    await self._start_mic_loop()
                    # Pre-warm STT session for faster first response
                    if (
                        self.hub is not None
                        and self.hub.stt is not None
                        and self._selected_stt_provider() not in self._LOCAL_STT_PROVIDERS
                    ):
                        with contextlib.suppress(Exception):
                            await self.hub.stt.warmup()

                if desired == self._stt_desired and not self._stt_restart_requested:
                    break

    def _cancel_stt_idle_release(self) -> None:
        task = self._stt_idle_release_task
        self._stt_idle_release_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _stt_idle_release_after(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if self.hub is not None and self.hub.stt is not None:
            stt = self.hub.stt
            self.log_basic(f"[STT] Idle release after {delay:.0f}s — closing backend")
            # Clear pending state before closing
            stt._pending_final_utterance_ids.clear()
            stt._pending_final_utterance_times.clear()
            if stt._audio_ring is not None:
                stt._audio_ring.clear()
            # Drain event queue
            while True:
                try:
                    stt._events.get_nowait()
                except asyncio.QueueEmpty:
                    break
            with contextlib.suppress(Exception):
                await stt.close()
            stt._closing = False


    async def verify_api_key(self, provider: str, key: str, base_url: str | None = None) -> tuple[bool, str]:
        """Verify API key using model_discovery (works reliably in Flet event loop)."""

        if not key:
            self.log_basic(f"[VerifyKey] provider={provider} result=empty_key")
            return False, "API Key is empty"

        masked = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***"

        if base_url is None:
            if provider == "openai_compatible":
                base_url = self.settings.provider.openai_compatible.base_url
            elif provider == "local_llm":
                base_url = self.settings.provider.local_llm.base_url
            else:
                self.log_basic(f"[VerifyKey] provider={provider} result=unknown_provider")
                return False, f"Unknown provider: {provider}"

        self.log_basic(f"[VerifyKey] provider={provider} key={masked} base_url={base_url}")

        try:
            discovery = self.model_discovery
            if discovery is None:
                return False, "Model discovery not initialized"
            status_code, body = await discovery.test_connection(base_url, key)

            if status_code == 200:
                self.log_basic(f"[VerifyKey] provider={provider} result=OK status={status_code}")
                return True, ""
            if status_code == 401:
                self.log_basic(f"[VerifyKey] provider={provider} result=bad_key status=401")
                return False, "API key invalid (401 Unauthorized)"
            if status_code == 0:
                self.log_basic(f"[VerifyKey] provider={provider} result=connection_error detail={body}")
                return False, f"Cannot reach endpoint {base_url}: {body}"
            self.log_basic(f"[VerifyKey] provider={provider} result=unexpected_status={status_code} body={body}")
            return False, f"Server returned {status_code}: {body}"
        except Exception as exc:
            self.log_basic(f"[VerifyKey] provider={provider} exception={exc}")
            self._log_error(f"Verification error for {provider}: {exc}")
            return False, str(exc)

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
        self._save_settings()
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
        if self.hub is None or self.settings is None:
            return

        # Close existing LLM provider
        previous_llm = self.hub.llm
        self.hub.llm = None
        if previous_llm is not None:
            with contextlib.suppress(Exception):
                await previous_llm.close()
        if self.hub is None:
            return

        # Create new LLM provider with current settings
        llm = None
        llm_error: Exception | None = None
        try:
            secrets = create_secret_store(config_path=self.config_path)
            llm = create_llm_provider(
                self.settings,
                secrets=secrets,
                runtime_logging=self.runtime_logging,
            )
        except Exception as exc:
            llm_error = exc

        if self.hub is None:
            return

        # Update hub's LLM provider
        self.hub.llm = llm

        # Rebuild fallback provider
        previous_fallback = self.hub.fallback_llm
        self.hub.fallback_llm = None
        if previous_fallback is not None:
            with contextlib.suppress(Exception):
                await previous_fallback.close()
        try:
            fallback_llm = create_fallback_llm_provider(
                self.settings,
                secrets=secrets,
                runtime_logging=self.runtime_logging,
            )
            self.hub.fallback_llm = fallback_llm
        except Exception as exc:
            logger.warning("[LLM] Failed to create fallback provider: %s", exc)

        # Sync TranslationService LLM references
        if self.hub.translation_service is not None:
            self.hub.translation_service.llm = llm
            self.hub.translation_service.fallback_llm = self.hub.fallback_llm

        dash = getattr(self.app, "view_dashboard", None)

        # Stop translation if provider changed while translation was active.
        # Without this the dashboard toggle stays green but translation is broken/stale.
        if self.hub.translation_enabled:
            self.hub.translation_enabled = False
            if dash is not None:
                dash.set_translation_enabled(False)

        # Update needs_key DATA only (no UI update).
        # The toggle handler checks this before enabling — if the provider has no key,
        # the first click shows a warning instead of turning ON.
        # We do NOT call set_translation_needs_key() here because that would
        # turn the button yellow just from switching providers in settings.
        if dash is not None:
            dash.translation_needs_key = (
                (llm is None) and self._llm_provider_requires_secret(self.settings.provider.llm)
            )

        if llm is None:
            message = "LLM provider not available"
            if llm_error is not None:
                message = f"{message}: {llm_error}"
            self._log_error(message)
            return

        self.log_basic("[Settings] LLM provider rebuilt successfully")

    async def _rebuild_stt_provider(self) -> None:
        """Rebuild only the STT provider so later enable uses current settings."""
        if self.hub is None or self.settings is None:
            return

        stt = None
        stt_error: Exception | None = None
        try:
            secrets = create_secret_store(config_path=self.config_path)
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
            stt_error = exc

        await self.hub.replace_stt_provider(stt)
        if self.hub is None:
            return
        self._sync_effective_hub_flags(self.settings)

        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            dash.set_stt_needs_key(False)
            if stt is None:
                dash.set_stt_enabled(False)

        if stt is None:
            assert stt_error is not None
            self._log_error(f"STT backend not available: {stt_error}")
            return

        self.log_basic("[Settings] STT provider replacement completed successfully")


    async def _init_pipeline(self) -> None:
        assert self.settings is not None
        self._sync_signature_caches(self.settings)
        secrets = create_secret_store(config_path=self.config_path)

        from puripuly_heart.app.wiring import create_model_discovery
        if self.model_discovery is None:
            self.model_discovery = create_model_discovery()

        settings_view = getattr(self.app, "view_settings", None)
        if settings_view is not None:
            settings_view.model_discovery = self.model_discovery

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

        from puripuly_heart.config.prompts import warm_prompt_cache
        warm_prompt_cache()
        hub = Pipeline(
            stt=stt,
            llm=llm,
            fallback_llm=fallback_llm,
            osc=osc,
            overlay_event_adapter=OverlayEventAdapter(clock=self.clock),
            peer_stt=None,
            clock=self.clock,
            runtime_logging=self.runtime_logging,
            source_language=self.settings.languages.source_language,
            target_language=self.settings.languages.target_language,
            second_target_language=self.settings.languages.second_target_language,
            peer_source_language=self.settings.languages.peer_source_language,
            peer_target_language=self.settings.languages.peer_target_language,
            system_prompt=self.settings.system_prompt,
            chatbox_include_source=self.settings.osc.chatbox_include_source,
            fallback_transcript_only=True,
            translation_enabled=True,
            peer_translation_enabled=False,
            integrated_context_enabled=False,
            low_latency_mode=self.settings.stt.low_latency_mode,
            low_latency_spec_retry_max=self.settings.stt.low_latency_spec_retry_max,
            hangover_s=(
                self.settings.stt.low_latency_vad_hangover_ms / 1000.0
                if self.settings.stt.low_latency_mode
                else DEFAULT_STABLE_VAD_HANGOVER_MS / 1000.0
            ),
            peer_hangover_s=self.settings.desktop_audio.vad_hangover_ms / 1000.0,
        )

        from puripuly_heart.core.translation_service import TranslationService
        from puripuly_heart.core.output_dispatcher import OutputDispatcher

        hub.translation_service = TranslationService(
            llm=llm,
            fallback_llm=fallback_llm,
            context_resolver=hub.context_resolver,
            clock=self.clock,
            system_prompt=self.settings.system_prompt,
            second_target_language=self.settings.languages.second_target_language,
            integrated_context_enabled=False,
            peer_translation_enabled=False,
            source_language=self.settings.languages.source_language,
            target_language=self.settings.languages.target_language,
            peer_source_language=self.settings.languages.peer_source_language,
            peer_target_language=self.settings.languages.peer_target_language,
            runtime_logging=self.runtime_logging,
            render_prompt=render_translation_prompt_template,
            render_dual_prompt=render_dual_translation_prompt_template,
        )
        hub.output_dispatcher = OutputDispatcher(
            osc=osc,
            clock=self.clock,
            chatbox_include_source=self.settings.osc.chatbox_include_source,
        )

        if self.vrc_mic_state is None:
            self.vrc_mic_state = VrcMicState()
        if self.vrc_mic_audio_gate is None:
            self.vrc_mic_audio_gate = VrcMicAudioGate(
                state=self.vrc_mic_state,
                enabled=self.settings.osc.vrc_mic_intercept,
            )
        else:
            self.vrc_mic_audio_gate.state = self.vrc_mic_state
            self.vrc_mic_audio_gate.set_enabled(self.settings.osc.vrc_mic_intercept)
        self.vrc_mic_audio_gate.set_receiver_active(self.receiver is not None)
        self.vrc_mic_audio_gate.reset()

        self.sender = sender
        self.osc = osc
        self.hub = hub

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
        await self._configure_vrc_mic_receiver(enabled=self.settings.osc.vrc_mic_intercept)


    async def _start_mic_loop(self) -> None:
        assert self.settings is not None
        assert self.hub is not None

        if self._mic_task is not None:
            return

        if self._audio_source is not None or self._last_mic_loop_close_exception is not None:
            await self._stop_mic_loop()
            if self._audio_source is not None or self._last_mic_loop_close_exception is not None:
                self.log_detailed(
                    "[STT] Skipping microphone start while previous microphone source close is pending",
                    level=logging.WARNING,
                    exception=self._last_mic_loop_close_exception,
                )
                return

        try:
            from puripuly_heart.config.paths import default_vad_model_path
            model_path = ensure_silero_vad_onnx(target_path=default_vad_model_path())
        except Exception as exc:
            self._log_error(f"Failed to prepare Silero VAD model ({SILERO_VAD_VERSION}): {exc}")
            return

        if self._mic_task is None:
            vad = VadGating(
                engine=SileroVadOnnx(model_path=model_path),
                sample_rate_hz=self.settings.audio.internal_sample_rate_hz,
                ring_buffer_ms=self.settings.audio.ring_buffer_ms,
                speech_threshold=self.settings.stt.vad_speech_threshold,
                hangover_ms=(
                    self.settings.stt.low_latency_vad_hangover_ms
                    if self.settings.stt.low_latency_mode
                    else DEFAULT_STABLE_VAD_HANGOVER_MS
                ),
                diagnostic_event_callback=lambda message: self.log_detailed(message),
                diagnostics_enabled=self._detailed_audio_diag_enabled,
                diagnostic_label="self",
            )

            def _resolve_device(host_api: str, device: str) -> int | None:
                try:
                    return resolve_sounddevice_input_device(host_api=host_api, device=device)
                except Exception as exc:
                    self.log_detailed(
                        "[STT] Device resolution detail: "
                        f"host_api={host_api!r} device={device!r} error={exc}",
                        level=logging.WARNING,
                    )
                    return None

            def _source_int(source: SoundDeviceAudioSource, attr: str, fallback: int) -> int:
                try:
                    value = getattr(source, attr, fallback)
                    return int(value)
                except Exception:
                    return fallback

            def _log_mic_capture_format(
                *,
                attempt: str,
                dev_idx: int | None,
                requested_channels: int,
                decision: SelfMicCaptureChannelDecision,
                source: SoundDeviceAudioSource,
                host_api_for_log: str,
                device_for_log: str,
                wasapi_auto_convert: bool,
                wasapi_exclusive: bool,
            ) -> None:
                metadata = decision.metadata
                opened_channels = _source_int(source, "opened_channels", requested_channels)
                frame_channels = _source_int(source, "frame_channels", opened_channels)
                frame_channels_source = "opened_fallback"
                actual_sample_rate_hz = _source_int(source, "actual_sample_rate_hz", 0)
                self.log_detailed(
                    "[STT] Microphone capture format: "
                    f"attempt={attempt!r} "
                    f"internal_channels={decision.internal_channels} "
                    f"preferred_capture_channels={decision.preferred_capture_channels} "
                    f"requested_channels={requested_channels} "
                    f"opened_channels={opened_channels} "
                    f"frame_channels={frame_channels} "
                    f"frame_channels_source={frame_channels_source!r} "
                    f"saved_host_api={saved_host_api!r} "
                    f"actual_host_api={host_api_for_log!r} "
                    f"device={device_for_log!r} "
                    f"device_idx={dev_idx} "
                    f"wasapi_auto_convert={wasapi_auto_convert} "
                    f"wasapi_exclusive={wasapi_exclusive} "
                    f"actual_sample_rate_hz={actual_sample_rate_hz or None} "
                    f"metadata_device_idx={metadata.device_idx} "
                    f"metadata_device_name={metadata.name!r} "
                    f"device_max_input_channels={metadata.max_input_channels} "
                    f"device_default_samplerate={metadata.default_samplerate} "
                    f"metadata_status={metadata.metadata_status!r} "
                    f"metadata_error={metadata.metadata_error!r}"
                )

            def _open_source_once(
                dev_idx: int | None,
                *,
                attempt: str,
                requested_channels: int,
                decision: SelfMicCaptureChannelDecision,
                host_api_for_log: str,
                device_for_log: str,
                wasapi_auto_convert: bool = False,
                wasapi_exclusive: bool = False,
            ) -> SoundDeviceAudioSource:
                source = SoundDeviceAudioSource(
                    sample_rate_hz=None,
                    channels=requested_channels,
                    device=dev_idx,
                    wasapi_auto_convert=wasapi_auto_convert,
                    wasapi_exclusive=wasapi_exclusive,
                )
                _log_mic_capture_format(
                    attempt=attempt,
                    dev_idx=dev_idx,
                    requested_channels=requested_channels,
                    decision=decision,
                    source=source,
                    host_api_for_log=host_api_for_log,
                    device_for_log=device_for_log,
                    wasapi_auto_convert=wasapi_auto_convert,
                    wasapi_exclusive=wasapi_exclusive,
                )
                return source

            def _open_source_with_mono_retry(
                dev_idx: int | None,
                *,
                attempt: str,
                host_api_for_log: str,
                device_for_log: str,
                wasapi_auto_convert: bool = False,
                wasapi_exclusive: bool = False,
            ) -> SoundDeviceAudioSource:
                decision = determine_self_mic_capture_channels(
                    device_idx=dev_idx,
                    internal_channels=self.settings.audio.internal_channels,
                )
                try:
                    return _open_source_once(
                        dev_idx,
                        attempt=attempt,
                        requested_channels=decision.preferred_capture_channels,
                        decision=decision,
                        host_api_for_log=host_api_for_log,
                        device_for_log=device_for_log,
                        wasapi_auto_convert=wasapi_auto_convert,
                        wasapi_exclusive=wasapi_exclusive,
                    )
                except Exception as exc:
                    if decision.preferred_capture_channels <= self.settings.audio.internal_channels:
                        raise
                    self.log_detailed(
                        "[STT] Microphone open detail: "
                        f"attempt={attempt!r} "
                        f"host_api={host_api_for_log!r} "
                        f"device={device_for_log!r} "
                        f"device_idx={dev_idx} "
                        f"preferred_capture_channels={decision.preferred_capture_channels} "
                        f"requested_channels={decision.preferred_capture_channels} "
                        f"wasapi_auto_convert={wasapi_auto_convert} "
                        f"wasapi_exclusive={wasapi_exclusive} "
                        f"metadata_status={decision.metadata.metadata_status!r} "
                        f"will_retry_mono=True "
                        f"error={exc}",
                        level=logging.WARNING,
                    )
                    retry_attempt = f"{attempt}_mono_retry"
                    return _open_source_once(
                        dev_idx,
                        attempt=retry_attempt,
                        requested_channels=self.settings.audio.internal_channels,
                        decision=decision,
                        host_api_for_log=host_api_for_log,
                        device_for_log=device_for_log,
                        wasapi_auto_convert=wasapi_auto_convert,
                        wasapi_exclusive=wasapi_exclusive,
                    )

            saved_host_api = self.settings.audio.input_host_api
            host_api_profile = normalize_input_host_api(saved_host_api)
            host_api = host_api_profile.actual_host_api
            first_open_used_wasapi_flags = (
                host_api_profile.wasapi_auto_convert or host_api_profile.wasapi_exclusive
            )
            device_name = self.settings.audio.input_device

            # 1차 시도: 설정된 Host API + 마이크
            device_idx = _resolve_device(host_api, device_name)
            source: SoundDeviceAudioSource | None = None

            try:
                source = _open_source_with_mono_retry(
                    device_idx,
                    attempt="primary",
                    host_api_for_log=host_api,
                    device_for_log=device_name,
                    wasapi_auto_convert=host_api_profile.wasapi_auto_convert,
                    wasapi_exclusive=host_api_profile.wasapi_exclusive,
                )
                self.log_detailed(
                    "[STT] Microphone opened: "
                    f"saved_host_api={saved_host_api!r} "
                    f"actual_host_api={host_api!r} "
                    f"device={device_name!r} "
                    f"device_idx={device_idx} "
                    f"wasapi_auto_convert={host_api_profile.wasapi_auto_convert} "
                    f"wasapi_exclusive={host_api_profile.wasapi_exclusive}"
                )
            except Exception as exc:
                self.log_detailed(
                    "[STT] Microphone open detail: "
                    f"host_api={host_api!r} device={device_name!r} error={exc}",
                    level=logging.ERROR,
                )

            # 2차 시도: Host API 무시, 마이크 이름만
            if source is None and device_name:
                fallback_idx = _resolve_device("", device_name)
                if fallback_idx != device_idx or first_open_used_wasapi_flags:
                    try:
                        source = _open_source_with_mono_retry(
                            fallback_idx,
                            attempt="name_fallback",
                            host_api_for_log="",
                            device_for_log=device_name,
                            wasapi_auto_convert=False,
                            wasapi_exclusive=False,
                        )
                        self.log_detailed(
                            f"[STT] Microphone opened with fallback: device_idx={fallback_idx}"
                        )
                    except Exception as exc:
                        self.log_detailed(
                            f"[STT] Fallback microphone detail: error={exc}",
                            level=logging.ERROR,
                        )

            # 3차 시도: 시스템 기본 장치
            if source is None:
                try:
                    source = _open_source_with_mono_retry(
                        None,
                        attempt="system_default",
                        host_api_for_log="",
                        device_for_log="",
                        wasapi_auto_convert=False,
                        wasapi_exclusive=False,
                    )
                    self.log_detailed("[STT] Microphone opened with system default")
                except Exception as exc:
                    self.log_detailed(
                        f"[STT] System default microphone detail: error={exc}",
                        level=logging.ERROR,
                    )

            if source is None:
                self._log_error("All microphone attempts failed")
                return

            self._vad = vad
            self._audio_source = self._wrap_diagnostic_audio_source(source, channel_label="self")
            self._mic_task = asyncio.create_task(self._run_mic_loop())

    async def _stop_mic_loop(self) -> None:
        if self._mic_task is not None:
            self._mic_task.cancel()
            await asyncio.gather(self._mic_task, return_exceptions=True)
            self._mic_task = None

        if self._audio_source is not None:
            try:
                await self._audio_source.close()
            except Exception as exc:
                self._last_mic_loop_close_exception = exc
            else:
                self._last_mic_loop_close_exception = None
                self._audio_source = None
        self._vad = None
        if self.vrc_mic_audio_gate is not None:
            self.vrc_mic_audio_gate.reset()

    async def _run_mic_loop(self) -> None:
        assert self.hub is not None
        assert self._audio_source is not None
        assert self._vad is not None

        from puripuly_heart.app.headless_mic import run_audio_vad_loop

        try:
            await run_audio_vad_loop(
                source=self._audio_source,
                vad=self._vad,
                sink=_HubVadSink(hub=self.hub),
                target_sample_rate_hz=self.settings.audio.internal_sample_rate_hz,  # type: ignore[union-attr]
                audio_gate=self.vrc_mic_audio_gate,
                channel_label="self",
                is_detailed_enabled=self._detailed_audio_diag_enabled,
                log_detailed=lambda message: self.log_detailed(message),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log_error(f"Mic loop error: {exc}")

    async def _configure_vrc_mic_receiver(self, *, enabled: bool) -> None:
        if self._vrc_receiver_lock is None:
            self._vrc_receiver_lock = asyncio.Lock()

        async with self._vrc_receiver_lock:
            self._last_vrc_mic_sync_enabled = enabled
            if self.vrc_mic_audio_gate is not None:
                self.vrc_mic_audio_gate.set_enabled(enabled)

            if not enabled:
                self._stop_vrc_mic_receiver()
                return

            if self.receiver is not None or self.vrc_mic_state is None:
                if self.vrc_mic_audio_gate is not None:
                    self.vrc_mic_audio_gate.set_receiver_active(self.receiver is not None)
                return

            receiver = VrcOscReceiver(
                state=self.vrc_mic_state,
                host=VRC_OSC_RECEIVER_HOST,
                port=VRC_OSC_RECEIVER_PORT,
            )
            try:
                await receiver.start()
            except OSError as exc:
                if self.vrc_mic_audio_gate is not None:
                    self.vrc_mic_audio_gate.set_receiver_active(False)
                self._log_error(
                    "VRChat mic sync receiver unavailable on "
                    f"{VRC_OSC_RECEIVER_HOST}:{VRC_OSC_RECEIVER_PORT}: {exc}"
                )
                return

            self.receiver = receiver
            if self.vrc_mic_audio_gate is not None:
                self.vrc_mic_audio_gate.set_receiver_active(True)
                self.vrc_mic_audio_gate.reset()

    def _stop_vrc_mic_receiver(self) -> None:
        if self.receiver is not None:
            with contextlib.suppress(Exception):
                self.receiver.stop()
            self.receiver = None
        if self.vrc_mic_audio_gate is not None:
            self.vrc_mic_audio_gate.set_receiver_active(False)


    @property
    def runtime_logging(self) -> SessionRuntimeLoggingService:
        if self._runtime_logging is None:
            from puripuly_heart.config.paths import user_config_dir
            self._runtime_logging = SessionRuntimeLoggingService(ui_handler_factory=FletLogHandler, log_dir=user_config_dir())
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


    async def _emit_overlay_runtime_logging_mode_update(self) -> None:
        bridge = self._overlay_bridge
        if bridge is None:
            return
        await bridge.broadcast_runtime_control(logging_mode=self.runtime_logging_mode)

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

    def _log_error(self, message: str) -> None:
        self.log_basic(message, level=logging.ERROR)
