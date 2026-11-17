"""Repro: staged provider-form edits become un-cancellable after a variant switch.

Documented contract (ProviderSettingsWindow docstring,
SettingsView._revert_provider_settings_dialog): "closing without accepting runs
on_close_request so the caller can discard staged edits".

Scenario (real UI):
1. Open the translation OpenAI settings window (gear on the translation card).
2. Type a different base URL and tab out — the field's change_end handler calls
   _commit_openai_field, staging OpenAICompatibleBaseUrlEdit.
3. Without pressing Accept, click the Local LLM gear on the same card
   (_open_provider_settings_dialog → _close_other_provider_settings_windows):
   the OpenAI window is closed without Accept, but its staged edit is kept and
   becomes the baseline of the new dialog.
4. Close the Local window without Accept as well.

Expected: both dialogs closed without Accept ⇒ the staged OpenAI field edit is
discarded (nothing the user typed is applied).
Actual at 66f9809f: the edit is still staged; leaving the Settings tab
auto-applies it to settings. Once the first dialog is abandoned there is no
UI action left that can cancel its staged field edits.

Run: PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/llmshim python3 -m pytest \
    -p no:cacheprovider tests/test_dialog_switch_staged_edits.py
(against a writable copy of the tree; see ../logs).
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
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import SettingsIntentRecorder

PROVIDER_KINDS = (
    "translation_openai",
    "translation_anthropic",
    "translation_local",
    "backup_openai",
    "backup_anthropic",
    "backup_local",
)


class FakeWindow:
    def __init__(self) -> None:
        self.is_open = False
        self.accepted = False
        self.on_accept = None
        self.on_close_request = None
        self.opened_with: dict = {}

    def open(self, schedule, *, title, content, on_accept, on_close_request, height=450) -> None:
        self.is_open = True
        self.accepted = False
        self.on_accept = on_accept
        self.on_close_request = on_close_request
        self.opened_with = dict(title=title, content=content, height=height)

    def accept(self) -> None:
        self.accepted = True
        if self.on_accept is not None:
            self.on_accept()
        self.is_open = False

    async def close(self) -> None:
        if self.is_open and not self.accepted and self.on_close_request is not None:
            self.on_close_request()
        self.is_open = False


class RecordingSchedule:
    def __init__(self) -> None:
        self.scheduled = []

    def __call__(self, factory):
        self.scheduled.append(factory)
        return factory

    def run_all(self) -> None:
        while self.scheduled:
            factory = self.scheduled.pop(0)
            result = factory()
            if asyncio.iscoroutine(result):
                asyncio.run(result)


def _view_with_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    view = settings_view.SettingsView()
    recorder = SettingsIntentRecorder()
    recorder.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    windows = {kind: FakeWindow() for kind in PROVIDER_KINDS}
    schedule = RecordingSchedule()
    view.attach_provider_settings_window(windows, schedule)
    return view, windows, schedule, recorder


def test_control_single_dialog_close_without_accept_discards_staged_edit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Control case: a lone dialog closed without Accept discards its edits."""
    view, windows, schedule, _ = _view_with_windows(monkeypatch, tmp_path)

    view._open_provider_settings_dialog("translation_openai")
    assert view._provider_dialog_kinds["translation"] == "translation_openai"

    view._commit_openai_field("base_url", "http://edited.example/v1")
    assert OpenAICompatibleBaseUrlEdit in {type(e) for e in view._provider_edits.values()}
    assert view.has_provider_changes is True

    asyncio.run(windows["translation_openai"].close())  # user closes the window (X)

    assert OpenAICompatibleBaseUrlEdit not in {type(e) for e in view._provider_edits.values()}
    assert view.has_provider_changes is False


def test_switched_dialog_close_without_accept_discards_abandoned_dialog_edits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both same-card dialogs closed without Accept ⇒ nothing typed survives."""
    view, windows, schedule, _ = _view_with_windows(monkeypatch, tmp_path)

    view._open_provider_settings_dialog("translation_openai")
    view._commit_openai_field("base_url", "http://edited.example/v1")
    assert OpenAICompatibleBaseUrlEdit in {type(e) for e in view._provider_edits.values()}

    # Rapid switch: open a different variant window of the same card; the
    # first window is closed without Accept (its close callback fires stale).
    view._open_provider_settings_dialog("translation_local")
    schedule.run_all()
    assert windows["translation_openai"].is_open is False

    # Close the replacement dialog without Accept too.
    asyncio.run(windows["translation_local"].close())

    assert OpenAICompatibleBaseUrlEdit not in {
        type(e) for e in view._provider_edits.values()
    }, "staged edit from the abandoned OpenAI dialog must not survive two close-without-accept"
    assert view.has_provider_changes is False

    pending = view.build_provider_apply_settings()
    assert pending is None or OpenAICompatibleBaseUrlEdit not in {type(e) for e in pending.edits}


def test_backup_switched_dialog_close_matches_translation_behavior(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view, windows, schedule, _ = _view_with_windows(monkeypatch, tmp_path)

    view._open_provider_settings_dialog("backup_openai")
    view._commit_backup_field("base_url", "http://edited.example/v1")

    view._open_provider_settings_dialog("backup_local")
    schedule.run_all()
    asyncio.run(windows["backup_local"].close())

    assert BackupOpenAICompatibleBaseUrlEdit not in {
        type(e) for e in view._provider_edits.values()
    }, "staged edit from the abandoned backup OpenAI dialog must not survive two close-without-accept"


def test_stale_close_after_switch_does_not_touch_the_new_dialog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Companion contract (also pinned upstream): the abandoned window's stale
    close callback must not revert state belonging to the replacement dialog."""
    from puripuly_heart.config.translation_values import TranslationModel

    view, windows, schedule, _ = _view_with_windows(monkeypatch, tmp_path)

    view._open_provider_settings_dialog("translation_openai")
    stale_close = windows["translation_openai"].on_close_request

    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    view._open_provider_settings_dialog("translation_local")
    stale_close()  # late on_close_request from the abandoned window

    display = view._provider_draft or view._provider_snapshot
    assert display.translation.model == TranslationModel.LOCAL_LLM
    assert view._provider_dialog_kinds["translation"] == "translation_local"
    assert windows["translation_local"].is_open is True
