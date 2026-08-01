from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

import numpy as np


class VadEngine(Protocol):
    def speech_probability(self, samples: np.ndarray, *, sample_rate_hz: int) -> float: ...
    def reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SpeechStart:
    utterance_id: UUID
    pre_roll: np.ndarray
    chunk: np.ndarray


@dataclass(frozen=True, slots=True)
class SpeechChunk:
    utterance_id: UUID
    chunk: np.ndarray


@dataclass(frozen=True, slots=True)
class SpeechEnd:
    utterance_id: UUID
    trailing_silence_ms: int = 0
    reason: Literal["silence", "max_duration"] = "silence"


VadEvent = SpeechStart | SpeechChunk | SpeechEnd


class VadEventSink(Protocol):
    async def handle_vad_event(self, event: VadEvent) -> None: ...
