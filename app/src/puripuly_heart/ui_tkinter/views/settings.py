"""Settings view — scrollable container with collapsible sections.

Each section is a :class:`CollapsibleSection` imported from
``ui_tkinter.sections.*``.  The view arranges them vertically inside a
``CTkScrollableFrame`` and exposes a ``reload`` method so the parent
window can refresh when settings change externally.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.ui_section import UISection
from puripuly_heart.ui_tkinter.sections.audio_section import AudioSection
from puripuly_heart.ui_tkinter.sections.stt_section import STTSection
from puripuly_heart.ui_tkinter.sections.llm_section import LLMSection
from puripuly_heart.ui_tkinter.sections.overlay_section import OverlaySection
from puripuly_heart.ui_tkinter.sections.osc_section import OSCSection
from puripuly_heart.ui_tkinter.sections.context_section import ContextSection
from puripuly_heart.ui_tkinter.sections.prompt_section import PromptSection
from puripuly_heart.ui_tkinter.sections.secrets_section import SecretsSection

# Display order: General (UI + Audio) first, then providers, then integrations.
_SECTION_ORDER: list[tuple[str, type]] = [
    ("general", UISection),
    ("audio", AudioSection),
    ("stt", STTSection),
    ("llm", LLMSection),
    ("overlay", OverlaySection),
    ("osc", OSCSection),
    ("context", ContextSection),
    ("prompt", PromptSection),
    ("secrets", SecretsSection),
]


class SettingsView(ctk.CTkScrollableFrame):
    """Scrollable settings form with collapsible sections.

    Parameters
    ----------
    master : widget
        Parent CTk widget.
    controller : GuiController
        Shared application controller.
    """

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(
            master,
            fg_color=th.COLOR_BACKGROUND,
            scrollbar_button_color=th.COLOR_DIVIDER,
            **kwargs,
        )
        self._controller = controller
        self._sections: dict[str, Any] = {}

        self._build_header()
        self._build_sections()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _build_header(self) -> None:
        ctk.CTkLabel(
            self,
            text=t("nav.settings", default="Settings"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_TITLE, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", padx=4, pady=(0, 12))

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _build_sections(self) -> None:
        """Instantiate and arrange all collapsible sections."""
        for key, section_cls in _SECTION_ORDER:
            section = section_cls(self, self._controller)
            section.pack(fill="x", pady=(0, 8))
            self._sections[key] = section
            self._add_debug_label(section, f"section.{key}")

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
    # Public API
    # ------------------------------------------------------------------

    def get_section(self, key: str) -> Any | None:
        """Return a section by its key, or ``None`` if not found."""
        return self._sections.get(key)

    def reload(self) -> None:
        """Destroy and rebuild all sections from current settings.

        Call this when settings change externally (e.g. locale switch).
        """
        for section in self._sections.values():
            section.destroy()
        self._sections.clear()
        self._build_sections()

    # ------------------------------------------------------------------
    # Controller compatibility stubs
    # ------------------------------------------------------------------

    def load_from_settings(self, settings: Any, config_path: Any = None, **kwargs: Any) -> None:
        """Stub — reload sections from updated settings."""
        self.reload()

    def set_overlay_calibration(self, calibration: Any) -> None:
        """Stub — overlay calibration state (not applicable to Tkinter)."""

    def consume_prompt_apply_settings(self) -> Any | None:
        """Stub — no pending prompt changes in Tkinter settings view."""
        return None

    def consume_provider_apply_settings(self) -> Any | None:
        """Stub — no pending provider changes in Tkinter settings view."""
        return None
