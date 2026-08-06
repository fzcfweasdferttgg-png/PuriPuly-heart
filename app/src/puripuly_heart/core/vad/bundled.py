"""Extract bundled Silero VAD ONNX model from package data to a target path.

The model is shipped inside the frozen package (data/vad/silero_vad.onnx).
ensure_silero_vad_onnx() copies it to target_path on first call; subsequent
calls are no-ops (existence check).  Uses atomic write (tmp + replace) to
avoid partial files on crash.

Called by app/headless_mic.py and ui/controller.py.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

SILERO_VAD_VERSION = "6.2.1"
SILERO_VAD_RESOURCE_RELATIVE_PATH = "data/vad/silero_vad.onnx"


def bundled_silero_vad_onnx_path() -> resources.abc.Traversable:
    return resources.files("puripuly_heart").joinpath(SILERO_VAD_RESOURCE_RELATIVE_PATH)


def ensure_silero_vad_onnx(*, target_path: Path) -> Path:
    try:
        if target_path.exists() and target_path.stat().st_size > 0:
            return target_path
    except OSError:
        pass

    bundled = bundled_silero_vad_onnx_path()
    if not bundled.is_file():
        raise FileNotFoundError(
            f"Bundled Silero VAD model missing: {SILERO_VAD_RESOURCE_RELATIVE_PATH}"
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target_path.with_suffix(target_path.suffix + ".tmp")
    with bundled.open("rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    tmp.replace(target_path)
    return target_path
