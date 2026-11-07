from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.about_window import (
    ABOUT_WINDOW_HEIGHT,
    ABOUT_WINDOW_WIDTH,
    AboutWindow,
)


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
        self.update_calls = 0

    def add(self, control):
        self.added.append(control)

    def update(self):
        self.update_calls += 1


def test_about_window_configures_default_size_and_resize() -> None:
    window = AboutWindow()
    page = FakePage()
    window.configure_page(page)

    assert (ABOUT_WINDOW_WIDTH, ABOUT_WINDOW_HEIGHT) == (960, 540)
    assert page.window.width == 960
    assert page.window.height == 540
    assert page.window.resizable is True
    assert page.window.frameless is True


async def test_about_window_open_is_single_instance() -> None:
    started = []
    release = asyncio.Event()

    async def fake_runner(target):
        started.append(target)
        page = FakePage()
        await target(page)
        await release.wait()

    window = AboutWindow(app_runner=fake_runner)
    scheduled = []

    def schedule(coro):
        task = asyncio.get_running_loop().create_task(coro)
        scheduled.append(task)
        return task

    window.open(schedule)
    await asyncio.sleep(0)
    window.open(schedule)

    assert len(scheduled) == 1
    assert window.is_open is True

    release.set()
    await scheduled[0]


async def test_about_window_close_releases_state() -> None:
    async def fake_runner(target):
        page = FakePage()
        await target(page)
        await asyncio.Event().wait()

    window = AboutWindow(app_runner=fake_runner)
    window.open(lambda coro: asyncio.get_running_loop().create_task(coro))
    await asyncio.sleep(0)
    assert window.is_open is True

    await window.close()
    assert window.is_open is False
    assert window._page is None
