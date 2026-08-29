"""About view — version info, links, licenses, and system information.

Displays application metadata, clickable links to project resources,
third-party license text, and current runtime environment details.
"""

from __future__ import annotations

import platform
import sys
import webbrowser
from importlib import resources
from typing import Any

import customtkinter as ctk

from puripuly_heart import __version__
from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_third_party_notices() -> str:
    """Load ``THIRD_PARTY_NOTICES.txt`` from package data."""
    try:
        return (
            resources.files("puripuly_heart.data")
            .joinpath("THIRD_PARTY_NOTICES.txt")
            .read_text(encoding="utf-8")
        )
    except Exception:
        return t("about.licenses.load_error", default="Could not load license information.")


def _detect_gpu() -> str:
    """Best-effort GPU name detection.  Returns 'Unknown' on failure."""
    try:
        import wmi  # type: ignore[import-untyped]

        w = wmi.WMI()
        for gpu in w.Win32_VideoController():
            name = gpu.Name
            if name:
                return name
    except Exception:
        pass

    # Fallback: try nvidia-smi
    try:
        import subprocess

        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]
    except Exception:
        pass

    return t("about.system.gpu_unknown", default="Unknown")


def _system_info_lines() -> list[tuple[str, str]]:
    """Return label/value pairs for the system info section."""
    return [
        (
            t("about.system.python", default="Python"),
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        ),
        (
            t("about.system.os", default="OS"),
            f"{platform.system()} {platform.release()}",
        ),
        (
            t("about.system.gpu", default="GPU"),
            _detect_gpu(),
        ),
    ]


# ---------------------------------------------------------------------------
# Link button — styled like a clickable text
# ---------------------------------------------------------------------------


class _LinkButton(ctk.CTkButton):
    """A CTkButton styled to look like a hyperlink."""

    def __init__(
        self,
        master: Any,
        text: str,
        url: str,
        **kwargs: Any,
    ) -> None:
        self._url = url
        super().__init__(
            master,
            text=text,
            fg_color="transparent",
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_PRIMARY,
            anchor="w",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "normal"),
            command=self._open,
            **kwargs,
        )

    def _open(self) -> None:
        webbrowser.open(self._url)


# ---------------------------------------------------------------------------
# AboutView
# ---------------------------------------------------------------------------


