from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.ports.settings_view import (
    BackupLocalLlmBaseUrlEdit,
    BackupLocalLlmModelEdit,
    BackupOpenAICompatibleBaseUrlEdit,
    BackupOpenAICompatibleModelEdit,
    BackupOpenAICompatibleProviderKeyEdit,
    BackupTranslationEnabledEdit,
    BackupVariantEdit,
    OpenAICompatibleBaseUrlEdit,
    OpenAICompatibleModelEdit,
    OpenAICompatibleProviderKeyEdit,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import apply_provider_intent


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


def _edit_types(pending) -> set[type]:
    return {type(edit) for edit in pending.edits}


def test_backup_variant_selection_enables_network_backup(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_backup_variant_selected("openai_compatible")

    assert view._provider_draft.backup_enabled is True
    assert view._provider_draft.backup_variant == "openai_compatible"
    assert view._backup_config_card.visible is True
    assert view._backup_network_group.visible is True
    assert view._backup_local_group.visible is False
    assert (
        view._backup_translation_text.content.value
        == settings_view.t("provider.openai_compatible")
    )
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert BackupTranslationEnabledEdit in _edit_types(pending)
    assert BackupVariantEdit in _edit_types(pending)
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.backup_translation.enabled is True
    assert applied.intent.backup_translation.model == "openai_compatible"


def test_backup_variant_off_disables_backup(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_backup_variant_selected("local_llm")
    assert view._backup_local_group.visible is True

    view._on_backup_variant_selected("off")

    assert view._provider_draft.backup_enabled is False
    assert view._provider_draft.backup_variant == ""
    assert view._backup_config_card.visible is False
    assert (
        view._backup_translation_text.content.value
        == settings_view.t("settings.backup_translation.disabled")
    )


def test_network_form_commits_provider_and_model(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._commit_openai_field("provider_key", "deepseek")
    view._commit_openai_field("base_url", "https://api.deepseek.com/v1")
    view._pick_openai_model("deepseek-chat")

    assert view._provider_draft.openai_compatible_provider_key == "deepseek"
    assert view._provider_draft.openai_compatible_base_url == "https://api.deepseek.com/v1"
    assert view._provider_draft.openai_compatible_model == "deepseek-chat"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert _edit_types(pending) >= {
        OpenAICompatibleProviderKeyEdit,
        OpenAICompatibleBaseUrlEdit,
        OpenAICompatibleModelEdit,
    }
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.openai_compatible.model == "deepseek-chat"


def test_provider_preset_fills_base_url(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_openai_provider_selected("deepseek")

    assert view._provider_draft.openai_compatible_provider_key == "deepseek"
    assert view._provider_draft.openai_compatible_base_url == "https://api.deepseek.com/v1"
    assert view._openai_base_url.value == "https://api.deepseek.com/v1"


def test_backup_forms_commit_fields(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_backup_variant_selected("openai_compatible")

    view._commit_backup_field("provider_key", "openai")
    view._commit_backup_field("base_url", "https://api.openai.com/v1")
    view._pick_backup_model("gpt-backup")
    view._commit_backup_field("local_base_url", "http://10.0.0.5:8080/v1")
    view._commit_backup_field("local_model", "backup-local-model")

    assert view._provider_draft.backup_provider_key == "openai"
    assert view._provider_draft.backup_base_url == "https://api.openai.com/v1"
    assert view._provider_draft.backup_model == "gpt-backup"
    assert view._provider_draft.backup_local_base_url == "http://10.0.0.5:8080/v1"
    assert view._provider_draft.backup_local_model == "backup-local-model"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert _edit_types(pending) >= {
        BackupOpenAICompatibleProviderKeyEdit,
        BackupOpenAICompatibleBaseUrlEdit,
        BackupOpenAICompatibleModelEdit,
        BackupLocalLlmBaseUrlEdit,
        BackupLocalLlmModelEdit,
    }
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.backup_translation.openai_compatible.model == "gpt-backup"
    assert applied.intent.backup_translation.local_llm.model == "backup-local-model"


def test_local_form_model_pick_commits_edit(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._pick_local_llm_model("qwen-local")

    assert view._provider_draft.local_llm_model == "qwen-local"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.local_llm.model == "qwen-local"


def test_searchable_modal_filters_options() -> None:
    from puripuly_heart.app.ports.ui_models import OptionItem
    from puripuly_heart.ui.components.settings.settings_modal import SettingsModal

    modal = SettingsModal(
        None,
        "t",
        [
            OptionItem(value="deepseek-chat", label="deepseek-chat"),
            OptionItem(value="gpt-test", label="gpt-test"),
        ],
        lambda _value: None,
        searchable=True,
    )

    modal._search_query = "deep"
    assert [option.value for option in modal._filtered_options()] == ["deepseek-chat"]
    modal._search_query = ""
    assert [option.value for option in modal._filtered_options()] == [
        "deepseek-chat",
        "gpt-test",
    ]
