"""Repro: editing a selected provider preset crashes when no draft is staged.

State reachable in the real UI:
1. settings.json already selects a preset (openai provider_key == "first");
   after load_from_settings the view has ``_provider_draft is None`` and
   ``_openai_provider.value == "first"``.
2. User opens the provider settings window (gear) and clicks the preset
   edit (pencil) button, then Save.
3. ProviderPresetModal._save persists the preset and calls ``on_changed`` →
   SettingsView._on_provider_presets_changed → _apply_edited_provider_preset,
   which reads ``self._provider_draft.openai_compatible_base_url``.

Expected: edit applies (base_url refreshed, dropdown options re-rendered).
Actual at 66f9809f: AttributeError: 'NoneType' object has no attribute
'openai_compatible_base_url' (or 'backup_base_url' for the backup card);
the modal's ``_close()`` never runs, so the dialog stays open and the
dropdown options are never refreshed even though the preset file changed.

Run: PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/llmshim python3 -m pytest \
    -p no:cacheprovider tests/test_preset_edit_without_draft.py
(against a writable copy of the tree; see ../logs).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.ports.settings_view import (
    BackupOpenAICompatibleProviderKeyEdit,
    OpenAICompatibleProviderKeyEdit,
    ProviderApplyIntent,
)
from puripuly_heart.config import providers_registry
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import SettingsIntentRecorder, apply_provider_intent


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


def _registry_with_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "providers.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "providers": {
                    "first": {"label": "First", "base_url": "http://first.example/v1"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(providers_registry, "ensure_providers_file", lambda protocol="openai": path)
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    return path


def _loaded_view_with_selected_preset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, card: str
) -> settings_view.SettingsView:
    """Load settings whose translation (or backup) provider is preset "first"."""
    _registry_with_first(tmp_path, monkeypatch)
    recorder = SettingsIntentRecorder()
    edit = (
        BackupOpenAICompatibleProviderKeyEdit("first")
        if card == "backup"
        else OpenAICompatibleProviderKeyEdit("first")
    )
    settings = apply_provider_intent(AppSettingsVNext(), ProviderApplyIntent((edit,)))
    view = _view(monkeypatch)
    recorder.load(view, settings, config_path=tmp_path / "settings.json")
    assert view._provider_draft is None
    return view


def test_edit_selected_translation_preset_without_draft_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view = _loaded_view_with_selected_preset(monkeypatch, tmp_path, card="translation")
    assert view._openai_provider.value == "first"

    providers_registry.update_provider_preset("first", "First renamed", "https://first.example/v2")
    view._on_provider_presets_changed("translation", "edit", "first")

    assert view._openai_provider.options[-1].text == "First renamed"
    assert view._openai_base_url.value == "https://first.example/v2"


def test_edit_selected_backup_preset_without_draft_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view = _loaded_view_with_selected_preset(monkeypatch, tmp_path, card="backup")
    assert view._backup_provider.value == "first"
    # Isolate the backup path: the openai dropdown falls back to the first
    # preset for unconfigured entries (see _provider_dropdown_value), so clear
    # it to force the backup branch of _apply_edited_provider_preset.
    view._openai_provider.value = None

    providers_registry.update_provider_preset("first", "First renamed", "https://first.example/v2")
    view._on_provider_presets_changed("backup", "edit", "first")

    assert view._backup_provider.options[-1].text == "First renamed"
    assert view._backup_base_url.value == "https://first.example/v2"


def test_preset_modal_save_leaves_dialog_open_when_callback_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Consequence: ProviderPresetModal._save runs _on_changed before _close;
    an exception there persists the preset but leaves the dialog mounted."""
    from puripuly_heart.ui.components.provider_preset_modal import ProviderPresetModal

    _registry_with_first(tmp_path, monkeypatch)

    class FakePage:
        def __init__(self) -> None:
            self.shown: list[object] = []
            self.popped = 0

        def show_dialog(self, dialog) -> None:
            self.shown.append(dialog)

        def pop_dialog(self) -> None:
            self.popped += 1

    page = FakePage()
    calls: list[tuple[str, str]] = []

    def on_changed(action: str, key: str) -> None:
        calls.append((action, key))
        raise AttributeError("'NoneType' object has no attribute 'openai_compatible_base_url'")

    modal = ProviderPresetModal(page, on_changed, protocol="openai")
    modal.open_edit("first")
    modal._name_field.value = "First renamed"
    modal._url_field.value = "https://first.example/v2"

    with pytest.raises(AttributeError):
        modal._save(None)

    # The preset was persisted...
    assert providers_registry.load_provider_presets("openai")["first"].label == "First renamed"
    # ...but the dialog was never closed.
    assert page.popped == 0
