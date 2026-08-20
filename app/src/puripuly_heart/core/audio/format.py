"""PCM/float32 conversion utilities + re-export of AudioFrameF32.

AudioFrameF32 is defined in domain/audio_types.py and re-exported here
for convenience.  Conversion functions operate on numpy arrays directly.

Conversion functions:
- reshape_audio_samples_f32: interleaved 1D → 2D (frames, channels)
- mixdown_to_mono_f32: multi-channel → mono (mean)
- float32_to_pcm16le_bytes: float32 [-1,1] → PCM16 little-endian bytes
- pcm16le_bytes_to_float32: PCM16 bytes → float32 [-1,1]

Called by audio/source.py, audio/desktop_source.py, audio/diagnostics.py,
audio/streaming_resampler.py, audio/desktop_pipeline.py, vad/gating.py,
inference/subprocess_backend.py, stt/controller.py.

pcm16le_bytes_to_float32 re-exported from domain.audio_format.
"""

from __future__ import annotations

import numpy as np

from puripuly_heart.domain.audio_types import AudioFrameF32

__all__ = [
    "AudioFrameF32",
    "reshape_audio_samples_f32",
    "mixdown_to_mono_f32",
    "float32_to_pcm16le_bytes",
    "pcm16le_bytes_to_float32",
]


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
    # CLIP to [-1.0, 1.0] before int16 conversion — prevents overflow/wrap.
    # Values outside this range are clamped to ±32767, losing dynamic range.
    clipped = np.clip(samples, -1.0, 1.0)
    int16 = np.round(clipped * 32767.0).astype("<i2")
    return int16.tobytes()


# Re-export from domain — canonical location is domain.audio_format
from puripuly_heart.domain.audio_format import pcm16le_bytes_to_float32
