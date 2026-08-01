from __future__ import annotations

from dataclasses import dataclass, field

from puripuly_heart.config.vad_defaults import DEFAULT_LOW_LATENCY_VAD_HANGOVER_MS

from .constants import DEFAULT_CUSTOM_VOCAB_TERMS, MAX_CUSTOM_VOCAB_TERMS


def _default_custom_terms() -> dict[str, list[str]]:
    return {language: list(terms) for language, terms in DEFAULT_CUSTOM_VOCAB_TERMS.items()}


@dataclass(slots=True)
class STTSettings:
    drain_timeout_s: float = 2.0
    vad_speech_threshold: float = 0.5
    low_latency_mode: bool = True
    low_latency_vad_hangover_ms: int = DEFAULT_LOW_LATENCY_VAD_HANGOVER_MS
    low_latency_merge_gap_ms: int = 600
    low_latency_spec_retry_max: int = 10
    custom_vocabulary_enabled: bool = True
    custom_terms: dict[str, list[str]] = field(default_factory=_default_custom_terms)

    def validate(self) -> None:
        if self.drain_timeout_s <= 0:
            raise ValueError("drain_timeout_s must be > 0")
        if not (0.0 <= self.vad_speech_threshold <= 1.0):
            raise ValueError("vad_speech_threshold must be in 0.0..1.0")
        if self.low_latency_vad_hangover_ms < 0:
            raise ValueError("low_latency_vad_hangover_ms must be >= 0")
        if self.low_latency_merge_gap_ms < 0:
            raise ValueError("low_latency_merge_gap_ms must be >= 0")
        if self.low_latency_spec_retry_max < 0:
            raise ValueError("low_latency_spec_retry_max must be >= 0")
        if not isinstance(self.custom_vocabulary_enabled, bool):
            raise ValueError("custom_vocabulary_enabled must be a bool")
        if not isinstance(self.custom_terms, dict):
            raise ValueError("custom_terms must be a dict[str, list[str]]")
        for language, terms in self.custom_terms.items():
            if not isinstance(language, str):
                raise ValueError("custom_terms keys must be strings")
            if not isinstance(terms, list):
                raise ValueError("custom_terms values must be lists of strings")
            for term in terms:
                if not isinstance(term, str):
                    raise ValueError("custom_terms values must be lists of strings")
