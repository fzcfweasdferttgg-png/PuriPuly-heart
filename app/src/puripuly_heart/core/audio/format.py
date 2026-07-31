from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class AudioFrameF32:
    samples: np.ndarray
    sample_rate_hz: int
    channels: int = 1


def reshape_audio_samples_f32(samples: np.ndarray, *, channels: int = 1) -> np.ndarray:
    if channels <= 0:
        raise ValueError("channels must be > 0")

    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim == 2:
        return samples
    if samples.ndim != 1:
        raise ValueError("samples must be 1D or 2D")
    if channels == 1:
        return samples
    if samples.size % channels != 0:
        raise ValueError("interleaved samples must divide evenly by channels")
    return samples.reshape((-1, channels))


def mixdown_to_mono_f32(samples: np.ndarray) -> np.ndarray:
    if samples.ndim == 1:
        mono = samples
    elif samples.ndim == 2:
        mono = samples.mean(axis=1)
    else:
        raise ValueError("samples must be 1D (mono) or 2D (frames, channels)")

    return np.asarray(mono, dtype=np.float32)


def float32_to_pcm16le_bytes(samples: np.ndarray) -> bytes:
    samples = np.asarray(samples, dtype=np.float32)
    clipped = np.clip(samples, -1.0, 1.0)
    int16 = np.round(clipped * 32767.0).astype("<i2")
    return int16.tobytes()


def pcm16le_bytes_to_float32(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype="<i2").astype(np.float32)
    return arr / 32768.0
