"""STT audio diagnostics — extracted fault injection and metrics for ManagedSTTProvider.

Handles per-utterance diagnostic state: chunk count, sample count, RMS computation,
peak tracking, zero-crossing ratio, and fault injection.

AI-CONTEXT: This is a pure data processor — no state machine, no async, no sessions.
  controller.py calls process_input() on every audio chunk and emit_for_utterance() on speech-end.

AI-CONTEXT: _apply_fault() is the single fault injection point for STT audio pipeline.
  Only STT_INPUT_LOW_SNR_VAD_PASS is implemented. Other AudioFaultProfile values are no-ops.
  Fault is applied AFTER VAD gating (in _send_audio, after pre-roll) — this is intentional:
  fault should degrade STT input quality, not trigger false VAD detections.

AI-CONTEXT: _record() is gated on SessionLoggingMode.DETAILED — zero overhead in BASIC mode.
  If metrics seem missing, check runtime_logging.mode is DETAILED.

AI-CONTEXT: emit_for_utterance() suppresses all exceptions (contextlib.suppress) to avoid
  disrupting the audio pipeline. Silent failures here are by design — diagnostics are
  best-effort, not critical path.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable
from uuid import UUID

import numpy as np

from puripuly_heart.core.audio.diagnostics import AudioFaultProfile, normalize_audio_fault_profile
from puripuly_heart.core.runtime_logging import SessionLoggingMode, SessionRuntimeLoggingService
from puripuly_heart.domain.models import ChannelId

if TYPE_CHECKING:
    from puripuly_heart.core.stt.stt_log_sink import STTLogSink


@dataclass
class STTAudioDiagnostics:
    """Per-utterance audio diagnostics: fault injection, metrics recording, emission."""

    runtime_logging: SessionRuntimeLoggingService | None
    channel: ChannelId
    sample_rate_hz: int
    log_sink: STTLogSink
    stt_input_fault_profile_provider: Callable[[], AudioFaultProfile | str | None] | None = None

    _chunk_count: int = field(default=0, init=False)
    _sample_count: int = field(default=0, init=False)
    _sum_squares: float = field(default=0.0, init=False)
    _peak: float = field(default=0.0, init=False)
    _zero_count: int = field(default=0, init=False)
    _fault_logged_for_utterance: bool = field(default=False, init=False)

    def reset_for_utterance(self) -> None:
        """Reset all diagnostic state for a new utterance."""
        self._chunk_count = 0
        self._sample_count = 0
        self._sum_squares = 0.0
        self._peak = 0.0
        self._zero_count = 0
        self._fault_logged_for_utterance = False

    def current_fault_profile(self) -> AudioFaultProfile:
        """Get the current fault profile from the provider."""
        if self.stt_input_fault_profile_provider is None:
            return AudioFaultProfile.NONE
        with contextlib.suppress(Exception):
            return normalize_audio_fault_profile(self.stt_input_fault_profile_provider())
        return AudioFaultProfile.NONE

    def process_input(self, samples_f32: np.ndarray) -> np.ndarray:
        """Apply fault injection and record diagnostics. Returns (possibly transformed) samples."""
        samples_f32 = self._apply_fault(samples_f32)
        self._record(samples_f32)
        return samples_f32

    def _apply_fault(self, samples_f32: np.ndarray) -> np.ndarray:
        profile = self.current_fault_profile()
        if profile is not AudioFaultProfile.STT_INPUT_LOW_SNR_VAD_PASS:
            return samples_f32
        with contextlib.suppress(Exception):
            flat = np.arange(samples_f32.size, dtype=np.float32)
            noise = np.sin(flat * np.float32(12.9898)) * np.float32(0.003)
            transformed = (samples_f32 * np.float32(0.01)) + noise.astype(np.float32)
            if not self._fault_logged_for_utterance:
                self._fault_logged_for_utterance = True
                self.log_sink.audio_diag_detailed(
                    "[AudioDiag][STTFault][%s] profile=%s applies_after_vad=True",
                    self.channel,
                    profile.value,
                )
            return transformed.astype(np.float32)
        return samples_f32

    def _record(self, samples_f32: np.ndarray) -> None:
        """Record audio metrics for diagnostics (only when DETAILED logging mode)."""
        if (
            self.runtime_logging is None
            or self.runtime_logging.mode is not SessionLoggingMode.DETAILED
        ):
            return
        with contextlib.suppress(Exception):
            samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1)
            if samples.size == 0:
                return
            self._chunk_count += 1
            self._sample_count += int(samples.size)
            self._sum_squares += float(np.sum(np.square(samples)))
            self._peak = max(self._peak, float(np.max(np.abs(samples))))
            self._zero_count += int(np.count_nonzero(np.abs(samples) < 1e-6))

    def emit_for_utterance(self, utterance_id: UUID, *, finalize: bool) -> None:
        """Emit accumulated diagnostic metrics for an utterance."""
        if (
            self.runtime_logging is None
            or self.runtime_logging.mode is not SessionLoggingMode.DETAILED
        ):
            return
        with contextlib.suppress(Exception):
            if self._sample_count <= 0:
                return
            audio_ms = self._sample_count * 1000.0 / float(self.sample_rate_hz)
            rms = float(np.sqrt(self._sum_squares / self._sample_count))
            rms_db = -120.0 if rms <= 0.0 else round(float(20.0 * np.log10(max(rms, 1e-6))), 1)
            peak_db = (
                -120.0
                if self._peak <= 0.0
                else round(float(20.0 * np.log10(max(self._peak, 1e-6))), 1)
            )
            zero_ratio = self._zero_count / float(self._sample_count)
            self.log_sink.audio_diag_detailed(
                "[AudioDiag][STTInput][%s] utterance_id=%s chunk_count=%s audio_ms=%.1f "
                "rms_db=%.1f peak_db=%.1f zero_ratio=%.3f finalize=%s",
                self.channel,
                str(utterance_id)[:8],
                self._chunk_count,
                audio_ms,
                rms_db,
                peak_db,
                zero_ratio,
                finalize,
            )
