from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from puripuly_heart.config import providers_registry
from puripuly_heart.config.provider_slots import load_provider_slots
from puripuly_heart.config.runtime_resolution import LOCAL_LLM_DEFAULT_MODEL
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.provider_settings import (
    attach_provider_windows,
    create_settings_view,
    install_provider_presets,
)
from tests.helpers.settings_view import apply_provider_intent


def test_first_use_concurrent_adds_do_not_overwrite_committed_preset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "user" / "providers.json"
    bundled = tmp_path / "bundled.json"
    bundled.write_text(
        json.dumps(
            {
                "version": "1.0",
                "providers": {
                    "bundled": {"label": "Bundled", "base_url": "http://127.0.0.1:8000/v1"}
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        providers_registry, "user_providers_path", lambda protocol="openai": destination
    )
    monkeypatch.setattr(
        providers_registry, "_bundled_providers_path", lambda protocol="openai": bundled
    )
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)

    first_copy_started = threading.Event()
    allow_first_copy = threading.Event()
    second_copy_started = threading.Event()
    release_second_copy = threading.Event()
    real_copy2 = shutil.copy2

    def coordinated_copy2(src, dst):
        if threading.current_thread().name == "second-add":
            second_copy_started.set()
            assert release_second_copy.wait(timeout=10)
        else:
            first_copy_started.set()
            assert allow_first_copy.wait(timeout=10)
        return real_copy2(src, dst)

    monkeypatch.setattr(providers_registry.shutil, "copy2", coordinated_copy2)
    errors: list[str] = []

    def add(label: str, url: str) -> None:
        try:
            providers_registry.add_provider_preset(label, url)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    first = threading.Thread(
        target=add, args=("First custom", "http://127.0.0.1:5001"), name="first-add"
    )
    second = threading.Thread(
        target=add, args=("Second custom", "http://127.0.0.1:5002"), name="second-add"
    )
    first.start()
    second.start()
    assert first_copy_started.wait(timeout=10)
    initialization_is_locked = Path(str(destination) + ".lock").exists()
    if not initialization_is_locked:
        assert second_copy_started.wait(timeout=10)
    allow_first_copy.set()
    first.join(timeout=10)
    assert not first.is_alive()
    release_second_copy.set()
    second.join(timeout=10)
    assert not second.is_alive()

    providers_registry._PROVIDER_PRESETS_CACHE = None
    saved = providers_registry.load_provider_presets()
    assert not errors
    assert {"first-custom", "second-custom"}.issubset(saved)


@pytest.mark.parametrize("card", ["translation", "backup"])
def test_accept_manual_local_url_without_blur_does_not_restore_stale_model(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
    card: str,
) -> None:
    install_provider_presets(monkeypatch, tmp_path)
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    attach_provider_windows(view)
    if card == "backup":
        view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
        view._on_backup_local_provider_selected("llamacpp")
        view._pick_backup_local_model("server-a-model")
        view._on_backup_gear_click(None)
        view._backup_local_base_url.value = "http://127.0.0.1:9999/v1"
    else:
        view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
        view._on_local_llm_provider_selected("llamacpp")
        view._pick_local_llm_model("server-a-model")
        view._on_local_llm_gear_click(None)
        view._local_llm_base_url.value = "http://127.0.0.1:9999/v1"

    accepted = view._accept_provider_settings_dialog(card)
    if card == "backup":
        assert accepted is False
        assert view.build_provider_apply_settings() is None
        assert view._backup_local_model.error == settings_view.t(
            "settings.local_llm.model.required"
        )
        return

    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    intent = (
        applied.intent.backup_translation.local_llm
        if card == "backup"
        else applied.intent.local_llm
    )
    expected_model = "" if card == "backup" else LOCAL_LLM_DEFAULT_MODEL
    assert (intent.provider_key, intent.base_url, intent.model) == (
        "",
        "http://127.0.0.1:9999/v1",
        expected_model,
    )
    slot = load_provider_slots(tmp_path / "provider_slots.json")[card].slot(1)
    assert slot is not None
    assert (slot.provider_key, slot.base_url, slot.model) == (
        "",
        "http://127.0.0.1:9999/v1",
        "",
    )


@pytest.mark.parametrize("card", ["translation", "backup"])
@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_accept_cloud_custom_url_without_blur_clears_preset_everywhere(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
    card: str,
    protocol: str,
) -> None:
    install_provider_presets(monkeypatch, tmp_path)
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    attach_provider_windows(view)
    if card == "translation" and protocol == "openai":
        view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
        view._on_openai_provider_selected("deepseek")
        view._openai_model.value = "custom-model"
        view._on_openai_gear_click(None)
        view._openai_base_url.value = "https://custom.example/v1"
    elif card == "translation":
        view._on_llm_selected(TranslationModel.ANTHROPIC.value)
        view._on_anthropic_provider_selected("anthropic")
        view._anthropic_model.value = "custom-model"
        view._on_anthropic_gear_click(None)
        view._anthropic_base_url.value = "https://custom.example/anthropic"
    elif protocol == "openai":
        view._on_backup_variant_selected(TranslationModel.OPENAI_COMPATIBLE.value)
        view._on_backup_provider_selected("deepseek")
        view._backup_model.value = "custom-model"
        view._on_backup_gear_click(None)
        view._backup_base_url.value = "https://custom.example/v1"
    else:
        view._on_backup_variant_selected(TranslationModel.ANTHROPIC.value)
        view._on_backup_anthropic_provider_selected("anthropic")
        view._backup_anthropic_model.value = "custom-model"
        view._on_backup_gear_click(None)
        view._backup_anthropic_base_url.value = "https://custom.example/anthropic"

    view._accept_provider_settings_dialog(card)

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
    assert (intent.provider_key, dropdown.value) == ("", None)
    slot = load_provider_slots(tmp_path / "provider_slots.json")[card].slot(1)
    assert slot is not None
    assert slot.provider_key == ""


@pytest.mark.parametrize("card", ["translation", "backup"])
def test_accept_rejects_invalid_extra_body_without_saving_or_applying(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
    card: str,
) -> None:
    install_provider_presets(monkeypatch, tmp_path)
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    applied: list[str] = []
    monkeypatch.setattr(view, "on_providers_changed", lambda: applied.append("apply"))
    attach_provider_windows(view)
    if card == "backup":
        view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
        view._on_backup_local_provider_selected("llamacpp")
        view._pick_backup_local_model("saved-model")
        view._on_backup_gear_click(None)
        view._backup_local_extra_body.value = "{not-json"
        view._on_backup_local_extra_body_change_end(None)
        assert view._backup_local_extra_body_error.visible
    else:
        view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
        view._on_local_llm_provider_selected("llamacpp")
        view._pick_local_llm_model("saved-model")
        view._on_local_llm_gear_click(None)
        view._local_llm_extra_body.value = "{not-json"
        view._on_local_llm_extra_body_change_end(None)
        assert view._local_llm_extra_body_error.visible

    view._accept_provider_settings_dialog(card)

    state = load_provider_slots(tmp_path / "provider_slots.json")
    card_state = state.get(card)
    slot = card_state.slot(1) if card_state is not None else None
    saved_extra_body = slot.extra_body_json if slot is not None else None
    assert (applied, saved_extra_body) == ([], None)


@pytest.mark.parametrize("card", ["translation", "backup"])
def test_form_slot_accept_preserves_custom_backend(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
    card: str,
) -> None:
    from puripuly_heart.config.provider_slots import ProviderCardSlots, ProviderSlotConfig

    install_provider_presets(monkeypatch, tmp_path)
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    attach_provider_windows(view)
    if card == "backup":
        view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
        view._on_backup_local_provider_selected("llamacpp")
        view._on_backup_gear_click(None)
    else:
        view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
        view._on_local_llm_provider_selected("llamacpp")
        view._on_local_llm_gear_click(None)
    view._provider_slots_state[card] = ProviderCardSlots(
        active=1,
        slots=(
            None,
            ProviderSlotConfig(
                variant=TranslationModel.LOCAL_LLM.value,
                provider_key="",
                backend="vllm",
                base_url="http://127.0.0.1:9999/v1",
                model="custom-slot-model",
                extra_body_json="{}",
            ),
            None,
        ),
    )

    view._on_form_slot_selected(card, 2)
    view._accept_provider_settings_dialog(card)

    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    intent = (
        applied.intent.backup_translation.local_llm
        if card == "backup"
        else applied.intent.local_llm
    )
    assert (intent.provider_key, intent.backend, intent.model) == (
        "",
        "vllm",
        "custom-slot-model",
    )
    slot = load_provider_slots(tmp_path / "provider_slots.json")[card].slot(2)
    assert slot is not None and slot.backend == "vllm"
