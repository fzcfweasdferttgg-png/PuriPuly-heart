"""UI language settings section.

Exposes a language selector that controls ``controller.settings.ui.locale``.
Changes are applied immediately via ``controller.apply_settings_with_sync``.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import available_locales, locale_label, t
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection


class UISection(CollapsibleSection):
    """UI language selector."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, t("settings.section.ui", default="General"), **kwargs)
        self._controller = controller
        self._build()

    def _build(self) -> None:
        locales = list(available_locales())
        labels = [locale_label(code) for code in locales]
        current = self._controller.settings.ui.locale
        current_label = locale_label(current) if current in locales else (labels[0] if labels else "")

        self._lang_menu = ctk.CTkOptionMenu(
            self._content,
            values=labels,
            width=200,
            command=lambda _: self._on_change(),
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        if current_label in labels:
            self._lang_menu.set(current_label)

        self._locales = locales
        self._labels = labels
        self.add_row(t("settings.ui_language", default="UI Language"), self._lang_menu)
        self._add_debug_label(self._lang_menu, "dropdown.ui_language")

    def _on_change(self) -> None:
        idx = self._labels.index(self._lang_menu.get())
        code = self._locales[idx]
        self._controller.settings.ui.locale = code
        self._controller.apply_settings_with_sync(self._controller.settings)
