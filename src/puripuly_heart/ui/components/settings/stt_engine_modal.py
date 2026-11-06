"""Speech recognition engine selection modal.

The engine list contains an off switch and one honest engine card
("transcribe.cpp") carrying a three-state backend control (CPU / Vulkan API /
CUDA). Clicking the card selects the engine with the shown backend; clicking a
segment selects the engine with that backend. Unavailable backends are greyed
out and inert.
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

_SEGMENT_WIDTH = 120
_SEGMENT_HEIGHT = 40
_GROUP_RADIUS = 12


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
        return self._build_option_card(
            self._none_label,
            selected=self._current_engine == "",
            on_click=self._select_none,
        )

    def _build_option_card(self, label: str, *, selected: bool, on_click) -> ft.Control:
        text_color = ft.Colors.WHITE if selected else COLOR_ON_BACKGROUND
        return ft.Container(
            content=ft.Text(
                label,
                size=20,
                color=text_color,
                weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
            ),
            bgcolor=COLOR_PRIMARY if selected else COLOR_BACKGROUND,
            border_radius=16,
            padding=ft.Padding.all(24),
            alignment=ft.Alignment.CENTER,
            on_click=on_click,
            on_hover=self._on_item_hover,
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            height=110,
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
                        size=15,
                        color=desc_color,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                spacing=6,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(vertical=4),
            on_click=lambda _e: self._select_engine(self._current_backend),
            on_hover=self._on_item_hover,
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        return ft.Container(
            content=ft.Column(
                [
                    label_area,
                    ft.Container(
                        content=self._build_backend_toggle(card_selected=selected),
                        alignment=ft.Alignment.CENTER,
                    ),
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=COLOR_PRIMARY if selected else COLOR_BACKGROUND,
            border_radius=16,
            padding=ft.Padding.all(24),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )

    def _build_backend_toggle(self, *, card_selected: bool) -> ft.Control:
        segments = [
            self._build_backend_segment(value, label, card_selected=card_selected)
            for value, label in ENGINE_BACKENDS
        ]
        group_border = (
            ft.Border.all(1, ft.Colors.with_opacity(0.5, ft.Colors.WHITE))
            if card_selected
            else ft.Border.all(1, ft.Colors.with_opacity(0.35, COLOR_NEUTRAL_DARK))
        )
        return ft.Container(
            content=ft.Row(controls=segments, spacing=0),
            border=group_border,
            border_radius=_GROUP_RADIUS,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

    def _build_backend_segment(
        self,
        value: str,
        label: str,
        *,
        card_selected: bool,
    ) -> ft.Control:
        available = self._backend_available.get(value, True)
        is_selected = available and self._current_backend == value
        if card_selected:
            hover_bg = ft.Colors.with_opacity(0.14, ft.Colors.WHITE)
            selected_bg = ft.Colors.WHITE
            text_color = (
                COLOR_PRIMARY
                if is_selected
                else ft.Colors.with_opacity(0.9, ft.Colors.WHITE)
            )
        else:
            hover_bg = ft.Colors.with_opacity(0.12, COLOR_PRIMARY)
            selected_bg = COLOR_PRIMARY
            text_color = ft.Colors.WHITE if is_selected else COLOR_ON_BACKGROUND
        if not available:
            text_color = ft.Colors.with_opacity(0.4, text_color)
        base_bg = selected_bg if is_selected else None

        def on_hover(
            e: ft.ControlEvent,
            base: object = base_bg,
            hover: object = hover_bg,
        ) -> None:
            container = e.control
            container.bgcolor = hover if is_hover_active(e) else base
            container.update()

        segment = ft.Container(
            content=ft.Text(
                label,
                size=15,
                weight=ft.FontWeight.BOLD,
                color=text_color,
                text_align=ft.TextAlign.CENTER,
            ),
            width=_SEGMENT_WIDTH,
            height=_SEGMENT_HEIGHT,
            alignment=ft.Alignment.CENTER,
            bgcolor=base_bg,
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        if available:
            segment.on_click = lambda _e, val=value: self._select_engine(val)
            segment.on_hover = on_hover
        return segment

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
