"""Inference worker — standalone subprocess for STT decoding.

Runs as a child process, reads JSON commands from stdin, writes JSON responses
to stdout.  Handles model loading and audio decoding; the parent process
(GUI) never imports onnxruntime or sherpa_onnx directly.

Usage:
    python -m puripuly_heart.core.inference.worker
"""

from __future__ import annotations

import base64
import importlib
import logging
import sys
import traceback

import numpy as np

logger = logging.getLogger("inference_worker")


def _write_message(payload: dict[str, object]) -> None:
    import json

    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def _send_ready() -> None:
    _write_message({"status": "ready"})


def _send_transcript(text: str, is_final: bool = True) -> None:
    _write_message({"status": "transcript", "text": text, "is_final": is_final})


def _send_error(error: str) -> None:
    _write_message({"status": "error", "error": error})


def _send_bye() -> None:
    _write_message({"status": "bye"})


def _create_recognizer(data: dict[str, object]) -> object:
    provider_name: str = str(data.get("provider", "local_qwen"))
    model_dir_str: str = str(data["model_dir"])
    provider_type: str = str(data.get("provider_type", "directml"))
    device: int = int(data.get("device", 0))
    num_threads: int = int(data.get("num_threads", 3))
    feature_dim: int = int(data.get("feature_dim", 128))
    language_hint = data.get("language_hint")
    raw_hotwords = data.get("hotwords")
    hotwords = tuple(raw_hotwords) if isinstance(raw_hotwords, list) else ()

    from pathlib import Path

    model_dir = Path(model_dir_str)

    if provider_name in ("local_qwen", "local_qwen_17b"):
        from puripuly_heart.adapters.stt.local_qwen_sherpa import (
            LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
            create_local_qwen_sherpa_recognizer,
        )

        recognizer = create_local_qwen_sherpa_recognizer(
            model_dir=model_dir,
            num_threads=num_threads,
            sample_rate_hz=LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
            feature_dim=feature_dim,
            provider=provider_type,
            device=device,
        )
        return _RecognizerHandle(
            recognizer=recognizer,
            sample_rate_hz=LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
            language_hint=str(language_hint) if language_hint else None,
            hotwords=hotwords,
        )

    if provider_name == "local_gigaam_rnnt":
        from puripuly_heart.adapters.stt.local_gigaam_rnnt import (
            GIGAAM_RECOGNIZER_SAMPLE_RATE_HZ,
            create_local_gigaam_rnnt_recognizer,
        )

        recognizer = create_local_gigaam_rnnt_recognizer(
            model_dir=model_dir,
            num_threads=num_threads,
            sample_rate_hz=GIGAAM_RECOGNIZER_SAMPLE_RATE_HZ,
            feature_dim=feature_dim,
            provider=provider_type,
            device=device,
        )
        return _RecognizerHandle(
            recognizer=recognizer,
            sample_rate_hz=GIGAAM_RECOGNIZER_SAMPLE_RATE_HZ,
            language_hint=str(language_hint) if language_hint else None,
            hotwords=hotwords,
        )

    if provider_name == "local_parakeet_tdt":
        from puripuly_heart.adapters.stt.local_parakeet_tdt import (
            PARAKEET_TDT_SAMPLE_RATE_HZ,
            create_local_parakeet_tdt_recognizer,
        )

        recognizer = create_local_parakeet_tdt_recognizer(
            model_dir=model_dir,
            num_threads=num_threads,
            sample_rate_hz=PARAKEET_TDT_SAMPLE_RATE_HZ,
            feature_dim=feature_dim,
            provider=provider_type,
            device=device,
        )
        return _RecognizerHandle(
            recognizer=recognizer,
            sample_rate_hz=PARAKEET_TDT_SAMPLE_RATE_HZ,
            language_hint=str(language_hint) if language_hint else None,
            hotwords=hotwords,
        )

    if provider_name == "local_transcribecpp":
        from puripuly_heart.adapters.stt.local_transcribecpp import (
            TRANSCRIBECPP_SAMPLE_RATE_HZ,
            create_transcribecpp_recognizer,
        )

        gguf_files = list(model_dir.glob("*.gguf"))
        if len(gguf_files) == 0:
            raise FileNotFoundError(f"no .gguf file found in {model_dir}")
        if len(gguf_files) > 1:
            logger.warning("multiple .gguf files in %s, using first: %s", model_dir, gguf_files[0].name)
        model_path = gguf_files[0]

        recognizer = create_transcribecpp_recognizer(
            model_path=model_path,
            backend=provider_type,
            gpu_device=device,
        )
        logger.info(
            "[InferenceWorker] transcribecpp ready: file=%s backend=%s device=%d threads=%d",
            model_path.name, provider_type, device, num_threads,
        )
        return _TranscribecppHandle(
            model=recognizer,
            sample_rate_hz=TRANSCRIBECPP_SAMPLE_RATE_HZ,
            language_hint=str(language_hint) if language_hint else None,
            num_threads=num_threads,
        )

    raise ValueError(f"unknown provider: {provider_name}")


