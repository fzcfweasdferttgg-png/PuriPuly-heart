from __future__ import annotations

from pathlib import Path

import pytest

from puripuly_heart.app.ports.settings_view import (
    AnthropicBaseUrlEdit,
    AnthropicProviderKeyEdit,
    OpenAICompatibleBaseUrlEdit,
    OpenAICompatibleProviderKeyEdit,
    ProviderApplyIntent,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from tests.helpers.provider_settings import (
    attach_provider_windows,
    create_settings_view,
    install_provider_presets,
)
from tests.helpers.settings_view import SettingsIntentRecorder, apply_provider_intent


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_trailing_slash_saved_preset_survives_load_and_accept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, protocol: str
) -> None:
    install_provider_presets(monkeypatch, tmp_path)
    if protocol == "anthropic":
        key = "anthropic"
        edits = (
            AnthropicProviderKeyEdit(key),
            AnthropicBaseUrlEdit("https://api.anthropic.com/"),
        )
    else:
        key = "deepseek"
        edits = (
            OpenAICompatibleProviderKeyEdit(key),
            OpenAICompatibleBaseUrlEdit("https://api.deepseek.com/v1/"),
        )
    settings = apply_provider_intent(AppSettingsVNext(), ProviderApplyIntent(edits))
    recorder = SettingsIntentRecorder()
    view = create_settings_view(monkeypatch)
    recorder.load(view, settings, config_path=tmp_path / "settings.json")
    attach_provider_windows(view)
    dropdown = view._anthropic_provider if protocol == "anthropic" else view._openai_provider

    assert dropdown.value == key
    if protocol == "anthropic":
        view._open_provider_settings_dialog("translation_anthropic")
        assert view._accept_provider_settings_dialog("translation", "translation_anthropic")
        current = view._provider_draft or view._provider_snapshot
        assert current.anthropic_provider_key == key
    else:
        view._open_provider_settings_dialog("translation_openai")
        assert view._accept_provider_settings_dialog("translation", "translation_openai")
        current = view._provider_draft or view._provider_snapshot
        assert current.openai_compatible_provider_key == key


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_trailing_slash_saved_preset_reselect_keeps_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, protocol: str
) -> None:
    from puripuly_heart.app.ports.settings_view import (
        AnthropicModelEdit,
        OpenAICompatibleModelEdit,
    )

    install_provider_presets(monkeypatch, tmp_path)
    if protocol == "anthropic":
        key = "anthropic"
        edits = (
            AnthropicProviderKeyEdit(key),
            AnthropicBaseUrlEdit("https://api.anthropic.com/"),
            AnthropicModelEdit("saved-model"),
        )
    else:
        key = "deepseek"
        edits = (
            OpenAICompatibleProviderKeyEdit(key),
            OpenAICompatibleBaseUrlEdit("https://api.deepseek.com/v1/"),
            OpenAICompatibleModelEdit("saved-model"),
        )
    settings = apply_provider_intent(AppSettingsVNext(), ProviderApplyIntent(edits))
    view = create_settings_view(monkeypatch)
    SettingsIntentRecorder().load(view, settings, config_path=tmp_path / "settings.json")

    if protocol == "anthropic":
        view._on_anthropic_provider_selected(key)
        current = view._provider_draft or view._provider_snapshot
        assert current.anthropic_model == "saved-model"
        assert view._anthropic_model.value == "saved-model"
    else:
        view._on_openai_provider_selected(key)
        current = view._provider_draft or view._provider_snapshot
        assert current.openai_compatible_model == "saved-model"
        assert view._openai_model.value == "saved-model"


@pytest.mark.parametrize("card", ["translation", "backup"])
def test_trailing_slash_local_blur_keeps_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, card: str
) -> None:
    from puripuly_heart.app.ports.settings_view import (
        BackupLocalLlmBaseUrlEdit,
        BackupLocalLlmModelEdit,
        BackupLocalLlmProviderKeyEdit,
        LocalLlmBaseUrlEdit,
        LocalLlmModelEdit,
        LocalLlmProviderKeyEdit,
    )
    from puripuly_heart.config.translation_values import TranslationModel

    install_provider_presets(monkeypatch, tmp_path)
    if card == "backup":
        edits = (
            BackupLocalLlmProviderKeyEdit("llamacpp"),
            BackupLocalLlmBaseUrlEdit("http://127.0.0.1:8080/v1/"),
            BackupLocalLlmModelEdit("saved-model"),
        )
    else:
        edits = (
            LocalLlmProviderKeyEdit("llamacpp"),
            LocalLlmBaseUrlEdit("http://127.0.0.1:8080/v1/"),
            LocalLlmModelEdit("saved-model"),
        )
    settings = apply_provider_intent(AppSettingsVNext(), ProviderApplyIntent(edits))
    view = create_settings_view(monkeypatch)
    SettingsIntentRecorder().load(view, settings, config_path=tmp_path / "settings.json")
    if card == "backup":
        view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
        view._on_backup_local_base_url_change_end(None)
        current = view._provider_draft or view._provider_snapshot
        assert current.backup_local_model == "saved-model"
        assert view._backup_local_model.value == "saved-model"
    else:
        view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
        view._on_local_llm_base_url_change_end(None)
        current = view._provider_draft or view._provider_snapshot
        assert current.local_llm_model == "saved-model"
        assert view._local_llm_model.value == "saved-model"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_trailing_slash_preset_rename_keeps_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, protocol: str
) -> None:
    from puripuly_heart.app.ports.settings_view import (
        AnthropicModelEdit,
        OpenAICompatibleModelEdit,
    )
    from puripuly_heart.config import providers_registry

    install_provider_presets(monkeypatch, tmp_path)
    if protocol == "anthropic":
        key = "anthropic"
        url = "https://api.anthropic.com"
        edits = (
            AnthropicProviderKeyEdit(key),
            AnthropicBaseUrlEdit(url + "/"),
            AnthropicModelEdit("saved-model"),
        )
    else:
        key = "deepseek"
        url = "https://api.deepseek.com/v1"
        edits = (
            OpenAICompatibleProviderKeyEdit(key),
            OpenAICompatibleBaseUrlEdit(url + "/"),
            OpenAICompatibleModelEdit("saved-model"),
        )
    settings = apply_provider_intent(AppSettingsVNext(), ProviderApplyIntent(edits))
    view = create_settings_view(monkeypatch)
    SettingsIntentRecorder().load(view, settings, config_path=tmp_path / "settings.json")
    providers_registry.update_provider_preset(key, "Renamed", url, protocol=protocol)

    view._on_provider_presets_changed(
        "anthropic" if protocol == "anthropic" else "translation", "edit", key
    )

    current = view._provider_draft or view._provider_snapshot
    model = current.anthropic_model if protocol == "anthropic" else current.openai_compatible_model
    assert model == "saved-model"
