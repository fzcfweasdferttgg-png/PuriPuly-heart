from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from puripuly_heart.core.local_asr import local_stt_runtime_installer as installer_module
from puripuly_heart.core.local_asr.local_gpu_assets import LocalGPUInstallSnapshot
from puripuly_heart.core.local_asr.local_stt_assets import (
    LOCAL_QWEN_GPU_MODEL_ID,
    InstalledLocalSTTManifest,
    LocalSTTAssetFile,
    LocalSTTAssetManifest,
    LocalSTTAssetSource,
    LocalSTTInstallState,
)
from puripuly_heart.core.local_asr.local_stt_runtime_installer import (
    RuntimeLocalSTTStatusUpdate,
)
from puripuly_heart.core.runtime import local_asr_provisioning as provisioning_module
from puripuly_heart.core.runtime.local_asr_provisioning import (
    LocalASRInstallRequest,
    LocalASRProvisioningOwner,
)


@pytest.fixture(autouse=True)
def isolated_provisioning_root(tmp_path, monkeypatch):
    root = tmp_path / "isolated-models"
    monkeypatch.setattr(provisioning_module, "default_local_stt_model_root", lambda: root)
    monkeypatch.setattr(installer_module, "default_local_stt_model_root", lambda: root)


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _fixture_manifest(model_id: str) -> LocalSTTAssetManifest:
    payload = f"synthetic {model_id}".encode()
    return LocalSTTAssetManifest(
        manifest_version=2,
        model_id=model_id,
        engine="transcribe.cpp-vulkan",
        install_dirname=model_id,
        sources={
            "huggingface": LocalSTTAssetSource(
                name="huggingface",
                revision=f"{model_id}-revision",
                repo_id="test/repository",
                download_url_template="https://example.invalid/{path}",
            ),
            "hf_mirror": LocalSTTAssetSource(
                name="hf_mirror",
                revision=f"{model_id}-revision",
                download_url_template="https://example.invalid/mirror/{path}",
            ),
        },
        files=(
            LocalSTTAssetFile(
                relative_path="synthetic.gguf",
                size_bytes=len(payload),
                sha256=_sha256(payload),
            ),
        ),
    )


def _installed(model_id: str) -> InstalledLocalSTTManifest:
    manifest = _fixture_manifest(model_id)
    source = next(iter(manifest.sources.values()))
    return InstalledLocalSTTManifest(
        manifest_version=1,
        model_id=model_id,
        engine=manifest.engine,
        install_dirname=manifest.install_dirname,
        selected_source=source.name,
        selected_revision=source.revision,
    )


def _state(status: str, model_id: str):
    return LocalSTTInstallState(
        status=status,
        installed_manifest=_installed(model_id) if status == "ready" else None,
    )


@dataclass(slots=True)
class MutableProvisioningBackend:
    states: dict[str, LocalSTTInstallState]
    install_calls: list[str] = field(default_factory=list)
    status_callbacks: list[Callable] = field(default_factory=list)
    install_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_install: asyncio.Event | None = None
    failure: Exception | None = None

    def inspect_gpu(self, **_kwargs) -> LocalGPUInstallSnapshot:
        state = self.states[LOCAL_QWEN_GPU_MODEL_ID]
        return LocalGPUInstallSnapshot(
            explicit_opt_in=True,
            status=state.status,
            state=state,
        )

    async def install(self, **kwargs) -> InstalledLocalSTTManifest:
        model_id = kwargs["model_id"]
        on_status = kwargs["on_status"]
        self.install_calls.append(model_id)
        self.status_callbacks.append(on_status)
        self.install_started.set()
        await on_status(RuntimeLocalSTTStatusUpdate(status="downloading", percent=37))
        if self.release_install is not None:
            await self.release_install.wait()
        if self.failure is not None:
            raise self.failure
        installed = _installed(model_id)
        self.states[model_id] = LocalSTTInstallState(
            status="ready",
            installed_manifest=installed,
        )
        await on_status(RuntimeLocalSTTStatusUpdate(status="ready", percent=None))
        return installed


def _owner(backend: MutableProvisioningBackend, **kwargs) -> LocalASRProvisioningOwner:
    kwargs.setdefault("manifest_loader", _fixture_manifest)
    return LocalASRProvisioningOwner(
        gpu_model_inspector=backend.inspect_gpu,
        installer=backend.install,
        **kwargs,
    )


def _all_states(status: str = "ready") -> dict[str, LocalSTTInstallState]:
    return {LOCAL_QWEN_GPU_MODEL_ID: _state(status, LOCAL_QWEN_GPU_MODEL_ID)}


@pytest.mark.asyncio
async def test_inspect_gpu_reports_integrity_and_not_requested() -> None:
    backend = MutableProvisioningBackend(_all_states("missing"))
    owner = _owner(backend)
    try:
        snapshot = await owner.inspect_gpu(explicit_intent=False)
        assert snapshot.state_for(LOCAL_QWEN_GPU_MODEL_ID).integrity == "not_requested"

        snapshot = await owner.inspect_gpu(explicit_intent=True)
        assert snapshot.state_for(LOCAL_QWEN_GPU_MODEL_ID).integrity == "missing"

        backend.states[LOCAL_QWEN_GPU_MODEL_ID] = _state("ready", LOCAL_QWEN_GPU_MODEL_ID)
        snapshot = await owner.inspect_gpu(explicit_intent=True)
        assert snapshot.state_for(LOCAL_QWEN_GPU_MODEL_ID).available
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_owner_delivers_install_result_through_owned_task() -> None:
    backend = MutableProvisioningBackend(_all_states("missing"))
    owner = _owner(backend)
    results: list = []
    try:
        task = owner.start_install(
            LocalASRInstallRequest(
                "gpu",
                (LOCAL_QWEN_GPU_MODEL_ID,),
                None,
                "activation",
                explicit_gpu_intent=True,
            ),
            result_handler=results.append,
        )
        result = await task
        assert result.installed_model_ids == (LOCAL_QWEN_GPU_MODEL_ID,)
        assert result.failed_model_ids == ()
        assert backend.install_calls == [LOCAL_QWEN_GPU_MODEL_ID]
        assert owner.snapshot.state_for(LOCAL_QWEN_GPU_MODEL_ID).integrity == "ready"
    finally:
        await owner.close()
