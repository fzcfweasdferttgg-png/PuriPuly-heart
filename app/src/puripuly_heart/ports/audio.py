from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol

import numpy as np


@dataclass(frozen=True, slots=True)
class AudioFrameF32:
    samples: np.ndarray
    sample_rate_hz: int
    channels: int = 1


class AudioSource(Protocol):
    async def frames(self) -> AsyncIterator[AudioFrameF32]: ...
    async def close(self) -> None: ...
