"""OSC settings section.

Host, port, chatbox send toggle, max characters, and VRC mic intercept.
Fields map to ``controller.settings.osc.*``.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection


class OSCSection(CollapsibleSection):
    """OSC connection and chatbox settings."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, t("settings.section.osc", default="OSC"), **kwargs)
        self._controller = controller
        self._build()

    def _build(self) -> None:
        osc = self._controller.settings.osc

        # --- Host ---
        self._host_entry = ctk.CTkEntry(self._content, width=200)
        self._host_entry.insert(0, osc.host)
        self._host_entry.bind("<FocusOut>", lambda _: self._apply_host())
        self._host_entry.bind("<Return>", lambda _: self._apply_host())
        self.add_row(t("settings.osc_host", default="Host"), self._host_entry)
        self._add_debug_label(self._host_entry, "input.osc_host")

        # --- Port ---
        self._port_entry = ctk.CTkEntry(self._content, width=120)
        self._port_entry.insert(0, str(osc.port))
        self._port_entry.bind("<FocusOut>", lambda _: self._apply_port())
        self._port_entry.bind("<Return>", lambda _: self._apply_port())
        self.add_row(t("settings.osc_port", default="Port"), self._port_entry)
        self._add_debug_label(self._port_entry, "input.osc_port")

        # --- Chatbox send toggle ---
        self._chatbox_switch = ctk.CTkSwitch(self._content, text="", command=self._on_chatbox_toggle)
        if osc.chatbox_send:
            self._chatbox_switch.select()
        self.add_row(t("settings.chatbox_send", default="Chatbox Send"), self._chatbox_switch)
        self._add_debug_label(self._chatbox_switch, "switch.chatbox_send")

        # --- Chatbox max chars ---
        self._max_chars_entry = ctk.CTkEntry(self._content, width=120)
        self._max_chars_entry.insert(0, str(osc.chatbox_max_chars))
        self._max_chars_entry.bind("<FocusOut>", lambda _: self._apply_max_chars())
        self._max_chars_entry.bind("<Return>", lambda _: self._apply_max_chars())
        self.add_row(t("settings.chatbox_max_chars", default="Max Characters"), self._max_chars_entry)
        self._add_debug_label(self._max_chars_entry, "input.chatbox_max_chars")

        # --- VRC mic intercept toggle ---
        self._mic_intercept_switch = ctk.CTkSwitch(
            self._content, text="", command=self._on_mic_intercept_toggle,
        )
        if osc.vrc_mic_intercept:
            self._mic_intercept_switch.select()
        self.add_row(t("settings.vrc_mic_intercept", default="VRC Mic Intercept"), self._mic_intercept_switch)
        self._add_debug_label(self._mic_intercept_switch, "switch.vrc_mic_intercept")

    # --- Handlers ---

    def _apply_host(self) -> None:
        host = self._host_entry.get().strip()
        if host:
            self._controller.settings.osc.host = host
            self._controller.apply_settings_with_sync(self._controller.settings)

    def _apply_port(self) -> None:
        try:
            port = int(self._port_entry.get().strip())
            if 0 < port <= 65535:
                self._controller.settings.osc.port = port
                self._controller.apply_settings_with_sync(self._controller.settings)
        except ValueError:
            pass

    def _on_chatbox_toggle(self) -> None:
        self._controller.settings.osc.chatbox_send = self._chatbox_switch.get() == 1
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _apply_max_chars(self) -> None:
        try:
            val = int(self._max_chars_entry.get().strip())
            if val > 0:
                self._controller.settings.osc.chatbox_max_chars = val
                self._controller.apply_settings_with_sync(self._controller.settings)
        except ValueError:
            pass

    def _on_mic_intercept_toggle(self) -> None:
        self._controller.settings.osc.vrc_mic_intercept = self._mic_intercept_switch.get() == 1
        self._controller.apply_settings_with_sync(self._controller.settings)
