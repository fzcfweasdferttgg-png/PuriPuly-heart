from __future__ import annotations

from dataclasses import dataclass


class STTError(Exception):
    """Base exception for STT subsystem failures."""


@dataclass(frozen=True, slots=True)
class STTBackendTranscriptEvent:
    text: str
    is_final: bool
