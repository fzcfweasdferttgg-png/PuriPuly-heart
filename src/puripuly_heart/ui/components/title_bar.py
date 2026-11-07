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
    COLOR_SURFACE,
)

ABOUT_MENU_ICON = ft.Icons.FAVORITE


class TitleBar(ft.Container):
    """Custom draggable title bar with an About item and window controls."""

    def __init__(
        self,
        page: ft.Page,
        *,
        on_close: Callable[[], None],
        on_about: Callable[[], None] | None = None,
        show_title: bool = False,
        show_minimize: bool = True,
    ):
        self._page = page
        self._on_close = on_close
        self._on_about = on_about

        self._about_icon = ft.Icon(ABOUT_MENU_ICON, size=16, color=COLOR_PRIMARY)
        self._about_label = ft.Text(t("about.title"), size=14, color=COLOR_NEUTRAL_DARK)
        self._about_item = None
        if on_about is not None or show_title:
            self._about_item = ft.Container(
                content=ft.Row([self._about_icon, self._about_label], spacing=8),
                height=36,
                padding=ft.Padding.only(left=12, right=12),
                border_radius=10,
                border=ft.Border.all(1, COLOR_DIVIDER),
                bgcolor=COLOR_SURFACE,
                on_click=self._on_about_click if on_about is not None else None,
                on_hover=self._on_about_hover if on_about is not None else None,
            )

        minimize_btn = ft.Container(
            content=ft.Icon(ft.Icons.REMOVE, size=18, color=COLOR_SECONDARY),
            width=40,
            height=40,
            alignment=ft.Alignment.CENTER,
            border_radius=10,
            bgcolor=COLOR_SURFACE,
            on_click=self._minimize,
            on_hover=self._on_btn_hover,
        )

        self._close_btn = ft.Container(
            content=ft.Icon(ft.Icons.CLOSE, size=18, color=COLOR_SECONDARY),
            width=40,
            height=40,
            alignment=ft.Alignment.CENTER,
            border_radius=ft.BorderRadius.only(top_right=16),
            bgcolor=COLOR_SURFACE,
            on_click=self._close,
            on_hover=self._on_close_hover,
        )

        buttons = []
        if show_minimize:
            buttons.append(minimize_btn)
        buttons.append(self._close_btn)
        self._window_controls = ft.Row(buttons, spacing=0)

        drag_area = ft.WindowDragArea(
            content=ft.Container(expand=True, height=48),
            expand=True,
        )

        controls: list[ft.Control] = []
        if self._about_item is not None and on_about is None:
            controls.append(self._about_item)
        controls.append(drag_area)
        if on_about is not None:
            controls.append(self._about_item)
            controls.append(
                ft.VerticalDivider(width=1, thickness=1, color=COLOR_DIVIDER)
            )
        controls.append(self._window_controls)

        super().__init__(
            content=ft.Row(controls, expand=True, spacing=8),
            bgcolor=COLOR_BACKGROUND,
            height=48,
            border_radius=ft.BorderRadius.only(top_left=16, top_right=16),
            border=ft.Border.only(bottom=ft.BorderSide(1, COLOR_DIVIDER)),
        )

    def apply_locale(self) -> None:
        """Refresh the About label when the locale changes."""
        self._about_label.value = t("about.title")
        update_control_if_mounted(self._about_label)

    def _on_about_click(self, _):
        if self._on_about is not None:
            self._on_about()

    def _on_about_hover(self, e):
        active = is_hover_active(e)
        self._about_item.bgcolor = COLOR_BACKGROUND if active else COLOR_SURFACE
        self._about_label.color = COLOR_PRIMARY if active else COLOR_NEUTRAL_DARK
        update_control_if_mounted(self._about_item)

    def _minimize(self, _):
        self._page.window.minimized = True
        self._page.update()

    def _close(self, _):
        self._on_close()

    def _on_btn_hover(self, e):
        container = e.control
        icon = container.content
        if is_hover_active(e):
            container.bgcolor = COLOR_DIVIDER
            icon.color = COLOR_NEUTRAL_DARK
        else:
            container.bgcolor = COLOR_SURFACE
            icon.color = COLOR_SECONDARY
        update_control_if_mounted(container)

    def _on_close_hover(self, e):
        container = e.control
        icon = container.content
        if is_hover_active(e):
            container.bgcolor = COLOR_ERROR
            icon.color = ft.Colors.WHITE
        else:
            container.bgcolor = COLOR_SURFACE
            icon.color = COLOR_SECONDARY
        update_control_if_mounted(container)
