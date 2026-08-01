from __future__ import annotations

from typing import Protocol

from puripuly_heart.domain.samples import Samples
from puripuly_heart.domain.vad_events import SpeechChunk, SpeechEnd, SpeechStart, VadEvent


class VadEngine(Protocol):
    def speech_probability(self, samples: Samples, *, sample_rate_hz: int) -> float: ...
    def reset(self) -> None: ...


class VadEventSink(Protocol):
    async def handle_vad_event(self, event: VadEvent) -> None: ...


__all__ = [
    "SpeechChunk",
    "SpeechEnd",
    "SpeechStart",
    "VadEngine",
    "VadEvent",
    "VadEventSink",
]