class AboutView(ctk.CTkFrame):
    """About page: version, links, system info, licenses.

    Parameters
    ----------
    master : widget
        Parent CTk widget.
    controller : GuiController
        Shared application controller (not directly used but kept for
        interface consistency with other views).
    """

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, fg_color=th.COLOR_BACKGROUND, **kwargs)
        self._controller = controller
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble all about-page sections."""
        # Title
        ctk.CTkLabel(
            self,
            text=t("nav.about", default="About"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_TITLE, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", padx=th.CONTENT_PAD_X, pady=(th.CONTENT_PAD_Y, 8))

        # Scrollable content area
        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=th.COLOR_BACKGROUND,
            scrollbar_button_color=th.COLOR_DIVIDER,
        )
        self._scroll.pack(fill="both", expand=True, padx=th.CONTENT_PAD_X)

        self._build_header_card(self._scroll)
        self._build_fork_notice(self._scroll)
        self._build_links_card(self._scroll)
        self._build_system_card(self._scroll)
        self._build_licenses_card(self._scroll)

    # --- Header card ----------------------------------------------------

    def _build_header_card(self, parent: ctk.CTkScrollableFrame) -> None:
        """Application name, version, description."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        ctk.CTkLabel(
            inner,
            text=t("app.title", default="PuriPuly Heart"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_TITLE + 8, "bold"),
            text_color=th.COLOR_PRIMARY,
        ).pack(anchor="w")

        ctk.CTkLabel(
            inner,
            text=f"{t('about.version', default='Version')} {__version__}",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkLabel(
            inner,
            text=t(
                "about.description",
                default="LLM-powered real-time translator for VRChat",
            ),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT,
            wraplength=500,
        ).pack(anchor="w", pady=(8, 0))

        # License + copyright
        ctk.CTkLabel(
            inner,
            text="License: AGPL-3.0-or-later  |  © 2026 TriOmegaOptimum",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(anchor="w", pady=(8, 0))

    # --- Fork notice ----------------------------------------------------

    def _build_fork_notice(self, parent: ctk.CTkScrollableFrame) -> None:
        """Fork disclaimer banner."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_PRIMARY_CONTAINER,
            corner_radius=th.CARD_CORNER_RADIUS,
            border_width=1,
            border_color=th.COLOR_PRIMARY,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        ctk.CTkLabel(
            inner,
            text=t(
                "about.fork_notice",
                default=(
                    "This is an unofficial fork. "
                    "The original author has no relation to this version."
                ),
            ),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            text_color=th.COLOR_ON_PRIMARY_CONTAINER,
            wraplength=500,
            justify="left",
        ).pack(anchor="w")

    # --- Links card -----------------------------------------------------

    def _build_links_card(self, parent: ctk.CTkScrollableFrame) -> None:
        """Clickable project links."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        ctk.CTkLabel(
            inner,
            text=t("about.developed_by", default="Developed by"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 4))

        link1 = _LinkButton(
            inner,
            text="salee — github.com/kapitalismho/PuriPuly-heart",
            url="https://github.com/kapitalismho/PuriPuly-heart",
        )
        link1.pack(fill="x", pady=2)
        self._add_debug_label(link1, "link.original_repo")

        ctk.CTkLabel(
            inner,
            text=t("about.inspired_by", default="Inspired by"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(12, 4))

        for i, (label, url) in enumerate([
            ("VRCT — github.com/misyaguziya/VRCT", "https://github.com/misyaguziya/VRCT"),
            ("mimiuchi — github.com/naeruru/mimiuchi", "https://github.com/naeruru/mimiuchi"),
            ("Yakutan — github.com/febilly/Yakutan", "https://github.com/febilly/Yakutan"),
        ]):
            link = _LinkButton(inner, text=label, url=url)
            link.pack(fill="x", pady=2)
            self._add_debug_label(link, f"link.inspired_{i}")

        # Fork repo
        ctk.CTkLabel(
            inner,
            text=t("about.fork", default="Fork:"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(12, 4))

        fork_link = _LinkButton(
            inner,
            text="github.com/fzcfweasdferttgg-png/PuriPuly-heart",
            url="https://github.com/fzcfweasdferttgg-png/PuriPuly-heart",
        )
        fork_link.pack(fill="x", pady=2)
        self._add_debug_label(fork_link, "link.fork_repo")

    # --- System info card -----------------------------------------------

    def _build_system_card(self, parent: ctk.CTkScrollableFrame) -> None:
        """Runtime environment details."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        ctk.CTkLabel(
            inner,
            text=t("about.system_info", default="System Info"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 8))

        for label, value in _system_info_lines():
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=2)

            ctk.CTkLabel(
                row,
                text=f"{label}:",
                font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
                text_color=th.COLOR_TEXT_SECONDARY,
                width=80,
                anchor="w",
            ).pack(side="left")

            ctk.CTkLabel(
                row,
                text=value,
                font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
                text_color=th.COLOR_TEXT,
                anchor="w",
            ).pack(side="left", padx=(4, 0))

    # --- Licenses card --------------------------------------------------

    def _build_licenses_card(self, parent: ctk.CTkScrollableFrame) -> None:
        """Third-party license notices."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        ctk.CTkLabel(
            inner,
            text=t("about.licenses", default="Licenses"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 6))

        notices_text = _load_third_party_notices()
        textbox = ctk.CTkTextbox(
            inner,
            height=250,
            fg_color=th.COLOR_BACKGROUND,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            corner_radius=8,
            border_width=1,
            border_color=th.COLOR_DIVIDER,
            state="disabled",
            wrap="word",
        )
        textbox.pack(fill="x")
        textbox.configure(state="normal")
        textbox.insert("end", notices_text)
        textbox.configure(state="disabled")
        self._add_debug_label(textbox, "about.licenses")

    # ------------------------------------------------------------------
    # Debug labels
    # ------------------------------------------------------------------

    def _add_debug_label(self, widget: ctk.CTkFrame, widget_id: str) -> None:
        """Add a small debug label showing the widget identifier.

        Labels start hidden and are toggled via the app's 🔍 button.
        Clicking a label copies its text to the clipboard with a flash.
        """
        app = getattr(self._controller, "app", None)
        if app is None or not getattr(app, "debug_ui_preview", False):
            return
        label = ctk.CTkLabel(
            widget,
            text=f"[{widget_id}]",
            font=("Consolas", 9),
            text_color="#00FF88",
            fg_color="transparent",
        )
        label.place(x=4, y=4)
        label.bind("<Button-1>", lambda e, t=widget_id: app._copy_debug_label(t))
        label.lower()
        app._debug_labels.append(label)

    # ------------------------------------------------------------------
    # Locale refresh
    # ------------------------------------------------------------------

    def apply_locale(self) -> None:
        """Rebuild UI when locale changes."""
        # Destroy all children and rebuild
        for child in self.winfo_children():
            child.destroy()
        self._build_ui()
