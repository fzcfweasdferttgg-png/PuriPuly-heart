from __future__ import annotations

import asyncio
import contextlib
import logging
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


class PeerRuntimeManagerMixin:
    def _build_peer_runtime_config(self, settings: AppSettings) -> PeerRuntimeConfig:
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

    def _enqueue_peer_translation_disclosure(self) -> None:
        hub = self.hub
        if hub is None:
            return
        enqueue_disclosure = getattr(hub, "enqueue_peer_translation_disclosure", None)
        if callable(enqueue_disclosure):
            from puripuly_heart.domain.i18n import t
            enqueue_disclosure(t("peer_translation.disclosure"))

    def _create_peer_stt_provider_from_runtime_config(
        self,
        config: PeerRuntimeConfig,
        on_terminal_failure,
    ) -> ManagedSTTProvider | None:
        from puripuly_heart.app.wiring import create_peer_stt_backend, create_secret_store

        assert self.settings is not None
        if self.settings.provider.peer_stt == STTProviderName.NONE:
            return None  # type: ignore[return-value]
        secrets = create_secret_store(config_path=self.config_path)
        peer_backend = create_peer_stt_backend(
            self.settings,
            secrets=secrets,
            diagnostics_enabled=self._detailed_audio_diag_enabled,
        )
        return ManagedSTTProvider(
            backend=peer_backend,
            sample_rate_hz=config.backend.sample_rate_hz,
            stt_provider_name=config.backend.provider,
            channel="peer",
            clock=self.clock,
            reset_deadline_s=STT_RESET_DEADLINE_S,
            drain_timeout_s=self.settings.stt.drain_timeout_s,
            bridging_ms=max(1, config.vad_pre_roll_ms),
            on_terminal_failure=on_terminal_failure,
            on_final_transcript_suppressed=self._on_final_transcript_suppressed,
            runtime_logging=self.runtime_logging,
            stt_input_fault_profile_provider=lambda: (
                self._debug_stt_fault_profile if self._debug_audio_fault_allowed() else "none"
            ),
        )

    def _create_peer_audio_source_from_runtime_config(self, config: PeerRuntimeConfig):
        raw_source = DesktopLoopbackAudioSource(device_name=config.output_device)
        self.log_detailed(
            "[AudioDiag][Loopback][peer] "
            f"requested_device={config.output_device!r} "
            f"resolved_device_name={getattr(raw_source, 'resolved_device_name', None)!r} "
            f"resolved_device_index={getattr(raw_source, 'resolved_device_index', None)} "
            f"resolved_channels={getattr(raw_source, 'resolved_channels', None)} "
            f"actual_sample_rate_hz={getattr(raw_source, 'actual_sample_rate_hz', None)} "
            f"used_default_fallback={getattr(raw_source, 'used_default_fallback', None)}"
        )
        wrapped_source = self._wrap_diagnostic_audio_source(raw_source, channel_label="peer")
        return DesktopPeerPipeline(
            source=wrapped_source,
            target_sample_rate_hz=config.backend.sample_rate_hz,
            is_detailed_enabled=self._detailed_audio_diag_enabled,
            log_detailed=lambda message: self.log_detailed(message),
        )

    def _create_peer_vad_from_runtime_config(self, config: PeerRuntimeConfig, model_path: Path):
        return create_peer_vad_gating(
            engine=SileroVadOnnx(model_path=model_path),
            sample_rate_hz=config.backend.sample_rate_hz,
            ring_buffer_ms=config.vad_pre_roll_ms,
            speech_threshold=config.vad_threshold,
            hangover_ms=config.vad_hangover_ms,
            diagnostic_event_callback=lambda message: self.log_detailed(message),
            diagnostics_enabled=self._detailed_audio_diag_enabled,
            diagnostic_label="peer",
        )

    async def _run_peer_audio_vad_loop(self, **kwargs: object) -> None:
        from puripuly_heart.app.headless_mic import run_audio_vad_loop

        await run_audio_vad_loop(
            **kwargs,
            channel_label="peer",
            is_detailed_enabled=self._detailed_audio_diag_enabled,
            log_detailed=lambda message: self.log_detailed(message),
        )

    async def _refresh_peer_stt_runtime(self) -> None:
        if self.settings is None or self.hub is None or self._peer_runtime is None:
            return

        config = self._build_peer_runtime_config(self.settings)
        desired_active = self._peer_runtime_should_be_active(self.settings)
        if (
            config.runtime_signature == self._signature_detector.last_peer_stt_runtime_signature
            and desired_active == self._signature_detector.last_peer_stt_desired_active
        ):
            return
        self.log_basic(
            f"[Settings] Peer STT provider replacement: "
            f"provider={config.backend.provider} "
            f"peer_stt_compute={self.settings.provider.peer_stt_compute} "
            f"desired_active={desired_active}"
        )
        if desired_active and not await self._ensure_peer_local_stt_ready():
            desired_active = False
        # Allow GPU driver to release resources before starting new peer backend
        if desired_active and self.settings.provider.peer_stt_compute == "gpu":
            await asyncio.sleep(0.5)
        await self._peer_runtime.apply_policy(config=config, desired_active=desired_active)
        if desired_active and self._peer_runtime is not None:
            with contextlib.suppress(Exception):
                await self._peer_runtime.warmup()
        self._signature_detector.last_peer_stt_runtime_signature = config.runtime_signature
        self._signature_detector.last_peer_stt_desired_active = desired_active
        self._sync_effective_hub_flags(self.settings)
        self.log_basic("[Settings] Peer STT provider replacement completed")
