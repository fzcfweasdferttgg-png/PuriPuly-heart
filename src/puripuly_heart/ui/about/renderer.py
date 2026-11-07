from __future__ import annotations

import flet as ft

from puripuly_heart.ui.about.contract import AboutSurfaceRegions, AboutSurfaceSlots
from puripuly_heart.ui.theme import COLOR_SURFACE

ABOUT_ROW_SPACING = 16
ABOUT_LICENSE_COMPONENTS_EXPAND = 2
ABOUT_LICENSE_TEXT_EXPAND = 3
LICENSES_CARD_BORDER_RADIUS = 16


def compose_about_surface(slots: AboutSurfaceSlots) -> AboutSurfaceRegions:
    """Arrange the compact info card and the license browser card."""
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
        spacing=12,
    )
    licenses_card = ft.Container(
        content=ft.Container(content=licenses_content, expand=True, padding=24),
        bgcolor=COLOR_SURFACE,
        border_radius=LICENSES_CARD_BORDER_RADIUS,
        expand=True,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )
    return AboutSurfaceRegions(
        controls=(slots.info_card, licenses_card),
        info_card=slots.info_card,
        licenses_card=licenses_card,
        components_scroll=components_scroll,
        text_scroll=text_scroll,
    )


__all__ = [
    "ABOUT_LICENSE_COMPONENTS_EXPAND",
    "ABOUT_LICENSE_TEXT_EXPAND",
    "ABOUT_ROW_SPACING",
    "LICENSES_CARD_BORDER_RADIUS",
    "compose_about_surface",
]
