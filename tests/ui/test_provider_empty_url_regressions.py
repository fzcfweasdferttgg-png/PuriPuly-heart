from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from puripuly_heart.app.wiring.wiring_llm_factory import user_translation_intent_from_vnext
from puripuly_heart.app.wiring.wiring_translation_variants import (
    build_backup_provider,
    build_variant_provider,
)
from puripuly_heart.config.provider_slots import ProviderCardSlots, ProviderSlotConfig
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.core.storage.secrets import InMemorySecretStore
from tests.helpers.provider_settings import attach_provider_windows, create_settings_view
from tests.helpers.settings_view import apply_provider_intent


def _view(monkeypatch, settings_events, tmp_path: Path, settings=None):
    view = create_settings_view(monkeypatch)
    settings_events.load(
        view,
        settings or AppSettingsVNext(),
        config_path=tmp_path / "settings.json",
    )
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    attach_provider_windows(view)
    return view


@pytest.mark.parametrize("card", ["translation", "backup"])
@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_form_slot_accept_preserves_new_selected_preset(
    monkeypatch, settings_events, tmp_path: Path, card: str, protocol: str
) -> None:
    view = _view(monkeypatch, settings_events, tmp_path)
    if protocol == "openai":
        variant = TranslationModel.OPENAI_COMPATIBLE
        provider_key = "deepseek"
        base_url = "https://api.deepseek.com/v1"
    else:
        variant = TranslationModel.ANTHROPIC
        provider_key = "openrouter"
        base_url = "https://openrouter.ai/api/anthropic"
    if card == "backup":
        view._on_backup_variant_selected(variant.value)
        view._on_backup_gear_click(None)
    else:
        view._on_llm_selected(variant.value)
        {
            TranslationModel.OPENAI_COMPATIBLE: view._on_openai_gear_click,
            TranslationModel.ANTHROPIC: view._on_anthropic_gear_click,
        }[variant](None)
    view._provider_slots_state[card] = ProviderCardSlots(
        active=2,
        slots=(
            None,
            ProviderSlotConfig(
                variant=variant.value,
                provider_key=provider_key,
                base_url=base_url,
                model="slot-model",
            ),
            None,
        ),
    )

    view._on_form_slot_selected(card, 2)
    assert view._accept_provider_settings_dialog(card) is True

    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    if card == "translation":
        intent = (
            applied.intent.openai_compatible if protocol == "openai" else applied.intent.anthropic
        )
        dropdown = view._openai_provider if protocol == "openai" else view._anthropic_provider
    else:
        backup = applied.intent.backup_translation
        intent = backup.openai_compatible if protocol == "openai" else backup.anthropic
        dropdown = (
            view._backup_provider if protocol == "openai" else view._backup_anthropic_provider
        )
    assert (intent.provider_key, intent.base_url, intent.model) == (
        provider_key,
        base_url,
        "slot-model",
    )
    assert dropdown.value == provider_key


@pytest.mark.parametrize("card", ["translation", "backup"])
def test_form_slot_accept_resolves_empty_url_for_selected_preset(
    monkeypatch, settings_events, tmp_path: Path, card: str
) -> None:
    view = _view(monkeypatch, settings_events, tmp_path)
    if card == "backup":
        view._on_backup_variant_selected(TranslationModel.OPENAI_COMPATIBLE.value)
        view._on_backup_gear_click(None)
    else:
        view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
        view._on_openai_gear_click(None)
    view._provider_slots_state[card] = ProviderCardSlots(
        active=2,
        slots=(
            None,
            ProviderSlotConfig(
                variant=TranslationModel.OPENAI_COMPATIBLE.value,
                provider_key="deepseek",
                base_url="",
                model="slot-model",
            ),
            None,
        ),
    )

    view._on_form_slot_selected(card, 2)
    assert view._accept_provider_settings_dialog(card) is True

    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    intent = (
        applied.intent.openai_compatible
        if card == "translation"
        else applied.intent.backup_translation.openai_compatible
    )
    assert intent.provider_key == "deepseek"
    assert intent.base_url == "https://api.deepseek.com/v1"


@pytest.mark.parametrize("card", ["translation", "backup"])
@pytest.mark.parametrize("quick", [False, True])
def test_local_empty_url_slot_uses_its_own_preset(
    monkeypatch, settings_events, tmp_path: Path, card: str, quick: bool
) -> None:
    view = _view(monkeypatch, settings_events, tmp_path)
    view._provider_slots_state[card] = ProviderCardSlots(
        active=2,
        slots=(
            None,
            ProviderSlotConfig(
                variant=TranslationModel.LOCAL_LLM.value,
                provider_key="ollama",
                backend="ollama",
                base_url="",
                model="slot-model",
                extra_body_json="{}",
            ),
            None,
        ),
    )

    if quick:
        view._on_quick_slot_selected(card, 2)
    else:
        if card == "backup":
            view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
            view._on_backup_gear_click(None)
        else:
            view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
            view._on_local_llm_gear_click(None)
        view._on_form_slot_selected(card, 2)
        assert view._accept_provider_settings_dialog(card) is True

    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    intent = (
        applied.intent.local_llm
        if card == "translation"
        else applied.intent.backup_translation.local_llm
    )
    assert (intent.provider_key, intent.backend, intent.base_url, intent.model) == (
        "ollama",
        "ollama",
        "http://127.0.0.1:11434",
        "slot-model",
    )


CASES = {
    "openai_compatible": ("deepseek", "openai_compatible_model", "backup_model"),
    "anthropic": ("openrouter", "anthropic_model", "backup_anthropic_model"),
    "local_llm": ("ollama", "local_llm_model", "backup_local_model"),
}


