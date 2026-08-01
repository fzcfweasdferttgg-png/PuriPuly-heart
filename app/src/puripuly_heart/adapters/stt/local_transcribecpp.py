from __future__ import annotations

import logging
import os
from pathlib import Path

TRANSCRIBECPP_SAMPLE_RATE_HZ = 16000
logger = logging.getLogger(__name__)


def _default_backend() -> str:
    return "cpu" if os.environ.get("PURIPULY_MODE", "gpu").lower() == "cpu" else "vulkan"


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
