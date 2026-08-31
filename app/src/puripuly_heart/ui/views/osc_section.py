"""OSC section mixin — VRC mic, microphone test, chatbox source handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.domain.settings_commands import ChangeVRCMic, ChangeChatboxSource
from puripuly_heart.ui.components.settings import (
    OptionItem,
    SettingsModal,
)
from puripuly_heart.domain.i18n import t
from puripuly_heart.ui.theme import COLOR_NEUTRAL

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


class OscSectionMixin:
    """OSC-related event handlers."""

    # ------------------------------------------------------------------
    # Load from settings
    # ------------------------------------------------------------------

    def _load_osc_from_settings(self, settings: "AppSettings") -> None:
        """Load OSC toggle states from settings into controls."""
        if not hasattr(self, '_vrc_mic_text'):
            return
        self._vrc_mic_text.content.value = t(
            "flet.settings.vrc_mic.on" if settings.osc.vrc_mic_intercept else "flet.settings.vrc_mic.off"
        )
        self._chatbox_source_text.content.value = t(
            "flet.settings.chatbox_source.on"
            if settings.osc.chatbox_include_source
            else "flet.settings.chatbox_source.off"
        )
        self._clipboard_auto_translate_text.content.value = t(
            "flet.settings.clipboard_auto_translate.on"
            if settings.ui.clipboard_auto_translate_enabled
            else "flet.settings.clipboard_auto_translate.off"
        )

    # ------------------------------------------------------------------
    # Widget builders
    # ------------------------------------------------------------------

    def _build_osc_widgets(self) -> tuple[ft.Control, ft.Control, ft.Control, ft.Control]:
        """Create chatbox-source, clipboard-auto-translate, VRC-mic, and microphone-test cards.

        Returns ``(chatbox_source_card, clipboard_auto_translate_card,
        vrc_mic_card, microphone_test_card)``.
        """
        # -- Chatbox source --
        self._chatbox_source_text = self._build_clickable_text(
            t("flet.settings.chatbox_source.on"),
            self._on_chatbox_source_click,
        )
        self._chatbox_source_title = ft.Text(
            t("flet.settings.chatbox_include_source"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        chatbox_source_card = self._wrap_unit_card(
            title=self._chatbox_source_title,
            value=self._chatbox_source_text,
        )

        # -- Clipboard auto-translate --
        self._clipboard_auto_translate_text = self._build_clickable_text(
            t("flet.settings.clipboard_auto_translate.off"),
            self._on_clipboard_auto_translate_click,
        )
        self._clipboard_auto_translate_title = ft.Text(
            t("flet.settings.clipboard_auto_translate"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        clipboard_auto_translate_card = self._wrap_unit_card(
            title=self._clipboard_auto_translate_title,
            value=self._clipboard_auto_translate_text,
        )

        # -- VRC mic intercept --
        self._vrc_mic_text = self._build_clickable_text(
            t("flet.settings.vrc_mic.on"),
            self._on_vrc_mic_click,
        )
        self._vrc_mic_title = ft.Text(
            t("flet.settings.vrc_mic_intercept"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        vrc_mic_card = self._wrap_unit_card(
            title=self._vrc_mic_title,
            value=self._vrc_mic_text,
        )

        # -- Microphone test --
        self._microphone_test_text = self._build_clickable_text(
            t("flet.settings.microphone_test.action"),
            self._on_microphone_test_click,
        )
        self._microphone_test_title = ft.Text(
            t("flet.settings.microphone_test"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        microphone_test_card = self._wrap_unit_card(
            title=self._microphone_test_title,
            value=self._microphone_test_text,
        )

        return (chatbox_source_card, clipboard_auto_translate_card, vrc_mic_card, microphone_test_card)

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
        """Handle VRC mic intercept selection result."""
        if not self._settings:
            return
        new_value = value == "on"
        self._emit_runtime_basic(f"[Settings] VRC mic intercept toggled: {new_value}")
        self._command_executor.execute(ChangeVRCMic(enabled=new_value))

        self._vrc_mic_text.content.value = t(
            "flet.settings.vrc_mic.on" if new_value else "flet.settings.vrc_mic.off"
        )
        try:
            if self.page:
                self._vrc_mic_text.update()
        except (AssertionError, RuntimeError):
            pass
        self._emit_settings_changed()

    def _on_chatbox_source_click(self, e) -> None:
        """Open chatbox source inclusion selection modal."""
        try:
            if not self.page:
                return
        except (AssertionError, RuntimeError):
            return
        options = [
            OptionItem(value="on", label=t("flet.settings.chatbox_source.on")),
            OptionItem(value="off", label=t("flet.settings.chatbox_source.off")),
        ]
        current = "on" if self._settings.osc.chatbox_include_source else "off"
        modal = SettingsModal(
            self.page,
            t("flet.settings.chatbox_include_source"),
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
        self._command_executor.execute(ChangeChatboxSource(include_source=new_value))

        self._chatbox_source_text.content.value = t(
            "flet.settings.chatbox_source.on" if new_value else "flet.settings.chatbox_source.off"
        )
        try:
            if self.page:
                self._chatbox_source_text.update()
        except (AssertionError, RuntimeError):
            pass
        self._emit_settings_changed()

    # --- Locale ---

    def _apply_locale_osc(self) -> None:
        """Update OSC section labels when locale changes."""
        if not hasattr(self, '_vrc_mic_title'):
            return
        self._vrc_mic_title.value = t("flet.settings.vrc_mic_intercept")
        self._chatbox_source_title.value = t("flet.settings.chatbox_include_source")
        self._clipboard_auto_translate_title.value = t("flet.settings.clipboard_auto_translate")

    def _locale_sensitive_controls(self) -> tuple[ft.Container, ...]:
        """Controls that need font/text updates on locale change."""
        return (
            self._chatbox_source_text,
            self._clipboard_auto_translate_text,
            self._microphone_test_text,
            self._vrc_mic_text,
        )
