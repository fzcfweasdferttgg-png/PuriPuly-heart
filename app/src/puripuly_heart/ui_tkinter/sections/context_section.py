"""Context settings section.

Integrated context toggle and clipboard auto-translate toggle.
Fields map to ``controller.settings.ui.*``.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection


class ContextSection(CollapsibleSection):
    """Integrated context and clipboard auto-translate toggles."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, t("settings.section.context", default="Context"), **kwargs)
        self._controller = controller
        self._build()

    def _build(self) -> None:
        ui = self._controller.settings.ui

        # --- Integrated context toggle ---
        self._context_switch = ctk.CTkSwitch(
            self._content, text="", command=self._on_context_toggle,
        )
        if ui.integrated_context_enabled:
            self._context_switch.select()
        self.add_row(
            t("settings.integrated_context", default="Integrated Context"),
            self._context_switch,
        )
        self._add_debug_label(self._context_switch, "switch.integrated_context")

        # --- Clipboard auto-translate toggle ---
        self._clipboard_switch = ctk.CTkSwitch(
            self._content, text="", command=self._on_clipboard_toggle,
        )
        if ui.clipboard_auto_translate_enabled:
            self._clipboard_switch.select()
        self.add_row(
            t("settings.clipboard_auto_translate", default="Clipboard Auto-Translate"),
            self._clipboard_switch,
        )
        self._add_debug_label(self._clipboard_switch, "switch.clipboard_auto_translate")

    # --- Handlers ---

    def _on_context_toggle(self) -> None:
        self._controller.settings.ui.integrated_context_enabled = self._context_switch.get() == 1
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_clipboard_toggle(self) -> None:
        self._controller.settings.ui.clipboard_auto_translate_enabled = (
            self._clipboard_switch.get() == 1
        )
        self._controller.apply_settings_with_sync(self._controller.settings)
