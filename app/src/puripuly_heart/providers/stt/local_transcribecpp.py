from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from puripuly_heart.core.audio.diagnostics import compute_audio_frame_metrics
from puripuly_heart.core.audio.format import AudioFrameF32, pcm16le_bytes_to_float32
from puripuly_heart.core.stt.backend import (
    STTBackend,
    STTBackendSession,
    STTBackendTranscriptEvent,
)

TRANSCRIBECPP_SAMPLE_RATE_HZ = 16000
logger = logging.getLogger(__name__)


def _default_backend() -> str:
    return "cpu" if os.environ.get("PURIPULY_MODE", "gpu").lower() == "cpu" else "vulkan"


def _default_gpu_device() -> int:
    env_device = os.environ.get("TRANSCRIBE_VULKAN_DEVICE", "")
    if env_device.isdigit() and int(env_device) > 0:
        logger.info("[STT][transcribecpp] TRANSCRIBE_VULKAN_DEVICE=%s", env_device)
        return int(env_device)
    return 0


class LocalTranscribecppLoadError(RuntimeError):
    """Raised when the transcribe.cpp model cannot be loaded."""


class LocalTranscribecppInferenceError(RuntimeError):
    """Raised when transcribe.cpp inference fails."""


def create_transcribecpp_recognizer(
    *,
    model_path: Path,
    backend: str | None = None,
    gpu_device: int = 0,
) -> object:
    if backend is None:
        backend = _default_backend()

    logger.info(
        "[STT][transcribecpp] Loading model: path=%s backend=%s device=%d",
        model_path, backend, gpu_device,
    )

    _app_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    _lib_path = _app_root / "bin" / ("transcribe.dll" if os.name == "nt" else "libtranscribe.so")
    if _lib_path.is_file():
        os.environ.setdefault("TRANSCRIBE_LIBRARY", str(_lib_path))

    from puripuly_heart.vendor.transcribe_cpp import Model

    model = Model(
        str(model_path),
        backend=backend,
        gpu_device=gpu_device,
    )
    logger.info(
        "[STT][transcribecpp] Model loaded: arch=%s backend=%s device=%s file=%s",
        model.arch, model.backend, model.device, model_path.name,
    )
    return model


@dataclass(slots=True)
class LocalTranscribecppSTTBackend(STTBackend):
    model_path: Path
    backend: str = field(default_factory=_default_backend)
    gpu_device: int = field(default_factory=_default_gpu_device)
    num_threads: int = 3
    language_hint: str | None = None
    stream_label: str | None = None
    diagnostics_enabled: Callable[[], bool] | None = None
    _model: object | None = field(init=False, default=None, repr=False)
    _session: object | None = field(init=False, default=None, repr=False)
    _load_lock: asyncio.Lock = field(init=False, repr=False)
    _decode_lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._load_lock = asyncio.Lock()
        self._decode_lock = asyncio.Lock()

    async def open_session(self) -> STTBackendSession:
        await self._ensure_model()
        return _LocalTranscribecppSession(backend=self)

    async def close(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            with contextlib.suppress(Exception):
                session.close()
        self._model = None
        logger.info("[STT][transcribecpp] Backend closed")

    async def _ensure_model(self) -> object:
        if self._model is not None:
            return self._model

        async with self._load_lock:
            if self._model is not None:
                return self._model
            self._model = await asyncio.to_thread(self._create_model)
            self._session = self._model.session(n_threads=self.num_threads)
            logger.info(
                "[STT][transcribecpp] Session created: n_threads=%d backend=%s device=%s",
                self.num_threads, self._model.backend, self._model.device,
            )
            return self._model

    def _create_model(self) -> object:
        try:
            return create_transcribecpp_recognizer(
                model_path=self.model_path,
                backend=self.backend,
                gpu_device=self.gpu_device,
            )
        except Exception as exc:
            logger.error(
                "[STT][transcribecpp] Model load FAILED: path=%s backend=%s device=%d error=%s",
                self.model_path, self.backend, self.gpu_device, exc,
            )
            raise LocalTranscribecppLoadError(str(exc)) from exc

    async def decode_pcm16le(self, pcm16le: bytes) -> str:
        return await self.decode_f32(pcm16le_bytes_to_float32(pcm16le))

    async def decode_f32(self, samples_f32: np.ndarray) -> str:
        model = await self._ensure_model()
        async with self._decode_lock:
            try:
                return await asyncio.to_thread(
                    self._decode_f32_sync, model, samples_f32,
                )
            except Exception as exc:
                logger.error("[STT][transcribecpp] Decode FAILED: %s", exc)
                raise LocalTranscribecppInferenceError(str(exc)) from exc

    def _decode_f32_sync(self, model: object, samples_f32: np.ndarray) -> str:
        samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1).copy()
        np.clip(samples, -1.0, 1.0, out=samples)

        result = self._session.run(
            samples,
            language=self.language_hint,
        )
        return result.text.strip()


