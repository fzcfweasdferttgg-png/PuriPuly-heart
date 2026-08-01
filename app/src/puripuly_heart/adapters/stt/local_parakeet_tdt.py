from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path

PARAKEET_TDT_SAMPLE_RATE_HZ = 16000
PARAKEET_TDT_FEATURE_DIM = 80
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


class _LocalParakeetTdtImportError(ImportError):
    """Internal sentinel for sherpa_onnx import failures."""


def create_local_parakeet_tdt_recognizer(
    *,
    model_dir: Path,
    num_threads: int,
    sample_rate_hz: int = PARAKEET_TDT_SAMPLE_RATE_HZ,
    feature_dim: int = PARAKEET_TDT_FEATURE_DIM,
    provider: str | None = None,
    device: int = 0,
) -> object:
    if provider is None:
        provider = _default_provider()
    if sample_rate_hz != PARAKEET_TDT_SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be {PARAKEET_TDT_SAMPLE_RATE_HZ}")
    logger.info("[STT][local_parakeet_tdt] Creating recognizer: provider=%s device=%d", provider, device)
    try:
        import sherpa_onnx

        recognizer_module = importlib.import_module("sherpa_onnx.offline_recognizer")
    except ImportError as exc:
        raise _LocalParakeetTdtImportError from exc

    transducer_config = sherpa_onnx.OfflineTransducerModelConfig(
        encoder_filename=str(model_dir / "encoder.int8.onnx"),
        decoder_filename=str(model_dir / "decoder.int8.onnx"),
        joiner_filename=str(model_dir / "joiner.int8.onnx"),
    )
    model_config = sherpa_onnx.OfflineModelConfig(
        transducer=transducer_config,
        tokens=str(model_dir / "tokens.txt"),
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
