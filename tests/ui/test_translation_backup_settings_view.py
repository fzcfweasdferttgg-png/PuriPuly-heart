from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.ports.settings_view import (
    BackupLocalLlmBaseUrlEdit,
    BackupLocalLlmExtraBodyEdit,
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
from puripuly_heart.config.translation_values import TranslationModel
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
        == settings_view.t("settings.translation_variant.openai_compatible")
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

    view._openai_model.value = "stale-model"

    view._on_openai_provider_selected("deepseek")

    assert view._provider_draft.openai_compatible_provider_key == "deepseek"
    assert view._provider_draft.openai_compatible_base_url == "https://api.deepseek.com/v1"
    assert view._openai_base_url.value == "https://api.deepseek.com/v1"
    assert view._openai_model.value == ""
    assert view._openai_provider.value == "deepseek"


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


def test_translation_picker_options_are_flat_and_distinct(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    options = view._translation_picker_options()

    assert [option.value for option in options] == [
        TranslationModel.NONE.value,
        TranslationModel.OPENAI_COMPATIBLE.value,
        TranslationModel.LOCAL_LLM.value,
    ]
    assert all(not option.section for option in options)
    labels = [option.label for option in options]
    assert len(set(labels)) == len(labels)
    assert all(option.description for option in options[1:])


def test_backup_picker_options_are_flat_and_distinct(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    options = view._backup_picker_options()

    assert [option.value for option in options] == [
        "off",
        TranslationModel.OPENAI_COMPATIBLE.value,
        TranslationModel.LOCAL_LLM.value,
    ]
    assert all(not option.section for option in options)
    labels = [option.label for option in options]
    assert len(set(labels)) == len(labels)


def test_variant_forms_follow_the_selected_model(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    assert view._local_llm_connection_card.visible is True
    assert view._openai_compatible_card.visible is False

    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    assert view._openai_compatible_card.visible is True
    assert view._local_llm_connection_card.visible is False

    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    assert view._local_llm_connection_card.visible is True
    assert view._openai_compatible_card.visible is False


def test_value_text_scales_down_for_long_labels(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._set_unit_card_value_text(view._backup_translation_text, "Выключено")
    assert view._backup_translation_text.content.size == 28

    view._set_unit_card_value_text(view._backup_translation_text, "Свой сервер")
    assert view._backup_translation_text.content.size == 22

    view._set_unit_card_value_text(view._backup_translation_text, "OpenAI-совместимые провайдеры")
    assert view._backup_translation_text.content.size == 16


def test_variant_selection_flushes_form_updates(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    flushed: list[object] = []
    monkeypatch.setattr(
        settings_view, "_update_control_if_mounted", lambda control: flushed.append(control)
    )

    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    assert view._openai_compatible_card in flushed

    view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
    assert view._backup_config_card in flushed
    assert view._backup_local_group in flushed


def test_reselecting_the_current_variant_still_shows_its_form(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._openai_compatible_card.visible = False

    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)

    assert view._openai_compatible_card.visible is True


def test_model_assets_card_does_not_stretch(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    assert view._model_assets_card.expand is False
    assert view._model_assets_card.width == 280


def test_translation_cards_share_the_same_value_text_shape(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    llm_text = view._llm_text.content
    backup_text = view._backup_translation_text.content
    assert llm_text.no_wrap == backup_text.no_wrap is True
    assert llm_text.max_lines == backup_text.max_lines == 1
    assert llm_text.overflow == backup_text.overflow


def test_apply_locale_localizes_backup_and_openai_surfaces(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    from puripuly_heart.ui import i18n as i18n_module

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    previous = i18n_module.get_locale()
    try:
        i18n_module.set_locale("ru")
        view.apply_locale()
        assert view._backup_translation_title.value == "Резервный перевод"
        assert view._openai_compatible_title.value == "Настройки перевода"
        assert view._local_llm_connection_title.value == "Настройки перевода"
        assert view._backup_config_title.value == "Настройки резервного перевода"
        assert view._openai_provider.label == "Провайдер"
        assert view._openai_fetch_btn.tooltip == "Получить список моделей"
        assert view._openai_test_btn.content.value == "Проверить соединение"
        assert view._backup_local_extra_body.label == "JSON extra body"
    finally:
        i18n_module.set_locale(previous)


def test_openai_form_layout_is_identical_for_translation_and_backup(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    primary = view._openai_compatible_card.content.content.controls
    backup = view._backup_network_group.controls
    assert [type(control) for control in primary[1:]] == [type(control) for control in backup]
    assert [type(child) for child in primary[3].controls] == [
        type(child) for child in backup[2].controls
    ]


def test_local_form_layout_is_identical_for_translation_and_backup(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    primary = view._local_llm_connection_card.content.content.controls
    backup = view._backup_local_group.controls
    assert [type(control) for control in primary[1:]] == [type(control) for control in backup]


def test_provider_dropdowns_share_the_same_presets(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    from puripuly_heart.config.providers_registry import load_provider_presets

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    expected = list(load_provider_presets().keys())
    assert expected
    assert [option.key for option in view._openai_provider.options] == expected
    assert [option.key for option in view._backup_provider.options] == expected


def test_backup_local_form_commits_extra_body(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    import json as _json

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_backup_variant_selected("local_llm")

    view._backup_local_extra_body.value = '{"temperature": 0.2}'
    view._on_backup_local_extra_body_change_end(None)

    assert _json.loads(view._provider_draft.backup_local_extra_body_json) == {
        "temperature": 0.2
    }
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert BackupLocalLlmExtraBodyEdit in _edit_types(pending)


def test_backup_provider_selection_commits_backup_edits(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_backup_provider_selected("deepseek")

    assert view._provider_draft.backup_provider_key == "deepseek"
    assert view._provider_draft.backup_base_url == "https://api.deepseek.com/v1"
    assert view._backup_provider.value == "deepseek"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert _edit_types(pending) >= {
        BackupOpenAICompatibleProviderKeyEdit,
        BackupOpenAICompatibleBaseUrlEdit,
    }
