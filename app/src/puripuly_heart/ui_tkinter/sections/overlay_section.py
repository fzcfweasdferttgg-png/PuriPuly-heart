"""Overlay settings section.

Target selection (SteamVR/Desktop), translation/peer toggles, desktop overlay
size preset, and lock toggle.  Fields map to ``controller.settings.overlay.*``.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from puripuly_heart.config.settings.constants import (
    DESKTOP_FLET_DEFAULT_SIZE_PRESET,
    DESKTOP_FLET_SIZE_PRESET_ORDER,
    OVERLAY_TARGET_DESKTOP,
    OVERLAY_TARGET_STEAMVR,
)
from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection


class OverlaySection(CollapsibleSection):
    """Overlay target, visibility toggles, and desktop overlay options."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, t("settings.section.overlay", default="Overlay"), **kwargs)
        self._controller = controller
        self._build()

    def _build(self) -> None:
        overlay = self._controller.settings.overlay

        # --- Target: SteamVR / Desktop ---
        target_labels = ["SteamVR", "Desktop"]
        target_values = [OVERLAY_TARGET_STEAMVR, OVERLAY_TARGET_DESKTOP]
        self._target_menu = ctk.CTkOptionMenu(
            self._content,
            values=target_labels,
            width=160,
            command=lambda _: self._on_target_change(),
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        current_target = "Desktop" if overlay.target == OVERLAY_TARGET_DESKTOP else "SteamVR"
        self._target_menu.set(current_target)
        self._target_values = target_values
        self._target_labels = target_labels
        self.add_row(t("settings.overlay_target", default="Overlay Target"), self._target_menu)
        self._add_debug_label(self._target_menu, "dropdown.overlay_target")

        # --- Show translation toggle ---
        self._show_trans_switch = ctk.CTkSwitch(self._content, text="", command=self._on_show_trans)
        if overlay.show_translation:
            self._show_trans_switch.select()
        self.add_row(t("settings.show_translation", default="Show Translation"), self._show_trans_switch)
        self._add_debug_label(self._show_trans_switch, "switch.show_translation")

        # --- Show peer original toggle ---
        self._show_peer_switch = ctk.CTkSwitch(self._content, text="", command=self._on_show_peer)
        if overlay.show_peer_original:
            self._show_peer_switch.select()
        self.add_row(t("settings.show_peer_original", default="Show Peer Original"), self._show_peer_switch)
        self._add_debug_label(self._show_peer_switch, "switch.show_peer")

        # --- Desktop overlay size preset ---
        preset_labels = list(DESKTOP_FLET_SIZE_PRESET_ORDER)
        self._size_menu = ctk.CTkOptionMenu(
            self._content,
            values=preset_labels,
            width=160,
            command=lambda _: self._on_size_change(),
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._size_menu.set(overlay.desktop_flet.size_preset or DESKTOP_FLET_DEFAULT_SIZE_PRESET)
        self.add_row(
            t("settings.overlay_size_preset", default="Size Preset"),
            self._size_menu,
        )
        self._add_debug_label(self._size_menu, "dropdown.overlay_size")

        # --- Desktop overlay locked toggle ---
        self._lock_switch = ctk.CTkSwitch(self._content, text="", command=self._on_lock_toggle)
        if overlay.desktop_flet.locked:
            self._lock_switch.select()
        self.add_row(t("settings.overlay_locked", default="Overlay Locked"), self._lock_switch)
        self._add_debug_label(self._lock_switch, "switch.overlay_locked")

    # --- Handlers ---

    def _on_target_change(self) -> None:
        idx = self._target_labels.index(self._target_menu.get())
        value = self._target_values[idx]
        self._controller.settings.overlay.target = value
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_show_trans(self) -> None:
        self._controller.settings.overlay.show_translation = self._show_trans_switch.get() == 1
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_show_peer(self) -> None:
        self._controller.settings.overlay.show_peer_original = self._show_peer_switch.get() == 1
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_size_change(self) -> None:
        self._controller.settings.overlay.desktop_flet.size_preset = self._size_menu.get()
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_lock_toggle(self) -> None:
        self._controller.settings.overlay.desktop_flet.locked = self._lock_switch.get() == 1
        self._controller.apply_settings_with_sync(self._controller.settings)
