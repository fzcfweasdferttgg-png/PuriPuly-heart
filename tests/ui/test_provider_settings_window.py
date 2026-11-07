from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.ui.components.provider_settings_window import ProviderSettingsWindow
from puripuly_heart.ui.views import settings as settings_view


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


class FakeWindow:
    def __init__(self) -> None:
        self.closed = False
        self.minimized = False
        self.visible = False

    def close(self):
        self.closed = True


class FakePage:
    def __init__(self) -> None:
        self.window = FakeWindow()
        self.updates = 0
        self.controls: list = []

    def add(self, *controls) -> None:
        self.controls.extend(controls)

    def update(self) -> None:
        self.updates += 1


def _fake_schedule(record: list) -> object:
    def schedule(coro):
        task = asyncio.get_running_loop().create_task(coro())
        record.append(task)
        return task

    return schedule


async def _hanging_app_runner(target) -> None:
    """Session-like runner: mounts the content and stays alive until cancelled."""
    page = FakePage()
    await target(page)
    await asyncio.Event().wait()


async def test_provider_settings_window_mounts_form_and_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted: list[str] = []
    closed: list[str] = []
    window = ProviderSettingsWindow(app_runner=_hanging_app_runner)
    content = object()

    def schedule(coro):
        task = asyncio.get_running_loop().create_task(coro())
        return task

    window.open(
        schedule,
        title="Настройки перевода",
        content=content,
        on_accept=lambda: accepted.append("ok"),
        on_close_request=lambda: closed.append("reverted"),
    )
    await asyncio.sleep(0)
    assert window.is_open

    window._on_accept_click(None)

    assert accepted == ["ok"]
    assert closed == []
    await window.close()
    assert not window.is_open


async def test_provider_settings_window_close_without_accept_reverts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []
    window = ProviderSettingsWindow(app_runner=_hanging_app_runner)

    def schedule(coro):
        task = asyncio.get_running_loop().create_task(coro())
        return task

    window.open(
        schedule,
        title="t",
        content=object(),
        on_accept=lambda: None,
        on_close_request=lambda: closed.append("reverted"),
    )
    await asyncio.sleep(0)
    await window.close()
    await asyncio.sleep(0)

    assert closed == ["reverted"]


def test_provider_settings_window_configures_resizable_frameless_defaults() -> None:
    window = ProviderSettingsWindow()
    page = FakePage()

    window._title = "Настройки перевода"
    window.configure_page(page)

    assert page.window.frameless is True
    assert page.window.resizable is True
    assert page.window.width > 0
    assert page.window.height > 0


def _fake_settings_window() -> types.SimpleNamespace:
    opened: dict[str, object] = {}
    accepted: list[str] = []
    reverted: list[str] = []

    class _Window:
        is_open = False

        def open(self, schedule, **kwargs) -> None:
            opened.update(kwargs)
            self.is_open = True

    return types.SimpleNamespace(
        window=_Window(), opened=opened, accepted=accepted, reverted=reverted
    )


def test_gear_opens_dialog_with_form_and_titles(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    fake = _fake_settings_window()
    view.attach_provider_settings_window(fake.window, lambda coro: coro)

    from puripuly_heart.ui.i18n import t

    view._on_openai_gear_click(None)

    assert fake.opened["title"] == "{}: {}".format(
        t("settings.translation.config_title"),
        t("settings.translation_variant.openai_compatible"),
    )
    assert fake.opened["content"] is view._openai_form

    fake2 = _fake_settings_window()
    view.attach_provider_settings_window(fake2.window, lambda coro: coro)
    view._on_backup_gear_click(None)

    assert fake2.opened["title"] == "{}: {}".format(
        t("settings.backup_translation.config_title"),
        t("settings.translation_variant.openai_compatible"),
    )
    assert fake2.opened["content"].controls == [
        view._backup_network_group,
        view._backup_local_group,
    ]


def test_dialog_accept_applies_staged_edits(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    applied: list[str] = []
    monkeypatch.setattr(view, "on_providers_changed", lambda: applied.append("apply"))
    fake = _fake_settings_window()
    view.attach_provider_settings_window(fake.window, lambda coro: coro)

    view._on_openai_gear_click(None)
    view._openai_model.value = "gpt-test"
    view._on_openai_model_change_end(
        types.SimpleNamespace(control=view._openai_model)
    )
    assert view.has_provider_changes is True

    view._accept_provider_settings_dialog()

    assert applied == ["apply"]
    assert view.has_provider_changes is True


def test_dialog_close_without_accept_discards_staged_edits(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    fake = _fake_settings_window()
    view.attach_provider_settings_window(fake.window, lambda coro: coro)

    view._on_openai_gear_click(None)
    view._openai_model.value = "gpt-test"
    view._on_openai_model_change_end(
        types.SimpleNamespace(control=view._openai_model)
    )
    assert view.has_provider_changes is True

    view._revert_provider_settings_dialog()

    assert view.has_provider_changes is False
    assert view._provider_draft is None


def test_gear_cards_visible_follow_variant_selection(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    assert view._openai_compatible_card.visible is True
    assert view._openai_gear is view._openai_compatible_card
    translation_row = view._translation_provider_card.content.content.controls[-1]
    backup_row = view._backup_translation_card.content.content.controls[-1]
    assert view._openai_compatible_card in translation_row.controls
    assert view._backup_config_card in backup_row.controls

    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    assert view._local_llm_connection_card.visible is True
    assert view._openai_compatible_card.visible is False
