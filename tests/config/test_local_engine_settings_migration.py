from __future__ import annotations

import json

from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.settings_vnext.serialization import from_dict, to_dict, to_json_text


def _legacy_raw(
    *,
    self_provider: str = "local_qwen_gpu",
    peer_provider: str = "local_qwen_gpu",
    gpu_device_id: str = "auto",
) -> dict:
    raw = to_dict(AppSettingsVNext())
    stt = raw["intent"]["stt"]
    peer = raw["intent"]["peer_stt"]
    for key in ("gpu_backend", "shared_engine"):
        stt.pop(key, None)
    for key in ("gpu_backend", "gpu_device_id"):
        peer.pop(key, None)
    stt["provider"] = self_provider
    stt["gpu_device_id"] = gpu_device_id
    peer["provider"] = peer_provider
    return raw


def test_legacy_cpu_device_migrates_to_cpu_backend_without_device() -> None:
    settings = from_dict(_legacy_raw(gpu_device_id="cpu"))

    assert settings.intent.stt.gpu_backend == "cpu"
    assert settings.intent.stt.gpu_device_id == "auto"
    assert settings.intent.peer_stt.gpu_backend == "cpu"
    assert settings.intent.peer_stt.gpu_device_id == "auto"


def test_legacy_gpu_device_migrates_to_vulkan_backend_keeping_device() -> None:
    settings = from_dict(_legacy_raw(gpu_device_id="vulkan-index-0"))

    assert settings.intent.stt.gpu_backend == "vulkan"
    assert settings.intent.stt.gpu_device_id == "vulkan-index-0"
    assert settings.intent.peer_stt.gpu_backend == "vulkan"
    assert settings.intent.peer_stt.gpu_device_id == "vulkan-index-0"


def test_legacy_two_local_channels_migrate_to_shared_engine() -> None:
    settings = from_dict(_legacy_raw())

    assert settings.intent.stt.shared_engine is True


def test_legacy_single_local_channel_stays_independent() -> None:
    settings = from_dict(_legacy_raw(peer_provider=""))

    assert settings.intent.stt.shared_engine is False


def test_saved_engine_fields_are_not_rewritten() -> None:
    raw = _legacy_raw(gpu_device_id="vulkan-index-0")
    stt = raw["intent"]["stt"]
    peer = raw["intent"]["peer_stt"]
    stt["gpu_backend"] = "cuda"
    stt["gpu_device_id"] = "cuda-index-1"
    stt["shared_engine"] = False
    peer["gpu_backend"] = "cpu"
    peer["gpu_device_id"] = "auto"

    settings = from_dict(raw)

    assert settings.intent.stt.gpu_backend == "cuda"
    assert settings.intent.stt.gpu_device_id == "cuda-index-1"
    assert settings.intent.stt.shared_engine is False
    assert settings.intent.peer_stt.gpu_backend == "cpu"
    assert settings.intent.peer_stt.gpu_device_id == "auto"


def test_roundtrip_preserves_engine_fields() -> None:
    migrated = from_dict(_legacy_raw(gpu_device_id="cpu"))

    reloaded = from_dict(json.loads(to_json_text(migrated)))

    assert reloaded.intent.stt.gpu_backend == "cpu"
    assert reloaded.intent.stt.gpu_device_id == "auto"
    assert reloaded.intent.stt.shared_engine is True
    assert reloaded.intent.peer_stt.gpu_backend == "cpu"


def test_unknown_backend_value_normalizes_to_vulkan() -> None:
    raw = to_dict(AppSettingsVNext())
    raw["intent"]["stt"]["gpu_backend"] = "metal"
    raw["intent"]["peer_stt"]["gpu_backend"] = "metal"

    settings = from_dict(raw)

    assert settings.intent.stt.gpu_backend == "vulkan"
    assert settings.intent.peer_stt.gpu_backend == "vulkan"
