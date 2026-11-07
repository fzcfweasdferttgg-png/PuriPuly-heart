from collections.abc import Callable

import flet as ft

from puripuly_heart.ui.flet_runtime import is_hover_active, update_control_if_mounted
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_DIVIDER,
    COLOR_ERROR,
    COLOR_NEUTRAL_DARK,
    COLOR_PRIMARY,
    COLOR_SECONDARY,
)

ABOUT_MENU_ICON = ft.Icons.FAVORITE


class TitleBar(ft.Container):
    """Custom draggable title bar with an About menu item and window controls."""

    def __init__(
        self,
        page: ft.Page,
        *,
        on_close: Callable[[], None],
        on_about: Callable[[], None] | None = None,
    ):
        self._page = page
        self._on_close = on_close
        self._on_about = on_about

        self._about_icon = ft.Icon(ABOUT_MENU_ICON, size=16, color=COLOR_PRIMARY)
        self._about_label = ft.Text(t("about.title"), size=14, color=COLOR_NEUTRAL_DARK)
        about_item = ft.Container(
            content=ft.Row(
                [self._about_icon, self._about_label],
                spacing=8,
            ),
            height=48,
            padding=ft.Padding.only(left=16, right=12),
            on_click=self._on_about_click if on_about else None,
            on_hover=self._on_about_hover if on_about else None,
        )

        minimize_btn = ft.Container(
            content=ft.Icon(ft.Icons.REMOVE, size=18, color=COLOR_SECONDARY),
            width=40,
            height=40,
            alignment=ft.Alignment.CENTER,
            on_click=self._minimize,
            on_hover=self._on_btn_hover,
        )

        self._close_btn = ft.Container(
            content=ft.Icon(ft.Icons.CLOSE, size=18, color=COLOR_SECONDARY),
            width=40,
            height=40,
            alignment=ft.Alignment.CENTER,
            border_radius=ft.BorderRadius.only(top_right=16),
            on_click=self._close,
            on_hover=self._on_close_hover,
        )

        window_controls = ft.Row(
            [minimize_btn, self._close_btn],
            spacing=0,
        )

        drag_area = ft.WindowDragArea(
            content=ft.Container(expand=True, height=48),
            expand=True,
        )

        super().__init__(
            content=ft.Row(
                [about_item, drag_area, window_controls],
                expand=True,
                spacing=0,
            ),
            bgcolor=COLOR_BACKGROUND,
            height=48,
            border_radius=ft.BorderRadius.only(top_left=16, top_right=16),
            border=ft.Border.only(bottom=ft.BorderSide(1, COLOR_DIVIDER)),
        )

    def apply_locale(self) -> None:
        """Refresh the About menu label when the locale changes."""
        self._about_label.value = t("about.title")
        update_control_if_mounted(self._about_label)

    def _on_about_click(self, _):
        if self._on_about is not None:
            self._on_about()

    def _on_about_hover(self, e):
        active = is_hover_active(e)
        self._about_label.color = COLOR_PRIMARY if active else COLOR_NEUTRAL_DARK
        self._about_icon.color = COLOR_PRIMARY if active else COLOR_SECONDARY
        update_control_if_mounted(self._about_label)
        update_control_if_mounted(self._about_icon)

    def _minimize(self, _):
        self._page.window.minimized = True
        self._page.update()

    def _close(self, _):
        self._on_close()

    def _on_btn_hover(self, e):
        container = e.control
        icon = container.content
        if is_hover_active(e):
            icon.color = COLOR_NEUTRAL_DARK
        else:
            icon.color = COLOR_SECONDARY
        icon.update()

    def _on_close_hover(self, e):
        container = e.control
        icon = container.content
        if is_hover_active(e):
            container.bgcolor = COLOR_ERROR
            icon.color = ft.Colors.WHITE
        else:
            container.bgcolor = ft.Colors.TRANSPARENT
            icon.color = COLOR_SECONDARY
        container.update()
        icon.update()
