"""agent-4 repro (HEAD 6c26bcd1): cross-card provider dialogs leak staged edits.

Contract (ProviderSettingsWindow docstring, SettingsView._revert_provider_settings_dialog,
pinned by tests/ui/test_dialog_switch_staged_edits.py): closing a dialog without
Accept discards every edit staged inside it.

Defect: `_provider_edits` / `_provider_draft` are global, but the revert
baselines `_provider_dialog_edits_before` / `_provider_dialog_draft_before` are
per-card ("translation" / "backup"). Both cards' dialogs can be open at once
(`_close_other_provider_settings_windows` only closes same-card windows), so:

1. Open translation dialog, stage edit X  -> baseline A = pre-X.
2. Open backup dialog (its baseline B is captured *after* X -> B contains X).
3. Close translation without Accept -> restores A: discards X and also Y, the
   edits of the still-open backup dialog (Y visible in its form is now lost
   from the staged draft).
4. Close backup without Accept -> restores B -> **X is resurrected** although
   every dialog was closed without Accept.

Expected: after closing both dialogs without Accept, nothing typed survives
(same contract as the same-card case). Actual: X survives in the staged edits
and would be auto-applied when leaving the Settings tab.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.ports.settings_view import (
    BackupOpenAICompatibleBaseUrlEdit,
    OpenAICompatibleBaseUrlEdit,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from tests.helpers.provider_settings import attach_provider_windows, create_settings_view
from tests.helpers.settings_view import SettingsIntentRecorder


def _view_with_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    view = create_settings_view(monkeypatch)
    recorder = SettingsIntentRecorder()
    recorder.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    windows, schedule = attach_provider_windows(view)
    return view, windows, schedule


def test_cross_card_both_closed_without_accept_leaves_no_edits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view, windows, _schedule = _view_with_windows(monkeypatch, tmp_path)

    view._open_provider_settings_dialog("translation_openai")
    view._commit_openai_field("base_url", "http://x.example/v1")
    assert OpenAICompatibleBaseUrlEdit in {type(e) for e in view._provider_edits.values()}

    # Backup dialog for another card may be open at the same time.
    view._open_provider_settings_dialog("backup_openai")
    view._commit_backup_field("base_url", "http://y.example/v1")
    assert BackupOpenAICompatibleBaseUrlEdit in {type(e) for e in view._provider_edits.values()}

    # Both users close their windows without Accept.
    asyncio.run(windows["translation_openai"].close())
    asyncio.run(windows["backup_openai"].close())

    kinds = {type(e) for e in view._provider_edits.values()}
    assert (
        OpenAICompatibleBaseUrlEdit not in kinds
    ), "translation edit X must not survive: its dialog closed without Accept"
    assert (
        BackupOpenAICompatibleBaseUrlEdit not in kinds
    ), "backup edit Y must not survive: its dialog closed without Accept"
    assert view.has_provider_changes is False


def test_cross_card_translation_revert_keeps_other_dialog_edits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """While the backup dialog stays open, closing the translation dialog must
    discard only the translation edits — not the backup dialog's staged edits."""
    view, windows, _schedule = _view_with_windows(monkeypatch, tmp_path)

    view._open_provider_settings_dialog("translation_openai")
    view._commit_openai_field("base_url", "http://x.example/v1")

    view._open_provider_settings_dialog("backup_openai")
    view._commit_backup_field("base_url", "http://y.example/v1")

    asyncio.run(windows["translation_openai"].close())

    kinds = {type(e) for e in view._provider_edits.values()}
    assert OpenAICompatibleBaseUrlEdit not in kinds
    assert (
        BackupOpenAICompatibleBaseUrlEdit in kinds
    ), "backup dialog is still open; its staged edit must remain staged"
