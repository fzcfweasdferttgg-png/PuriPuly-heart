"""Streaming audio resampler — soxr-based with noop fast path.

MonoFirstStreamingResampler converts multi-channel audio at any sample rate
to mono at output_sample_rate_hz (default 16kHz for STT).

Key design:
- **MonoFirst**: reshapes to mono BEFORE resampling (mixdown_to_mono_f32).
- **Noop path**: when input and output are both 16kHz, skips soxr entirely
  (just does mono mixdown).  Avoids soxr import overhead.
- **Lazy soxr import**: soxr is only imported when actually needed (not 16kHz).

Called by audio/desktop_pipeline.py and app/headless_mic.py for peer STT resampling.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from puripuly_heart.core.audio.format import mixdown_to_mono_f32, reshape_audio_samples_f32

# NOOP threshold: if input == output == 16kHz, skip soxr entirely.
# 16kHz is STT standard; changing this breaks the optimization.
# Not configurable: this is a compile-time optimization constant.
NOOP_SAMPLE_RATE_HZ = 16000


def _import_soxr() -> Any:
    return importlib.import_module("soxr")


@dataclass(slots=True)
class MonoFirstStreamingResampler:
    input_sample_rate_hz: int
    output_sample_rate_hz: int = NOOP_SAMPLE_RATE_HZ
    input_channels: int = 1
    _stream: Any | None = field(init=False, default=None, repr=False)
    _flushed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        if self.input_sample_rate_hz <= 0 or self.output_sample_rate_hz <= 0:
            raise ValueError("sample rates must be > 0")
        if self.input_channels <= 0:
            raise ValueError("input_channels must be > 0")
        if self._uses_noop_path:
            return
        self._stream = _import_soxr().ResampleStream(
            self.input_sample_rate_hz,
            self.output_sample_rate_hz,
            1,
            dtype="float32",
            quality="MQ",
        )

    @property
    def _uses_noop_path(self) -> bool:
        return (
            self.input_sample_rate_hz == NOOP_SAMPLE_RATE_HZ
            and self.output_sample_rate_hz == NOOP_SAMPLE_RATE_HZ
        )

    def resample_chunk(self, samples: np.ndarray, *, last: bool = False) -> np.ndarray:
        # last=True signals end-of-stream to soxr — triggers internal buffer flush.
        # After last=True, calling resample_chunk() again raises RuntimeError.
        # Normal usage: last=False for all frames, then flush() calls with last=True.
        if self._flushed:
            raise RuntimeError("stream has already been flushed")

        mono = self._prepare_mono_chunk(samples)
        if self._uses_noop_path:
            output = mono
        else:
            if self._stream is None:
                raise RuntimeError("soxr stream is unavailable")
            output = np.asarray(self._stream.resample_chunk(mono, last=last), dtype=np.float32)

        if last:
            self._flushed = True
        return output

    def flush(self) -> np.ndarray:
        # Calls resample_chunk(empty, last=True) to drain soxr internal buffer.
        # MUST be called after all frames are consumed, otherwise last samples are lost.
        # After flush(), resample_chunk() raises RuntimeError (guarded by _flushed).
        return self.resample_chunk(np.empty((0,), dtype=np.float32), last=True)

    def _prepare_mono_chunk(self, samples: np.ndarray) -> np.ndarray:
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size == 0:
            return np.empty((0,), dtype=np.float32)

        reshaped = reshape_audio_samples_f32(samples, channels=self.input_channels)
        if reshaped.ndim == 2 and reshaped.shape[1] != self.input_channels:
            raise ValueError("2D samples channel count must match input_channels")
        return mixdown_to_mono_f32(reshaped)


__all__ = ["MonoFirstStreamingResampler"]
