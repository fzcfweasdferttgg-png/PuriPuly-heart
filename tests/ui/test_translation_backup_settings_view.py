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
    assert view._backup_translation_text.content.value == settings_view.t(
        "settings.translation_variant.openai_compatible"
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
    assert view._backup_translation_text.content.value == settings_view.t("settings.not_selected")


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
        TranslationModel.ANTHROPIC.value,
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
        TranslationModel.ANTHROPIC.value,
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

    view._set_unit_card_value_text(view._backup_translation_text, "Не выбрано")
    assert view._backup_translation_text.content.size == 28

    view._set_unit_card_value_text(view._backup_translation_text, "Иные сервера")
    assert view._backup_translation_text.content.size == 28

    view._set_unit_card_value_text(view._backup_translation_text, "OpenAI-совместимые провайдеры")
    assert view._backup_translation_text.content.size == 21


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
    assert view._openai_compatible_card.visible is True

    view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
    assert view._backup_config_card.visible is True
    assert view._backup_local_group.visible is True
    assert len(flushed) >= 0


def test_reselecting_the_current_variant_still_shows_its_form(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._openai_compatible_card.visible = False

    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)

    assert view._openai_compatible_card.visible is True


def test_translation_cards_share_the_same_value_text_shape(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    llm_text = view._llm_text.content
    backup_text = view._backup_translation_text.content
    assert llm_text.no_wrap == backup_text.no_wrap is False
    assert llm_text.max_lines == backup_text.max_lines == 2
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

    primary = view._openai_form.controls
    backup = view._backup_network_group.controls
    assert [type(control) for control in primary] == [type(control) for control in backup]
    assert [type(child) for child in primary[3].controls] == [
        type(child) for child in backup[3].controls
    ]


def test_local_form_layout_is_identical_for_translation_and_backup(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    primary = view._local_llm_form.controls
    backup = view._backup_local_group.controls
    assert [type(control) for control in primary] == [type(control) for control in backup]


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

    assert _json.loads(view._provider_draft.backup_local_extra_body_json) == {"temperature": 0.2}
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert BackupLocalLlmExtraBodyEdit in _edit_types(pending)


def test_backup_value_label_localizes_on_load_and_locale_apply(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    from puripuly_heart.ui import i18n as i18n_module

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    previous = i18n_module.get_locale()
    try:
        i18n_module.set_locale("ru")
        view.apply_locale()
        assert view._backup_translation_text.content.value == "Не выбрано"
        settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
        assert view._backup_translation_text.content.value == "Не выбрано"
    finally:
        i18n_module.set_locale(previous)


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


def test_local_form_defaults_are_empty_and_errors_can_wrap(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    assert view._local_llm_base_url.value == ""
    assert view._local_llm_model.value == ""
    assert view._local_llm_base_url.error_max_lines == 4
    assert view._backup_local_base_url.error_max_lines == 4
    assert view._openai_base_url.error_max_lines == 4


def test_local_forms_keep_buttons_at_top_and_content_scrollable(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    import flet as ft

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    for form in (view._local_llm_form, view._backup_local_group):
        assert form.scroll == ft.ScrollMode.AUTO
        rows = [control for control in form.controls if type(control) is ft.Row]
        assert len(rows) == 4
        assert all(row.vertical_alignment == ft.CrossAxisAlignment.START for row in rows[2:])


def test_api_key_optional_explanation_is_not_duplicated(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    from puripuly_heart.ui import i18n as i18n_module

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    previous = i18n_module.get_locale()
    try:
        i18n_module.set_locale("ru")
        view.apply_locale()
        assert view._local_llm_api_key._text_field.label == "API-ключ сервера"
        assert "необязательно" in view._local_llm_api_key_helper.value.lower()
    finally:
        i18n_module.set_locale(previous)


def test_url_validation_message_is_short(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    from puripuly_heart.ui import i18n as i18n_module

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    previous = i18n_module.get_locale()
    try:
        i18n_module.set_locale("ru")
        view.apply_locale()
        view._local_llm_base_url.value = "not-a-url"
        view._on_local_llm_base_url_change_end(None)
        assert view._local_llm_base_url.error == "Введите URL"
    finally:
        i18n_module.set_locale(previous)


def test_action_buttons_are_centered_in_fixed_height_containers(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    import flet as ft

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    for form in (view._local_llm_form, view._backup_local_group):
        rows = [control for control in form.controls if type(control) is ft.Row]
        assert len(rows) == 4
        for row in rows[2:]:
            action = row.controls[1]
            assert isinstance(action, ft.Container)
            assert action.height == 64
            assert action.alignment == ft.Alignment.CENTER


def test_other_servers_variant_uses_renamed_picker_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view(monkeypatch)

    option = next(
        option
        for option in view._translation_picker_options()
        if option.value == TranslationModel.LOCAL_LLM.value
    )

    assert option.value == TranslationModel.LOCAL_LLM.value

    from puripuly_heart.ui.i18n import t_for_locale

    assert t_for_locale("ru", "settings.translation_variant.local_llm") == "Иные сервера"
    assert t_for_locale("en", "settings.translation_variant.local_llm") == "Other servers"
    assert t_for_locale("ru", "settings.translation_variant.local_llm.description") == (
        "Поддерживаемый серверный API: адрес, ключ, список моделей"
    )


def test_provider_preset_changes_update_primary_and_backup_forms(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    import json

    from puripuly_heart.config import providers_registry

    path = tmp_path / "providers.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "providers": {
                    "first": {"label": "First", "base_url": "http://first.example/v1"},
                    "second": {"label": "Second", "base_url": "http://second.example/v1"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(providers_registry, "ensure_providers_file", lambda protocol="openai": path)
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_openai_provider_selected("first")
    view._on_backup_provider_selected("first")

    added = providers_registry.add_provider_preset("Third", "http://third.example/v1")
    view._on_provider_presets_changed("translation", "add", added.key)

    assert view._provider_draft.openai_compatible_provider_key == added.key
    assert view._provider_draft.backup_provider_key == "first"
    assert len(view._openai_provider.options) == 3
    assert len(view._backup_provider.options) == 3

    view._on_backup_provider_selected(added.key)
    view._commit_openai_field("model", "primary-model")
    view._commit_backup_field("model", "backup-model")
    providers_registry.update_provider_preset(added.key, "Third renamed", "http://third.example/v1")
    view._on_provider_presets_changed("translation", "edit", added.key)

    assert view._provider_draft.openai_compatible_model == "primary-model"
    assert view._provider_draft.backup_model == "backup-model"
    assert [option.text for option in view._openai_provider.options][-1] == "Third renamed"

    providers_registry.update_provider_preset(added.key, "Third renamed", "https://new.example/v1")
    view._on_provider_presets_changed("translation", "edit", added.key)

    assert view._provider_draft.openai_compatible_base_url == "https://new.example/v1"
    assert view._provider_draft.backup_base_url == "https://new.example/v1"
    assert view._provider_draft.openai_compatible_model == ""
    assert view._provider_draft.backup_model == ""

    providers_registry.delete_provider_preset(added.key)
    view._on_provider_presets_changed("translation", "delete", added.key)

    assert view._provider_draft.openai_compatible_provider_key == "first"
    assert view._provider_draft.backup_provider_key == "first"
    assert view._provider_draft.openai_compatible_base_url == "http://first.example/v1"
    assert view._provider_draft.backup_base_url == "http://first.example/v1"
    assert len(view._openai_provider.options) == 2
    assert len(view._backup_provider.options) == 2


def test_provider_preset_buttons_open_add_edit_and_delete_modals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types

    view = _view(monkeypatch)
    opened = []

    class Modal:
        def __init__(self, page, on_changed, *, protocol="openai") -> None:
            self.page = page
            self.on_changed = on_changed
            self.protocol = protocol

        def open_add(self) -> None:
            opened.append(("add", self.page))

        def open_edit(self, key: str) -> None:
            opened.append(("edit", key))

        def open_delete(self, key: str) -> None:
            opened.append(("delete", key))

    monkeypatch.setattr(settings_view, "ProviderPresetModal", Modal)
    event = types.SimpleNamespace(control=types.SimpleNamespace(page=object()))

    view._openai_preset_add.on_click(event)
    view._openai_preset_edit.on_click(event)
    view._backup_preset_delete.on_click(event)

    assert opened[0][0] == "add"
    assert opened[1] == ("edit", view._openai_provider.value)
    assert opened[2] == ("delete", view._backup_provider.value)


def test_anthropic_preset_changes_update_primary_and_backup_forms(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    import json

    from puripuly_heart.config import providers_registry

    path = tmp_path / "anthropic_providers.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "providers": {
                    "anthropic": {"label": "Anthropic", "base_url": "https://api.anthropic.com"},
                    "openrouter": {
                        "label": "OpenRouter",
                        "base_url": "https://openrouter.ai/api/anthropic",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(providers_registry, "ensure_providers_file", lambda protocol="openai": path)
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_anthropic_provider_selected("anthropic")
    view._on_backup_anthropic_provider_selected("anthropic")

    added = providers_registry.add_provider_preset(
        "Custom Claude", "http://127.0.0.1:5000", protocol="anthropic"
    )
    view._on_provider_presets_changed("anthropic", "add", added.key)

    assert view._provider_draft.anthropic_provider_key == added.key
    assert view._provider_draft.backup_anthropic_provider_key == "anthropic"
    assert view._provider_draft.anthropic_base_url == "http://127.0.0.1:5000"

    view._on_backup_anthropic_provider_selected(added.key)
    view._commit_anthropic_field("model", "primary-claude")
    view._commit_backup_anthropic_field("model", "backup-claude")
    providers_registry.update_provider_preset(
        added.key, "Custom Claude edited", "http://127.0.0.1:5000", protocol="anthropic"
    )
    view._on_provider_presets_changed("anthropic", "edit", added.key)

    assert view._provider_draft.anthropic_model == "primary-claude"
    assert view._provider_draft.backup_anthropic_model == "backup-claude"

    providers_registry.update_provider_preset(
        added.key, "Custom Claude edited", "https://new.example/anthropic", protocol="anthropic"
    )
    view._on_provider_presets_changed("anthropic", "edit", added.key)

    assert view._provider_draft.anthropic_base_url == "https://new.example/anthropic"
    assert view._provider_draft.backup_anthropic_base_url == "https://new.example/anthropic"
    assert view._provider_draft.anthropic_model == ""
    assert view._provider_draft.backup_anthropic_model == ""

    providers_registry.delete_provider_preset(added.key, protocol="anthropic")
    view._on_provider_presets_changed("anthropic", "delete", added.key)

    assert view._provider_draft.anthropic_provider_key == "anthropic"
    assert view._provider_draft.backup_anthropic_provider_key == "anthropic"
    assert view._provider_draft.anthropic_base_url == "https://api.anthropic.com"
    assert view._provider_draft.backup_anthropic_base_url == "https://api.anthropic.com"


def test_local_preset_crud_updates_both_forms_and_preserves_namesake_models(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    import json

    from puripuly_heart.config import providers_registry

    path = tmp_path / "local_providers.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "providers": {
                    "ollama": {
                        "label": "Ollama",
                        "base_url": "http://127.0.0.1:11434",
                    },
                    "llamacpp": {
                        "label": "llama.cpp",
                        "base_url": "http://127.0.0.1:8080/v1",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    original_ensure = providers_registry.ensure_providers_file
    monkeypatch.setattr(
        providers_registry,
        "ensure_providers_file",
        lambda protocol="openai": path if protocol == "local" else original_ensure(protocol),
    )
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_local_llm_provider_selected("ollama")
    view._on_backup_local_provider_selected("ollama")

    added = providers_registry.add_provider_preset(
        "Custom llama", "http://127.0.0.1:8081/v1", protocol="local"
    )
    view._on_provider_presets_changed("local", "add", added.key)

    assert view._provider_draft.local_llm_provider_key == added.key
    assert view._provider_draft.backup_local_provider_key == "ollama"
    assert view._provider_draft.local_llm_base_url == "http://127.0.0.1:8081/v1"

    view._on_backup_local_provider_selected(added.key)
    view._commit_local_field("model", "primary-model")
    view._commit_backup_field("local_model", "backup-model")
    providers_registry.update_provider_preset(
        added.key, "Custom llama renamed", "http://127.0.0.1:8081/v1", protocol="local"
    )
    view._on_provider_presets_changed("local", "edit", added.key)

    assert view._provider_draft.local_llm_model == "primary-model"
    assert view._provider_draft.backup_local_model == "backup-model"

    providers_registry.update_provider_preset(
        added.key, "Custom llama renamed", "http://127.0.0.1:8082/v1", protocol="local"
    )
    view._on_provider_presets_changed("local", "edit", added.key)

    assert view._provider_draft.local_llm_base_url == "http://127.0.0.1:8082/v1"
    assert view._provider_draft.backup_local_base_url == "http://127.0.0.1:8082/v1"
    assert view._provider_draft.local_llm_model == ""
    assert view._provider_draft.backup_local_model == ""

    providers_registry.delete_provider_preset(added.key, protocol="local")
    view._on_provider_presets_changed("local", "delete", added.key)

    assert view._provider_draft.local_llm_provider_key == "ollama"
    assert view._provider_draft.backup_local_provider_key == "ollama"
    assert view._provider_draft.local_llm_base_url == "http://127.0.0.1:11434"
    assert view._provider_draft.backup_local_base_url == "http://127.0.0.1:11434"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.local_llm.provider_key == "ollama"
    assert applied.intent.backup_translation.local_llm.provider_key == "ollama"

    view._local_llm_base_url.value = "http://10.0.0.8:9999/v1"
    view._on_local_llm_base_url_change_end(None)

    assert view._provider_draft.local_llm_provider_key == ""
    assert view._local_llm_provider.value is None
