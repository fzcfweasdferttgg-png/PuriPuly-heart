from __future__ import annotations

import flet as ft

from puripuly_heart.ui.about.contract import AboutSurfaceRegions, AboutSurfaceSlots
from puripuly_heart.ui.theme import COLOR_SURFACE

ABOUT_ROW_SPACING = 12
ABOUT_INFO_EXPAND = 2
ABOUT_LICENSES_EXPAND = 3
ABOUT_LICENSE_COMPONENTS_EXPAND = 2
ABOUT_LICENSE_TEXT_EXPAND = 3
LICENSES_CARD_BORDER_RADIUS = 16


def _wrap_card(content: ft.Control, *, expand: int) -> ft.Container:
    return ft.Container(
        content=ft.Container(content=content, expand=True, padding=24),
        bgcolor=COLOR_SURFACE,
        border_radius=LICENSES_CARD_BORDER_RADIUS,
        expand=expand,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )


def compose_about_surface(slots: AboutSurfaceSlots) -> AboutSurfaceRegions:
    """Arrange the adaptive info card and the scrolling license browser card."""
    info_scroll = ft.Column(
        controls=[slots.info_content],
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
    info_card = _wrap_card(info_scroll, expand=ABOUT_INFO_EXPAND)

    components_scroll = ft.Column(
        controls=[slots.license_components],
        expand=ABOUT_LICENSE_COMPONENTS_EXPAND,
        scroll=ft.ScrollMode.AUTO,
    )
    text_scroll = ft.Column(
        controls=[slots.license_text],
        expand=ABOUT_LICENSE_TEXT_EXPAND,
        scroll=ft.ScrollMode.AUTO,
    )
    licenses_content = ft.Column(
        controls=[
            slots.licenses_title,
            components_scroll,
            ft.Divider(height=1, thickness=1),
            text_scroll,
        ],
        expand=True,
        spacing=ABOUT_ROW_SPACING,
    )
    licenses_card = _wrap_card(licenses_content, expand=ABOUT_LICENSES_EXPAND)

    return AboutSurfaceRegions(
        controls=(info_card, licenses_card),
        info_card=info_card,
        licenses_card=licenses_card,
        components_scroll=components_scroll,
        text_scroll=text_scroll,
    )


__all__ = [
    "ABOUT_INFO_EXPAND",
    "ABOUT_LICENSE_COMPONENTS_EXPAND",
    "ABOUT_LICENSE_TEXT_EXPAND",
    "ABOUT_LICENSES_EXPAND",
    "ABOUT_ROW_SPACING",
    "LICENSES_CARD_BORDER_RADIUS",
    "compose_about_surface",
]
