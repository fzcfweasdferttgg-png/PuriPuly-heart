from __future__ import annotations

import asyncio

import pytest

from puripuly_heart.core.local_asr.local_stt_assets import LOCAL_QWEN_GPU_MODEL_ID
from puripuly_heart.core.local_asr.local_stt_runtime_installer import LocalSTTRuntimeInstallError
from puripuly_heart.core.runtime.local_asr_provisioning import LocalASRInstallRequest
from tests.core.runtime.test_local_asr_provisioning import (
    MutableProvisioningBackend,
    _all_states,
    _fixture_manifest,
    _owner,
    _state,
)


@pytest.mark.asyncio
async def test_delete_removes_the_install_and_republishes_integrity(tmp_path) -> None:
    backend = MutableProvisioningBackend(_all_states())
    owner = _owner(backend, model_root=tmp_path)
    install = tmp_path / _fixture_manifest(LOCAL_QWEN_GPU_MODEL_ID).install_dirname
    install.mkdir()
    (install / "model.gguf").write_bytes(b"weights")
    try:
        await owner.inspect_gpu(explicit_intent=True)
        assert owner.snapshot.state_for(LOCAL_QWEN_GPU_MODEL_ID).integrity == "ready"
        backend.states[LOCAL_QWEN_GPU_MODEL_ID] = _state("missing", LOCAL_QWEN_GPU_MODEL_ID)

        snapshot = await owner.delete_model(LOCAL_QWEN_GPU_MODEL_ID)

        assert not install.exists()
        assert snapshot.state_for(LOCAL_QWEN_GPU_MODEL_ID).integrity == "missing"
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_delete_is_refused_while_the_backend_is_installing(tmp_path) -> None:
    backend = MutableProvisioningBackend(_all_states("missing"), release_install=asyncio.Event())
    owner = _owner(backend, model_root=tmp_path)
    install = tmp_path / _fixture_manifest(LOCAL_QWEN_GPU_MODEL_ID).install_dirname
    install.mkdir()
    try:
        task = owner.start_install(
            LocalASRInstallRequest(
                "gpu",
                (LOCAL_QWEN_GPU_MODEL_ID,),
                None,
                "activation",
                explicit_gpu_intent=True,
            )
        )
        await backend.install_started.wait()

        with pytest.raises(LocalSTTRuntimeInstallError) as caught:
            await owner.delete_model(LOCAL_QWEN_GPU_MODEL_ID)

        assert caught.value.failure_code == "task_busy"
        assert install.exists()
        backend.release_install.set()
        await task
    finally:
        await owner.close()
