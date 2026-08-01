from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from puripuly_heart.domain.samples import Samples


@dataclass(frozen=True, slots=True)
class SpeechStart:
    utterance_id: UUID
    pre_roll: Samples
    chunk: Samples


@dataclass(frozen=True, slots=True)
class SpeechChunk:
    utterance_id: UUID
    chunk: Samples


@dataclass(frozen=True, slots=True)
class SpeechEnd:
    utterance_id: UUID
    trailing_silence_ms: int = 0
    reason: Literal["silence", "max_duration"] = "silence"


VadEvent = SpeechStart | SpeechChunk | SpeechEnd
