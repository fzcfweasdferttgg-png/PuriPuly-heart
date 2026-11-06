"""Speech recognition engine selection modal.

The engine list contains an off switch and one honest engine card
("transcribe.cpp") carrying a three-state backend control (CPU / Vulkan / CUDA).
Clicking the card selects the engine with the shown backend; clicking a segment
selects the engine with that backend. Unavailable backends are greyed out and
inert. The same backend control is reused on the settings surface so the mode
can be switched without reopening this dialog.
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
    ("vulkan", "Vulkan"),
    ("cuda", "CUDA"),
)


def build_backend_toggle(
    current_backend: str,
    backend_available: Mapping[str, bool],
    on_select: Callable[[str], None] | None,
    *,
    compact: bool = False,
) -> ft.Control:
    """Build the connected three-segment CPU / Vulkan / CUDA control."""
    segment_width = 66 if compact else 120
    segment_height = 30 if compact else 40
    font_size = 12 if compact else 15
    group_radius = 10 if compact else 12
    segments: list[ft.Control] = []
    for value, label in ENGINE_BACKENDS:
        available = backend_available.get(value, True)
        is_selected = available and current_backend == value
        text_color = ft.Colors.WHITE if is_selected else COLOR_ON_BACKGROUND
        if not available:
            text_color = ft.Colors.with_opacity(0.4, text_color)
        base_bg = COLOR_PRIMARY if is_selected else None
        hover_bg = ft.Colors.with_opacity(0.12, COLOR_PRIMARY)

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
                size=font_size,
                weight=ft.FontWeight.BOLD,
                color=text_color,
                text_align=ft.TextAlign.CENTER,
            ),
            width=segment_width,
            height=segment_height,
            alignment=ft.Alignment.CENTER,
            bgcolor=base_bg,
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        if available and on_select is not None:
            segment.on_click = lambda _e, val=value: on_select(val)
            segment.on_hover = on_hover
        segments.append(segment)
    return ft.Container(
        content=ft.Row(controls=segments, spacing=0),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.35, COLOR_NEUTRAL_DARK)),
        border_radius=group_radius,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )


def _card_style(selected: bool) -> tuple[object, object, object]:
    if selected:
        return (
            ft.Colors.with_opacity(0.06, COLOR_PRIMARY),
            ft.Border.all(2, COLOR_PRIMARY),
            COLOR_PRIMARY,
        )
    return (
        COLOR_BACKGROUND,
        ft.Border.all(1, ft.Colors.with_opacity(0.25, COLOR_NEUTRAL_DARK)),
        COLOR_ON_BACKGROUND,
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
        bg, border, text_color = _card_style(selected)
        return ft.Container(
            content=ft.Text(
                self._none_label,
                size=20,
                color=text_color,
                weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
            ),
            bgcolor=bg,
            border=border,
            border_radius=16,
            padding=ft.Padding.all(24),
            alignment=ft.Alignment.CENTER,
            on_click=self._select_none,
            on_hover=self._make_card_hover(selected),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            height=110,
        )

    def _build_engine_card(self) -> ft.Control:
        selected = self._current_engine != ""
        bg, border, text_color = _card_style(selected)
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
                        color=COLOR_NEUTRAL_DARK,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                spacing=6,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(vertical=4),
            on_click=lambda _e: self._select_engine(self._current_backend),
            on_hover=self._make_card_hover(selected),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        return ft.Container(
            content=ft.Column(
                [
                    label_area,
                    ft.Container(
                        content=build_backend_toggle(
                            self._current_backend,
                            self._backend_available,
                            self._select_engine,
                        ),
                        alignment=ft.Alignment.CENTER,
                    ),
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=bg,
            border=border,
            border_radius=16,
            padding=ft.Padding.all(24),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )

    def _make_card_hover(self, selected: bool):
        def on_hover(e: ft.ControlEvent) -> None:
            if selected:
                return
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
            if is_hover_active(e):
                text_control.color = COLOR_PRIMARY
                if desc_control is not None:
                    desc_control.color = COLOR_PRIMARY
            else:
                text_control.color = COLOR_ON_BACKGROUND
                if desc_control is not None:
                    desc_control.color = COLOR_NEUTRAL_DARK
            container.update()

        return on_hover

    def _select_none(self, _e) -> None:
        self._close()
        self._on_select_none()

    def _select_engine(self, backend: str) -> None:
        self._close()
        self._on_select_engine(backend)
