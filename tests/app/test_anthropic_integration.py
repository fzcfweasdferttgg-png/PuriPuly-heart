from __future__ import annotations

import copy
from pathlib import Path

import pytest

from puripuly_heart.app.ports.settings_view import (
    AnthropicBaseUrlEdit,
    AnthropicModelEdit,
    BackupAnthropicBaseUrlEdit,
    BackupAnthropicModelEdit,
    BackupTranslationEnabledEdit,
    BackupVariantEdit,
    ProviderApplyIntent,
    TranslationSelectionEdit,
    TranslationSelectionSnapshot,
)
from puripuly_heart.app.services.settings.settings_application import (
    materialize_provider_apply_intent,
)
from puripuly_heart.app.wiring.wiring_llm_factory import user_translation_intent_from_vnext
from puripuly_heart.app.wiring.wiring_provider_runtime_policy import (
    build_llm_provider_signature,
)
from puripuly_heart.app.wiring.wiring_translation_variants import (
    build_backup_provider,
    build_variant_provider,
)
from puripuly_heart.composition.application_runtime import (
    _copy_provider_prompt_apply_fields,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.core.storage.secrets import InMemorySecretStore
from puripuly_heart.core.translation_backend import TranslationVariantConfigurationError
from puripuly_heart.providers.llm.anthropic import AnthropicLLMProvider
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import SettingsIntentRecorder


@pytest.fixture
def settings_events() -> SettingsIntentRecorder:
    return SettingsIntentRecorder()


def _selection(model: str) -> TranslationSelectionEdit:
    return TranslationSelectionEdit(
        TranslationSelectionSnapshot(
            model=model,
            connection="ollama",
            connection_history=(),
            http_extension_id=None,
            previous_llm_model=None,
            gpu_device_id="auto",
        ),
        (),
    )


def _anthropic_settings() -> AppSettingsVNext:
    return materialize_provider_apply_intent(
        AppSettingsVNext(),
        ProviderApplyIntent(
            (
                _selection("anthropic"),
                AnthropicBaseUrlEdit("https://anthropic.example.com"),
                AnthropicModelEdit("claude-main"),
                BackupTranslationEnabledEdit(True),
                BackupVariantEdit("anthropic"),
                BackupAnthropicBaseUrlEdit("https://backup-anthropic.example.com"),
                BackupAnthropicModelEdit("claude-backup"),
            )
        ),
        materialize_translation=lambda settings: settings,
    )


def test_anthropic_intent_survives_materialize_merge_and_runtime_projection() -> None:
    settings = _anthropic_settings()

    merged = _copy_provider_prompt_apply_fields(settings, copy.deepcopy(AppSettingsVNext()))
    runtime = user_translation_intent_from_vnext(merged)

    assert merged.intent.translation.model == "anthropic"
    assert merged.intent.anthropic.base_url == "https://anthropic.example.com"
    assert merged.intent.anthropic.model == "claude-main"
    assert runtime.backup.anthropic.base_url == "https://backup-anthropic.example.com"
    assert runtime.backup.anthropic.model == "claude-backup"


def test_anthropic_primary_and_backup_use_separate_sdk_providers_and_secrets() -> None:
    runtime = user_translation_intent_from_vnext(_anthropic_settings())
    secrets = InMemorySecretStore()
    secrets.set("anthropic_api_key", "primary-key")
    secrets.set("backup_anthropic_api_key", "backup-key")

    primary = build_variant_provider(
        "anthropic",
        local_llm=runtime.local_llm,
        openai_compatible=runtime.openai_compatible,
        anthropic=runtime.anthropic,
        secrets=secrets,
    )
    backup = build_backup_provider(runtime.backup, secrets=secrets)

    assert isinstance(primary, AnthropicLLMProvider)
    assert isinstance(backup, AnthropicLLMProvider)
    assert primary.api_key == "primary-key"
    assert backup.api_key == "backup-key"
    assert primary.model == "claude-main"
    assert backup.model == "claude-backup"


def test_anthropic_signature_tracks_primary_and_enabled_backup_fields() -> None:
    settings = _anthropic_settings()
    initial = build_llm_provider_signature(settings)

    primary_changed = materialize_provider_apply_intent(
        settings,
        ProviderApplyIntent((AnthropicModelEdit("claude-next"),)),
        materialize_translation=lambda current: current,
    )
    assert build_llm_provider_signature(primary_changed) != initial

    backup_changed = materialize_provider_apply_intent(
        settings,
        ProviderApplyIntent((BackupAnthropicModelEdit("claude-backup-next"),)),
        materialize_translation=lambda current: current,
    )
    assert build_llm_provider_signature(backup_changed) != initial

    backup_disabled = materialize_provider_apply_intent(
        settings,
        ProviderApplyIntent((BackupTranslationEnabledEdit(False),)),
        materialize_translation=lambda current: current,
    )
    disabled_changed = materialize_provider_apply_intent(
        backup_disabled,
        ProviderApplyIntent((BackupAnthropicModelEdit("ignored-while-disabled"),)),
        materialize_translation=lambda current: current,
    )
    assert build_llm_provider_signature(disabled_changed) == build_llm_provider_signature(
        backup_disabled
    )


def test_unconfigured_anthropic_variant_uses_expected_configuration_error() -> None:
    from puripuly_heart.app.wiring.wiring_llm_factory import runtime_resolution_input_from_vnext
    from puripuly_heart.app.wiring.wiring_translation_backend import create_translation_backend
    from puripuly_heart.core.http_extensions import HttpExtensionRegistry

    settings = materialize_provider_apply_intent(
        AppSettingsVNext(),
        ProviderApplyIntent((_selection("anthropic"), AnthropicModelEdit(""))),
        materialize_translation=lambda current: current,
    )
    with pytest.raises(TranslationVariantConfigurationError):
        create_translation_backend(
            translation_model="anthropic",
            secrets=InMemorySecretStore(),
            http_extensions=HttpExtensionRegistry(Path("unused")),
            runtime_input=runtime_resolution_input_from_vnext(settings),
        )


class _Window:
    def __init__(self) -> None:
        self.is_open = False
        self.state = {}

    def open(self, _schedule, **kwargs) -> None:
        self.state.update(kwargs)
        self.is_open = True

    def replace_content(self, content, *, title: str, height: int) -> None:
        self.state.update(content=content, title=title, height=height)

    def close(self) -> None:
        self.is_open = False


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


def test_anthropic_window_remains_independent_from_the_openai_window(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    from puripuly_heart.ui.i18n import t

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    anthropic_window = _Window()
    openai_window = _Window()
    view.attach_provider_settings_window(
        {
            "translation_anthropic": anthropic_window,
            "translation_openai": openai_window,
        },
        lambda coro: coro(),
    )

    view._on_llm_selected(TranslationModel.ANTHROPIC.value)
    view._on_anthropic_gear_click(None)
    anthropic_content = anthropic_window.state["content"]
    assert anthropic_content is view._anthropic_form
    assert anthropic_window.state["height"] == 450
    assert anthropic_window.state["title"].endswith(t("settings.translation_variant.anthropic"))

    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    assert anthropic_window.state["content"] is anthropic_content
    assert anthropic_window.state["title"].endswith(t("settings.translation_variant.anthropic"))

    view._on_openai_gear_click(None)
    assert anthropic_window.is_open is False
    assert openai_window.state["content"] is view._openai_form

    view._on_llm_selected(TranslationModel.ANTHROPIC.value)
    assert view._anthropic_compatible_card.visible is True
    assert view._openai_compatible_card.visible is False


def test_anthropic_slot_roundtrip_uses_its_own_controls(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    from puripuly_heart.config.provider_slots import ProviderSlotConfig

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    window = _Window()
    view.attach_provider_settings_window(
        {"translation_anthropic": window, "backup_anthropic": _Window()}, lambda coro: coro
    )
    view._on_llm_selected(TranslationModel.ANTHROPIC.value)
    view._on_anthropic_gear_click(None)
    view._anthropic_base_url.value = "https://slot-anthropic.example.com"
    view._anthropic_model.value = "claude-slot"

    view._save_dialog_slot("translation")
    state = view._provider_slots_state["translation"]
    assert state.slots[0] == ProviderSlotConfig(
        variant="anthropic",
        base_url="https://slot-anthropic.example.com",
        model="claude-slot",
    )

    view._openai_base_url.value = ""
    view._openai_model.value = ""
    view._apply_provider_slot_to_controls("translation", state.slots[0])
    assert view._anthropic_base_url.value == "https://slot-anthropic.example.com"
    assert view._anthropic_model.value == "claude-slot"
    assert view._openai_model.value == ""
