from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from puripuly_heart.app.wiring.wiring_llm_factory import user_translation_intent_from_vnext
from puripuly_heart.app.wiring.wiring_translation_variants import (
    build_backup_provider,
    build_variant_provider,
)
from puripuly_heart.config import providers_registry
from puripuly_heart.config.provider_slots import (
    ProviderCardSlots,
    ProviderSlotConfig,
    load_provider_slots,
)
from puripuly_heart.config.settings_vnext.schema import (
    AnthropicIntent,
    AppSettingsVNext,
    LocalLLMIntent,
    OpenAICompatibleIntent,
)
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import apply_provider_intent


class _EmptySecrets:
    def get(self, _key: str) -> str:
        return ""


class _Window:
    is_open = False

    def open(self, _schedule, **_kwargs) -> None:
        return None


@pytest.fixture(autouse=True)
def preset_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    documents = {
        "openai": {
            "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
            "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1"},
        },
        "anthropic": {
            "anthropic": {"label": "Anthropic", "base_url": "https://api.anthropic.com"},
            "openrouter": {
                "label": "OpenRouter",
                "base_url": "https://openrouter.ai/api/anthropic",
            },
        },
        "local": {
            "colibri": {
                "label": "colibri",
                "base_url": "http://127.0.0.1:8000/v1",
                "backend": "colibri",
            },
            "vllm": {
                "label": "vLLM",
                "base_url": "http://127.0.0.1:8000/v1",
                "backend": "vllm",
            },
            "llamacpp": {
                "label": "llama.cpp",
                "base_url": "http://127.0.0.1:8080/v1",
                "backend": "llamacpp",
            },
            "ollama": {
                "label": "Ollama",
                "base_url": "http://127.0.0.1:11434",
                "backend": "ollama",
            },
        },
    }
    paths = {protocol: tmp_path / f"{protocol}_providers.json" for protocol in documents}
    for protocol, providers in documents.items():
        paths[protocol].write_text(
            json.dumps({"version": "1.0", "providers": providers}), encoding="utf-8"
        )
    monkeypatch.setattr(
        providers_registry,
        "ensure_providers_file",
        lambda protocol="openai": paths[protocol],
    )
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    return paths


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


def _attach_windows(view: settings_view.SettingsView) -> None:
    window = _Window()
    view.attach_provider_settings_window(
        {
            "translation_openai": window,
            "translation_anthropic": window,
            "translation_local": window,
            "backup_openai": window,
            "backup_anthropic": window,
            "backup_local": window,
        },
        lambda coro: coro,
    )


