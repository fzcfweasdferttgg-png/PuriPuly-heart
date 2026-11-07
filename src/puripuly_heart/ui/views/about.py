"""About window content: compact program info and the full license canvas."""

from __future__ import annotations

import webbrowser

import flet as ft

from puripuly_heart import __version__
from puripuly_heart.runtime_layout import current_runtime_layout
from puripuly_heart.ui.about.contract import AboutSurfaceSlots
from puripuly_heart.ui.about.renderer import compose_about_surface
from puripuly_heart.ui.flet_runtime import is_hover_active
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SECONDARY,
)

GITHUB_REPOSITORY_URL = "https://github.com/kapitalismho/PuriPuly-heart"


def _load_third_party_notices() -> str:
    """Load THIRD_PARTY_NOTICES.txt from package data."""
    try:
        return (
            current_runtime_layout()
            .package_resource("data", "THIRD_PARTY_NOTICES.txt")
            .read_text(encoding="utf-8")
        )
    except Exception:
        return "Could not load license information."


def _get_profile_image_path() -> str:
    """Get the profile image path from package data."""
    try:
        return str(current_runtime_layout().package_resource("data", "pictures", "salee_pic.png"))
    except Exception:
        return ""


class AboutView(ft.Column):
    """About window content with compact info and the full license canvas."""

    # Special thanks name keys - add new names here and update locale bundles
    _SPECIAL_THANKS_NAME_KEYS = [
        "about.special_thanks.name.sui_32c",
        "about.special_thanks.name.nagikokoro",
        "about.special_thanks.name.motoka96",
        "about.special_thanks.name.ykol",
        "about.special_thanks.name.kascr",
        "about.special_thanks.name.just_monika_v",
        "about.special_thanks.name.fluvia",
        "about.special_thanks.name.han_chole",
        "about.special_thanks.name.ea_pe",
        "about.special_thanks.name.ephedrine",
        "about.special_thanks.name.eri",
        "about.special_thanks.name.fzcfweasdferttgg_png",
        "about.special_thanks.name.welcius",
        "about.special_thanks.name.nunu299",
        "about.special_thanks.name.tsuyu_shiro",
    ]

    def __init__(self):
        super().__init__(expand=True, spacing=16)
        self._license_text = ft.Text(
            value=_load_third_party_notices(),
            size=14,
            selectable=True,
            color=COLOR_ON_BACKGROUND,
        )
        self._build_ui()

    def _build_ui(self) -> None:
        """Build the compact info card and the full license canvas card."""
        regions = compose_about_surface(
            AboutSurfaceSlots(
                info_content=self._build_info_content(),
                licenses_title=ft.Text(
                    t("about.licenses"),
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=COLOR_SECONDARY,
                ),
                license_text=self._license_text,
            )
        )
        self.controls = list(regions.controls)

    def _build_info_content(self) -> ft.Control:
        app_name = ft.Text(
            t("app.title"),
            size=28,
            weight=ft.FontWeight.BOLD,
            color=COLOR_PRIMARY,
        )
        version_link = ft.Container(
            content=ft.Text(f"v{__version__}", size=18, color=COLOR_ON_BACKGROUND),
            on_click=lambda _: webbrowser.open(GITHUB_REPOSITORY_URL),
            on_hover=self._on_version_hover,
        )
        identity = ft.Column(controls=[app_name, version_link], spacing=4)

        profile_path = _get_profile_image_path()
        photo = ft.Container(
            content=(
                ft.Image(
                    src=profile_path,
                    width=48,
                    height=48,
                    fit=ft.BoxFit.COVER,
                    border_radius=24,
                )
                if profile_path
                else ft.Icon(ft.Icons.PERSON, size=32, color=COLOR_ON_BACKGROUND)
            ),
            width=48,
            height=48,
            border_radius=24,
            bgcolor=COLOR_DIVIDER,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )
        name_link = ft.Container(
            content=ft.Text(
                "salee",
                size=18,
                weight=ft.FontWeight.BOLD,
                color=COLOR_ON_BACKGROUND,
            ),
            on_click=lambda _: webbrowser.open("https://x.com/kapitalismho"),
            on_hover=self._on_name_hover,
        )
        credits = ft.Column(
            controls=[
                ft.Text(
                    t("about.developed_by"),
                    size=14,
                    weight=ft.FontWeight.BOLD,
                    color=COLOR_SECONDARY,
                ),
                ft.Row(
                    controls=[photo, name_link],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=4,
        )

        projects = [
            ("VRCT", "https://github.com/misyaguziya/VRCT"),
            ("mimiuchi", "https://github.com/naeruru/mimiuchi"),
            ("Yakutan", "https://github.com/febilly/Yakutan"),
        ]
        project_links = [
            ft.Container(
                content=ft.Text(name, size=16, color=COLOR_ON_BACKGROUND),
                on_click=lambda _, u=url: webbrowser.open(u),
                on_hover=self._on_link_hover,
            )
            for name, url in projects
        ]
        inspired = ft.Column(
            controls=[
                ft.Text(
                    t("about.inspired_by"),
                    size=14,
                    weight=ft.FontWeight.BOLD,
                    color=COLOR_SECONDARY,
                ),
                ft.Row(controls=project_links, spacing=12),
            ],
            spacing=4,
        )

        thanks_names = [
            ft.Container(
                content=ft.Text(t(key), size=14, color=COLOR_ON_BACKGROUND),
                on_hover=self._on_thanks_hover,
            )
            for key in self._SPECIAL_THANKS_NAME_KEYS
        ]
        thanks_names.append(
            ft.Text("\nand you!", size=14, color=COLOR_ON_BACKGROUND, italic=True)
        )
        thanks = ft.Column(
            controls=[
                ft.Text(
                    t("about.special_thanks"),
                    size=14,
                    weight=ft.FontWeight.BOLD,
                    color=COLOR_SECONDARY,
                ),
                ft.Row(controls=thanks_names, spacing=8, run_spacing=4, wrap=True),
            ],
            spacing=4,
        )

        return ft.Row(
            controls=[identity, credits, inspired, thanks],
            spacing=32,
            run_spacing=12,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _on_name_hover(self, e):
        """Handle hover on name link."""
        text = e.control.content
        text.color = COLOR_PRIMARY if is_hover_active(e) else COLOR_ON_BACKGROUND
        text.update()

    def _on_link_hover(self, e):
        """Handle hover on project links."""
        text = e.control.content
        text.color = COLOR_PRIMARY if is_hover_active(e) else COLOR_ON_BACKGROUND
        text.update()

    def _on_version_hover(self, e):
        """Handle hover on version link."""
        text = e.control.content
        text.color = COLOR_PRIMARY if is_hover_active(e) else COLOR_ON_BACKGROUND
        text.update()

    def _on_thanks_hover(self, e):
        """Handle hover on thanks text."""
        text = e.control.content
        text.color = COLOR_PRIMARY if is_hover_active(e) else COLOR_ON_BACKGROUND
        text.update()

    def apply_locale(self) -> None:
        """Refresh UI text when locale changes."""
        self._build_ui()
        self.update()
