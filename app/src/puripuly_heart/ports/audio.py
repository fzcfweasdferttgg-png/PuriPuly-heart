from __future__ import annotations

from typing import AsyncIterator, Protocol

from puripuly_heart.domain.audio_types import AudioFrameF32
from puripuly_heart.domain.samples import Samples


class AudioSource(Protocol):
    async def frames(self) -> AsyncIterator[AudioFrameF32]: ...
    async def close(self) -> None: ...


__all__ = [
    "AudioFrameF32",
    "AudioSource",
]
