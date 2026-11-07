"""Owns the separate About window (its own Flet session)."""

from __future__ import annotations

import contextlib
import inspect
from typing import Any, Callable

import flet as ft

from puripuly_heart.runtime_layout import current_runtime_layout
from puripuly_heart.ui.components.title_bar import TitleBar
from puripuly_heart.ui.fonts import (
    assets_dir,
    register_ui_fonts,
    typeface_for_language,
)
from puripuly_heart.ui.foundation.resources import DEFAULT_FOUNDATION_RESOURCES
from puripuly_heart.ui.foundation.tokens import FOUNDATION_DESIGN_TOKENS
from puripuly_heart.ui.flet_runtime import invoke_control_method, run_control_method
from puripuly_heart.ui.i18n import get_locale, t
from puripuly_heart.ui.theme import COLOR_BACKGROUND, get_app_theme
from puripuly_heart.ui.views.about import AboutView

ABOUT_WINDOW_WIDTH = 960
ABOUT_WINDOW_HEIGHT = 540
ABOUT_CONTENT_PADDING = FOUNDATION_DESIGN_TOKENS.spacing.page


async def _default_about_app_runner(target: Callable[[Any], Any]) -> None:
    """Run the About Flet session, bypassing the main window's single-view owner."""
    from puripuly_heart.ui.flet_desktop_runtime import patch_hidden_view_launcher

    run_kwargs = dict(
        main=target,
        view=ft.AppView.FLET_APP_HIDDEN,
        assets_dir=str(assets_dir()),
    )
    if current_runtime_layout().host_kind == "native":
        await ft.run_async(**run_kwargs)
    else:
        with patch_hidden_view_launcher():
            await ft.run_async(**run_kwargs)


class AboutWindow:
    """Owns the separate About window (its own Flet session)."""

    def __init__(self, *, app_runner: Callable[[Any], Any] | None = None) -> None:
        self._app_runner = app_runner
        self._page: Any | None = None
        self._view: Any | None = None
        self._title_bar: Any | None = None
        self._task: Any | None = None

    @property
    def is_open(self) -> bool:
        return self._task is not None and not self._task.done()

    def open(self, schedule: Callable[[Any], Any]) -> None:
        """Open the About window, or bring the existing one to the front."""
        if self.is_open:
            self._raise_existing()
            return
        self._task = schedule(self._run)

    def apply_locale(self) -> None:
        """Refresh About window text for the active locale."""
        if self._view is not None:
            self._view.apply_locale()
        if self._title_bar is not None:
            self._title_bar.apply_locale()
        if self._page is not None:
            with contextlib.suppress(Exception):
                self._page.title = t("about.title")
                self._page.update()

    async def close(self) -> None:
        """Close the About window and finish its session."""
        page, task = self._page, self._task
        self._page = None
        self._view = None
        self._title_bar = None
        self._task = None
        if page is not None:
            with contextlib.suppress(Exception):
                await invoke_control_method(page.window, "close")
        if task is not None:
            with contextlib.suppress(BaseException):
                task.cancel()
            if inspect.isawaitable(task):
                with contextlib.suppress(BaseException):
                    await task

    def configure_page(self, page: Any) -> None:
        """Apply About window defaults: 960x540, resizable, main-window style."""
        page.title = t("about.title")
        page.theme_mode = ft.ThemeMode.LIGHT
        register_ui_fonts(page)
        page.theme = get_app_theme(face=typeface_for_language(get_locale()))
        page.bgcolor = COLOR_BACKGROUND
        page.padding = 0
        page.window.frameless = True
        page.window.resizable = True
        page.window.maximizable = True
        page.window.width = ABOUT_WINDOW_WIDTH
        page.window.height = ABOUT_WINDOW_HEIGHT
        page.window.icon = str(
            DEFAULT_FOUNDATION_RESOURCES.require_file(FOUNDATION_DESIGN_TOKENS.icon_asset)
        )

    def _raise_existing(self) -> None:
        if self._page is None:
            return
        with contextlib.suppress(Exception):
            run_control_method(self._page.window, "to_front")

    def _on_close_requested(self) -> None:
        if self._page is None:
            return
        with contextlib.suppress(Exception):
            run_control_method(self._page.window, "close")

    async def _run(self) -> None:
        runner = self._app_runner or _default_about_app_runner

        async def target(page: Any) -> None:
            self._page = page
            self.configure_page(page)
            self._view = AboutView()
            self._title_bar = TitleBar(page, on_close=self._on_close_requested)
            page.add(
                ft.Column(
                    controls=[
                        self._title_bar,
                        ft.Container(
                            content=self._view,
                            expand=True,
                            padding=ABOUT_CONTENT_PADDING,
                        ),
                    ],
                    expand=True,
                    spacing=0,
                )
            )
            page.window.visible = True
            page.update()

        try:
            await runner(target)
        finally:
            self._page = None
            self._view = None
            self._title_bar = None
