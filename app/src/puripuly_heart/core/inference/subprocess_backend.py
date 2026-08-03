from __future__ import annotations

import asyncio
import base64
import contextlib
import ctypes
import ctypes.wintypes
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import numpy as np

from puripuly_heart.core.audio.format import pcm16le_bytes_to_float32
from puripuly_heart.core.stt.backend import (
    STTBackend,
    STTBackendSession,
    STTBackendTranscriptEvent,
)

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000


def _assign_to_job(pid: int, job_handle: int | None) -> None:
    """Assign a process to a Job Object by PID."""
    if job_handle is None:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        proc_handle = kernel32.OpenProcess(0x1F0FFF, False, pid)  # PROCESS_ALL_ACCESS
        if not proc_handle:
            logger.warning("[JobObject] OpenProcess failed for pid=%d", pid)
            return
        result = kernel32.AssignProcessToJobObject(job_handle, proc_handle)
        kernel32.CloseHandle(proc_handle)
        if result:
            logger.info("[JobObject] Assigned pid=%d to job", pid)
        else:
            logger.warning("[JobObject] AssignProcessToJobObject failed for pid=%d", pid)
    except Exception as exc:
        logger.warning("[JobObject] Failed to assign pid=%d: %s", pid, exc)


class SubprocessSTTError(RuntimeError):
    """Raised when the inference worker subprocess fails."""


