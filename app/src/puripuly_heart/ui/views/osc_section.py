"""OSC section mixin — VRC mic, microphone test, chatbox source handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.ui.components.settings import (
    OptionItem,
    SettingsModal,
)
from puripuly_heart.ui.i18n import t

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


class OscSectionMixin:
    """OSC-related event handlers extracted from SettingsView."""

    def _on_vrc_mic_click(self, e) -> None:
        """Toggle VRC mic intercept immediately from the unit card."""
        if not self._settings:
            return
        next_value = "off" if self._settings.osc.vrc_mic_intercept else "on"
        self._on_vrc_mic_selected(next_value)

    def _on_microphone_test_click(self, e) -> None:
        """Request the app/controller-owned microphone-test lifecycle."""
        _ = e
        if self.on_start_microphone_test is not None:
            self.on_start_microphone_test()

    def _on_vrc_mic_selected(self, value: str) -> None:
        """处理选项卡的选择结果

        Handle VRC mic intercept selection result.
        """
        if not self._settings:
            return
        new_value = value == "on"
        self._emit_runtime_basic(f"[Settings] VRC mic intercept toggled: {new_value}")
        self._settings.osc.vrc_mic_intercept = new_value

        self._vrc_mic_text.content.value = t(
            "settings.vrc_mic.on" if new_value else "settings.vrc_mic.off"
        )
        if self.page:
            self._vrc_mic_text.update()
        self._emit_settings_changed()

    def _on_chatbox_source_click(self, e) -> None:
        """Open chatbox source inclusion selection modal."""
        if not self.page:
            return
        options = [
            OptionItem(value="on", label=t("settings.chatbox_source.on")),
            OptionItem(value="off", label=t("settings.chatbox_source.off")),
        ]
        current = "on" if self._settings.osc.chatbox_include_source else "off"
        modal = SettingsModal(
            self.page,
            t("settings.chatbox_include_source"),
            options,
            self._on_chatbox_source_selected,
            show_description=False,
        )
        modal.open(current)

    def _on_chatbox_source_selected(self, value: str) -> None:
        """Handle chatbox source inclusion selection result."""
        if not self._settings:
            return
        new_value = value == "on"
        self._emit_runtime_basic(f"[Settings] Chatbox include source toggled: {new_value}")
        self._settings.osc.chatbox_include_source = new_value

        self._chatbox_source_text.content.value = t(
            "settings.chatbox_source.on" if new_value else "settings.chatbox_source.off"
        )
        if self.page:
            self._chatbox_source_text.update()
        self._emit_settings_changed()
