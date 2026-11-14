from __future__ import annotations

import types

from puripuly_heart.ui.components.provider_settings_window import ProviderSettingsWindow
from puripuly_heart.ui.flet_runtime import update_control_if_mounted


class _Page:
    def __init__(self) -> None:
        self.window = types.SimpleNamespace(height=680)
        self.update_calls = 0

    def update(self) -> None:
        self.update_calls += 1


class _Task:
    def __init__(self, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


class _Control:
    page = object()

    def update(self) -> None:
        raise RuntimeError("An attempt to fetch destroyed session.")


def test_replace_content_does_not_update_a_closed_window() -> None:
    page = _Page()
    window = ProviderSettingsWindow()
    window._page = page
    window._task = _Task(done=True)

    window.replace_content(object(), height=695)

    assert page.window.height == 680
    assert page.update_calls == 0


def test_replace_content_updates_an_open_window() -> None:
    page = _Page()
    window = ProviderSettingsWindow()
    window._page = page
    window._task = _Task(done=False)

    window.replace_content(object(), height=695)

    assert page.window.height == 695
    assert page.update_calls == 1


def test_destroyed_session_is_treated_as_unmounted() -> None:
    assert update_control_if_mounted(_Control()) is False
