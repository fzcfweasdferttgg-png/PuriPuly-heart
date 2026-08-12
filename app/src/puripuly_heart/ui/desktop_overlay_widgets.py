from __future__ import annotations

from typing import Any, Callable

from puripuly_heart.ui.desktop_overlay_caption_plan import (
    DesktopCaptionLine,
    DesktopCaptionPlan,
    DesktopCaptionSlot,
    _DESKTOP_CAPTION_AMBIENT_SHADOW_BLUR,
    _DESKTOP_CAPTION_AMBIENT_SHADOW_COLOR,
    _DESKTOP_CAPTION_AMBIENT_SHADOW_OFFSET,
    _DESKTOP_CAPTION_CONTACT_SHADOW_BLUR,
    _DESKTOP_CAPTION_CONTACT_SHADOW_COLOR,
    _DESKTOP_CAPTION_CONTACT_SHADOW_OFFSET,
    _DESKTOP_CAPTION_PRIMARY_REGION_ALIGNMENT_Y,
    _DESKTOP_CAPTION_SECONDARY_MAX_LINES,
    _DESKTOP_CAPTION_TEXT_STACK_ALIGNMENT_Y,
    _DESKTOP_CAPTION_WHITE,
    _DESKTOP_EMPTY_LOCK_ACTION_DEFAULT_COLOR,
    _DESKTOP_EMPTY_LOCK_ACTION_FOCUS_COLOR,
    _DESKTOP_EMPTY_LOCK_ACTION_HORIZONTAL_PADDING,
    _DESKTOP_EMPTY_LOCK_ACTION_MIN_HIT_TARGET,
    _DESKTOP_EMPTY_LOCK_ACTION_TEXT_WIDTH_SAFETY,
    _DESKTOP_EMPTY_LOCK_ACTION_VERTICAL_PADDING,
    _clamp,
    _desktop_caption_font_family_for_text,
    _estimated_caption_line_width,
)


def _background_transparency_label_for_alpha(background_alpha: float) -> str:
    transparency = 1.0 - _clamp(background_alpha, 0.0, 1.0)
    return f"{int(round(transparency * 100))}%"


def _desktop_empty_lock_action_font_size(plan: DesktopCaptionPlan) -> int:
    return max(_DESKTOP_EMPTY_LOCK_ACTION_MIN_HIT_TARGET, plan.primary_font_size)


def _desktop_empty_lock_action_width(label: str, font_size: int) -> float:
    return max(
        _DESKTOP_EMPTY_LOCK_ACTION_MIN_HIT_TARGET,
        _estimated_caption_line_width(label, font_size)
        + (_DESKTOP_EMPTY_LOCK_ACTION_HORIZONTAL_PADDING * 2)
        + _DESKTOP_EMPTY_LOCK_ACTION_TEXT_WIDTH_SAFETY,
    )


def build_desktop_empty_lock_action(
    plan: DesktopCaptionPlan,
    *,
    label: str,
    on_click: Callable[[object], object] | None,
) -> Any:
    """Build the bounded text-only lock action shown in empty moving mode."""

    import flet as ft

    font_size = _desktop_empty_lock_action_font_size(plan)
    text_style = ft.TextStyle(
        size=font_size,
        height=1.0,
        weight=ft.FontWeight.BOLD,
        font_family=_desktop_caption_font_family_for_text(label),
        shadow=_caption_text_shadow(ft),
        decoration=None,
    )
    return ft.TextButton(
        text=label,
        tooltip=label,
        on_click=on_click,
        width=_desktop_empty_lock_action_width(label, font_size),
        height=max(
            _DESKTOP_EMPTY_LOCK_ACTION_MIN_HIT_TARGET,
            font_size + (_DESKTOP_EMPTY_LOCK_ACTION_VERTICAL_PADDING * 2),
        ),
        style=ft.ButtonStyle(
            color={
                ft.ControlState.DEFAULT: _DESKTOP_EMPTY_LOCK_ACTION_DEFAULT_COLOR,
                ft.ControlState.HOVERED: _DESKTOP_EMPTY_LOCK_ACTION_FOCUS_COLOR,
                ft.ControlState.FOCUSED: _DESKTOP_EMPTY_LOCK_ACTION_FOCUS_COLOR,
            },
            bgcolor=ft.Colors.TRANSPARENT,
            overlay_color=ft.Colors.TRANSPARENT,
            elevation=0,
            padding=ft.padding.symmetric(
                horizontal=_DESKTOP_EMPTY_LOCK_ACTION_HORIZONTAL_PADDING,
                vertical=_DESKTOP_EMPTY_LOCK_ACTION_VERTICAL_PADDING,
            ),
            text_style=text_style,
            mouse_cursor=ft.MouseCursor.CLICK,
            animation_duration=0,
        ),
    )


