from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.ui.components.settings import (
    OptionItem,
    SettingsModal,
)
from puripuly_heart.ui.i18n import (
    available_locales,
    get_locale,
    locale_label,
    native_locale_label,
    t,
)
from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL,
    COLOR_PRIMARY,
)
from puripuly_heart.ui.views.settings_helpers import _CENTER_ALIGNMENT, _update_control_if_mounted
from puripuly_heart.domain.settings_commands import (
    ChangeLocale,
    ChangeVADThreshold,
    ChangeHangover,
    ChangePreRoll,
    ChangeClipboardAutoTranslate,
    ChangeLowLatency,
)

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


# CROSS-TAB CRITICAL — _build_low_latency_widgets creates self._low_latency_card
# which is embedded in _translation_connection_row inside _build_api_tab (settings.py).
# This is the ONLY cross-tab attribute dependency in the entire SettingsView.
# _build_general_tab must call _build_low_latency_widgets BEFORE _build_api_tab runs.

class UiSectionMixin:

    # ------------------------------------------------------------------
    # Widget builders
    # ------------------------------------------------------------------

    def _build_ui_language_widgets(self) -> ft.Control:
        self._ui_text = self._build_clickable_text(
            locale_label(get_locale()),
            self._on_ui_click,
        )
        self._ui_title = ft.Text(
            t("settings.section.ui"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        ui_card = self._wrap_unit_card(
            title=self._ui_title,
            value=self._ui_text,
        )
        return ui_card

    def _build_low_latency_widgets(self) -> ft.Control:
        """Create low-latency mode card. Stores on self; returns the card."""
        self._low_latency_text = self._build_clickable_text(
            t("toggle.off"),
            self._on_low_latency_click,
        )
        self._low_latency_title = ft.Text(
            t("settings.low_latency_mode"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._low_latency_card = self._wrap_unit_card(
            title=self._low_latency_title,
            value=self._low_latency_text,
        )
        return self._low_latency_card

    def _build_vad_widgets(self) -> tuple[ft.Control, ft.Control]:
        # -- Self VAD --
        self._self_vad_title = ft.Text(
            t("settings.section.self_vad_sensitivity"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._vad_slider = ft.Slider(
            min=0.0,
            max=1.0,
            divisions=20,
            value=0.5,
            label="0.50",
            active_color=COLOR_PRIMARY,
            on_change=self._handle_vad_visual_change,
            on_change_end=self._handle_vad_change,
        )
        self._self_vad_card = self._wrap_unit_card(
            title=self._self_vad_title,
            value=ft.Container(content=self._vad_slider, alignment=_CENTER_ALIGNMENT, expand=True),
        )

        # -- Peer VAD --
        self._peer_vad_title = ft.Text(
            t("settings.section.peer_vad_sensitivity"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._peer_vad_slider = ft.Slider(
            min=0.0,
            max=1.0,
            divisions=20,
            value=0.6,
            label="0.60",
            active_color=COLOR_PRIMARY,
            on_change=self._handle_peer_vad_visual_change,
            on_change_end=self._handle_peer_vad_change,
        )
        self._peer_vad_field = self._build_numeric_setting_field(
            label=t("settings.vad.peer"),
            value="0.60",
            on_change_end=self._on_peer_vad_threshold_change,
        )
        self._peer_hangover_field = self._build_numeric_setting_field(
            label=t("settings.vad.peer_hangover_ms"),
            value="700",
            on_change_end=self._on_peer_hangover_change,
        )
        self._peer_pre_roll_field = self._build_numeric_setting_field(
            label=t("settings.vad.peer_pre_roll_ms"),
            value="500",
            on_change_end=self._on_peer_pre_roll_change,
        )
        self._peer_vad_card = self._wrap_unit_card(
            title=self._peer_vad_title,
            value=ft.Container(
                content=self._peer_vad_slider,
                alignment=_CENTER_ALIGNMENT,
                expand=True,
            ),
        )
        return (self._self_vad_card, self._peer_vad_card)

    # ------------------------------------------------------------------
    # Load from settings
    # ------------------------------------------------------------------

    def _load_ui_from_settings(self, settings: "AppSettings") -> None:
        if not hasattr(self, '_ui_text'):
            return
        self._ui_text.content.value = locale_label(settings.ui.locale)

    def _load_vad_and_latency_from_settings(self, settings: "AppSettings") -> None:
        if not hasattr(self, '_vad_slider'):
            return
        self._vad_slider.value = settings.stt.vad_speech_threshold
        self._vad_slider.label = f"{settings.stt.vad_speech_threshold:.2f}"
        self._peer_vad_slider.value = settings.desktop_audio.vad_speech_threshold
        self._peer_vad_slider.label = f"{settings.desktop_audio.vad_speech_threshold:.2f}"
        self._peer_vad_field.value = f"{settings.desktop_audio.vad_speech_threshold:.2f}"
        self._peer_hangover_field.value = str(settings.desktop_audio.vad_hangover_ms)
        self._peer_pre_roll_field.value = str(settings.desktop_audio.vad_pre_roll_ms)
        self._low_latency_text.content.value = t(
            "toggle.on" if settings.stt.low_latency_mode else "toggle.off"
        )

    # ------------------------------------------------------------------
    # UI language
    # ------------------------------------------------------------------

    def _on_ui_click(self, e) -> None:
        if not self.page:
            return
        options = [OptionItem(value=code, label=native_locale_label(code)) for code in available_locales()]
        current = self._settings.ui.locale if self._settings else "en"
        modal = SettingsModal(
            self.page,
            t("settings.section.ui"),
            options,
            self._on_ui_selected,
            show_description=False,
        )
        modal.open(current)

    def _on_ui_selected(self, value: str) -> None:
        if not self._settings:
            return
        old_locale = self._settings.ui.locale
        self._emit_runtime_basic(f"[Settings] Language changed: {old_locale} -> {value}")
        self._command_executor.execute(ChangeLocale(locale=value))

        # Update text
        self._ui_text.content.value = locale_label(value)
        if self.page:
            self._ui_text.update()
        self._emit_settings_changed()

    # ------------------------------------------------------------------
    # VAD (microphone)
    # ------------------------------------------------------------------

    def _handle_vad_visual_change(self, e) -> None:
        self._vad_slider.label = f"{float(e.control.value):.2f}"
        _update_control_if_mounted(self._vad_slider)

    def _handle_vad_change(self, e) -> None:
        if not self._settings:
            return

        new_vad = float(e.control.value)
        old_vad = self._settings.stt.vad_speech_threshold

        if abs(old_vad - new_vad) > 0.001:
            self._emit_runtime_detailed(
                f"[Settings] VAD sensitivity changed: {old_vad:.2f} -> {new_vad:.2f}"
            )

        self._command_executor.execute(ChangeVADThreshold(threshold=new_vad, channel="self"))
        self._emit_settings_changed()

    # ------------------------------------------------------------------
    # Peer VAD (desktop audio)
    # ------------------------------------------------------------------

    def _handle_peer_vad_visual_change(self, e) -> None:
        self._peer_vad_slider.label = f"{float(e.control.value):.2f}"
        _update_control_if_mounted(self._peer_vad_slider)

    def _handle_peer_vad_change(self, e) -> None:
        if not self._settings:
            return

        new_vad = float(e.control.value)
        old_vad = self._settings.desktop_audio.vad_speech_threshold

        if abs(old_vad - new_vad) > 0.001:
            self._emit_runtime_detailed(
                f"[Settings] Peer VAD threshold changed: {old_vad:.2f} -> {new_vad:.2f}"
            )

        self._command_executor.execute(ChangeVADThreshold(threshold=new_vad, channel="peer"))
        self._peer_vad_field.value = f"{new_vad:.2f}"
        self._peer_vad_slider.label = f"{new_vad:.2f}"
        _update_control_if_mounted(self._peer_vad_field)
        _update_control_if_mounted(self._peer_vad_slider)
        self._emit_settings_changed()

    def _on_peer_vad_threshold_change(self, e) -> None:
        if not self._settings:
            return

        old_value = self._settings.desktop_audio.vad_speech_threshold
        new_value = self._parse_setting_float(
            e.control.value,
            fallback=old_value,
            minimum=0.0,
            maximum=1.0,
        )
        if abs(old_value - new_value) > 0.001:
            self._emit_runtime_detailed(
                f"[Settings] Peer VAD threshold changed: {old_value:.2f} -> {new_value:.2f}"
            )

        self._command_executor.execute(ChangeVADThreshold(threshold=new_value, channel="peer"))
        self._peer_vad_field.value = f"{new_value:.2f}"
        _update_control_if_mounted(self._peer_vad_field)
        self._emit_settings_changed()

    def _on_peer_hangover_change(self, e) -> None:
        if not self._settings:
            return

        old_value = self._settings.desktop_audio.vad_hangover_ms
        new_value = self._parse_setting_int(
            e.control.value,
            fallback=old_value,
            minimum=0,
        )
        if old_value != new_value:
            self._emit_runtime_detailed(
                f"[Settings] Peer hangover changed: {old_value} -> {new_value}"
            )

        self._command_executor.execute(ChangeHangover(hangover_ms=new_value, channel="peer"))
        self._peer_hangover_field.value = str(new_value)
        _update_control_if_mounted(self._peer_hangover_field)
        self._emit_settings_changed()

    def _on_peer_pre_roll_change(self, e) -> None:
        if not self._settings:
            return

        old_value = self._settings.desktop_audio.vad_pre_roll_ms
        new_value = self._parse_setting_int(
            e.control.value,
            fallback=old_value,
            minimum=0,
        )
        if old_value != new_value:
            self._emit_runtime_detailed(
                f"[Settings] Peer pre-roll changed: {old_value} -> {new_value}"
            )

        self._command_executor.execute(ChangePreRoll(pre_roll_ms=new_value, channel="peer"))
        self._peer_pre_roll_field.value = str(new_value)
        _update_control_if_mounted(self._peer_pre_roll_field)
        self._emit_settings_changed()

    # ------------------------------------------------------------------
    # Clipboard auto-translate
    # ------------------------------------------------------------------

    def _on_clipboard_auto_translate_click(self, e) -> None:
        if not self._settings:
            return
        next_value = "off" if self._settings.ui.clipboard_auto_translate_enabled else "on"
        self._on_clipboard_auto_translate_selected(next_value)

    def _on_clipboard_auto_translate_selected(self, value: str) -> None:
        if not self._settings:
            return
        new_value = value == "on"
        self._emit_runtime_basic(f"[Settings] Clipboard auto translate toggled: {new_value}")
        self._command_executor.execute(ChangeClipboardAutoTranslate(enabled=new_value))
        # CROSS-MIXIN: _clipboard_auto_translate_text is created by OscSectionMixin._build_osc_widgets
        self._clipboard_auto_translate_text.content.value = t(
            "settings.clipboard_auto_translate.on"
            if new_value
            else "settings.clipboard_auto_translate.off"
        )
        if self.page:
            self._clipboard_auto_translate_text.update()
        self._emit_settings_changed()

    # ------------------------------------------------------------------
    # Low latency mode
    # ------------------------------------------------------------------

    def _on_low_latency_click(self, e) -> None:
        if not self.page:
            return
        options = [
            OptionItem(
                value="on",
                label=t("toggle.on"),
                description=t("toggle.on.description", default=""),
            ),
            OptionItem(
                value="off",
                label=t("toggle.off"),
                description=t("toggle.off.description", default=""),
            ),
        ]
        current = "on" if self._settings.stt.low_latency_mode else "off"
        modal = SettingsModal(
            self.page,
            t("settings.low_latency_mode"),
            options,
            self._on_low_latency_selected,
            show_description=True,
        )
        modal.open(current)

    def _on_low_latency_selected(self, value: str) -> None:
        if not self._settings:
            return
        new_value = value == "on"
        old_value = self._settings.stt.low_latency_mode
        if new_value != old_value:
            self._emit_runtime_detailed(
                f"[Settings] Low latency mode changed: {old_value} -> {new_value}"
            )
        self._command_executor.execute(ChangeLowLatency(enabled=new_value))

        # Update text
        self._low_latency_text.content.value = t("toggle.on" if new_value else "toggle.off")
        if self.page:
            self._low_latency_text.update()
        self._emit_settings_changed()

    # --- Locale ---

    def _apply_locale_ui(self) -> None:
        if not hasattr(self, '_ui_title'):
            return
        self._ui_title.value = t("settings.section.ui")
        self._self_vad_title.value = t("settings.section.self_vad_sensitivity")
        self._peer_vad_title.value = t("settings.section.peer_vad_sensitivity")
        # CROSS-MIXIN: _microphone_test_title is created by OscSectionMixin._build_osc_widgets
        self._microphone_test_title.value = t("settings.microphone_test")
        self._peer_vad_field.label = t("settings.vad.peer")
        self._peer_hangover_field.label = t("settings.vad.peer_hangover_ms")
        self._peer_pre_roll_field.label = t("settings.vad.peer_pre_roll_ms")
        self._low_latency_title.value = t("settings.low_latency_mode")
