"""About page view with version, credits, acknowledgments, and license info."""

import webbrowser
from importlib import resources

import flet as ft

from puripuly_heart import __version__
from puripuly_heart.domain.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
)


def _load_third_party_notices() -> str:
    """Load THIRD_PARTY_NOTICES.txt from package data."""
    try:
        return (
            resources.files("puripuly_heart.data")
            .joinpath("THIRD_PARTY_NOTICES.txt")
            .read_text(encoding="utf-8")
        )
    except Exception:
        return "Could not load license information."


class AboutView(ft.Column):
    """Compact About page — single column, no cards."""

    def __init__(self):
        super().__init__(expand=True, scroll=ft.ScrollMode.AUTO, spacing=24)
        self._build_ui()

    def _build_ui(self):
        self.controls = [
            self._build_header(),
            self._build_fork_notice(),
            self._build_links(),
            self._build_special_thanks(),
            self._build_licenses(),
        ]

    def _build_header(self) -> ft.Control:
        return ft.Column(
            [
                ft.Text(
                    t("app.title"),
                    size=36,
                    weight=ft.FontWeight.BOLD,
                    color=COLOR_PRIMARY,
                ),
                ft.Text(
                    f"v{__version__}",
                    size=18,
                    color=COLOR_NEUTRAL,
                ),
            ],
            spacing=4,
        )

    def _build_fork_notice(self) -> ft.Control:
        return ft.Container(
            content=ft.Text(
                t("about.fork_notice"),
                size=14,
                color=COLOR_ON_BACKGROUND,
                weight=ft.FontWeight.BOLD,
            ),
            padding=12,
            bgcolor=ft.Colors.with_opacity(0.05, COLOR_PRIMARY),
            border_radius=8,
            border=ft.Border.all(1, ft.Colors.with_opacity(0.2, COLOR_PRIMARY)),
        )

    def _build_links(self) -> ft.Control:
        def _link(text: str, url: str) -> ft.Container:
            return ft.Container(
                content=ft.Text(text, size=16, color=COLOR_PRIMARY),
                on_click=lambda _: webbrowser.open(url),
                on_hover=self._on_link_hover,
                padding=ft.Padding.only(bottom=4),
            )

        return ft.Column(
            [
                ft.Text(t("about.developed_by"), size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL),
                _link("salee — github.com/kapitalismho/PuriPuly-heart", "https://github.com/kapitalismho/PuriPuly-heart"),
                ft.Container(height=8),
                ft.Text(t("about.inspired_by"), size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL),
                _link("VRCT — github.com/misyaguziya/VRCT", "https://github.com/misyaguziya/VRCT"),
                _link("mimiuchi — github.com/naeruru/mimiuchi", "https://github.com/naeruru/mimiuchi"),
                _link("Yakutan — github.com/febilly/Yakutan", "https://github.com/febilly/Yakutan"),
                ft.Container(height=8),
                ft.Text("Fork:", size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL),
                _link("github.com/fzcfweasdferttgg-png/PuriPuly-heart", "https://github.com/fzcfweasdferttgg-png/PuriPuly-heart"),
            ],
            spacing=4,
        )

    def _build_special_thanks(self) -> ft.Control:
        names = [
            t("about.special_thanks.name.sui_32c"),
            t("about.special_thanks.name.nagikokoro"),
            t("about.special_thanks.name.motoka96"),
            t("about.special_thanks.name.ykol"),
            t("about.special_thanks.name.kascr"),
            t("about.special_thanks.name.just_monika_v"),
            t("about.special_thanks.name.fluvia"),
            t("about.special_thanks.name.han_chole"),
            t("about.special_thanks.name.ea_pe"),
            t("about.special_thanks.name.ephedrine"),
            t("about.special_thanks.name.eri"),
        ]
        return ft.Column(
            [
                ft.Text(t("about.special_thanks"), size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL),
                ft.Text(", ".join(names), size=14, color=COLOR_ON_BACKGROUND),
                ft.Text("and you!", size=14, color=COLOR_ON_BACKGROUND, italic=True),
            ],
            spacing=4,
        )

    def _build_licenses(self) -> ft.Control:
        return ft.Column(
            [
                ft.Text(t("about.licenses"), size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL),
                ft.Container(
                    content=ft.Text(
                        _load_third_party_notices(),
                        size=12,
                        color=COLOR_ON_BACKGROUND,
                        selectable=True,
                    ),
                    padding=12,
                    bgcolor=COLOR_SURFACE,
                    border=ft.Border.all(1, COLOR_DIVIDER),
                    border_radius=8,
                ),
            ],
            spacing=8,
        )

    def _on_link_hover(self, e):
        text = e.control.content
        text.color = COLOR_ON_BACKGROUND if e.data == "true" else COLOR_PRIMARY
        try:
            text.update()
        except (AssertionError, RuntimeError):
            pass

    def apply_locale(self) -> None:
        """Rebuild UI when locale changes."""
        self._build_ui()
        try:
            self.update()
        except (AssertionError, RuntimeError):
            pass
