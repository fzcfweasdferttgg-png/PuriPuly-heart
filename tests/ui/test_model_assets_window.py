from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.model_assets_window import (
    MODEL_ASSETS_WINDOW_HEIGHT,
    MODEL_ASSETS_WINDOW_WIDTH,
    ModelAssetsWindow,
)
from puripuly_heart.ui.i18n import t


@dataclass(frozen=True)
class Entry:
    model_id: str
    name: str
    description_key: str
    purpose: str
    size_bytes: int
    status: str = "missing"
    progress_percent: int | None = None
    failure_code: str | None = None
    importable: bool = False
    required_files: tuple[str, ...] = ()


class Application:
    def __init__(self) -> None:
        self.entries: tuple[Entry, ...] = ()
        self.model_operation_progress = None

    def model_asset_entries(self):
        return self.entries

    async def refresh_model_assets(self):
        return self.entries


class FakeWindow:
    def __init__(self):
        self.frameless = False
        self.resizable = False
        self.maximizable = False
        self.width = 0
        self.height = 0
        self.icon = ""
        self.visible = False
        self.closed = False

    def to_front(self):
        pass

    def close(self):
        self.closed = True


class FakePage:
    def __init__(self):
        self.window = FakeWindow()
        self.title = ""
        self.theme_mode = None
        self.theme = None
        self.bgcolor = None
        self.padding = None
        self.fonts = None
        self.added = []
        self.overlay: list[object] = []
        self.update_calls = 0

    def add(self, control):
        self.added.append(control)

    def update(self):
        self.update_calls += 1


def test_model_assets_window_configures_default_size_and_title() -> None:
    window = ModelAssetsWindow(Application())
    page = FakePage()
    window.configure_page(page)

    assert (MODEL_ASSETS_WINDOW_WIDTH, MODEL_ASSETS_WINDOW_HEIGHT) == (1280, 680)
    assert page.window.width == 1280
    assert page.window.height == 680
    assert page.window.resizable is True
    assert page.window.frameless is True
    assert page.title == t("settings.model_assets")


async def test_model_assets_window_open_is_single_instance() -> None:
    release = asyncio.Event()

    async def fake_runner(target):
        page = FakePage()
        await target(page)
        await release.wait()

    window = ModelAssetsWindow(Application(), app_runner=fake_runner)
    scheduled = []

    def schedule(coro):
        if coro.__name__ == "_refresh_and_watch":
            return None
        task = asyncio.get_running_loop().create_task(coro())
        scheduled.append(task)
        return task

    window.open(schedule)
    await asyncio.sleep(0)
    window.open(schedule)

    assert len(scheduled) == 1
    assert window.is_open is True

    release.set()
    await scheduled[0]


async def test_model_assets_window_close_releases_state_and_stops_the_view() -> None:
    async def fake_runner(target):
        page = FakePage()
        await target(page)
        await asyncio.Event().wait()

    window = ModelAssetsWindow(Application(), app_runner=fake_runner)
    started: list = []

    def schedule(coro):
        if coro.__name__ == "_refresh_and_watch":
            started.append(coro)
            return None
        return asyncio.get_running_loop().create_task(coro())

    window.open(schedule)
    await asyncio.sleep(0)
    assert window.is_open is True
    view = window._view
    assert view.is_open is True

    await window.close()
    assert window.is_open is False
    assert window._page is None
    assert view.is_open is False
