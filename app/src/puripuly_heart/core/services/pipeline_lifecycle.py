"""Pipeline lifecycle manager — init, start, stop, mic loop, VAD.

Architecture:
- PipelineLifecycleManager creates and owns Pipeline instance
- Controller delegates start/stop to manager
- Manager uses callbacks for UI side-effects (dashboard updates)
- Mic loop runs inside manager, controller only observes state
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from puripuly_heart.config.settings import AppSettings
from puripuly_heart.config.vad_defaults import DEFAULT_STABLE_VAD_HANGOVER_MS
from puripuly_heart.core.audio.gate import VrcMicAudioGate
from puripuly_heart.core.audio.source import (
    SelfMicCaptureChannelDecision,
    SoundDeviceAudioSource,
    determine_self_mic_capture_channels,
    resolve_sounddevice_input_device,
)
from puripuly_heart.core.clock import SystemClock
from puripuly_heart.core.osc.receiver import (
    VRC_OSC_RECEIVER_HOST,
    VRC_OSC_RECEIVER_PORT,
    VrcMicState,
    VrcOscReceiver,
)
from puripuly_heart.core.pipeline.pipeline import Pipeline
from puripuly_heart.core.runtime.peer_channel import PeerChannelRuntime
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.core.vad.bundled import SILERO_VAD_VERSION, ensure_silero_vad_onnx
from puripuly_heart.core.vad.gating import VadGating
from puripuly_heart.core.vad.silero import SileroVadOnnx
from puripuly_heart.ports.osc import OscSink

if TYPE_CHECKING:
    from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService

logger = logging.getLogger(__name__)

STT_RESET_DEADLINE_S = 300.0


@dataclass(slots=True)
class _HubVadSink:
    """Adapter between VAD events and Pipeline."""
    hub: Pipeline
    channel: str = "self"

    async def handle_vad_event(self, event) -> None:  # noqa: ANN001
        if self.channel == "peer":
            await self.hub.handle_peer_vad_event(event)
            return
        await self.hub.handle_vad_event(event)


@dataclass
class PipelineLifecycleManager:
    """Does NOT know about Flet/UI. Uses callbacks for side-effects."""

    config_path: Path
    clock: SystemClock = field(default_factory=SystemClock)

    # Runtime state
    hub: Pipeline | None = None
    sender: object | None = None
    osc: OscSink | None = None
    vrc_mic_state: VrcMicState | None = None
    vrc_mic_audio_gate: VrcMicAudioGate | None = None
    _peer_runtime: PeerChannelRuntime | None = None

    # Mic loop state
    _mic_task: asyncio.Task[None] | None = None
    _audio_source: object | None = None  # AudioSource
    _vad: VadGating | None = None
    _last_mic_loop_close_exception: BaseException | None = field(
        init=False, default=None, repr=False,
    )
    _vrc_receiver_lock: asyncio.Lock | None = None
    _last_vrc_mic_sync_enabled: bool | None = None
    receiver: object | None = None  # VrcOscReceiver

    # Dependency-injected audio loop runner (to avoid core/ → app/ import)
    run_audio_vad_loop: object = None  # injected from app.headless_mic

    # Callbacks
    on_pipeline_created: Callable[[Pipeline], None] | None = None
    on_pipeline_started: Callable[[], None] | None = None
    on_pipeline_stopped: Callable[[], None] | None = None
    on_error: Callable[[str], None] | None = None
    log_detailed: Callable[[str], None] | None = None
    diagnostic_wrapper: Callable[[object, str], object] | None = None
    is_detailed_diag_enabled: Callable[[], bool] | None = None

    async def init_pipeline(
        self,
        settings: AppSettings,
        runtime_logging: SessionRuntimeLoggingService,
        *,
        llm_provider: object | None = None,
        fallback_llm: object | None = None,
        stt_provider: ManagedSTTProvider | None = None,
        overlay_adapter: object | None = None,
        osc: OscSink | None = None,
        sender: object | None = None,
        peer_stt_factory: object | None = None,
        peer_source_factory: object | None = None,
        peer_vad_factory: object | None = None,
        peer_run_audio_loop: object | None = None,
        peer_vad_model_resolver: object | None = None,
    ) -> Pipeline:
        from puripuly_heart.config.prompts import (
            render_dual_translation_prompt_template,
            render_translation_prompt_template,
            warm_prompt_cache,
        )
        from puripuly_heart.core.output_dispatcher import OutputDispatcher
        from puripuly_heart.core.translation_service import TranslationService

        assert osc is not None, "osc (OscSink) must be passed by controller"
        assert sender is not None, "sender must be passed by controller"

        warm_prompt_cache()

        if overlay_adapter is None:
            overlay_adapter = object()

        hub = Pipeline(
            stt=stt_provider,
            llm=llm_provider,
            fallback_llm=fallback_llm,
            osc=osc,
            overlay_event_adapter=overlay_adapter,
            peer_stt=None,
            clock=self.clock,
            runtime_logging=runtime_logging,
            source_language=settings.languages.source_language,
            target_language=settings.languages.target_language,
            second_target_language=settings.languages.second_target_language,
            peer_source_language=settings.languages.peer_source_language,
            peer_target_language=settings.languages.peer_target_language,
            system_prompt=settings.system_prompt,
            chatbox_include_source=settings.osc.chatbox_include_source,
            fallback_transcript_only=True,
            translation_enabled=True,
            peer_translation_enabled=False,
            integrated_context_enabled=False,
            low_latency_mode=settings.stt.low_latency_mode,
            low_latency_spec_retry_max=settings.stt.low_latency_spec_retry_max,
            hangover_s=(
                settings.stt.low_latency_vad_hangover_ms / 1000.0
                if settings.stt.low_latency_mode
                else DEFAULT_STABLE_VAD_HANGOVER_MS / 1000.0
            ),
            peer_hangover_s=settings.desktop_audio.vad_hangover_ms / 1000.0,
        )

        hub.translation_service = TranslationService(
            llm=llm_provider,
            fallback_llm=fallback_llm,
            context_resolver=hub.context_resolver,
            clock=self.clock,
            system_prompt=settings.system_prompt,
            second_target_language=settings.languages.second_target_language,
            integrated_context_enabled=False,
            peer_translation_enabled=False,
            source_language=settings.languages.source_language,
            target_language=settings.languages.target_language,
            peer_source_language=settings.languages.peer_source_language,
            peer_target_language=settings.languages.peer_target_language,
            runtime_logging=runtime_logging,
            render_prompt=render_translation_prompt_template,
            render_dual_prompt=render_dual_translation_prompt_template,
        )
        hub.output_dispatcher = OutputDispatcher(
            osc=osc,
            clock=self.clock,
            chatbox_include_source=settings.osc.chatbox_include_source,
        )

        if self.vrc_mic_state is None:
            self.vrc_mic_state = VrcMicState()
        if self.vrc_mic_audio_gate is None:
            self.vrc_mic_audio_gate = VrcMicAudioGate(
                state=self.vrc_mic_state,
                enabled=settings.osc.vrc_mic_intercept,
            )
        else:
            self.vrc_mic_audio_gate.state = self.vrc_mic_state
            self.vrc_mic_audio_gate.set_enabled(settings.osc.vrc_mic_intercept)

        self.sender = sender
        self.osc = osc
        self.hub = hub

        if self.on_pipeline_created is not None:
            self.on_pipeline_created(hub)

        return hub

    async def start(self, settings: AppSettings) -> None:
        if self.hub is None:
            return
        await self.hub.start(auto_flush_osc=True)
        await self.configure_vrc_receiver(
            enabled=settings.osc.vrc_mic_intercept,
            settings=settings,
        )
        if self.on_pipeline_started is not None:
            self.on_pipeline_started()

    async def stop(self) -> None:
        await self.configure_vrc_receiver(enabled=False)
        await self.stop_mic_loop()

        if self._peer_runtime is not None:
            with contextlib.suppress(Exception):
                await self._peer_runtime.close()
            self._peer_runtime = None

        if self.hub is not None:
            with contextlib.suppress(Exception):
                await self.hub.stop()
            self.hub = None

        if self.sender is not None:
            with contextlib.suppress(Exception):
                self.sender.close()
            self.sender = None
        self.osc = None

        if self.on_pipeline_stopped is not None:
            self.on_pipeline_stopped()

    async def start_mic_loop(self, settings: AppSettings) -> None:
        """Complex retry logic: primary → name fallback → system default."""
        if self.hub is None:
            return

        diag_enabled = (
            self.is_detailed_diag_enabled()
            if self.is_detailed_diag_enabled is not None
            else False
        )

        from puripuly_heart.config.paths import default_vad_model_path

        vad_model_path = default_vad_model_path()
        ensure_silero_vad_onnx(target_path=vad_model_path)

        vad = VadGating(
            vad_model=SileroVadOnnx(str(vad_model_path)),
            sample_rate=settings.audio.internal_sample_rate_hz,
            speech_start_min_frames=1,
            speech_end_min_frames=1,
            chunk_samples=settings.audio.ring_buffer_ms * settings.audio.internal_sample_rate_hz // 1000,
            hangover_s=(
                settings.stt.low_latency_vad_hangover_ms / 1000.0
                if settings.stt.low_latency_mode
                else DEFAULT_STABLE_VAD_HANGOVER_MS / 1000.0
            ),
            diagnostics_enabled=diag_enabled,
        )

        from puripuly_heart.core.audio.source import normalize_input_host_api
        normalize_input_host_api(settings.audio.input_host_api)

        device_info = resolve_sounddevice_input_device(
            preferred_device_index=settings.audio.input_device_index,
            preferred_device_name=settings.audio.input_device_name,
        )
        if device_info is None:
            if self.on_error is not None:
                self.on_error("No microphone found — STT disabled")
            return

        channels_decision = determine_self_mic_capture_channels(
            device_info=device_info,
            requested_channels=settings.audio.input_channels,
        )
        if channels_decision is SelfMicCaptureChannelDecision.SKIP_DEVICE:
            if self.on_error is not None:
                self.on_error("Microphone not suitable — STT disabled")
            return

        source = SoundDeviceAudioSource(
            device_info=device_info,
            sample_rate=settings.audio.internal_sample_rate_hz,
            channels=channels_decision.resolved_channels,
            blocksize_samples=settings.audio.ring_buffer_ms * settings.audio.internal_sample_rate_hz // 1000,
        )

        if self.diagnostic_wrapper is not None:
            source = self.diagnostic_wrapper(source, "self")

        self._audio_source = source
        self._vad = vad

        sink = _HubVadSink(hub=self.hub, channel="self")
        self._mic_task = asyncio.create_task(
            self.run_audio_vad_loop(
                source=source,
                vad=vad,
                sink=sink,
                clock=self.clock,
                is_detailed_diag_enabled=diag_enabled,
            )
        )

    async def stop_mic_loop(self) -> None:
        if self._mic_task is not None:
            self._mic_task.cancel()
            with contextlib.suppress(Exception):
                await self._mic_task
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

    @property
    def mic_task_active(self) -> bool:
        return self._mic_task is not None and not self._mic_task.done()

    async def configure_vrc_receiver(
        self, *, enabled: bool, settings: AppSettings | None = None,
    ) -> None:
        """Enable/disable VRC OSC receiver for mic sync."""
        if self._vrc_receiver_lock is None:
            self._vrc_receiver_lock = asyncio.Lock()

        async with self._vrc_receiver_lock:
            self._last_vrc_mic_sync_enabled = enabled
            if self.vrc_mic_audio_gate is not None:
                self.vrc_mic_audio_gate.set_enabled(enabled)

            if not enabled:
                self._stop_vrc_receiver()
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
                if self.on_error is not None:
                    self.on_error(
                        f"VRChat mic sync receiver unavailable on "
                        f"{VRC_OSC_RECEIVER_HOST}:{VRC_OSC_RECEIVER_PORT}: {exc}"
                    )
                return

            self.receiver = receiver
            if self.vrc_mic_audio_gate is not None:
                self.vrc_mic_audio_gate.set_receiver_active(True)
                self.vrc_mic_audio_gate.reset()

    def _stop_vrc_receiver(self) -> None:
        if self.receiver is not None:
            with contextlib.suppress(Exception):
                self.receiver.stop()
            self.receiver = None
        if self.vrc_mic_audio_gate is not None:
            self.vrc_mic_audio_gate.set_receiver_active(False)