class _RecognizerHandle:
    __slots__ = ("recognizer", "sample_rate_hz", "language_hint", "hotwords")

    def __init__(
        self,
        recognizer: object,
        sample_rate_hz: int,
        language_hint: str | None,
        hotwords: tuple[str, ...],
    ) -> None:
        self.recognizer = recognizer
        self.sample_rate_hz = sample_rate_hz
        self.language_hint = language_hint
        self.hotwords = hotwords

    def decode(self, samples_f32: np.ndarray) -> str:
        samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1).copy()
        stream = self.recognizer.create_stream()
        set_option = getattr(stream, "set_option", None)
        if callable(set_option):
            if self.language_hint:
                set_option("language", self.language_hint)
            if self.hotwords:
                set_option("hotwords", ",".join(self.hotwords))
        np.clip(samples, -1.0, 1.0, out=samples)
        stream.accept_waveform(self.sample_rate_hz, samples)
        self.recognizer.decode_stream(stream)
        result = getattr(stream, "result", None)
        text = getattr(result, "text", "")
        return str(text).strip()

    def close(self) -> None:
        self.recognizer = None
        logger.info("[InferenceWorker] recognizer handle closed")


class _TranscribecppHandle:
    __slots__ = ("model", "session", "sample_rate_hz", "language_hint")

    def __init__(
        self,
        model: object,
        sample_rate_hz: int,
        language_hint: str | None,
        num_threads: int = 3,
    ) -> None:
        self.model = model
        self.session = model.session(n_threads=num_threads)
        self.sample_rate_hz = sample_rate_hz
        self.language_hint = language_hint

    def decode(self, samples_f32: np.ndarray) -> str:
        samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1).copy()
        np.clip(samples, -1.0, 1.0, out=samples)
        result = self.session.run(
            samples,
            language=self.language_hint,
        )
        return result.text.strip()

    def close(self) -> None:
        if self.session is not None:
            self.session.close()
            self.session = None
        self.model = None
        logger.info("[InferenceWorker] transcribecpp handle closed")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    import json

    handle: _RecognizerHandle | _TranscribecppHandle | None = None

    _send_ready()

    try:
        while True:
            raw_line = sys.stdin.buffer.readline()
            if not raw_line:
                logger.info("stdin closed, exiting")
                break

            line = raw_line.decode("utf-8").strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                _send_error(f"invalid JSON: {exc}")
                continue

            cmd = data.get("cmd")

            if cmd == "init":
                try:
                    handle = _create_recognizer(data)
                    _send_ready()
                except Exception as exc:
                    _send_error(f"init failed: {exc}")
                    logger.exception("init failed")

            elif cmd == "decode":
                if handle is None:
                    _send_error("not initialized")
                    continue
                try:
                    audio_b64: str = str(data.get("audio_b64", ""))
                    raw = base64.b64decode(audio_b64)
                    samples = np.frombuffer(raw, dtype=np.float32)
                    text = handle.decode(samples)
                    _send_transcript(text, is_final=bool(data.get("is_final", True)))
                except Exception as exc:
                    _send_error(f"decode failed: {exc}")
                    logger.exception("decode failed")

            elif cmd == "shutdown":
                logger.info("shutdown requested")
                break

            else:
                _send_error(f"unknown command: {cmd}")

    except Exception:
        tb = traceback.format_exc()
        logger.error("worker crashed:\n%s", tb)
        _send_error(f"worker crashed: {tb}")
    finally:
        if handle is not None:
            close_fn = getattr(handle, "close", None)
            if callable(close_fn):
                close_fn()
        _send_bye()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