def build_desktop_caption_surface(plan: DesktopCaptionPlan) -> Any:
    """Build no-outline fixed-slot Flet caption controls from a caption plan."""

    import flet as ft

    stack_controls: list[Any] = []
    if plan.full_window_background_visible:
        stack_controls.append(
            ft.Container(
                bgcolor=plan.background_color,
                border_radius=plan.border_radius,
                alignment=ft.alignment.center,
                left=0,
                top=0,
                right=0,
                bottom=0,
            )
        )
    slot_controls = [_build_flet_caption_slot(ft, plan, slot) for slot in plan.slots]
    if slot_controls:
        slot_stack_height = (plan.slot_height * len(slot_controls)) + (
            plan.slot_gap * max(0, len(slot_controls) - 1)
        )
        stack_controls.append(
            ft.Column(
                controls=slot_controls,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=plan.slot_gap,
                tight=True,
                width=plan.window_width,
                height=slot_stack_height,
            )
        )
    return ft.Container(
        content=ft.Stack(
            controls=stack_controls,
            width=plan.window_width,
            height=plan.window_height,
        ),
        width=plan.window_width,
        height=plan.window_height,
        bgcolor=ft.Colors.TRANSPARENT,
        border_radius=plan.border_radius,
        alignment=ft.alignment.center,
        visible=plan.surface_visible,
    )


def build_desktop_transparent_sizing_host(plan: DesktopCaptionPlan) -> Any:
    """Build a transparent, layout-stable host for locked empty runtime state."""

    import flet as ft

    return ft.Container(
        width=plan.window_width,
        height=plan.window_height,
        bgcolor=ft.Colors.TRANSPARENT,
        alignment=ft.alignment.center,
    )


def _build_flet_caption_slot(ft: Any, plan: DesktopCaptionPlan, slot: DesktopCaptionSlot) -> Any:
    if plan.full_window_background_visible:
        card_text_width = plan.text_width
        card_width = plan.window_width
    else:
        card_text_width = slot.card_text_width or plan.text_width
        card_width = slot.card_width or plan.window_width
    slot_lines = _slot_lines_with_reserved_regions(
        slot,
        secondary_font_size=plan.secondary_font_size,
        font_family=slot.lines[0].font_family if slot.lines else None,
    )
    has_secondary_region = any(line.slot == "secondary" for line in slot_lines)
    line_controls = [
        _build_flet_caption_line(
            ft,
            plan,
            line,
            text_width=card_text_width,
            center_primary_region=not has_secondary_region,
        )
        for line in slot_lines
    ]
    column = ft.Column(
        controls=line_controls,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=0,
        tight=True,
        scroll=None,
    )
    text_layer = ft.Container(
        content=column,
        width=card_text_width,
        bgcolor=ft.Colors.TRANSPARENT,
        alignment=(
            ft.Alignment(0, _DESKTOP_CAPTION_TEXT_STACK_ALIGNMENT_Y)
            if has_secondary_region
            else ft.alignment.center
        ),
    )
    inner_card = ft.Container(
        content=text_layer,
        width=card_width,
        height=plan.slot_height,
        bgcolor=(
            ft.Colors.TRANSPARENT if plan.full_window_background_visible else plan.background_color
        ),
        border_radius=plan.border_radius,
        padding=ft.padding.symmetric(
            horizontal=plan.padding_horizontal,
            vertical=plan.padding_vertical,
        ),
        alignment=ft.alignment.center,
    )
    return ft.Container(
        content=inner_card,
        width=plan.window_width,
        height=plan.slot_height,
        bgcolor=ft.Colors.TRANSPARENT,
        alignment=ft.alignment.center,
    )


