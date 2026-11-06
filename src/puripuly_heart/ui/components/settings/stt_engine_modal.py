"""Speech recognition engine selection modal.

The engine list contains an off switch and one honest engine card
("transcribe.cpp") carrying a three-state backend toggle (CPU / Vulkan API /
CUDA). Clicking the card selects the engine with the shown backend; clicking a
toggle segment selects the engine with that backend. Unavailable backends are
greyed out.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import flet as ft

from puripuly_heart.ui.flet_runtime import is_hover_active
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_NEUTRAL_DARK,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SECONDARY,
    COLOR_SURFACE,
)

ENGINE_BACKENDS: tuple[tuple[str, str], ...] = (
    ("cpu", "CPU"),
    ("vulkan", "Vulkan API"),
    ("cuda", "CUDA"),
)


class SttEngineModal:
    """Modal dialog for choosing the recognition engine and its backend."""

    def __init__(
        self,
        page: ft.Page,
        title: str,
        *,
        none_label: str,
        engine_label: str,
        engine_description: str,
        current_engine: str,
        current_backend: str,
        backend_available: Mapping[str, bool],
        on_select_none: Callable[[], None],
        on_select_engine: Callable[[str], None],
    ) -> None:
        self._page = page
        self._title = title
        self._none_label = none_label
        self._engine_label = engine_label
        self._engine_description = engine_description
        self._current_engine = current_engine
        self._current_backend = current_backend
        self._backend_available = dict(backend_available)
        self._on_select_none = on_select_none
        self._on_select_engine = on_select_engine
        self._dialog: ft.AlertDialog | None = None

    def open(self) -> None:
        content = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        self._title,
                        size=24,
                        weight=ft.FontWeight.BOLD,
                        color=COLOR_SECONDARY,
                    ),
                    ft.Container(height=16),
                    self._build_none_card(),
                    ft.Container(height=12),
                    self._build_engine_card(),
                ],
                spacing=8,
            ),
            width=600,
            padding=ft.Padding.symmetric(horizontal=32, vertical=32),
            bgcolor=COLOR_SURFACE,
            border_radius=28,
        )
        self._dialog = ft.AlertDialog(
            modal=False,
            content=content,
            content_padding=0,
            bgcolor=ft.Colors.TRANSPARENT,
        )
        self._page.show_dialog(self._dialog)

    def _close(self) -> None:
        if self._dialog is not None:
            self._page.pop_dialog()

    def _build_none_card(self) -> ft.Control:
        selected = self._current_engine == ""
        return self._build_option_card(
            self._none_label,
            None,
            selected=selected,
            on_click=self._select_none,
        )

    def _build_engine_card(self) -> ft.Control:
        selected = self._current_engine != ""
        text_color = ft.Colors.WHITE if selected else COLOR_ON_BACKGROUND
        desc_color = (
            ft.Colors.with_opacity(0.8, ft.Colors.WHITE)
            if selected
            else COLOR_NEUTRAL_DARK
        )
        label_area = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        self._engine_label,
                        size=20,
                        color=text_color,
                        weight=ft.FontWeight.BOLD,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        self._engine_description,
                        size=16,
                        color=desc_color,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            expand=True,
            on_click=lambda _e: self._select_engine(self._current_backend),
            on_hover=self._on_item_hover,
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        toggle = ft.Row(
            [self._build_backend_segment(value, label) for value, label in ENGINE_BACKENDS],
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        return ft.Container(
            content=ft.Column(
                [label_area, ft.Container(content=toggle, alignment=ft.Alignment.CENTER)],
                spacing=12,
            ),
            bgcolor=COLOR_PRIMARY if selected else COLOR_BACKGROUND,
            border_radius=16,
            padding=ft.Padding.all(24),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )

    def _build_backend_segment(self, value: str, label: str) -> ft.Control:
        available = self._backend_available.get(value, True)
        is_selected = available and self._current_backend == value
        if not available:
            text_color = ft.Colors.with_opacity(0.4, COLOR_ON_BACKGROUND)
            bg_color = COLOR_BACKGROUND
            border = ft.Border.all(1, COLOR_NEUTRAL_DARK)
        elif is_selected:
            text_color = ft.Colors.WHITE
            bg_color = COLOR_PRIMARY
            border = None
        else:
            text_color = COLOR_ON_BACKGROUND
            bg_color = COLOR_BACKGROUND
            border = ft.Border.all(1, COLOR_NEUTRAL_DARK)
        segment = ft.Container(
            content=ft.Text(
                label,
                size=16,
                weight=ft.FontWeight.BOLD,
                color=text_color,
                text_align=ft.TextAlign.CENTER,
            ),
            bgcolor=bg_color,
            border_radius=12,
            border=border,
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            on_click=(None if not available else lambda _e, val=value: self._select_engine(val)),
            on_hover=None if not available else self._on_segment_hover,
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        if not available:
            segment.tooltip = label
        return segment

    def _build_option_card(
        self,
        label: str,
        description: str | None,
        *,
        selected: bool,
        on_click,
    ) -> ft.Control:
        text_color = ft.Colors.WHITE if selected else COLOR_ON_BACKGROUND
        desc_color = (
            ft.Colors.with_opacity(0.8, ft.Colors.WHITE)
            if selected
            else COLOR_NEUTRAL_DARK
        )
        if description:
            content: ft.Control = ft.Column(
                [
                    ft.Text(
                        label,
                        size=20,
                        color=text_color,
                        weight=ft.FontWeight.BOLD,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        description,
                        size=16,
                        color=desc_color,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
        else:
            content = ft.Text(
                label,
                size=20,
                color=text_color,
                weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
            )
        return ft.Container(
            content=content,
            bgcolor=COLOR_PRIMARY if selected else COLOR_BACKGROUND,
            border_radius=16,
            padding=ft.Padding.all(24),
            alignment=ft.Alignment.CENTER,
            on_click=on_click,
            on_hover=self._on_item_hover,
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            height=110,
        )

    def _select_none(self, _e) -> None:
        self._close()
        self._on_select_none()

    def _select_engine(self, backend: str) -> None:
        self._close()
        self._on_select_engine(backend)

    def _on_item_hover(self, e: ft.ControlEvent) -> None:
        container = e.control
        content = container.content
        if isinstance(content, ft.Text):
            text_control = content
            desc_control = None
        elif isinstance(content, ft.Column) and content.controls:
            text_control = content.controls[0]
            desc_control = content.controls[1] if len(content.controls) > 1 else None
        else:
            return
        if text_control.color == ft.Colors.WHITE:
            return
        if is_hover_active(e):
            text_control.color = COLOR_PRIMARY
            if desc_control is not None:
                desc_control.color = COLOR_PRIMARY
        else:
            text_control.color = COLOR_ON_BACKGROUND
            if desc_control is not None:
                desc_control.color = COLOR_NEUTRAL_DARK
        container.update()

    def _on_segment_hover(self, e: ft.ControlEvent) -> None:
        container = e.control
        content = container.content
        if not isinstance(content, ft.Text):
            return
        if content.color == ft.Colors.WHITE:
            return
        content.color = COLOR_PRIMARY if is_hover_active(e) else COLOR_ON_BACKGROUND
        container.update()
