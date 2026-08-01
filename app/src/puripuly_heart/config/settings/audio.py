from __future__ import annotations

from dataclasses import dataclass

from puripuly_heart.config.audio_host_api import WINDOWS_WASAPI_COMPATIBILITY_HOST_API

from .constants import DEFAULT_DESKTOP_AUDIO_VAD_HANGOVER_MS, STT_INTERNAL_SAMPLE_RATE_HZ


@dataclass(slots=True)
class AudioSettings:
    internal_sample_rate_hz: int = STT_INTERNAL_SAMPLE_RATE_HZ
    internal_channels: int = 1
    ring_buffer_ms: int = 500
    input_host_api: str = WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    input_device: str = ""

    def validate(self) -> None:
        if self.internal_sample_rate_hz != STT_INTERNAL_SAMPLE_RATE_HZ:
            raise ValueError(f"internal_sample_rate_hz must be {STT_INTERNAL_SAMPLE_RATE_HZ}")
        if self.internal_channels != 1:
            raise ValueError("internal_channels must be 1 (mono)")
        if self.ring_buffer_ms <= 0:
            raise ValueError("ring_buffer_ms must be > 0")
        if self.input_host_api is None:
            raise ValueError("input_host_api must be a string")
        if self.input_device is None:
            raise ValueError("input_device must be a string")


@dataclass(slots=True)
class DesktopAudioSettings:
    output_device: str = ""
    vad_speech_threshold: float = 0.4
    vad_hangover_ms: int = DEFAULT_DESKTOP_AUDIO_VAD_HANGOVER_MS
    vad_pre_roll_ms: int = 500

    def validate(self) -> None:
        if self.output_device is None:
            raise ValueError("output_device must be a string")
        if not (0.0 <= self.vad_speech_threshold <= 1.0):
            raise ValueError("vad_speech_threshold must be in 0.0..1.0")
        if self.vad_hangover_ms < 0:
            raise ValueError("vad_hangover_ms must be >= 0")
        if self.vad_pre_roll_ms < 0:
            raise ValueError("vad_pre_roll_ms must be >= 0")
