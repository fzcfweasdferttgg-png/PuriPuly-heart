from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.provider_slots import (
    ProviderCardSlots,
    ProviderSlotConfig,
    load_provider_slots,
    save_provider_slots,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.ui.views import settings as settings_view


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


def test_provider_slots_store_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "provider_slots.json"
    state = {
        "translation": ProviderCardSlots(
            active=2,
            slots=(
                ProviderSlotConfig(
                    variant="openai_compatible",
                    provider_key="openrouter",
                    base_url="https://openrouter.ai/api/v1",
                    model="gpt-test",
                ),
                None,
                ProviderSlotConfig(
                    variant="local_llm",
                    base_url="http://127.0.0.1:11434/v1",
                    model="llama3.1:8b",
                    extra_body_json="{}",
                ),
            ),
        ),
    }

    save_provider_slots(path, state)
    loaded = load_provider_slots(path)

    assert loaded["translation"].active == 2
    assert loaded["translation"].slot(1) == state["translation"].slot(1)
    assert loaded["translation"].slot(2) is None
    assert loaded["translation"].slot(3) == state["translation"].slot(3)


def test_provider_slots_store_tolerates_missing_or_broken_files(tmp_path: Path) -> None:
    assert load_provider_slots(tmp_path / "missing.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_provider_slots(broken) == {}


def test_form_slot_row_selects_and_fills(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._provider_slots_state["translation"] = ProviderCardSlots(
        active=1,
        slots=(
            ProviderSlotConfig(
                variant="openai_compatible",
                provider_key="",
                base_url="",
                model="saved-model",
            ),
            None,
            None,
        ),
    )

    view._on_form_slot_selected("translation", 1)

    assert view._provider_slots_state["translation"].active == 1
    assert view._openai_model.value == "saved-model"


def test_form_slot_updates_mounted_fields_for_every_variant(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    updated = []
    monkeypatch.setattr(
        settings_view,
        "_update_control_if_mounted",
        lambda control: updated.append(control),
    )
    cases = (
        (
            "translation",
            "openai_compatible",
            ("_openai_provider", "_openai_base_url", "_openai_model"),
        ),
        ("backup", "openai_compatible", ("_backup_provider", "_backup_base_url", "_backup_model")),
        (
            "translation",
            "anthropic",
            ("_anthropic_provider", "_anthropic_base_url", "_anthropic_model"),
        ),
        (
            "backup",
            "anthropic",
            ("_backup_anthropic_provider", "_backup_anthropic_base_url", "_backup_anthropic_model"),
        ),
        (
            "translation",
            "local_llm",
            (
                "_local_llm_provider",
                "_local_llm_base_url",
                "_local_llm_model",
                "_local_llm_extra_body",
            ),
        ),
        (
            "backup",
            "local_llm",
            (
                "_backup_local_provider",
                "_backup_local_base_url",
                "_backup_local_model",
                "_backup_local_extra_body",
            ),
        ),
    )

    for card, variant, field_names in cases:
        config = ProviderSlotConfig(
            variant=variant,
            provider_key="",
            base_url="https://slot.example/v1",
            model="saved-model",
            extra_body_json="{}",
            backend="ollama",
        )
        view._provider_slots_state[card] = ProviderCardSlots(
            active=1,
            slots=(config, None, None),
        )
        updated.clear()

        view._on_form_slot_selected(card, 1)

        for field_name in field_names:
            assert getattr(view, field_name) in updated
        assert getattr(view, field_names[1]).value == config.base_url
        assert getattr(view, field_names[2]).value == config.model


def test_quick_switch_applies_saved_slot(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    applied: list[str] = []
    monkeypatch.setattr(view, "on_providers_changed", lambda: applied.append("apply"))
    view._provider_slots_state["translation"] = ProviderCardSlots(
        active=1,
        slots=(
            ProviderSlotConfig(
                variant="openai_compatible",
                provider_key="",
                base_url="",
                model="quick-model",
            ),
            None,
            None,
        ),
    )

    view._on_quick_slot_selected("translation", 1)

    assert applied == ["apply"]
    assert view._openai_model.value == "quick-model"
    assert view.has_provider_changes is True


def test_accept_saves_current_form_to_active_slot(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    window = type("W", (), {"is_open": False, "open": lambda self, schedule, **kw: None})()
    view.attach_provider_settings_window(
        {"translation_openai": window, "backup_openai": window}, lambda coro: coro
    )

    view._on_form_slot_selected("translation", 3)
    view._on_openai_gear_click(None)
    view._openai_model.value = "typed-model"
    view._accept_provider_settings_dialog("translation")

    saved = view._provider_slots_state["translation"].slot(3)
    assert saved is not None
    assert saved.model == "typed-model"
    assert saved.variant == TranslationModel.OPENAI_COMPATIBLE.value

    on_disk = load_provider_slots(tmp_path / "provider_slots.json")
    assert on_disk["translation"].slot(3).model == "typed-model"


def test_translation_and_backup_slots_are_independent(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    window = type("W", (), {"is_open": False, "open": lambda self, schedule, **kw: None})()
    view.attach_provider_settings_window(
        {"translation_openai": window, "backup_openai": window}, lambda coro: coro
    )

    view._on_form_slot_selected("translation", 1)
    view._on_openai_gear_click(None)
    view._openai_model.value = "translation-model"
    view._accept_provider_settings_dialog("translation")

    view._on_form_slot_selected("backup", 1)
    view._on_backup_gear_click(None)
    view._backup_model.value = "backup-model"
    view._accept_provider_settings_dialog("backup")

    assert view._provider_slots_state["translation"].slot(1).model == "translation-model"
    assert view._provider_slots_state["backup"].slot(1).model == "backup-model"


def test_slot_rows_hide_when_variant_not_selected(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    assert view._translation_card_row.visible is False
    assert view._backup_card_row.visible is False

    view._on_llm_selected(TranslationModel.NONE.value)
    assert view._translation_card_row.visible is False

    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    assert view._translation_card_row.visible is True

    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    assert view._translation_card_row.visible is True

    view._on_backup_variant_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    assert view._backup_card_row.visible is True


def test_quick_switch_can_change_variant(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    view._provider_slots_state["translation"] = ProviderCardSlots(
        active=1,
        slots=(
            ProviderSlotConfig(
                variant=TranslationModel.LOCAL_LLM.value,
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

    view._on_quick_slot_selected("translation", 1)

    assert view._local_llm_base_url.value == "http://127.0.0.1:11434"
    assert view._local_llm_model.value == "slot-model"
    assert view._provider_draft.local_llm_provider_key == "ollama"
