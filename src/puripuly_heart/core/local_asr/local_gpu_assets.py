"""Local GPU (transcribe.cpp Vulkan) model assets: multi-model resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .local_stt_assets import (
    LOCAL_QWEN_GPU_MODEL_FILENAME,
    LOCAL_QWEN_GPU_MODEL_ID,
    LocalSTTAssetError,
    LocalSTTAssetManifest,
    LocalSTTInstallState,
    default_local_stt_model_root,
    inspect_local_stt_install_state,
    load_local_stt_asset_manifest,
)

LOCAL_QWEN_GPU_ENGINE = "transcribe.cpp-vulkan"
LocalGPUOptInStatus = Literal["not_requested", "missing", "invalid", "ready"]


@dataclass(frozen=True, slots=True)
class LocalGPUInstallSnapshot:
    explicit_opt_in: bool
    status: LocalGPUOptInStatus
    model_id: str = LOCAL_QWEN_GPU_MODEL_ID
    state: LocalSTTInstallState | None = None

    @property
    def activation_allowed(self) -> bool:
        return (
            self.explicit_opt_in
            and self.status == "ready"
            and self.state is not None
            and self.state.installed_manifest is not None
            and self.state.installed_manifest.model_id == self.model_id
        )


def load_local_gpu_asset_manifest(
    model_id: str = LOCAL_QWEN_GPU_MODEL_ID,
) -> LocalSTTAssetManifest:
    manifest = load_local_stt_asset_manifest(model_id)
    if manifest.engine != LOCAL_QWEN_GPU_ENGINE:
        raise ValueError("local GPU model manifest engine is not strict Vulkan transcribe.cpp")
    return manifest


def inspect_local_gpu_install(
    *,
    explicit_opt_in: bool,
    model_root: Path | None = None,
    verify_checksums: bool = True,
    manifest: LocalSTTAssetManifest | None = None,
) -> LocalGPUInstallSnapshot:
    resolved_manifest = manifest or load_local_gpu_asset_manifest()
    if not explicit_opt_in:
        return LocalGPUInstallSnapshot(
            explicit_opt_in=False,
            status="not_requested",
            model_id=resolved_manifest.model_id,
        )
    root = model_root or default_local_stt_model_root()
    state = inspect_local_stt_install_state(
        root / resolved_manifest.install_dirname,
        manifest=resolved_manifest,
        verify_checksums=verify_checksums,
    )
    return LocalGPUInstallSnapshot(
        explicit_opt_in=True,
        status=state.status,
        model_id=resolved_manifest.model_id,
        state=state,
    )


def local_gpu_model_path(
    model_root: Path | None = None,
    *,
    model_id: str | None = None,
    manifest: LocalSTTAssetManifest | None = None,
) -> Path:
    root = model_root or default_local_stt_model_root()
    if manifest is None:
        try:
            resolved_manifest = load_local_gpu_asset_manifest(
                model_id or LOCAL_QWEN_GPU_MODEL_ID
            )
        except LocalSTTAssetError:
            return _registry_model_path(root, model_id)
    else:
        resolved_manifest = manifest
    if len(resolved_manifest.files) != 1:
        raise ValueError("local GPU model manifest must contain exactly one model file")
    return root / resolved_manifest.install_dirname / resolved_manifest.files[0].relative_path


def _registry_model_path(root: Path, model_id: str | None) -> Path:
    """Resolve a model installed outside the manifest catalog."""
    from .model_registry import ModelRegistry

    record = ModelRegistry(root).get(model_id or "")
    if record is None or not record.files:
        raise ValueError(f"unknown local model: {model_id}")
    return ModelRegistry(root).model_file_path(record)


__all__ = [
    "LOCAL_QWEN_GPU_ENGINE",
    "LOCAL_QWEN_GPU_MODEL_FILENAME",
    "LOCAL_QWEN_GPU_MODEL_ID",
    "LocalGPUInstallSnapshot",
    "LocalGPUOptInStatus",
    "inspect_local_gpu_install",
    "load_local_gpu_asset_manifest",
    "local_gpu_model_path",
]
