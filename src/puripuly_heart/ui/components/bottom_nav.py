from typing import Callable

import flet as ft

from puripuly_heart.ui.flet_runtime import is_hover_active
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_DIVIDER,
    COLOR_PRIMARY,
    COLOR_SECONDARY,
)


class BottomNavBar(ft.Container):
    """Bottom 3-tab navigation bar with a model-assets launcher, icons only."""

    def __init__(
        self,
        on_change: Callable[[int], None],
        *,
        on_open_models: Callable[[], None] | None = None,
    ):
        self._on_change = on_change
        self._on_open_models = on_open_models
        self._selected = 0
        self._launcher_icon: ft.Icon | None = None

        self._tabs = [
            ft.Icons.GRID_VIEW,
            ft.Icons.SETTINGS,
            ft.Icons.ARTICLE,
        ]

        self._icons: list[ft.Icon] = []
        self._tab_containers: list[ft.Container] = []
        self._build_tabs()

        tabs_row = ft.Row(
            controls=self._build_tabs_with_dividers(),
            expand=True,
            spacing=0,
        )

        super().__init__(
            content=tabs_row,
            bgcolor=COLOR_BACKGROUND,
            height=80,
            border=ft.Border.only(top=ft.BorderSide(1, COLOR_DIVIDER)),
        )

    def _build_tabs(self):
        """Build tab containers with icons only."""
        self._tab_containers = []
        self._icons = []
        for i, icon_name in enumerate(self._tabs):
            is_selected = i == self._selected
            icon_color = COLOR_PRIMARY if is_selected else COLOR_SECONDARY

            icon = ft.Icon(icon=icon_name, size=30, color=icon_color)
            self._icons.append(icon)

            container = ft.Container(
                content=icon,
                expand=True,
                alignment=ft.Alignment.CENTER,
                on_click=lambda _, idx=i: self._on_tab_click(idx),
                on_hover=lambda e, idx=i: self._on_tab_hover(e, idx),
            )
            self._tab_containers.append(container)

    def _build_tabs_with_dividers(self) -> list[ft.Control]:
        """Build tabs with vertical dividers between them."""
        result: list[ft.Control] = []
        for i, tab in enumerate(self._tab_containers):
            result.append(tab)
            if i < len(self._tab_containers) - 1:
                result.append(ft.VerticalDivider(width=1, color=COLOR_DIVIDER, thickness=1))
        launcher = self._build_launcher()
        if launcher is not None:
            result.append(ft.VerticalDivider(width=1, color=COLOR_DIVIDER, thickness=1))
            result.append(launcher)
        return result

    def _build_launcher(self) -> ft.Container | None:
        """Model-assets launcher button: opens the window, never changes the tab."""
        if self._on_open_models is None:
            return None
        self._launcher_icon = ft.Icon(
            icon=ft.Icons.DOWNLOAD_FOR_OFFLINE, size=30, color=COLOR_SECONDARY
        )
        return ft.Container(
            content=self._launcher_icon,
            expand=True,
            alignment=ft.Alignment.CENTER,
            on_click=self._on_launcher_click,
            on_hover=self._on_launcher_hover,
        )

    def _on_launcher_click(self, _event) -> None:
        if self._on_open_models is not None:
            self._on_open_models()

    def _on_launcher_hover(self, e) -> None:
        if self._launcher_icon is None:
            return
        self._launcher_icon.color = COLOR_PRIMARY if is_hover_active(e) else COLOR_SECONDARY
        self._launcher_icon.update()

    def _on_tab_click(self, index: int):
        """Handle tab click."""
        if self._selected != index:
            self._selected = index
            self._update_visuals()
            self._on_change(index)

    def _on_tab_hover(self, e, index: int):
        """Handle tab hover."""
        if index != self._selected:
            icon = self._icons[index]
            icon.color = COLOR_PRIMARY if is_hover_active(e) else COLOR_SECONDARY
            icon.update()

    def _update_visuals(self):
        """Update all tab visuals based on selection."""
        for i, icon in enumerate(self._icons):
            icon.color = COLOR_PRIMARY if i == self._selected else COLOR_SECONDARY
            icon.update()
