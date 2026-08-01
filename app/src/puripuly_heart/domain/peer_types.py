from __future__ import annotations

from dataclasses import dataclass

from puripuly_heart.config.settings import STTProviderName


@dataclass(frozen=True, slots=True)
class ResolvedPeerSTTConfig:
    provider: STTProviderName
    source_language: str
    sample_rate_hz: int
    keyterms: tuple[str, ...]
