"""Provider management service — handles LLM/STT provider lifecycle.

Manages provider rebuild and signature comparison.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.core.stt.controller import ManagedSTTProvider

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings
    from puripuly_heart.ports.clock import Clock
    from puripuly_heart.core.pipeline.pipeline import Pipeline

logger = logging.getLogger(__name__)

STT_RESET_DEADLINE_S = 300.0


@dataclass
class ProviderManager:
    """Manages LLM/STT provider lifecycle.

    Responsibilities:
    - Rebuild LLM provider (primary + fallback)
    - Rebuild STT provider (ManagedSTTProvider wrapping STTBackend)

    Does NOT know about Flet/UI. Uses callbacks for UI side-effects.
    """

    hub: Pipeline
    settings: AppSettings
    config_path: Path
    clock: Clock
    runtime_logging: SessionRuntimeLoggingService

    # Callbacks for UI side-effects
    on_llm_rebuilt: Callable[[object | None], None] | None = None
    on_stt_rebuilt: Callable[[object | None], None] | None = None
    on_error: Callable[[str], None] | None = None

    # Debug state (passed from controller)
    debug_stt_fault_profile_provider: Callable[[], str] | None = None
    detailed_audio_diag_enabled_provider: Callable[[], bool] | None = None

    # STT callbacks (from controller mixins)
    on_terminal_failure: Callable[[Exception], None] | None = None
    on_final_transcript_suppressed: Callable[..., None] | None = None

    # Dependency-injected factories (to avoid core/ → app/ import)
    create_secret_store: Callable[..., object] = None  # type: ignore[assignment]
    create_llm_provider: Callable[..., object] = None  # type: ignore[assignment]
    create_fallback_llm_provider: Callable[..., object] = None  # type: ignore[assignment]
    create_stt_backend: Callable[..., object] = None  # type: ignore[assignment]

    async def rebuild_llm_provider(self) -> None:
        """Rebuild LLM provider without tearing down the pipeline."""
        if self.hub is None or self.settings is None:
            return

        await self.hub.set_llm(None)

        llm = None
        llm_error: Exception | None = None
        secrets = None
        try:
            secrets = self.create_secret_store(config_path=self.config_path)
            llm = self.create_llm_provider(
                self.settings,
                secrets=secrets,
                runtime_logging=self.runtime_logging,
            )
        except Exception as exc:
            llm_error = exc

        if self.hub is None:
            return

        # Rebuild fallback
        await self.hub.set_fallback_llm(None)
        fallback_llm = None
        if secrets is not None:
            try:
                fallback_llm = self.create_fallback_llm_provider(
                    self.settings,
                    secrets=secrets,
                    runtime_logging=self.runtime_logging,
                )
            except Exception as exc:
                logger.warning("[LLM] Failed to create fallback provider: %s", exc)
        else:
            logger.warning("[LLM] Skipping fallback — secret store unavailable")

        # Set final providers and sync TranslationService
        await self.hub.set_llm(llm)
        if fallback_llm is not None:
            await self.hub.set_fallback_llm(fallback_llm)

        # Callback for UI side-effects (translation disable, needs_key, etc.)
        if self.on_llm_rebuilt is not None:
            self.on_llm_rebuilt(llm)

        if llm is None:
            message = "LLM provider not available"
            if llm_error is not None:
                message = f"{message}: {llm_error}"
            if self.on_error is not None:
                self.on_error(message)
            return

        logger.info("[Settings] LLM provider rebuilt successfully")

    async def rebuild_stt_provider(self) -> None:
        """Rebuild STT provider for later enable."""
        if self.hub is None or self.settings is None:
            return

        stt = None
        stt_error: Exception | None = None
        try:
            secrets = self.create_secret_store(config_path=self.config_path)
            diag_enabled = (
                self.detailed_audio_diag_enabled_provider()
                if self.detailed_audio_diag_enabled_provider is not None
                else False
            )
            backend = self.create_stt_backend(
                self.settings,
                secrets=secrets,
                diagnostics_enabled=diag_enabled,
            )
            fault_profile = (
                self.debug_stt_fault_profile_provider()
                if self.debug_stt_fault_profile_provider is not None
                else "none"
            )
            stt = ManagedSTTProvider(
                backend=backend,
                sample_rate_hz=self.settings.audio.internal_sample_rate_hz,
                stt_provider_name=self.settings.provider.stt,
                clock=self.clock,
                reset_deadline_s=STT_RESET_DEADLINE_S,
                drain_timeout_s=self.settings.stt.drain_timeout_s,
                bridging_ms=self.settings.audio.ring_buffer_ms,
                on_terminal_failure=self.on_terminal_failure,
                on_final_transcript_suppressed=self.on_final_transcript_suppressed,
                runtime_logging=self.runtime_logging,
                stt_input_fault_profile_provider=lambda: fault_profile,
            )
        except Exception as exc:
            stt_error = exc

        await self.hub.replace_stt_provider(stt)

        # Callback for UI side-effects
        if self.on_stt_rebuilt is not None:
            self.on_stt_rebuilt(stt)

        if stt is None:
            assert stt_error is not None
            if self.on_error is not None:
                self.on_error(f"STT backend not available: {stt_error}")
            return

        logger.info("[Settings] STT provider replacement completed successfully")