@dataclass(slots=True)
class _LocalTranscribecppSession(STTBackendSession):
    backend: LocalTranscribecppSTTBackend
    _buffer_f32: list[np.ndarray] = field(init=False, repr=False)
    _events: asyncio.Queue[STTBackendTranscriptEvent | BaseException | None] = field(
        init=False, repr=False,
    )
    _closed: bool = field(init=False, default=False)
    _closed_event_enqueued: bool = field(init=False, default=False)
    _utterances: int = field(init=False, default=0)
    _total_audio_ms: float = field(init=False, default=0.0)
    _total_inference_ms: float = field(init=False, default=0.0)
    _total_rtf: float = field(init=False, default=0.0)
    _summary_logged: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._buffer_f32 = []
        self._events = asyncio.Queue()

    async def send_audio(self, pcm16le: bytes) -> None:
        if self._closed:
            return
        await self.send_audio_f32(pcm16le_bytes_to_float32(pcm16le))

    async def send_audio_f32(self, samples_f32: np.ndarray) -> None:
        if self._closed:
            return
        samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return
        self._buffer_f32.append(samples.copy())

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms
        if self._closed or not self._buffer_f32:
            return

        samples_f32 = np.concatenate(self._buffer_f32)
        self._buffer_f32.clear()
        audio_ms = (
            samples_f32.size * 1000.0 / float(TRANSCRIBECPP_SAMPLE_RATE_HZ)
            if samples_f32.size > 0
            else 0.0
        )

        try:
            started_at = time.perf_counter()
            text = await self.backend.decode_f32(samples_f32)
            inference_ms = (time.perf_counter() - started_at) * 1000.0
        except Exception as exc:
            await self._events.put(exc)
            return

        rtf = inference_ms / audio_ms if audio_ms > 0 else 0.0
        self._utterances += 1
        self._total_audio_ms += audio_ms
        self._total_inference_ms += inference_ms
        self._total_rtf += rtf

        if text:
            logger.info(
                "[STT][transcribecpp] Transcript: '%s' (final, audio_ms=%.1f, inference_ms=%.1f, rtf=%.3f)",
                text, audio_ms, inference_ms, rtf,
            )
            await self._events.put(STTBackendTranscriptEvent(text=text, is_final=True))

    async def stop(self) -> None:
        self._log_summary_once()
        await self.close()

    async def close(self) -> None:
        self._log_summary_once()
        self._closed = True
        self._buffer_f32.clear()
        if self._closed_event_enqueued:
            return
        self._closed_event_enqueued = True
        await self._events.put(None)

    async def events(self):
        while True:
            event = await self._events.get()
            if event is None:
                break
            if isinstance(event, BaseException):
                raise event
            yield event

    def _log_summary_once(self) -> None:
        if self._summary_logged or self._utterances == 0:
            return
        self._summary_logged = True
        weighted_total_rtf = (
            self._total_inference_ms / self._total_audio_ms
            if self._total_audio_ms > 0
            else 0.0
        )
        mean_rtf = self._total_rtf / self._utterances if self._utterances > 0 else 0.0
        logger.info(
            "[STT][transcribecpp] Session summary: utterances=%d total_audio_ms=%.1f total_inference_ms=%.1f weighted_total_rtf=%.3f mean_rtf=%.3f",
            self._utterances, self._total_audio_ms, self._total_inference_ms,
            weighted_total_rtf, mean_rtf,
        )
