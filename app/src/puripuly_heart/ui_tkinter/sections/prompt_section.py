"""Prompt settings section.

System prompt multi-line editor. Field maps to
``controller.settings.system_prompt``.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection


class PromptSection(CollapsibleSection):
    """System prompt editor (multi-line)."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, t("settings.section.prompt", default="Prompt"), **kwargs)
        self._controller = controller
        self._build()

    def _build(self) -> None:
        # --- System prompt textbox ---
        self._prompt_textbox = ctk.CTkTextbox(
            self._content,
            width=500,
            height=200,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            fg_color="#FFFFFF",
            text_color=th.COLOR_TEXT,
            corner_radius=8,
            border_width=1,
            border_color=th.COLOR_DIVIDER,
        )
        current_prompt = self._controller.settings.system_prompt or ""
        self._prompt_textbox.insert("1.0", current_prompt)
        self._prompt_textbox.bind("<FocusOut>", lambda _: self._apply_prompt())

        # Layout: full-width row
        row = ctk.CTkFrame(self._content, fg_color="transparent")
        row.pack(fill="x", padx=th.CARD_PAD_X, pady=4)
        self._prompt_textbox.pack(in_=row, fill="x", expand=True)
        self._add_debug_label(self._prompt_textbox, "input.system_prompt")

    # --- Handlers ---

    def _apply_prompt(self) -> None:
        text = self._prompt_textbox.get("1.0", "end-1c")
        self._controller.settings.system_prompt = text
        self._controller.apply_settings_with_sync(self._controller.settings)