@dataclass(slots=True)
class SubprocessSTTBackend(STTBackend):
    provider: str
    model_dir: Path
    provider_type: str
    device: int
    num_threads: int = 3
    feature_dim: int = 128
    language_hint: str | None = None
    hotwords: tuple[str, ...] = ()
    job_handle: int | None = None

    _proc: subprocess.Popen | None = field(init=False, default=None, repr=False)
    _init_lock: asyncio.Lock = field(init=False, repr=False)
    _io_lock: asyncio.Lock = field(init=False, repr=False)
    _stderr_drain_task: asyncio.Task[None] | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self._init_lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()

    async def open_session(self) -> STTBackendSession:
        await self._ensure_worker()
        return SubprocessSTTSession(backend=self)

    async def close(self) -> None:
        await self._kill_worker()

    async def _ensure_worker(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return

        async with self._init_lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            await self._kill_worker()
            await self._start_worker()

    async def _start_worker(self) -> None:
        worker_script = str(
            Path(__file__).resolve().parent.parent.parent / "adapters" / "inference" / "worker.py"
        )

        python_exe = sys.executable

        logger.info(
            "[InferenceWorker] Starting: provider=%s model_dir=%s provider_type=%s device=%d",
            self.provider, self.model_dir, self.provider_type, self.device,
        )

        try:
            proc = await asyncio.to_thread(self._spawn_worker, python_exe, worker_script, self.job_handle)
        except Exception as exc:
            raise SubprocessSTTError(f"failed to start inference worker: {exc}") from exc

        self._proc = proc
        self._stderr_drain_task = asyncio.create_task(self._drain_stderr(proc))

        try:
            ready = await self._read_message(timeout=30.0)
        except Exception as exc:
            await self._kill_worker()
            raise SubprocessSTTError(f"worker did not send ready: {exc}") from exc

        if ready.get("status") != "ready":
            await self._kill_worker()
            raise SubprocessSTTError(f"worker unexpected ready response: {ready}")

        logger.info("[InferenceWorker] Worker ready, sending init...")

        init_cmd = {
            "cmd": "init",
            "provider": self.provider,
            "model_dir": str(self.model_dir),
            "provider_type": self.provider_type,
            "device": self.device,
            "num_threads": self.num_threads,
            "feature_dim": self.feature_dim,
        }
        if self.language_hint is not None:
            init_cmd["language_hint"] = self.language_hint
        if self.hotwords:
            init_cmd["hotwords"] = list(self.hotwords)

        await self._write_command(init_cmd)

        try:
            resp = await self._read_message(timeout=120.0)
        except Exception as exc:
            await self._kill_worker()
            raise SubprocessSTTError(f"worker did not respond to init: {exc}") from exc

        if resp.get("status") == "error":
            await self._kill_worker()
            raise SubprocessSTTError(f"worker init failed: {resp.get('error', 'unknown')}")

        if resp.get("status") != "ready":
            await self._kill_worker()
            raise SubprocessSTTError(f"worker unexpected init response: {resp}")

        logger.info("[InferenceWorker] Worker initialized successfully")

    @staticmethod
    def _spawn_worker(python_exe: str, worker_script: str, job_handle: int | None = None) -> subprocess.Popen:
        proc = subprocess.Popen(
            [python_exe, "-u", worker_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW,
            cwd=str(Path(worker_script).resolve().parent.parent.parent.parent),
        )
        _assign_to_job(proc.pid, job_handle)
        return proc

    async def _write_command(self, payload: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise SubprocessSTTError("worker not running")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        try:
            await asyncio.to_thread(self._write_stdin, data)
        except (BrokenPipeError, OSError) as exc:
            await self._kill_worker()
            raise SubprocessSTTError(f"worker stdin broken: {exc}") from exc

    def _write_stdin(self, data: bytes) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(data)
        self._proc.stdin.flush()

    async def _read_message(self, timeout: float = 30.0) -> dict:
        if self._proc is None or self._proc.stdout is None:
            raise SubprocessSTTError("worker not running")
        try:
            line = await asyncio.wait_for(
                asyncio.to_thread(self._read_stdout_line),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await self._kill_worker()
            raise SubprocessSTTError(f"worker read timed out after {timeout}s")
        except (BrokenPipeError, OSError) as exc:
            await self._kill_worker()
            raise SubprocessSTTError(f"worker stdout broken: {exc}") from exc
        if not line:
            stderr_tail = await self._read_stderr_tail()
            await self._kill_worker()
            raise SubprocessSTTError(f"worker exited unexpectedly. stderr: {stderr_tail}")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise SubprocessSTTError(f"invalid JSON from worker: {exc}") from exc

    def _read_stdout_line(self) -> str:
        assert self._proc is not None and self._proc.stdout is not None
        line = self._proc.stdout.readline()
        return line.decode("utf-8").strip() if line else ""

    async def _read_stderr_tail(self) -> str:
        if self._proc is None or self._proc.stderr is None:
            return "(no stderr)"
        try:
            remaining = await asyncio.to_thread(self._proc.stderr.read)
            return remaining.decode("utf-8", errors="replace")[-2000:] if remaining else "(empty)"
        except Exception:
            return "(could not read stderr)"

    async def _kill_worker(self) -> None:
        proc = self._proc
        self._proc = None
        if self._stderr_drain_task is not None:
            self._stderr_drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_drain_task
            self._stderr_drain_task = None
        if proc is None:
            return
        logger.info(
            "[InferenceWorker] Killing worker: pid=%d provider=%s provider_type=%s",
            proc.pid, self.provider, self.provider_type,
        )
        try:
            await asyncio.to_thread(self._terminate_process, proc)
        except Exception:
            pass

    @staticmethod
    def _terminate_process(proc: subprocess.Popen) -> None:
        try:
            proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "[InferenceWorker] Worker did not exit after kill, using taskkill: pid=%d",
                    proc.pid,
                )
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(proc.pid)],
                        timeout=5,
                        capture_output=True,
                    )
                except Exception:
                    pass

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @staticmethod
    async def _drain_stderr(proc: subprocess.Popen) -> None:
        try:
            while proc.poll() is None:
                line = await asyncio.to_thread(proc.stderr.readline)
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("[InferenceWorker][stderr] %s", text)
        except Exception:
            pass


@dataclass(slots=True)
class SubprocessSTTSession(STTBackendSession):
    backend: SubprocessSTTBackend
    _buffer: list[np.ndarray] = field(init=False, repr=False)
    _events: asyncio.Queue[STTBackendTranscriptEvent | BaseException | None] = field(
        init=False, repr=False,
    )
    _decode_queue: asyncio.Queue[np.ndarray | None] = field(init=False, repr=False)
    _decode_task: asyncio.Task[None] | None = field(init=False, default=None, repr=False)
    _closed: bool = field(init=False, default=False)
    _closed_event_enqueued: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._buffer = []
        self._events = asyncio.Queue()
        self._decode_queue = asyncio.Queue(maxsize=10)
        self._decode_task = asyncio.create_task(self._decode_worker())

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
        self._buffer.append(samples.copy())

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms
        if self._closed or not self._buffer:
            return

        samples = np.concatenate(self._buffer)
        self._buffer.clear()

        try:
            self._decode_queue.put_nowait(samples)
        except asyncio.QueueFull:
            logger.warning("[InferenceWorker] Decode queue full, dropping utterance")
            await self._events.put(SubprocessSTTError("decode queue full"))

    async def _decode_worker(self) -> None:
        try:
            while True:
                samples = await self._decode_queue.get()
                if samples is None:
                    break
                await self._run_decode(samples)
        except asyncio.CancelledError:
            return

    async def _run_decode(self, samples: np.ndarray) -> None:
        if not self.backend._is_alive():
            await self._events.put(SubprocessSTTError("worker not running"))
            return

        audio_b64 = base64.b64encode(samples.tobytes()).decode("ascii")
        audio_ms = len(samples) * 1000.0 / 16000

        decode_cmd = {
            "cmd": "decode",
            "audio_b64": audio_b64,
            "sample_rate_hz": 16000,
            "is_final": True,
        }

        logger.info("[InferenceWorker] Decode start: audio_ms=%.0f", audio_ms)
        t0 = time.monotonic()
        try:
            async with self.backend._io_lock:
                await self.backend._write_command(decode_cmd)
                resp = await self.backend._read_message()
        except SubprocessSTTError as exc:
            await self._events.put(exc)
            return
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info("[InferenceWorker] Decode done: elapsed_ms=%.0f", elapsed_ms)

        status = resp.get("status")
        if status == "transcript":
            text = str(resp.get("text", ""))
            is_final = bool(resp.get("is_final", True))
            if text:
                logger.info("[InferenceWorker] Transcript: '%s'", text)
            await self._events.put(STTBackendTranscriptEvent(text=text, is_final=is_final))
        elif status == "error":
            await self._events.put(SubprocessSTTError(f"decode error: {resp.get('error', 'unknown')}"))
        else:
            await self._events.put(SubprocessSTTError(f"unexpected response: {resp}"))

    async def stop(self) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        self._buffer.clear()
        if self._decode_task is not None:
            self._decode_queue.put_nowait(None)
            self._decode_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._decode_task
            self._decode_task = None
        if self._closed_event_enqueued:
            return
        self._closed_event_enqueued = True
        await self._events.put(None)

    async def events(self) -> AsyncIterator[STTBackendTranscriptEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                break
            if isinstance(event, BaseException):
                raise event
            yield event