def _build_flet_caption_line(
    ft: Any,
    plan: DesktopCaptionPlan,
    line: DesktopCaptionLine,
    *,
    text_width: float,
    center_primary_region: bool = False,
) -> Any:
    height = plan.primary_region_height if line.slot == "primary" else plan.secondary_region_height
    return ft.Container(
        content=_build_flet_text(ft, line, text_width),
        width=text_width,
        height=height,
        bgcolor=ft.Colors.TRANSPARENT,
        alignment=_caption_line_region_alignment(
            ft,
            line,
            center_primary_region=center_primary_region,
        ),
    )


def _caption_line_region_alignment(
    ft: Any,
    line: DesktopCaptionLine,
    *,
    center_primary_region: bool = False,
) -> Any:
    if line.slot == "primary":
        if center_primary_region:
            return ft.alignment.center
        return ft.Alignment(0, _DESKTOP_CAPTION_PRIMARY_REGION_ALIGNMENT_Y)
    return ft.alignment.center


def _slot_lines_with_reserved_regions(
    slot: DesktopCaptionSlot,
    *,
    secondary_font_size: int,
    font_family: str | None,
) -> tuple[DesktopCaptionLine, ...]:
    primary_lines = tuple(line for line in slot.lines if line.slot == "primary")
    secondary_lines = tuple(line for line in slot.lines if line.slot == "secondary")
    if secondary_lines:
        return (*primary_lines, secondary_lines[0])
    if not _slot_should_reserve_empty_secondary_region(slot, primary_lines):
        return primary_lines
    return (
        *primary_lines,
        DesktopCaptionLine(
            text="",
            role="reserved_secondary",
            slot="secondary",
            color=_DESKTOP_CAPTION_WHITE,
            priority=0,
            block_id=slot.block_id,
            channel=slot.channel,
            block_variant=slot.block_variant,
            appearance_seq=slot.appearance_seq,
            max_lines=_DESKTOP_CAPTION_SECONDARY_MAX_LINES,
            font_size=secondary_font_size,
            font_family=font_family,
        ),
    )


def _slot_should_reserve_empty_secondary_region(
    slot: DesktopCaptionSlot,
    primary_lines: tuple[DesktopCaptionLine, ...],
) -> bool:
    if not slot.secondary_enabled:
        return False
    return any(not line.promoted for line in primary_lines)


def _build_flet_text(
    ft: Any,
    line: DesktopCaptionLine,
    text_width: float,
) -> Any:
    return ft.Text(
        value=line.text,
        width=text_width,
        text_align=ft.TextAlign.CENTER,
        font_family=line.font_family,
        size=line.font_size,
        weight=_flet_font_weight(ft, line.weight),
        max_lines=line.max_lines,
        overflow=ft.TextOverflow.ELLIPSIS,
        no_wrap=False,
        color=line.color,
        style=ft.TextStyle(
            size=line.font_size,
            height=line.line_height,
            weight=_flet_font_weight(ft, line.weight),
            font_family=line.font_family,
            shadow=_caption_text_shadow(ft),
            foreground=None,
        ),
    )


def _caption_text_shadow(ft: Any) -> list[Any]:
    return [
        ft.BoxShadow(
            color=_DESKTOP_CAPTION_CONTACT_SHADOW_COLOR,
            offset=_DESKTOP_CAPTION_CONTACT_SHADOW_OFFSET,
            blur_radius=_DESKTOP_CAPTION_CONTACT_SHADOW_BLUR,
        ),
        ft.BoxShadow(
            color=_DESKTOP_CAPTION_AMBIENT_SHADOW_COLOR,
            offset=_DESKTOP_CAPTION_AMBIENT_SHADOW_OFFSET,
            blur_radius=_DESKTOP_CAPTION_AMBIENT_SHADOW_BLUR,
        ),
    ]


def _flet_font_weight(ft: Any, weight: str) -> Any:
    if weight == "semibold":
        return ft.FontWeight.W_600
    if weight == "medium":
        return ft.FontWeight.W_500
    if weight == "bold":
        return ft.FontWeight.BOLD
    return None
