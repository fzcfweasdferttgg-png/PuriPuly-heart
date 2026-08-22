from __future__ import annotations

from dataclasses import dataclass

from puripuly_heart.config.vad_defaults import DEFAULT_LOW_LATENCY_VAD_HANGOVER_MS


@dataclass(slots=True)
class STTSettings:
    drain_timeout_s: float = 2.0
    vad_speech_threshold: float = 0.5
    low_latency_mode: bool = True
    low_latency_vad_hangover_ms: int = DEFAULT_LOW_LATENCY_VAD_HANGOVER_MS
    low_latency_spec_retry_max: int = 10

    def validate(self) -> None:
        if self.drain_timeout_s <= 0:
            raise ValueError("drain_timeout_s must be > 0")
        if not (0.0 <= self.vad_speech_threshold <= 1.0):
            raise ValueError("vad_speech_threshold must be in 0.0..1.0")
        if self.low_latency_vad_hangover_ms < 0:
            raise ValueError("low_latency_vad_hangover_ms must be >= 0")
        if self.low_latency_spec_retry_max < 0:
            raise ValueError("low_latency_spec_retry_max must be >= 0")
