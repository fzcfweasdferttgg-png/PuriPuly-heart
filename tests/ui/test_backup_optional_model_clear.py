from __future__ import annotations

from pathlib import Path

import pytest

from puripuly_heart.config.runtime_resolution import LOCAL_LLM_DEFAULT_MODEL
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.provider_settings import attach_provider_windows, create_settings_view
from tests.helpers.settings_view import SettingsIntentRecorder, apply_provider_intent


def test_backup_optional_model_can_be_cleared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = SettingsIntentRecorder()
    view = create_settings_view(monkeypatch)
    recorder.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    attach_provider_windows(view)
    view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
    view._on_backup_local_provider_selected("llamacpp")
    view._pick_backup_local_model("stale-model")
    view._on_backup_gear_click(None)

    view._backup_local_model.value = ""
    view._on_backup_local_model_change_end(None)

    assert view._backup_local_model.error is None
    assert view._accept_provider_settings_dialog("backup", "backup_local") is True
    current = view._provider_draft or view._provider_snapshot
    assert current is not None
    assert current.backup_local_model == ""
    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(recorder.settings, pending)
    assert applied.intent.backup_translation.local_llm.model == LOCAL_LLM_DEFAULT_MODEL
    slot = view._provider_slots_state["backup"].slot(1)
    assert slot is not None and slot.model == ""


def test_backup_ollama_model_stays_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = SettingsIntentRecorder()
    view = create_settings_view(monkeypatch)
    recorder.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
    view._on_backup_local_provider_selected("ollama")
    view._backup_local_model.value = ""

    view._on_backup_local_model_change_end(None)

    assert view._backup_local_model.error == settings_view.t("settings.local_llm.model.required")