def _settings_with(variant: str, backup: bool, blank_url: str, provider_key=None):
    settings = AppSettingsVNext()
    parent = settings.intent.backup_translation if backup else settings.intent
    config = replace(
        getattr(parent, variant),
        provider_key=CASES[variant][0] if provider_key is None else provider_key,
        base_url=blank_url,
        model="saved-model",
        **({"backend": "ollama"} if variant == "local_llm" else {}),
    )
    if backup:
        parent = replace(parent, enabled=True, model=variant, **{variant: config})
        return replace(settings, intent=replace(settings.intent, backup_translation=parent))
    return replace(settings, intent=replace(settings.intent, **{variant: config}))


@pytest.mark.parametrize("variant", CASES)
@pytest.mark.parametrize("backup", [False, True])
@pytest.mark.parametrize("blank_url", ["", "   "])
def test_reselect_with_preset_resolved_blank_url_preserves_applied_model(
    monkeypatch, settings_events, tmp_path: Path, variant: str, backup: bool, blank_url: str
) -> None:
    settings = _settings_with(variant, backup, blank_url)
    view = _view(monkeypatch, settings_events, tmp_path, settings)
    select = {
        ("openai_compatible", False): view._on_openai_provider_selected,
        ("openai_compatible", True): view._on_backup_provider_selected,
        ("anthropic", False): view._on_anthropic_provider_selected,
        ("anthropic", True): view._on_backup_anthropic_provider_selected,
        ("local_llm", False): view._on_local_llm_provider_selected,
        ("local_llm", True): view._on_backup_local_provider_selected,
    }[variant, backup]

    select(CASES[variant][0])

    current = view._provider_draft or view._provider_snapshot
    assert current is not None
    assert getattr(current, CASES[variant][2 if backup else 1]) == "saved-model"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings, pending)
    runtime = user_translation_intent_from_vnext(applied)
    if backup:
        provider = build_backup_provider(runtime.backup, secrets=InMemorySecretStore())
    else:
        provider = build_variant_provider(
            variant,
            local_llm=runtime.local_llm,
            openai_compatible=runtime.openai_compatible,
            anthropic=runtime.anthropic,
            secrets=InMemorySecretStore(),
        )
    assert provider is not None
    assert provider.model == "saved-model"


@pytest.mark.parametrize("backup", [False, True])
def test_blur_after_empty_url_autofill_preserves_model_without_preset_key(
    monkeypatch, settings_events, tmp_path: Path, backup: bool
) -> None:
    settings = _settings_with("local_llm", backup, "", provider_key="")
    view = _view(monkeypatch, settings_events, tmp_path, settings)
    if backup:
        view._on_backup_local_base_url_change_end(None)
        current = view._provider_draft or view._provider_snapshot
        assert current.backup_local_model == "saved-model"
    else:
        view._on_local_llm_base_url_change_end(None)
        current = view._provider_draft or view._provider_snapshot
        assert current.local_llm_model == "saved-model"


def test_rename_preset_with_empty_url_and_unchanged_backend_keeps_model(
    monkeypatch, settings_events, tmp_path: Path
) -> None:
    from puripuly_heart.config.providers_registry import ProviderPreset

    settings = _settings_with("local_llm", False, "", provider_key="koboldcpp")
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            local_llm=replace(settings.intent.local_llm, backend="koboldcpp"),
        ),
    )
    view = _view(monkeypatch, settings_events, tmp_path, settings)
    view._local_llm_provider.value = "koboldcpp"
    preset = ProviderPreset(
        key="koboldcpp",
        label="KoboldCpp renamed",
        base_url="http://127.0.0.1:5001/v1",
        backend="koboldcpp",
    )
    monkeypatch.setattr(
        "puripuly_heart.config.providers_registry.load_provider_presets",
        lambda protocol="openai": {"koboldcpp": preset} if protocol == "local" else {},
    )

    view._on_provider_presets_changed("local", "edit", "koboldcpp")

    current = view._provider_draft or view._provider_snapshot
    assert current.local_llm_model == "saved-model"
    assert view._local_llm_model.value == "saved-model"


@pytest.mark.parametrize("card", ["translation", "backup"])
def test_model_hint_and_validation_follow_selected_backend(
    monkeypatch, settings_events, tmp_path: Path, card: str
) -> None:
    from puripuly_heart.ui.views.settings import t

    view = _view(monkeypatch, settings_events, tmp_path)
    if card == "backup":
        view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
        select = view._on_backup_local_provider_selected
        model = view._backup_local_model
    else:
        view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
        select = view._on_local_llm_provider_selected
        model = view._local_llm_model

    select("ollama")
    assert model.hint_text == t("settings.local_llm.model_hint.required")
    assert view._validate_and_commit_local_form(card) is False
    assert model.error == t("settings.local_llm.model.required")

    select("koboldcpp")
    assert model.hint_text == t("settings.local_llm.model_hint.optional")
    assert view._validate_and_commit_local_form(card) is True


@pytest.mark.parametrize("card", ["translation", "backup"])
def test_colibri_unsupported_extra_body_is_rejected_in_form(
    monkeypatch, settings_events, tmp_path: Path, card: str
) -> None:
    view = _view(monkeypatch, settings_events, tmp_path)
    if card == "backup":
        view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
        view._on_backup_local_provider_selected("colibri")
        field = view._backup_local_extra_body
        error = view._backup_local_extra_body_error
        commit = view._on_backup_local_extra_body_change_end
    else:
        view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
        view._on_local_llm_provider_selected("colibri")
        field = view._local_llm_extra_body
        error = view._local_llm_extra_body_error
        commit = view._on_local_llm_extra_body_change_end
    field.value = '{"frequency_penalty": 0.2}'

    commit(None)

    assert error.visible is True
    assert "frequency_penalty" in error.value
