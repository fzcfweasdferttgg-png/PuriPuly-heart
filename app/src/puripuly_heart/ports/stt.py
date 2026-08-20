from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from puripuly_heart.domain.samples import Samples
from puripuly_heart.domain.stt_events import STTBackendTranscriptEvent
from puripuly_heart.domain.events import FinalTranscriptSuppressedNotification


class STTBackendSession(Protocol):
    async def send_audio(self, pcm16le: bytes) -> None: ...
    async def on_speech_end(
        self, *, trailing_silence_ms: int | None = None
    ) -> None: ...
    async def stop(self) -> None: ...
    async def close(self) -> None: ...
    async def events(self) -> AsyncIterator[STTBackendTranscriptEvent]: ...


@runtime_checkable
class STTBackendFloat32Session(Protocol):
    async def send_audio_f32(self, samples_f32: Samples) -> None: ...


class STTBackend(Protocol):
    async def open_session(self) -> STTBackendSession: ...


class SuppressionCallback(Protocol):
    """Typed callback for suppressed final transcript notifications."""
    async def __call__(
        self, notification: FinalTranscriptSuppressedNotification
    ) -> None: ...


__all__ = [
    "STTBackend",
    "STTBackendFloat32Session",
    "STTBackendSession",
    "SuppressionCallback",
    "STTBackendTranscriptEvent",
]