@pytest.mark.parametrize("card", ["translation", "backup"])
def test_local_form_slot_accept_commits_visible_backend(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
    card: str,
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    _attach_windows(view)
    if card == "backup":
        view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
        view._on_backup_local_provider_selected("ollama")
        view._on_backup_gear_click(None)
    else:
        view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
        view._on_local_llm_provider_selected("ollama")
        view._on_local_llm_gear_click(None)
    view._provider_slots_state[card] = ProviderCardSlots(
        active=1,
        slots=(
            None,
            ProviderSlotConfig(
                variant=TranslationModel.LOCAL_LLM.value,
                provider_key="llamacpp",
                backend="llamacpp",
                base_url="http://127.0.0.1:8080/v1",
                model="slot-model",
                extra_body_json='{"temperature":0.2}',
            ),
            None,
        ),
    )

    view._on_form_slot_selected(card, 2)
    assert (
        view._local_llm_provider.value
        if card == "translation"
        else view._backup_local_provider.value
    )
    view._accept_provider_settings_dialog(card)

    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    user = user_translation_intent_from_vnext(applied)
    if card == "backup":
        intent = applied.intent.backup_translation.local_llm
        runtime = build_backup_provider(user.backup, secrets=_EmptySecrets())
        slot = load_provider_slots(tmp_path / "provider_slots.json")[card].slot(2)
    else:
        intent = applied.intent.local_llm
        runtime = build_variant_provider(
            "local_llm",
            local_llm=user.local_llm,
            openai_compatible=user.openai_compatible,
            anthropic=user.anthropic,
            secrets=_EmptySecrets(),
        )
        slot = load_provider_slots(tmp_path / "provider_slots.json")[card].slot(2)
    assert runtime is not None
    actual = (intent.provider_key, intent.backend, intent.base_url, intent.model, runtime.backend)
    expected = ("llamacpp", "llamacpp", "http://127.0.0.1:8080/v1", "slot-model", "llamacpp")
    assert actual == expected
    assert slot is not None and slot.backend == "llamacpp"


@pytest.mark.parametrize("card", ["translation", "backup"])
def test_local_manual_url_change_clears_stale_model(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
    card: str,
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    if card == "backup":
        view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
        view._on_backup_local_provider_selected("llamacpp")
        view._pick_backup_local_model("server-a-model")
        view._backup_local_base_url.value = "http://127.0.0.1:9999/v1"
        view._on_backup_local_base_url_change_end(None)
        actual = (
            view._provider_draft.backup_local_provider_key,
            view._provider_draft.backup_local_model,
            view._backup_local_model.value,
        )
    else:
        view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
        view._on_local_llm_provider_selected("llamacpp")
        view._pick_local_llm_model("server-a-model")
        view._local_llm_base_url.value = "http://127.0.0.1:9999/v1"
        view._on_local_llm_base_url_change_end(None)
        actual = (
            view._provider_draft.local_llm_provider_key,
            view._provider_draft.local_llm_model,
            view._local_llm_model.value,
        )
    assert actual == ("", "", "")


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_manual_cloud_url_clears_preset_selection(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
    protocol: str,
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    if protocol == "anthropic":
        view._on_anthropic_provider_selected("anthropic")
        view._anthropic_base_url.value = "https://custom.example/anthropic"
        view._on_anthropic_base_url_change_end(
            type("Event", (), {"control": view._anthropic_base_url})()
        )
        actual = (view._provider_draft.anthropic_provider_key, view._anthropic_provider.value)
    else:
        view._on_openai_provider_selected("deepseek")
        view._openai_base_url.value = "https://custom.example/v1"
        view._on_openai_base_url_change_end(type("Event", (), {"control": view._openai_base_url})())
        actual = (view._provider_draft.openai_compatible_provider_key, view._openai_provider.value)
    assert actual == ("", None)


def test_custom_cloud_urls_do_not_reload_as_first_presets(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
) -> None:
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            openai_compatible=OpenAICompatibleIntent(
                base_url="https://custom.example/v1", model="custom-model"
            ),
            anthropic=AnthropicIntent(
                base_url="https://custom.example/anthropic", model="custom-claude"
            ),
        ),
    )
    view = _view(monkeypatch)

    settings_events.load(view, settings, config_path=tmp_path / "settings.json")

    assert (view._openai_provider.value, view._anthropic_provider.value) == (None, None)


def test_local_duplicate_url_projection_uses_saved_backend(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
) -> None:
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            local_llm=LocalLLMIntent(
                provider_key="",
                backend="vllm",
                base_url="http://127.0.0.1:8000/v1",
                model="saved-model",
            ),
        ),
    )
    view = _view(monkeypatch)

    settings_events.load(view, settings, config_path=tmp_path / "settings.json")

    assert view._local_llm_provider.value == "vllm"
    assert view._provider_snapshot.local_llm_backend == "vllm"


def test_cross_variant_form_slot_accept_does_not_overwrite_slot(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    _attach_windows(view)
    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._on_openai_provider_selected("deepseek")
    view._on_openai_gear_click(None)
    view._provider_slots_state["translation"] = ProviderCardSlots(
        active=1,
        slots=(
            None,
            ProviderSlotConfig(
                variant=TranslationModel.LOCAL_LLM.value,
                provider_key="llamacpp",
                backend="llamacpp",
                base_url="http://127.0.0.1:8080/v1",
                model="slot-model",
                extra_body_json='{"temperature":0.2}',
            ),
            None,
        ),
    )

    view._on_form_slot_selected("translation", 2)
    view._accept_provider_settings_dialog("translation")

    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    slot = load_provider_slots(tmp_path / "provider_slots.json")["translation"].slot(2)
    assert slot is not None
    actual = (
        applied.intent.translation.model,
        applied.intent.local_llm.provider_key,
        applied.intent.local_llm.backend,
        slot.variant,
        slot.provider_key,
        slot.backend,
        slot.base_url,
        slot.model,
    )
    assert actual == (
        "local_llm",
        "llamacpp",
        "llamacpp",
        "local_llm",
        "llamacpp",
        "llamacpp",
        "http://127.0.0.1:8080/v1",
        "slot-model",
    )
