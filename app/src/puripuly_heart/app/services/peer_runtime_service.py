"""PeerRuntimeService — peer STT/audio pipeline lifecycle.

Extracted from PeerRuntimeManagerMixin. Manages PeerChannelRuntime,
peer STT provider creation, and peer audio source/VAD configuration.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from puripuly_heart.app.wiring import build_peer_stt_provider_signature
from puripuly_heart.config.settings import STT_RESET_DEADLINE_S
from puripuly_heart.domain.providers import STTProviderName
from puripuly_heart.core.audio.desktop_pipeline import DesktopPeerPipeline
from puripuly_heart.core.audio.desktop_source import DesktopLoopbackAudioSource
from puripuly_heart.core.runtime.peer_channel import PeerChannelRuntime, PeerRuntimeConfig
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.core.vad.gating import create_peer_vad_gating
from puripuly_heart.core.vad.silero import SileroVadOnnx

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings

logger = logging.getLogger(__name__)


def build_peer_runtime_config(settings: AppSettings) -> PeerRuntimeConfig:
    """Pure function: build PeerRuntimeConfig from settings.

    Extracted as module-level function so SettingsManagerMixin can import
    directly without going through the service.
    """
    from puripuly_heart.app.wiring import resolve_peer_stt_config

    backend = resolve_peer_stt_config(settings)
    provider_signature = build_peer_stt_provider_signature(settings)
    return PeerRuntimeConfig(
        backend=backend,
        output_device=settings.desktop_audio.output_device,
        vad_threshold=settings.desktop_audio.vad_speech_threshold,
        vad_hangover_ms=settings.desktop_audio.vad_hangover_ms,
        vad_pre_roll_ms=settings.desktop_audio.vad_pre_roll_ms,
        provider_signature=provider_signature,
        runtime_signature=(
            backend.source_language,
            settings.desktop_audio.output_device,
            settings.desktop_audio.vad_speech_threshold,
            settings.desktop_audio.vad_hangover_ms,
            settings.desktop_audio.vad_pre_roll_ms,
            provider_signature,
        ),
    )


@dataclass
class PeerRuntimeService:
    """Peer STT/audio pipeline lifecycle management."""

    # Injected dependencies
    _peer_runtime: PeerChannelRuntime | None = None
    _config_path: Path | None = None
    _clock: object | None = None
    _runtime_logging: object | None = None
    _signature_detector: object | None = None

    # Callbacks
    _log_basic: Callable[[str], None] | None = None
    _log_detailed: Callable[[str], None] | None = None
    _detailed_audio_diag_enabled: Callable[[], bool] | None = None
    _debug_audio_fault_allowed: Callable[[], bool] | None = None
    _debug_stt_fault_profile_provider: Callable[[], str] | None = None
    _on_final_transcript_suppressed: Callable[[object], None] | None = None
    _wrap_diagnostic_audio_source: Callable[[object, str], object] | None = None
    _ensure_peer_local_stt_ready: Callable[[], Awaitable[bool]] | None = None
    _sync_effective_hub_flags: Callable[[object], None] | None = None
    _peer_runtime_should_be_active: Callable[[object], bool] | None = None
    _settings_provider: Callable[[], object | None] | None = None
    _hub_provider: Callable[[], object | None] | None = None

    def _emit_log_basic(self, message: str) -> None:
        if self._log_basic is not None:
            self._log_basic(message)

    def _emit_log_detailed(self, message: str) -> None:
        if self._log_detailed is not None:
            self._log_detailed(message)

    def build_peer_runtime_config(self, settings: AppSettings) -> PeerRuntimeConfig:
        """Delegate to module-level pure function."""
        return build_peer_runtime_config(settings)

    def enqueue_peer_translation_disclosure(self) -> None:
        hub = self._hub_provider() if self._hub_provider else None
        if hub is None:
            return
        enqueue_disclosure = getattr(hub, "enqueue_peer_translation_disclosure", None)
        if callable(enqueue_disclosure):
            from puripuly_heart.domain.i18n import t
            enqueue_disclosure(t("peer_translation.disclosure"))

    def create_peer_stt_provider_from_runtime_config(
        self,
        config: PeerRuntimeConfig,
        on_terminal_failure: object,
    ) -> ManagedSTTProvider | None:
        from puripuly_heart.app.wiring import create_peer_stt_backend, create_secret_store

        settings = self._settings_provider() if self._settings_provider else None
        if settings is None:
            logger.warning("[PeerSTT] Settings unavailable — cannot create peer STT provider")
            return None
        if settings.provider.peer_stt == STTProviderName.NONE:
            return None  # type: ignore[return-value]
        secrets = create_secret_store(config_path=self._config_path)
        peer_backend = create_peer_stt_backend(
            settings,
            secrets=secrets,
            diagnostics_enabled=self._detailed_audio_diag_enabled,
        )
        return ManagedSTTProvider(
            backend=peer_backend,
            sample_rate_hz=config.backend.sample_rate_hz,
            stt_provider_name=config.backend.provider,
            channel="peer",
            clock=self._clock,
            reset_deadline_s=STT_RESET_DEADLINE_S,
            drain_timeout_s=settings.stt.drain_timeout_s,
            bridging_ms=max(1, config.vad_pre_roll_ms),
            on_terminal_failure=on_terminal_failure,
            on_final_transcript_suppressed=self._on_final_transcript_suppressed,
            runtime_logging=self._runtime_logging,
            stt_input_fault_profile_provider=lambda: (
                self._debug_stt_fault_profile_provider()
                if self._debug_audio_fault_allowed and self._debug_audio_fault_allowed()
                else "none"
            ),
        )

    def create_peer_audio_source_from_runtime_config(self, config: PeerRuntimeConfig):
        raw_source = DesktopLoopbackAudioSource(device_name=config.output_device)
        self._emit_log_detailed(
            "[AudioDiag][Loopback][peer] "
            f"requested_device={config.output_device!r} "
            f"resolved_device_name={getattr(raw_source, 'resolved_device_name', None)!r} "
            f"resolved_device_index={getattr(raw_source, 'resolved_device_index', None)} "
            f"resolved_channels={getattr(raw_source, 'resolved_channels', None)} "
            f"actual_sample_rate_hz={getattr(raw_source, 'actual_sample_rate_hz', None)} "
            f"used_default_fallback={getattr(raw_source, 'used_default_fallback', None)}"
        )
        wrapped_source = self._wrap_diagnostic_audio_source(raw_source, "peer") if self._wrap_diagnostic_audio_source else raw_source
        return DesktopPeerPipeline(
            source=wrapped_source,
            target_sample_rate_hz=config.backend.sample_rate_hz,
            is_detailed_enabled=self._detailed_audio_diag_enabled,
            log_detailed=lambda message: self._emit_log_detailed(message),
        )

    def create_peer_vad_from_runtime_config(self, config: PeerRuntimeConfig, model_path: Path):
        return create_peer_vad_gating(
            engine=SileroVadOnnx(model_path=model_path),
            sample_rate_hz=config.backend.sample_rate_hz,
            ring_buffer_ms=config.vad_pre_roll_ms,
            speech_threshold=config.vad_threshold,
            hangover_ms=config.vad_hangover_ms,
            diagnostic_event_callback=lambda message: self._emit_log_detailed(message),
            diagnostics_enabled=self._detailed_audio_diag_enabled,
            diagnostic_label="peer",
        )

    async def run_peer_audio_vad_loop(self, **kwargs: object) -> None:
        from puripuly_heart.app.headless_mic import run_audio_vad_loop

        await run_audio_vad_loop(
            **kwargs,
            channel_label="peer",
            is_detailed_enabled=self._detailed_audio_diag_enabled,
            log_detailed=lambda message: self._emit_log_detailed(message),
        )

    async def refresh_peer_stt_runtime(self) -> None:
        settings = self._settings_provider() if self._settings_provider else None
        hub = self._hub_provider() if self._hub_provider else None
        if settings is None or hub is None or self._peer_runtime is None:
            return

        config = self.build_peer_runtime_config(settings)
        desired_active = self._peer_runtime_should_be_active(settings) if self._peer_runtime_should_be_active else False
        det = self._signature_detector
        if (
            config.runtime_signature == getattr(det, "last_peer_stt_runtime_signature", None)
            and desired_active == getattr(det, "last_peer_stt_desired_active", None)
        ):
            return
        self._emit_log_basic(
            f"[Settings] Peer STT provider replacement: "
            f"provider={config.backend.provider} "
            f"peer_stt_compute={settings.provider.peer_stt_compute} "
            f"desired_active={desired_active}"
        )
        if desired_active and self._ensure_peer_local_stt_ready is not None:
            if not await self._ensure_peer_local_stt_ready():
                desired_active = False
        # Allow GPU driver to release resources before starting new peer backend
        if desired_active and settings.provider.peer_stt_compute == "gpu":
            await asyncio.sleep(0.5)
        await self._peer_runtime.apply_policy(config=config, desired_active=desired_active)
        if desired_active and self._peer_runtime is not None:
            with contextlib.suppress(Exception):
                await self._peer_runtime.warmup()
        det.last_peer_stt_runtime_signature = config.runtime_signature
        det.last_peer_stt_desired_active = desired_active
        if self._sync_effective_hub_flags is not None:
            self._sync_effective_hub_flags(settings)
        self._emit_log_basic("[Settings] Peer STT provider replacement completed")
