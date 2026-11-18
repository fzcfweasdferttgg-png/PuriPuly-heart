from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.provider_slots import ProviderCardSlots, ProviderSlotConfig
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.provider_settings import attach_provider_windows, create_settings_view


def _view(monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path):
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    attach_provider_windows(view)
    return view


def test_inactive_empty_ollama_does_not_block_cloud_primary_apply(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch, settings_events, tmp_path)
    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    view._on_local_llm_provider_selected("ollama")
    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._openai_model.value = "gpt-test"

    pending = view.build_provider_apply_settings()

    assert pending is not None
    assert view._local_llm_model.error is None


def test_backup_accept_saves_the_visible_dialog_variant_after_card_switch(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch, settings_events, tmp_path)
    view._on_backup_variant_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._on_backup_gear_click(None)

    view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
    view._backup_model.value = "typed-openai-model"
    assert view._accept_provider_settings_dialog("backup", "backup_openai") is True

    slot = view._provider_slots_state["backup"].slot(1)
    assert slot is not None
    assert (slot.variant, slot.model) == (
        TranslationModel.OPENAI_COMPATIBLE.value,
        "typed-openai-model",
    )
    current = view._provider_draft or view._provider_snapshot
    assert current.backup_variant == TranslationModel.LOCAL_LLM.value


@pytest.mark.parametrize("card", ["translation", "backup"])
@pytest.mark.parametrize("operation", ["fetch", "test"])
async def test_local_probe_uses_backend_from_selected_slot(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
    card: str,
    operation: str,
) -> None:
    view = _view(monkeypatch, settings_events, tmp_path)
    variant = TranslationModel.LOCAL_LLM.value
    view._provider_slots_state[card] = ProviderCardSlots(
        active=1,
        slots=(
            ProviderSlotConfig(
                variant=variant,
                provider_key="ollama",
                backend="ollama",
                base_url="http://127.0.0.1:11434",
                model="slot-model",
                extra_body_json="{}",
            ),
            None,
            None,
        ),
    )
    if card == "backup":
        view._on_backup_variant_selected(variant)
        view._on_backup_gear_click(None)
    else:
        view._on_llm_selected(variant)
        view._on_local_llm_gear_click(None)
    view._on_form_slot_selected(card, 1)
    calls: list[tuple[str, str]] = []

    async def fetch(backend: str, base_url: str, api_key: str):
        calls.append(("fetch", backend))
        return []

    async def test(backend: str, base_url: str, api_key: str):
        calls.append(("test", backend))
        return 200, ""

    monkeypatch.setattr(settings_view, "_fetch_llm_lib_models_default", fetch)
    monkeypatch.setattr(settings_view, "_test_llm_lib_connection_default", test)

    if card == "backup":
        handler = (
            view._on_test_backup_local_connection
            if operation == "test"
            else view._on_fetch_backup_local_models
        )
    else:
        handler = (
            view._on_test_local_llm_connection
            if operation == "test"
            else view._on_fetch_local_llm_models
        )
    await handler(None)

    assert calls == [(operation, "ollama")]


@pytest.mark.parametrize("card", ["translation", "backup"])
def test_custom_slot_backend_controls_required_model_validation(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path, card: str
) -> None:
    view = _view(monkeypatch, settings_events, tmp_path)
    variant = TranslationModel.LOCAL_LLM.value
    view._provider_slots_state[card] = ProviderCardSlots(
        active=1,
        slots=(
            ProviderSlotConfig(
                variant=variant,
                provider_key="deleted-preset",
                backend="ollama",
                base_url="http://127.0.0.1:9999",
                model="",
                extra_body_json="{}",
            ),
            None,
            None,
        ),
    )
    if card == "backup":
        view._on_backup_variant_selected(variant)
        view._on_backup_gear_click(None)
        model = view._backup_local_model
    else:
        view._on_llm_selected(variant)
        view._on_local_llm_gear_click(None)
        model = view._local_llm_model

    view._on_form_slot_selected(card, 1)

    assert view._accept_provider_settings_dialog(card) is False
    assert model.error == settings_view.t("settings.local_llm.model.required")
    current = view._provider_draft or view._provider_snapshot
    backend = current.backup_local_backend if card == "backup" else current.local_llm_backend
    assert backend == "ollama"
