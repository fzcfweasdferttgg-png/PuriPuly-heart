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
        super().__init__(
            master,
            t("tk.settings.section.osc", default="OSC"),
            title_i18n_key="tk.settings.section.osc",
            title_default="OSC",
            **kwargs,
        )
        self._controller = controller
        self._build()

    def _build(self) -> None:
        osc = self._controller.settings.osc

        # --- Host ---
        def _make_host_entry(row):
            entry = ctk.CTkEntry(row, width=200)
            entry.insert(0, osc.host)
            entry.bind("<FocusOut>", lambda _: self._apply_host())
            entry.bind("<Return>", lambda _: self._apply_host())
            return entry

        self._host_entry = self.add_row(
            t("tk.settings.osc_host", default="Host"),
            _make_host_entry,
            label_i18n_key="tk.settings.osc_host",
            label_default="Host",
            label_id="label.osc_host",
            control_id="input.osc_host",
        )

        # --- Port ---
        def _make_port_entry(row):
            entry = ctk.CTkEntry(row, width=120)
            entry.insert(0, str(osc.port))
            entry.bind("<FocusOut>", lambda _: self._apply_port())
            entry.bind("<Return>", lambda _: self._apply_port())
            return entry

        self._port_entry = self.add_row(
            t("tk.settings.osc_port", default="Port"),
            _make_port_entry,
            label_i18n_key="tk.settings.osc_port",
            label_default="Port",
            label_id="label.osc_port",
            control_id="input.osc_port",
        )

        # --- Chatbox send toggle ---
        def _make_chatbox_switch(row):
            switch = ctk.CTkSwitch(row, text="", command=self._on_chatbox_toggle)
            if osc.chatbox_send:
                switch.select()
            return switch

        self._chatbox_switch = self.add_row(
            t("tk.settings.chatbox_send", default="Chatbox Send"),
            _make_chatbox_switch,
            label_i18n_key="tk.settings.chatbox_send",
            label_default="Chatbox Send",
            label_id="label.chatbox_send",
            control_id="switch.chatbox_send",
        )

        # --- Chatbox max chars ---
        def _make_max_chars_entry(row):
            entry = ctk.CTkEntry(row, width=120)
            entry.insert(0, str(osc.chatbox_max_chars))
            entry.bind("<FocusOut>", lambda _: self._apply_max_chars())
            entry.bind("<Return>", lambda _: self._apply_max_chars())
            return entry

        self._max_chars_entry = self.add_row(
            t("tk.settings.chatbox_max_chars", default="Max Characters"),
            _make_max_chars_entry,
            label_i18n_key="tk.settings.chatbox_max_chars",
            label_default="Max Characters",
            label_id="label.chatbox_max_chars",
            control_id="input.chatbox_max_chars",
        )

        # --- VRC mic intercept toggle ---
        def _make_mic_intercept_switch(row):
            switch = ctk.CTkSwitch(row, text="", command=self._on_mic_intercept_toggle)
            if osc.vrc_mic_intercept:
                switch.select()
            return switch

        self._mic_intercept_switch = self.add_row(
            t("tk.settings.vrc_mic_intercept", default="VRC Mic Intercept"),
            _make_mic_intercept_switch,
            label_i18n_key="tk.settings.vrc_mic_intercept",
            label_default="VRC Mic Intercept",
            label_id="label.vrc_mic_intercept",
            control_id="switch.vrc_mic_intercept",
        )

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
