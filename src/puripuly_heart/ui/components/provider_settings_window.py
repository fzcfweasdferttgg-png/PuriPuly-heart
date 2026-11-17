"""Owns the separate provider settings window (its own Flet session)."""

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
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_DIVIDER,
    COLOR_ERROR,
    COLOR_NEUTRAL_DARK,
    COLOR_SECONDARY,
    COLOR_SURFACE,
    get_app_theme,
)

PROVIDER_WINDOW_WIDTH = 680
PROVIDER_WINDOW_HEIGHT = 450
ANTHROPIC_PROVIDER_WINDOW_HEIGHT = 550
LOCAL_PROVIDER_WINDOW_HEIGHT = 765
PROVIDER_CONTENT_PADDING = FOUNDATION_DESIGN_TOKENS.spacing.page
RESIZE_GRIP_SIZE = 6
RESIZE_CORNER_SIZE = 16
PROVIDER_SETTINGS_WINDOW_KINDS = (
    "translation_openai",
    "translation_anthropic",
    "translation_local",
    "backup_openai",
    "backup_anthropic",
    "backup_local",
)


def _resize_zones() -> tuple[tuple[ft.WindowResizeEdge, dict[str, int]], ...]:
    """Edge and corner zones forwarded to native window resizing."""
    return (
        (
            ft.WindowResizeEdge.TOP,
            dict(top=0, left=RESIZE_CORNER_SIZE, right=RESIZE_CORNER_SIZE, height=RESIZE_GRIP_SIZE),
        ),
        (
            ft.WindowResizeEdge.BOTTOM,
            dict(bottom=0, left=RESIZE_CORNER_SIZE, right=RESIZE_CORNER_SIZE, height=RESIZE_GRIP_SIZE),
        ),
        (
            ft.WindowResizeEdge.LEFT,
            dict(left=0, top=RESIZE_CORNER_SIZE, bottom=RESIZE_CORNER_SIZE, width=RESIZE_GRIP_SIZE),
        ),
        (
            ft.WindowResizeEdge.RIGHT,
            dict(right=0, top=RESIZE_CORNER_SIZE, bottom=RESIZE_CORNER_SIZE, width=RESIZE_GRIP_SIZE),
        ),
        (
            ft.WindowResizeEdge.TOP_LEFT,
            dict(top=0, left=0, width=RESIZE_CORNER_SIZE, height=RESIZE_CORNER_SIZE),
        ),
        (
            ft.WindowResizeEdge.TOP_RIGHT,
            dict(top=0, right=0, width=RESIZE_CORNER_SIZE, height=RESIZE_CORNER_SIZE),
        ),
        (
            ft.WindowResizeEdge.BOTTOM_LEFT,
            dict(bottom=0, left=0, width=RESIZE_CORNER_SIZE, height=RESIZE_CORNER_SIZE),
        ),
        (
            ft.WindowResizeEdge.BOTTOM_RIGHT,
            dict(bottom=0, right=0, width=RESIZE_CORNER_SIZE, height=RESIZE_CORNER_SIZE),
        ),
    )


async def _default_provider_app_runner(target: Callable[[Any], Any]) -> None:
    """Run the settings Flet session, bypassing the main window's single-view owner."""
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


