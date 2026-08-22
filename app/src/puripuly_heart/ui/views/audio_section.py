"""Audio section mixin — host API, microphone, loopback audio handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.domain.settings_commands import ChangeAudioDevice
from puripuly_heart.ui.components.settings import (
    AudioSettings,
    OptionItem,
    SettingsModal,
)
from puripuly_heart.domain.i18n import t
from puripuly_heart.ui.theme import COLOR_NEUTRAL

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


# ATTRIBUTE OWNERSHIP — _build_audio_widgets creates:
#   _audio_settings (AudioSettings component — owns device enumeration),
#   _audio_host_api_title/text, _mic_audio_title/text, _loopback_audio_title/text
#
# AudioSettings is a stateful component that enumerates audio devices.
# Host API selection resets microphone (device list changes per host API).
# _sync_general_audio_card_texts (SettingsHelpersMixin) updates display labels.

class AudioSectionMixin:

    # ------------------------------------------------------------------
    # Load from settings
    # ------------------------------------------------------------------

    def _load_audio_from_settings(self, settings: "AppSettings") -> None:
        if not hasattr(self, '_audio_settings'):
            return
        self._audio_settings.host_api = settings.audio.input_host_api
        self._audio_settings.microphone = settings.audio.input_device
        self._audio_settings.desktop_output_device = settings.desktop_audio.output_device
        self._sync_general_audio_card_texts()

    # ------------------------------------------------------------------
    # Widget builders
    # ------------------------------------------------------------------

    def _build_audio_widgets(self) -> tuple[ft.Control, ft.Control, ft.Control]:
        """Create host-API, mic-audio, and loopback-audio cards.

        Also initialises ``self._audio_settings``.
        Returns ``(host_api_card, mic_audio_card, loopback_audio_card)``.
        """
        self._audio_settings = AudioSettings(on_change=self._on_audio_change)

        # -- Host API --
        self._audio_host_api_title = ft.Text(
            t("settings.audio_host_api"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._audio_host_api_text = self._build_clickable_text(
            t("settings.default_option"),
            self._on_mic_host_api_click,
        )
        host_api_card = self._wrap_unit_card(
            title=self._audio_host_api_title,
            value=self._audio_host_api_text,
        )

        # -- Microphone Audio --
        self._mic_audio_title = ft.Text(
            t("settings.section.microphone_audio"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._mic_audio_text = self._build_clickable_text(
            t("settings.default_option"),
            self._on_mic_audio_click,
        )
        mic_audio_card = self._wrap_unit_card(
            title=self._mic_audio_title,
            value=self._mic_audio_text,
        )

        # -- Loopback Audio --
        self._loopback_audio_title = ft.Text(
            t("settings.section.loopback_audio"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._loopback_audio_text = self._build_clickable_text(
            t("settings.default_option"),
            self._on_loopback_audio_click,
        )
        loopback_audio_card = self._wrap_unit_card(
            title=self._loopback_audio_title,
            value=self._loopback_audio_text,
        )

        return (host_api_card, mic_audio_card, loopback_audio_card)

    def _on_audio_change(self) -> None:
        if not self._settings:
            return

        new_host = self._audio_settings.host_api
        new_device = self._audio_settings.microphone
        new_desktop_output = self._audio_settings.desktop_output_device
        old_host = self._settings.audio.input_host_api
        old_device = self._settings.audio.input_device
        old_desktop_output = self._settings.desktop_audio.output_device

        if old_host != new_host:
            self._emit_runtime_basic(f"[Settings] Audio Host changed: {old_host} -> {new_host}")
        if old_device != new_device:
            self._emit_runtime_basic(f"[Settings] Microphone changed: {old_device} -> {new_device}")
        if old_desktop_output != new_desktop_output:
            self._emit_runtime_basic(
                f"[Settings] Desktop loopback output changed: {old_desktop_output} -> {new_desktop_output}"
            )

        self._command_executor.execute(ChangeAudioDevice(
            host_api=new_host,
            input_device=new_device,
            desktop_output_device=new_desktop_output,
        ))
        self._emit_settings_changed()

    def _on_mic_host_api_click(self, e) -> None:
        try:
            if not self.page:
                return
        except (AssertionError, RuntimeError):
            return
        options = self._audio_settings._get_host_api_options()
        modal = SettingsModal(
            self.page,
            t("settings.audio_host_api"),
            options,
            self._on_mic_host_api_selected,
            show_description=False,
        )
        modal.open(self._audio_settings.host_api)

    def _on_mic_host_api_selected(self, value: str) -> None:
        self._audio_settings.host_api = value
        self._audio_settings.microphone = ""
        self._sync_general_audio_card_texts()
        try:
            if self.page:
                self._mic_audio_text.update()
        except (AssertionError, RuntimeError):
            pass
        try:
            if self.page:
                self._audio_host_api_text.update()
        except (AssertionError, RuntimeError):
            pass
        self._on_audio_change()

    def _on_mic_audio_click(self, e) -> None:
        try:
            if not self.page:
                return
        except (AssertionError, RuntimeError):
            return
        options = self._audio_settings._get_microphone_options()
        modal = SettingsModal(
            self.page,
            t("settings.section.microphone_audio"),
            options,
            self._on_mic_audio_selected,
            show_description=False,
        )
        modal.open(self._audio_settings.microphone)

    def _on_mic_audio_selected(self, value: str) -> None:
        self._audio_settings.microphone = value
        self._sync_general_audio_card_texts()
        try:
            if self.page:
                self._mic_audio_text.update()
        except (AssertionError, RuntimeError):
            pass
        self._on_audio_change()

    def _on_loopback_audio_click(self, e) -> None:
        try:
            if not self.page:
                return
        except (AssertionError, RuntimeError):
            return
        options = self._audio_settings._get_desktop_output_options()
        modal = SettingsModal(
            self.page,
            t("settings.section.loopback_audio"),
            options,
            self._on_loopback_audio_selected,
            show_description=False,
        )
        modal.open(self._audio_settings.desktop_output_device)

    def _on_loopback_audio_selected(self, value: str) -> None:
        self._audio_settings.desktop_output_device = value
        self._sync_general_audio_card_texts()
        try:
            if self.page:
                self._loopback_audio_text.update()
        except (AssertionError, RuntimeError):
            pass
        self._on_audio_change()

    # --- Locale ---

    def _apply_locale_audio(self) -> None:
        if not hasattr(self, '_audio_host_api_title'):
            return
        self._audio_host_api_title.value = t("settings.audio_host_api")
        self._mic_audio_title.value = t("settings.section.microphone_audio")
        self._loopback_audio_title.value = t("settings.section.loopback_audio")
        self._audio_settings.apply_locale()
        self._sync_general_audio_card_texts()

    def _locale_sensitive_controls(self) -> tuple[ft.Container, ...]:
        """Controls that need font/text updates on locale change."""
        return (
            self._mic_audio_text,
            self._audio_host_api_text,
            self._loopback_audio_text,
        )
