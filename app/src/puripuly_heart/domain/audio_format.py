"""Low-level PCM/float32 conversion — pure functions, no side effects.

Domain layer: no dependencies on core, adapters, or ports.
"""
from __future__ import annotations

import numpy as np


def pcm16le_bytes_to_float32(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype="<i2").astype(np.float32)
    # Divide by 32768.0 (not 32767.0) — symmetric with int16 range [-32768, 32767].
    # Result: max positive = 0.99997, max negative = -1.0.
    # Not perfectly symmetric, but matches standard audio convention.
    return arr / 32768.0