class ProviderSettingsWindow:
    """Owns the separate provider settings window (its own Flet session).

    The window shows a form supplied by the settings view and an Accept
    button in the bottom-right corner. Accept runs ``on_accept`` and closes
    the window; closing without accepting runs ``on_close_request`` so the
    caller can discard staged edits.
    """

    def __init__(self, *, app_runner: Callable[[Any], Any] | None = None) -> None:
        self._app_runner = app_runner
        self._page: Any | None = None
        self._title_bar: Any | None = None
        self._form_host: Any | None = None
        self._task: Any | None = None
        self._title: str = ""
        self._content: Any | None = None
        self._height = PROVIDER_WINDOW_HEIGHT
        self._on_accept: Callable[[], None] | None = None
        self._on_close_request: Callable[[], None] | None = None
        self._accepted = False
        self._generation = 0

    @property
    def is_open(self) -> bool:
        return self._task is not None and not self._task.done()

    def open(
        self,
        schedule: Callable[[Any], Any],
        *,
        title: str,
        content: Any,
        on_accept: Callable[[], None],
        on_close_request: Callable[[], None],
        height: int = PROVIDER_WINDOW_HEIGHT,
    ) -> None:
        """Open the settings window, or bring the existing one to the front."""
        if self.is_open:
            self._raise_existing()
            return
        self._title = title
        self._content = content
        self._height = height
        self._on_accept = on_accept
        self._on_close_request = on_close_request
        self._accepted = False
        self._generation += 1
        generation = self._generation

        async def run_generation() -> None:
            await self._run(generation)

        self._task = schedule(run_generation)

    def replace_content(
        self,
        content,
        *,
        title: str | None = None,
        height: int | None = None,
    ) -> None:
        """Swap the mounted form, title, and height as one window-state update."""
        self._content = content
        if title is not None:
            self._title = title
        if height is not None:
            self._height = height
        if self._page is not None and self.is_open:
            with contextlib.suppress(Exception):
                if self._form_host is not None:
                    self._form_host.content = self._content
                if self._title_bar is not None:
                    self._title_bar.set_title(self._title)
                self._page.title = self._title
                self._page.window.height = self._height
                self._page.update()

    async def close(self) -> None:
        """Close the settings window and finish its session."""
        page, task = self._page, self._task
        close_request = self._on_close_request
        accepted = self._accepted
        self._generation += 1
        self._page = None
        self._title_bar = None
        self._form_host = None
        self._content = None
        self._task = None
        self._on_accept = None
        self._on_close_request = None
        if page is not None:
            with contextlib.suppress(Exception):
                await invoke_control_method(page.window, "close")
        if task is not None:
            with contextlib.suppress(BaseException):
                task.cancel()
            if inspect.isawaitable(task):
                with contextlib.suppress(BaseException):
                    await task
        if not accepted and close_request is not None:
            with contextlib.suppress(Exception):
                close_request()

    def configure_page(self, page: Any) -> None:
        """Apply window defaults: compact size, resizable, main-window style."""
        page.title = self._title
        page.theme_mode = ft.ThemeMode.LIGHT
        register_ui_fonts(page)
        page.theme = get_app_theme(face=typeface_for_language(get_locale()))
        page.bgcolor = ft.Colors.TRANSPARENT
        page.padding = 0
        page.window.frameless = True
        page.window.resizable = True
        page.window.maximizable = True
        page.window.width = PROVIDER_WINDOW_WIDTH
        page.window.height = self._height
        page.window.bgcolor = ft.Colors.TRANSPARENT
        page.window.icon = str(
            DEFAULT_FOUNDATION_RESOURCES.require_file(FOUNDATION_DESIGN_TOKENS.icon_asset)
        )

    def _build_resize_grips(self) -> list[ft.Control]:
        grips: list[ft.Control] = []
        for edge, position in _resize_zones():
            grips.append(
                ft.Container(
                    content=ft.GestureDetector(
                        content=ft.Container(bgcolor=ft.Colors.TRANSPARENT),
                        on_tap_down=lambda _e, edge=edge: self._start_resize(edge),
                    ),
                    **position,
                )
            )
        return grips

    def _start_resize(self, edge: ft.WindowResizeEdge) -> None:
        if self._page is None:
            return
        with contextlib.suppress(Exception):
            run_control_method(self._page.window, "start_resizing", edge)

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

    def _on_accept_click(self, _e) -> None:
        self._accepted = True
        accept = self._on_accept
        if accept is not None:
            with contextlib.suppress(Exception):
                accept()
        self._on_close_requested()

    def _build_accept_button(self) -> ft.Control:
        label = ft.Text(
            t("settings.provider_form.accept"),
            size=16,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.WHITE,
        )
        return ft.Container(
            content=label,
            padding=ft.Padding.symmetric(horizontal=20, vertical=10),
            border_radius=10,
            bgcolor=COLOR_SECONDARY,
            on_click=self._on_accept_click,
            on_hover=self._on_accept_hover,
        )

    def _on_accept_hover(self, e) -> None:
        container = e.control
        if getattr(e, "data", "") == "true":
            container.bgcolor = COLOR_ERROR
        else:
            container.bgcolor = COLOR_SECONDARY
        with contextlib.suppress(Exception):
            container.update()

    def _mount(self, page: Any) -> None:
        """Build the settings window content on the given page."""
        self._page = page
        self.configure_page(page)
        self._title_bar = TitleBar(
            page,
            on_close=self._on_close_requested,
            show_title=True,
            show_minimize=False,
            title_text=self._title,
        )
        self._form_host = ft.Container(content=self._content)
        content = ft.Container(
            content=ft.Column(
            controls=[
                self._title_bar,
                ft.Container(
                    content=ft.Column(
                        [
                            self._form_host,
                            ft.Container(height=16),
                            ft.Row(
                                [ft.Container(expand=True), self._build_accept_button()],
                                alignment=ft.MainAxisAlignment.END,
                            ),
                        ],
                        spacing=0,
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    expand=True,
                    padding=PROVIDER_CONTENT_PADDING,
                ),
            ],
            expand=True,
            spacing=0,
            ),
            expand=True,
            bgcolor=COLOR_BACKGROUND,
            border=ft.Border.all(1, COLOR_DIVIDER),
            border_radius=16,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )
        page.add(ft.Stack([content, *self._build_resize_grips()], fit=ft.StackFit.EXPAND, expand=True))
        page.window.visible = True
        page.update()

    async def _run(self, generation: int) -> None:
        if generation != self._generation:
            return
        runner = self._app_runner or _default_provider_app_runner

        async def target(page: Any) -> None:
            if generation != self._generation:
                with contextlib.suppress(Exception):
                    await invoke_control_method(page.window, "close")
                return
            self._mount(page)

        try:
            await runner(target)
        finally:
            if generation == self._generation:
                self._page = None
                self._title_bar = None
                self._form_host = None
                self._content = None
                close_request = self._on_close_request
                self._on_accept = None
                self._on_close_request = None
                if not self._accepted and close_request is not None:
                    with contextlib.suppress(Exception):
                        close_request()


def create_provider_settings_windows() -> dict[str, ProviderSettingsWindow]:
    return {kind: ProviderSettingsWindow() for kind in PROVIDER_SETTINGS_WINDOW_KINDS}
