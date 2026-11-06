from __future__ import annotations

import json

from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.settings_vnext.serialization import from_dict, to_dict, to_json_text


def test_backup_and_openai_compatible_defaults_are_off() -> None:
    settings = AppSettingsVNext()

    assert settings.intent.backup_translation.enabled is False
    assert settings.intent.backup_translation.model == ""
    assert settings.intent.backup_translation.openai_compatible.model == ""
    assert settings.intent.backup_translation.local_llm.base_url == ""
    assert settings.intent.backup_translation.local_llm.model == ""
    assert settings.intent.openai_compatible.provider_key == ""
    assert settings.intent.openai_compatible.model == ""


def test_backup_and_openai_compatible_roundtrip() -> None:
    raw = to_dict(AppSettingsVNext())
    raw["intent"]["openai_compatible"] = {
        "provider_key": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-test",
    }
    raw["intent"]["backup_translation"] = {
        "enabled": True,
        "model": "openai_compatible",
        "openai_compatible": {"provider_key": "deepseek", "base_url": "", "model": "backup-model"},
        "local_llm": {"base_url": "http://127.0.0.1:11434/v1", "model": "llama3.1:8b", "extra_body": {}},
    }

    loaded = from_dict(raw)

    assert loaded.intent.openai_compatible.provider_key == "openai"
    assert loaded.intent.openai_compatible.model == "gpt-test"
    assert loaded.intent.backup_translation.enabled is True
    assert loaded.intent.backup_translation.model == "openai_compatible"
    assert loaded.intent.backup_translation.openai_compatible.model == "backup-model"
    assert loaded.intent.backup_translation.local_llm.model == "llama3.1:8b"

    reloaded = from_dict(json.loads(to_json_text(loaded)))

    assert reloaded.intent.openai_compatible == loaded.intent.openai_compatible
    assert reloaded.intent.backup_translation == loaded.intent.backup_translation


def test_legacy_settings_gain_disabled_backup() -> None:
    raw = to_dict(AppSettingsVNext())
    raw["intent"].pop("openai_compatible", None)
    raw["intent"].pop("backup_translation", None)

    loaded = from_dict(raw)

    assert loaded.intent.backup_translation.enabled is False
    assert loaded.intent.openai_compatible.base_url == ""
