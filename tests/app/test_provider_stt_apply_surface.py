from __future__ import annotations

import pytest

from puripuly_heart.app.ports.settings_view import (
    CustomSttEndpointEdit,
    CustomSttModelEdit,
    PeerSttGpuBackendEdit,
    ProviderApplyIntent,
    SttGpuBackendEdit,
    SttLocalModelEdit,
    SttSharedEngineEdit,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from tests.app.test_translation_backup_apply_surface import (
    _apply_with_production_merge,
    _FakeSettingsOwner,
)


@pytest.mark.asyncio
async def test_stt_edits_survive_production_apply() -> None:
    state = _FakeSettingsOwner(AppSettingsVNext())
    intent = ProviderApplyIntent(
        (
            SttLocalModelEdit("qwen3-asr-1.7b-gguf-q6_k"),
            SttGpuBackendEdit("cuda"),
            SttSharedEngineEdit(True),
            PeerSttGpuBackendEdit("cuda"),
            CustomSttEndpointEdit("http://127.0.0.1:9000/v1"),
            CustomSttModelEdit("custom-stt-model"),
        )
    )

    assert await _apply_with_production_merge(state, intent)

    committed = state.committed_settings.intent
    assert committed.stt.model == "qwen3-asr-1.7b-gguf-q6_k"
    assert committed.stt.gpu_backend == "cuda"
    assert committed.stt.shared_engine is True
    assert committed.peer_stt.gpu_backend == "cuda"
    assert committed.stt.custom.endpoint == "http://127.0.0.1:9000/v1"
    assert committed.stt.custom.model == "custom-stt-model"
