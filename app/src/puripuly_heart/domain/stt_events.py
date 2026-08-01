from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class STTBackendTranscriptEvent:
    text: str
    is_final: bool
