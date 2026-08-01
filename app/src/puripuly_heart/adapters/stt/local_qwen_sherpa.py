from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path

from puripuly_heart.core.local_qwen_runtime import (
    ensure_local_qwen_windows_runtime,
)

LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ = 16000
logger = logging.getLogger(__name__)


def _default_provider() -> str:
    return "cpu" if os.environ.get("PURIPULY_MODE", "gpu").lower() == "cpu" else "directml"


def _default_device() -> int:
    if _default_provider() == "cpu":
        return 0
    env_device = os.environ.get("SHERPA_GPU_DEVICE", "")
    if env_device.isdigit() and int(env_device) > 0:
        return int(env_device)
    return 0


class LocalQwenSherpaLoadError(RuntimeError):
    """Raised when the local sherpa recognizer cannot be initialized."""


class _LocalQwenSherpaImportError(ImportError):
    """Internal sentinel for sherpa_onnx import failures."""


def create_local_qwen_sherpa_recognizer(
    *,
    model_dir: Path,
    num_threads: int,
    sample_rate_hz: int = LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ,
    feature_dim: int = 128,
    provider: str | None = None,
    device: int = 0,
) -> object:
    if provider is None:
        provider = _default_provider()
    if sample_rate_hz != LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {LOCAL_QWEN_RECOGNIZER_SAMPLE_RATE_HZ}")
    ensure_local_qwen_windows_runtime()
    logger.info("[STT][local_qwen] Creating recognizer: provider=%s device=%d", provider, device)
    try:
        import sherpa_onnx

        recognizer_module = importlib.import_module("sherpa_onnx.offline_recognizer")
    except ImportError as exc:
        raise _LocalQwenSherpaImportError from exc

    qwen3_config = sherpa_onnx.OfflineQwen3ASRModelConfig(
        conv_frontend=str(model_dir / "conv_frontend.onnx"),
        encoder=str(model_dir / "encoder.int8.onnx"),
        decoder=str(model_dir / "decoder.int8.onnx"),
        tokenizer=str(model_dir / "tokenizer"),
        max_total_len=512,
        max_new_tokens=128,
        temperature=1e-6,
        top_p=0.8,
        seed=42,
    )
    model_config = sherpa_onnx.OfflineModelConfig(
        qwen3_asr=qwen3_config,
        num_threads=num_threads,
        debug=False,
        provider=provider,
        device=device,
    )
    feat_config = sherpa_onnx.FeatureExtractorConfig(
        sampling_rate=sample_rate_hz,
        feature_dim=feature_dim,
    )
    recognizer_config = sherpa_onnx.OfflineRecognizerConfig(
        feat_config=feat_config,
        model_config=model_config,
        decoding_method="greedy_search",
    )
    recognizer_cls = getattr(recognizer_module, "_Recognizer")
    return recognizer_cls(recognizer_config)
