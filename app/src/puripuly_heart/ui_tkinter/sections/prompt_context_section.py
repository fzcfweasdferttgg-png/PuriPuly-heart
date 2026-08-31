"""Prompt & Context settings section.

Merged section combining the system prompt editor with context toggles
(integrated context, clipboard auto-translate).  Replaces the separate
``PromptSection`` and ``ContextSection``.

Fields map to ``controller.settings.system_prompt`` and
``controller.settings.ui.*``.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection


class PromptContextSection(CollapsibleSection):
    """System prompt editor + context toggles in one collapsible section."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(
            master,
            t("tk.settings.section.prompt_context", default="Prompt & Context"),
            title_i18n_key="tk.settings.section.prompt_context",
            title_default="Prompt & Context",
            **kwargs,
        )
        self._controller = controller
        self._build()

    def _build(self) -> None:
        ui = self._controller.settings.ui

        # --- System prompt textbox (top) ---
        self._prompt_textbox = ctk.CTkTextbox(
            self._content,
            width=500,
            height=160,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            fg_color=th.COLOR_BACKGROUND,
            text_color=th.COLOR_TEXT,
            corner_radius=8,
            border_width=1,
            border_color=th.COLOR_DIVIDER,
        )
        current_prompt = self._controller.settings.system_prompt or ""
        self._prompt_textbox.insert("1.0", current_prompt)
        self._prompt_textbox.bind("<FocusOut>", lambda _: self._apply_prompt())

        # Layout: full-width row for textbox
        row = ctk.CTkFrame(self._content, fg_color="transparent")
        row.pack(fill="x", padx=th.CARD_PAD_X, pady=4)
        self._prompt_textbox.pack(in_=row, fill="x", expand=True)
        self._add_debug_label(self._prompt_textbox, "section.prompt_context.textarea")

        # --- Divider ---
        ctk.CTkFrame(
            self._content,
            height=1,
            fg_color=th.COLOR_DIVIDER,
        ).pack(fill="x", padx=th.CARD_PAD_X, pady=8)

        # --- Integrated context toggle ---
        def _make_context_switch(row):
            switch = ctk.CTkSwitch(
                row,
                text="",
                command=self._on_context_toggle,
            )
            if ui.integrated_context_enabled:
                switch.select()
            return switch

        self._context_switch = self.add_row(
            t("tk.settings.integrated_context", default="Integrated Context"),
            _make_context_switch,
            label_i18n_key="tk.settings.integrated_context",
            label_default="Integrated Context",
            label_id="label.integrated_context",
            control_id="switch.integrated_context",
        )

        # --- Clipboard auto-translate toggle ---
        def _make_clipboard_switch(row):
            switch = ctk.CTkSwitch(
                row,
                text="",
                command=self._on_clipboard_toggle,
            )
            if ui.clipboard_auto_translate_enabled:
                switch.select()
            return switch

        self._clipboard_switch = self.add_row(
            t("tk.settings.clipboard_auto_translate", default="Clipboard Auto-Translate"),
            _make_clipboard_switch,
            label_i18n_key="tk.settings.clipboard_auto_translate",
            label_default="Clipboard Auto-Translate",
            label_id="label.clipboard_auto_translate",
            control_id="switch.clipboard_auto_translate",
        )

    # --- Handlers ---

    def _apply_prompt(self) -> None:
        """Save prompt text on focus-out."""
        text = self._prompt_textbox.get("1.0", "end-1c")
        self._controller.settings.system_prompt = text
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_context_toggle(self) -> None:
        """Toggle integrated context mode."""
        self._controller.settings.ui.integrated_context_enabled = (
            self._context_switch.get() == 1
        )
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_clipboard_toggle(self) -> None:
        """Toggle clipboard auto-translate."""
        self._controller.settings.ui.clipboard_auto_translate_enabled = (
            self._clipboard_switch.get() == 1
        )
        self._controller.apply_settings_with_sync(self._controller.settings)

    # --- Locale refresh ---

    def apply_locale(self) -> None:
        """Update all translatable widgets when locale changes."""
        super().apply_locale()
