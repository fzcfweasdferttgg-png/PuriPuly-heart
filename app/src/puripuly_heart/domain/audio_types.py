from __future__ import annotations

from dataclasses import dataclass

from puripuly_heart.domain.samples import Samples


@dataclass(frozen=True, slots=True)
class AudioFrameF32:
    samples: Samples
    sample_rate_hz: int
    channels: int = 1
