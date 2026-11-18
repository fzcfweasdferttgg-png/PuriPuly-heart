from __future__ import annotations

from dataclasses import replace

import pytest

from puripuly_heart.config.provider_values import STTProviderName
from puripuly_heart.config.settings_vnext import serialization
from puripuly_heart.config.settings_vnext.schema import (
    AppSettingsVNext,
)

LIVE_LOCAL_PROVIDER_VALUES = (
    STTProviderName.LOCAL_QWEN_GPU,
    STTProviderName.NONE,
)

RETIRED_LOCAL_PROVIDER_VALUES = (
    STTProviderName.DEEPGRAM,
    STTProviderName.SONIOX,
)


@pytest.mark.parametrize("provider", LIVE_LOCAL_PROVIDER_VALUES)
def test_live_provider_identities_roundtrip_through_canonical_serialization(
    provider: STTProviderName,
) -> None:
    current = AppSettingsVNext()
    settings = replace(
        current,
        intent=replace(
            current.intent,
            stt=replace(
                current.intent.stt,
                provider=provider.value,
                gpu_device_id="vulkan-device-2",
            ),
            peer_stt=replace(current.intent.peer_stt, provider=provider.value),
        ),
    )

    persisted = serialization.to_dict(settings)
    restored = serialization.from_dict(persisted)

    assert persisted["intent"]["stt"]["provider"] == provider.value
    assert persisted["intent"]["peer_stt"]["provider"] == provider.value
    assert persisted["intent"]["stt"]["gpu_device_id"] == "vulkan-device-2"
    assert restored.intent.stt.provider == provider.value
    assert restored.intent.peer_stt.provider == provider.value
    assert restored.intent.stt.gpu_device_id == "vulkan-device-2"


@pytest.mark.parametrize("provider", RETIRED_LOCAL_PROVIDER_VALUES)
def test_retired_provider_identities_normalize_to_not_selected(
    provider: STTProviderName,
) -> None:
    current = AppSettingsVNext()
    settings = replace(
        current,
        intent=replace(
            current.intent,
            stt=replace(current.intent.stt, provider=provider.value),
            peer_stt=replace(current.intent.peer_stt, provider=provider.value),
        ),
    )

    assert settings.intent.stt.provider == STTProviderName.NONE.value
    assert settings.intent.peer_stt.provider == STTProviderName.NONE.value
