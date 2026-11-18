from __future__ import annotations

import json

from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.settings_vnext.serialization import from_dict, to_dict, to_json_text


def test_saved_engine_fields_are_not_rewritten() -> None:
    raw = to_dict(AppSettingsVNext())
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
    raw = to_dict(AppSettingsVNext())
    raw["intent"]["stt"].update(
        gpu_backend="cpu",
        gpu_device_id="auto",
        shared_engine=True,
    )
    raw["intent"]["peer_stt"].update(gpu_backend="cpu", gpu_device_id="auto")
    settings = from_dict(raw)

    reloaded = from_dict(json.loads(to_json_text(settings)))

    assert reloaded.intent.stt.gpu_backend == "cpu"
    assert reloaded.intent.stt.gpu_device_id == "auto"
    assert reloaded.intent.stt.shared_engine is True
    assert reloaded.intent.peer_stt.gpu_backend == "cpu"
    assert reloaded.intent.peer_stt.gpu_device_id == "auto"


def test_unknown_backend_value_normalizes_to_vulkan() -> None:
    raw = to_dict(AppSettingsVNext())
    raw["intent"]["stt"]["gpu_backend"] = "metal"
    raw["intent"]["peer_stt"]["gpu_backend"] = "metal"

    settings = from_dict(raw)

    assert settings.intent.stt.gpu_backend == "vulkan"
    assert settings.intent.peer_stt.gpu_backend == "vulkan"
