"""A backup URL edit must not apply an empty model and disable the fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import SettingsIntentRecorder, apply_provider_intent


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


def test_backup_url_typo_fix_blocks_apply_until_model_is_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = SettingsIntentRecorder()
    view = _view(monkeypatch)
    recorder.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_backup_variant_selected("local_llm")
    view._on_backup_local_provider_selected("ollama")

    view._backup_local_base_url.value = "http://127.0.0.1:11434"
    view._on_backup_local_base_url_change_end(None)
    view._backup_local_model.value = "old-backup-model"
    view._on_backup_local_model_change_end(None)

    view._backup_local_base_url.value = "http://127.0.0.1:11435"
    view._on_backup_local_base_url_change_end(None)

    assert view.build_provider_apply_settings() is None
    assert view._backup_local_model.error == settings_view.t("settings.local_llm.model.required")

    view._backup_local_model.value = "new-backup-model"
    view._on_backup_local_model_change_end(None)
    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(recorder.settings, pending)
    assert applied.intent.backup_translation.local_llm.model == "new-backup-model"
    assert applied.intent.backup_translation.enabled is True
