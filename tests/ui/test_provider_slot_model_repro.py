"""agent-4 repro (HEAD 6c26bcd1): applying a local slot whose base_url differs
from the currently staged base_url silently resets the slot's model.

Root cause chain (ui/views/settings.py):
- _apply_provider_slot_as_current() (quick slot switch / cross-variant Accept)
  calls _on_local_llm_base_url_change_end(None) to commit the slot URL.
- Since 6c26bcd1 that handler clears the model whenever the URL changed
  (url_changed -> self._local_llm_model.value = ""; commit model "").
- _apply_provider_slot_as_current() then calls _on_local_llm_model_change_end,
  which maps the now-empty control back to LOCAL_LLM_DEFAULT_MODEL.

Committing sibling fields correctly (see _commit_local_llm_fields_from_controls,
which saves the model, runs the URL handler, then restores the model), but the
slot-apply path does not, so the slot's saved model is lost and the default
model is applied/staged instead.

Expected: quick-switch/cross-variant Accept to a local slot restores the slot's
saved model verbatim. Actual: model becomes LOCAL_LLM_DEFAULT_MODEL whenever
slot.base_url != current draft base_url.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.provider_slots import (
    ProviderCardSlots,
    ProviderSlotConfig,
)
from puripuly_heart.config.runtime_resolution import LOCAL_LLM_DEFAULT_MODEL
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from tests.helpers.provider_settings import attach_provider_windows, create_settings_view

SLOT = ProviderSlotConfig(
    variant=TranslationModel.LOCAL_LLM.value,
    provider_key="llamacpp",
    backend="llamacpp",
    base_url="http://127.0.0.1:8080/v1",
    model="slot-model",
    extra_body_json="{}",
)


def test_quick_switch_local_slot_with_new_url_keeps_slot_model(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)

    # Fresh settings: local defaults are URL http://127.0.0.1:11434 + default
    # model. The saved slot uses a *different* URL (llama.cpp on :8080).
    view._provider_slots_state["translation"] = ProviderCardSlots(
        active=1,
        slots=(SLOT, None, None),
    )

    view._on_quick_slot_selected("translation", 1)

    assert view._local_llm_base_url.value == "http://127.0.0.1:8080/v1"
    assert view._local_llm_model.value == "slot-model", (
        f"slot model must survive quick switch; control lost it "
        f"(default {LOCAL_LLM_DEFAULT_MODEL!r} would appear instead)"
    )
    assert (
        view._provider_draft.local_llm_model == "slot-model"
    ), "applied settings must use the slot's model, not the default model"


def test_quick_switch_backup_local_slot_with_new_url_keeps_slot_model(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)

    view._provider_slots_state["backup"] = ProviderCardSlots(
        active=1,
        slots=(SLOT, None, None),
    )

    view._on_quick_slot_selected("backup", 1)

    assert view._backup_local_model.value == "slot-model"
    assert view._provider_draft.backup_local_model == "slot-model"


def test_cross_variant_accept_local_slot_keeps_slot_model(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    """Accept in an OpenAI dialog with an activated local slot (different URL):
    the cross-variant switch must restore the slot's model too."""

    def _view_with_windows():
        view = create_settings_view(monkeypatch)
        settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
        monkeypatch.setattr(view, "on_providers_changed", lambda: None)
        attach_provider_windows(view)
        return view

    view = _view_with_windows()
    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._on_openai_gear_click(None)
    view._provider_slots_state["translation"] = ProviderCardSlots(
        active=1,
        slots=(SLOT, None, None),
    )
    # The user explicitly picks the local slot inside the OpenAI form
    # (the cross-variant flow the 6c26bcd1 guard was written for).
    view._on_form_slot_selected("translation", 1)

    view._accept_provider_settings_dialog("translation")

    display = view._provider_draft or view._provider_snapshot
    assert display.translation.model == TranslationModel.LOCAL_LLM
    assert (
        view._provider_draft.local_llm_model == "slot-model"
    ), "cross-variant Accept must apply the slot model, not the default model"
    # The slot itself must not be overwritten by the dialog fields.
    slot = view._provider_slots_state["translation"].slot(1)
    assert slot is not None and slot.model == "slot-model"
