from __future__ import annotations

import flet as ft

from puripuly_heart.ui.about.contract import AboutSurfaceRegions, AboutSurfaceSlots
from puripuly_heart.ui.theme import COLOR_SURFACE

ABOUT_ROW_SPACING = 12
ABOUT_CARD_SPACING = 16
LICENSES_CARD_BORDER_RADIUS = 16
LICENSES_CARD_PADDING = 24


def _wrap_card(content: ft.Control) -> ft.Container:
    return ft.Container(
        content=content,
        bgcolor=COLOR_SURFACE,
        border_radius=LICENSES_CARD_BORDER_RADIUS,
        padding=LICENSES_CARD_PADDING,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )


def compose_about_surface(slots: AboutSurfaceSlots) -> AboutSurfaceRegions:
    """Stack the info card and the license browser into one scrolling surface."""
    info_card = _wrap_card(slots.info_content)

    licenses_content = ft.Column(
        controls=[
            slots.licenses_title,
            slots.license_components,
            ft.Divider(height=1, thickness=1),
            slots.license_text,
        ],
        spacing=ABOUT_ROW_SPACING,
    )
    licenses_card = _wrap_card(licenses_content)

    root_scroll = ft.Column(
        controls=[info_card, licenses_card],
        spacing=ABOUT_CARD_SPACING,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )

    return AboutSurfaceRegions(
        controls=(root_scroll,),
        root_scroll=root_scroll,
        info_card=info_card,
        licenses_card=licenses_card,
    )


__all__ = [
    "ABOUT_CARD_SPACING",
    "ABOUT_ROW_SPACING",
    "LICENSES_CARD_BORDER_RADIUS",
    "LICENSES_CARD_PADDING",
    "compose_about_surface",
]
