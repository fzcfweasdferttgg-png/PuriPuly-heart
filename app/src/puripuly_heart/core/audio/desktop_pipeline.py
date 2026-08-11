"""Desktop peer audio pipeline — resample loopback to 16kHz mono for STT.

Wraps a desktop audio source (loopback from desktop_source.py), resamples
to target_sample_rate_hz (default 16kHz) mono using MonoFirstStreamingResampler.

Key behavior:
- Format change detection: raises if source format (rate/channels) changes
  mid-stream (shouldn't happen for loopback, but defensive).
- Resampler flush: yields remaining samples at end of stream.
- Diagnostic logging: periodic RMS/peak metrics when detailed mode enabled.

Called by ui/peer_runtime_manager.py, ui/controller.py, app/headless_mic.py.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import numpy as np

from puripuly_heart.core.audio.diagnostics import compute_audio_frame_metrics
from puripuly_heart.core.audio.format import AudioFrameF32
from puripuly_heart.core.audio.source import AudioSource
from puripuly_heart.core.audio.streaming_resampler import MonoFirstStreamingResampler

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DesktopPeerPipeline:
    source: AudioSource
    target_sample_rate_hz: int = 16000
    is_detailed_enabled: Callable[[], bool] | None = None
    log_detailed: Callable[[str], object] | None = None
    _logged_formats: set[tuple[int, int]] = field(default_factory=set, init=False, repr=False)
    _diag_accumulated_audio_ms: float = field(default=0.0, init=False, repr=False)

    async def frames(self) -> AsyncIterator[AudioFrameF32]:
        resampler: MonoFirstStreamingResampler | None = None
        source_format: tuple[int, int] | None = None

        async for frame in self.source.frames():
            format_key = (frame.sample_rate_hz, frame.channels)
            if format_key not in self._logged_formats:
                self._logged_formats.add(format_key)
                logger.info(
                    "Desktop peer audio format: source_rate=%sHz source_channels=%s -> target_rate=%sHz",
                    frame.sample_rate_hz,
                    frame.channels,
                    self.target_sample_rate_hz,
                )

            # Resampler created LAZILY on first frame — not in __post_init__.
            # This allows caller to detect actual device format before committing.
            frame_format = (frame.sample_rate_hz, frame.channels)
            if source_format is None:
                source_format = frame_format
                resampler = MonoFirstStreamingResampler(
                    input_sample_rate_hz=frame.sample_rate_hz,
                    output_sample_rate_hz=self.target_sample_rate_hz,
                    input_channels=frame.channels,
                )
            elif frame_format != source_format:
                raise ValueError(
                    "source audio format changed during streaming: "
                    f"expected {source_format[0]}Hz/{source_format[1]}ch, "
                    f"got {frame.sample_rate_hz}Hz/{frame.channels}ch"
                )

            assert resampler is not None
            normalized = resampler.resample_chunk(frame.samples)
            self._maybe_log_peer_diagnostics(
                source_rate=frame.sample_rate_hz,
                source_channels=frame.channels,
                normalized=normalized,
            )
            if normalized.size:
                yield self._build_output_frame(normalized.reshape(-1))

        if resampler is None:
            return

        tail = resampler.flush()
        if tail.size:
            yield self._build_output_frame(tail.reshape(-1))

    async def close(self) -> None:
        # Delegates to source.close() — no local resources to release.
        # Resampler has no cleanup; Python GC handles it.
        await self.source.close()

    def _maybe_log_peer_diagnostics(
        self,
        *,
        source_rate: int,
        source_channels: int,
        normalized: np.ndarray,
    ) -> None:
        if self.is_detailed_enabled is None or self.log_detailed is None:
            return
        detailed_enabled = False
        with contextlib.suppress(Exception):
            detailed_enabled = bool(self.is_detailed_enabled())
        if not detailed_enabled:
            return

        with contextlib.suppress(Exception):
            frame = AudioFrameF32(
                samples=normalized.reshape(-1),
                sample_rate_hz=self.target_sample_rate_hz,
                channels=1,
            )
            metrics = compute_audio_frame_metrics(frame)
            # Diagnostics log at ~1-second intervals (accumulated_audio_ms >= 1000).
            # Not real-time: log frequency depends on audio data arrival rate.
            self._diag_accumulated_audio_ms += metrics.audio_ms
            if self._diag_accumulated_audio_ms < 1000.0:
                return

            self._diag_accumulated_audio_ms = 0.0
            self.log_detailed(
                f"[AudioDiag][PeerPipeline] source_rate={source_rate} "
                f"source_channels={source_channels} target_rate={self.target_sample_rate_hz} "
                f"samples={metrics.samples} audio_ms={metrics.audio_ms:.1f} "
                f"rms_db={metrics.rms_db:.1f} peak_db={metrics.peak_db:.1f} "
                f"zero_ratio={metrics.zero_ratio:.3f}"
            )

    def _build_output_frame(self, samples: np.ndarray) -> AudioFrameF32:
        return AudioFrameF32(
            samples=samples,
            sample_rate_hz=self.target_sample_rate_hz,
            channels=1,
        )
