"""Settings view - Bento grid layout with SegmentedButton providers."""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import math
import re
from pathlib import Path
from typing import Callable

import flet as ft

from puripuly_heart.app.wiring import create_secret_store
from puripuly_heart.config.prompts import load_prompt_for_provider
from puripuly_heart.config.settings import (
    DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
    DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER,
    DESKTOP_FLET_SIZE_PRESET_ORDER,
    LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS,
    LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS,
    MAX_CUSTOM_VOCAB_TERMS,
    OVERLAY_TARGET_DESKTOP,
    OVERLAY_TARGET_STEAMVR,
    AppSettings,
    LLMProviderName,
    STTProviderName,
    TranslationConnection,
    TranslationFallbackSelectionAlias,
    TranslationModel,
    _normalize_local_llm_base_url,
    default_translation_connection,
    materialize_translation_settings,
    supported_translation_connections,
)
from puripuly_heart.core.language import get_stt_compatibility_warning
from puripuly_heart.ui.components.settings import (
    ApiKeyField,
    AudioSettings,
    CustomVocabularyTagEditor,
    OptionItem,
    PromptEditor,
    SettingsModal,
    SettingsUnitCard,
)
from puripuly_heart.ui.components.shared_card_wrapper import SharedCardWrapper
from puripuly_heart.ui.components.subtab_shell import TextSubtab, TextSubtabShell
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import (
    available_locales,
    get_locale,
    language_name,
    locale_label,
    native_locale_label,
    provider_label,
    t,
)
from puripuly_heart.ui.overlay_calibration import (
    OVERLAY_CALIBRATION_ANCHORS,
    OverlayCalibration,
)
from puripuly_heart.ui.overlay_peer_contract import OverlayPeerConsumerContract
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_NEUTRAL_DARK,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    COLOR_SURFACE_DIM,
)

logger = logging.getLogger(__name__)

_CJK_START = 0x3000
_CENTER_ALIGNMENT = ft.alignment.Alignment(0, 0)
_CENTER_RIGHT_ALIGNMENT = ft.alignment.Alignment(1, 0)
_SETTINGS_SUBTAB_ORDER = ("api", "general", "prompt", "overlay")
_OVERLAY_DISTANCE_MIN = 0.5
_OVERLAY_DISTANCE_MAX = 2.0
_OVERLAY_DISTANCE_DIVISIONS = 30
_OVERLAY_OFFSET_STEP = 0.05
_DESKTOP_OVERLAY_BACKGROUND_ALPHA_STEP = 0.1
_OVERLAY_TEXT_SCALE_PRESETS = (
    ("large", 1.2),
    ("normal", 1.0),
    ("small", 0.8),
)
_DESKTOP_OVERLAY_REOPEN_FAILURE_REASONS = frozenset({"window_configuration_failed"})
_CUSTOM_VOCAB_DELIMITER_RE = re.compile(r"\s+")
_TRANSLATION_MODEL_LABEL_KEYS = {
    TranslationModel.GEMMA4: "provider.gemma4_26b_a4b_it",
    TranslationModel.DEEPSEEK_V4_FLASH: "provider.deepseek_v4_flash",
    TranslationModel.DEEPSEEK_V4_PRO: "provider.deepseek_v4_pro",
    TranslationModel.GEMINI_3_FLASH: "provider.gemini3_flash",
    TranslationModel.GEMINI_31_FLASH_LITE: "provider.gemini31_flash_lite",
    TranslationModel.QWEN_35_PLUS: "provider.qwen35_plus",
    TranslationModel.LOCAL_LLM: "provider.local_llms",
    TranslationModel.GEMMA4_31B_CEREBRAS: "provider.gemma4_31b_cerebras",
    TranslationModel.OPENAI_COMPATIBLE: "provider.openai_compatible",
}
_TRANSLATION_CONNECTION_LABEL_KEYS = {
    TranslationConnection.OPENROUTER: "settings.translation_connection.openrouter",
    TranslationConnection.OFFICIAL_BYOK: "settings.translation_connection.official_byok",
    TranslationConnection.OLLAMA: "settings.translation_connection.ollama",
    TranslationConnection.OPENAI_COMPATIBLE: "settings.translation_connection.openai_compatible",
}
_TRANSLATION_CONNECTION_DESCRIPTION_KEYS = {
    TranslationConnection.OPENROUTER: "settings.translation_connection.openrouter.description",
    TranslationConnection.OFFICIAL_BYOK: "settings.translation_connection.official_byok.description",
    TranslationConnection.OLLAMA: "settings.translation_connection.ollama.description",
    TranslationConnection.OPENAI_COMPATIBLE: "settings.translation_connection.openai_compatible.description",
}
_TRANSLATION_CONNECTION_ONLY_SUPPORTED_KEY = "settings.translation_connection.only_supported"
_TRANSLATION_FALLBACK_SELECTION_ORDER = (
    TranslationFallbackSelectionAlias.NONE,
    TranslationFallbackSelectionAlias.DEEPSEEK_V4_FLASH_OFFICIAL,
    TranslationFallbackSelectionAlias.OPENROUTER_DEEPSEEK_V4_FLASH,
    TranslationFallbackSelectionAlias.OPENROUTER_GEMMA4_26B_A4B,
    TranslationFallbackSelectionAlias.CEREBRAS_GEMMA4_31B,
)
_TRANSLATION_FALLBACK_LABEL_KEYS = {
    TranslationFallbackSelectionAlias.NONE: "settings.fallback.none",
    TranslationFallbackSelectionAlias.DEEPSEEK_V4_FLASH_OFFICIAL: "settings.fallback.deepseek_v4_flash_official",
    TranslationFallbackSelectionAlias.OPENROUTER_DEEPSEEK_V4_FLASH: "settings.fallback.openrouter_deepseek_v4_flash",
    TranslationFallbackSelectionAlias.OPENROUTER_GEMMA4_26B_A4B: "settings.fallback.openrouter_gemma4_26b_a4b",
    TranslationFallbackSelectionAlias.CEREBRAS_GEMMA4_31B: "settings.fallback.cerebras_gemma4_31b",
}
_TRANSLATION_FALLBACK_DESCRIPTION_KEYS = {
    TranslationFallbackSelectionAlias.CEREBRAS_GEMMA4_31B: "settings.fallback.cerebras_gemma4_31b.description",
}


def _make_text_button(label: str, **kwargs) -> ft.TextButton:
    return ft.TextButton(text=label, **kwargs)


def _set_text_button_label(button: ft.TextButton, label: str) -> None:
    button.text = label


def _reject_json_constant(value: str) -> None:
    raise json.JSONDecodeError(f"invalid JSON constant: {value}", value, 0)


def _update_control_if_mounted(control: ft.Control) -> None:
    """Update a Flet control only while it is attached to a page."""
    if getattr(control, "page", None) is None:
        return
    try:
        control.update()
    except AssertionError as exc:
        if "Control must be added" not in str(exc):
            raise


def _make_overlay_anchor_dropdown(value: str, on_change) -> ft.Dropdown:
    return ft.Dropdown(
        value=value,
        options=[
            ft.dropdown.Option(
                key=anchor,
                text=t(f"settings.overlay.calibration.anchor.{anchor}"),
            )
            for anchor in OVERLAY_CALIBRATION_ANCHORS
        ],
        text_size=14,
        border_radius=10,
        border_color=COLOR_DIVIDER,
        focused_border_color=COLOR_PRIMARY,
        on_change=on_change,
    )


def _load_secret_value(store, key: str, *, legacy_keys: tuple[str, ...] = ()) -> str:
    """Load secret value with legacy key fallback."""
    value = store.get(key) or ""
    if value or not legacy_keys:
        return value
    for legacy_key in legacy_keys:
        legacy_value = store.get(legacy_key) or ""
        if legacy_value:
            with contextlib.suppress(Exception):
                store.set(key, legacy_value)
            return legacy_value
    return ""


def _weighted_len(text: str) -> int:
    return sum(2 if ord(char) >= _CJK_START else 1 for char in text)


def _setting_action_text_size(text: str) -> int:
    length = _weighted_len(text or "")
    if length <= 6:
        return 22
    if length <= 10:
        return 20
    if length <= 18:
        return 18
    return 16


class SettingsView(ft.Column):
    """Settings view with Bento grid layout."""

    def __init__(self, initial_settings: AppSettings | None = None):
        super().__init__(expand=True, spacing=16)
        self._initial_settings = initial_settings

        # Callbacks (assigned by App)
        self.on_settings_changed: Callable[[AppSettings], None] | None = None
        self.on_prompt_apply_settings: Callable[[AppSettings], None] | None = None
        self.on_providers_changed: Callable[[], None] | None = None
        self.on_local_llm_secret_changed: Callable[[], None] | None = None
        self.on_verify_api_key: Callable[[str, str], object] | None = None
        self.on_secret_cleared: Callable[[str], None] | None = None  # key name
        self.on_overlay_calibration_begin: Callable[[], OverlayCalibration] | None = None
        self.on_overlay_calibration_change: Callable[[str, object], OverlayCalibration] | None = (
            None
        )
        self.on_overlay_calibration_apply: Callable[[], OverlayCalibration] | None = None
        self.on_overlay_calibration_cancel: Callable[[], OverlayCalibration] | None = None
        self.on_desktop_overlay_lock_change: Callable[[bool], None] | None = None
        self.on_desktop_overlay_size_change: Callable[[str], None] | None = None
        self.on_desktop_overlay_recovery_action: Callable[[str], None] | None = None
        self.on_desktop_overlay_position_reset: Callable[[], None] | None = None
        self.on_view_logs: Callable[[], None] | None = None
        self.on_start_microphone_test: Callable[[], None] | None = None
        self.show_snackbar: Callable[[str, str], None] | None = None
        self.runtime_log_basic: Callable[..., None] | None = None
        self.runtime_log_detailed: Callable[..., None] | None = None

        # State
        self._settings: AppSettings | None = None
        self._provider_settings_draft: AppSettings | None = None
        self._config_path: Path | None = None
        self.has_provider_changes: bool = False
        self.has_pending_prompt_changes: bool = False
        self._overlay_state: str = "off"
        self._overlay_failure_reason: str | None = None
        self._overlay_runtime_target: str = OVERLAY_TARGET_STEAMVR
        self._desktop_overlay_captions_locked = False
        self._desktop_overlay_pending_locked: bool | None = None
        self._desktop_overlay_primary_action_kind: str | None = None
        self._desktop_overlay_pending_size_preset: str | None = None
        self._desktop_overlay_pending_position_reset = False
        self._overlay_calibration = OverlayCalibration()
        self._overlay_calibration_draft = self._overlay_calibration.copy()
        self._overlay_calibration_session_active = False
        self._overlay_peer_contract: OverlayPeerConsumerContract | None = None

        # Build UI components
        self._build_ui()

    # --- Card Wrapper (About page pattern) ---
    def _wrap_card(
        self,
        content: ft.Control,
        *,
        expand: bool | None = None,
        height: float | int | None = SharedCardWrapper.DEFAULT_HEIGHT,
    ) -> SharedCardWrapper:
        """Wrap content in the shared card shell used across settings/about."""
        return SharedCardWrapper(
            content,
            expand=expand,
            height=height,
        )

    def _wrap_unit_card(
        self,
        *,
        title: ft.Control,
        value: ft.Control,
        extra_controls: tuple[ft.Control, ...] = (),
        height: float | int | None = SettingsUnitCard.DEFAULT_HEIGHT,
    ) -> SettingsUnitCard:
        return SettingsUnitCard(
            title=title,
            value=value,
            extra_controls=extra_controls,
            height=height,
        )

    def _wrap_empty_unit_card(
        self,
        *,
        height: float | int | None = SettingsUnitCard.DEFAULT_HEIGHT,
    ) -> SharedCardWrapper:
        return self._wrap_card(ft.Container(expand=True), expand=True, height=height)

    # --- Clickable Text Builders ---
    def _build_clickable_text(
        self,
        text: str,
        on_click,
        *,
        size: int = 28,
        text_align: ft.TextAlign = ft.TextAlign.CENTER,
        alignment=_CENTER_ALIGNMENT,
        no_wrap: bool = False,
        max_lines: int | None = None,
        overflow: ft.TextOverflow | None = None,
        width: float | int | None = None,
        height: float | int | None = None,
        expand: bool | int | None = True,
    ) -> ft.Container:
        """Build a clickable centered text with hover effect."""
        text_control = ft.Text(
            text,
            size=size,
            font_family=font_for_language(get_locale()),
            color=COLOR_ON_BACKGROUND,
            text_align=text_align,
            no_wrap=no_wrap,
            max_lines=max_lines,
            overflow=overflow,
        )
        return ft.Container(
            content=text_control,
            alignment=alignment,
            width=width,
            height=height,
            expand=expand,
            on_click=on_click,
            on_hover=self._on_text_hover,
        )

    def _build_setting_action_text(self, text: str, on_click) -> ft.Container:
        return self._build_clickable_text(
            text,
            on_click,
            size=_setting_action_text_size(text),
            text_align=ft.TextAlign.RIGHT,
            alignment=_CENTER_RIGHT_ALIGNMENT,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

    def _set_setting_action_text(self, control: ft.Container, text: str) -> None:
        text_control = control.content
        text_control.value = text
        text_control.size = _setting_action_text_size(text)

    def _set_unit_card_value_text(
        self, control: ft.Container, text: str, *, size: int = 28
    ) -> None:
        text_control = control.content
        text_control.value = text
        text_control.size = size

    def _iter_locale_sensitive_clickable_text_controls(self) -> tuple[ft.Container, ...]:
        return (
            self._integrated_context_button,
            self._stt_text,
            self._peer_stt_text,
            self._llm_text,
            self._ui_text,
            self._chatbox_source_text,
            self._clipboard_auto_translate_text,
            self._microphone_test_text,
            self._vrc_mic_text,
            self._mic_audio_text,
            self._audio_host_api_text,
            self._loopback_audio_text,
            self._low_latency_text,
            self._overlay_translation_button,
            self._overlay_peer_original_button,
            self._overlay_target_button,
            self._overlay_anchor_button,
            self._overlay_text_scale_text,
            self._desktop_overlay_size_button,
            self._desktop_overlay_lock_button,
            self._overlay_vr_reset_button,
            self._overlay_desktop_reset_button,
            self._desktop_overlay_primary_action,
            self._desktop_overlay_view_logs_action,
            self._translation_connection_text,
            self._openrouter_fallback_text,
        )

    def _sync_clickable_text_control_fonts(self, font_family: str | None) -> None:
        for control in self._iter_locale_sensitive_clickable_text_controls():
            if control:
                control.content.font_family = font_family

    def _sync_general_audio_card_texts(self) -> None:
        default_label = t("settings.default_option")
        self._set_unit_card_value_text(
            self._mic_audio_text,
            self._audio_settings.microphone or default_label,
        )
        self._set_unit_card_value_text(
            self._audio_host_api_text,
            self._audio_settings.host_api_display_label,
        )
        self._set_unit_card_value_text(
            self._loopback_audio_text,
            self._audio_settings.desktop_output_device or default_label,
        )

    def _on_text_hover(self, e: ft.ControlEvent) -> None:
        """Handle hover effect on clickable text."""
        container = e.control
        text_control = container.content
        next_color = COLOR_PRIMARY if e.data == "true" else COLOR_ON_BACKGROUND
        if text_control.color == next_color:
            return
        text_control.color = next_color
        container.update()

    def _make_overlay_step_hover_handler(self, text_control: ft.Text):
        def _on_hover(e: ft.ControlEvent) -> None:
            next_color = COLOR_PRIMARY if e.data == "true" else COLOR_ON_BACKGROUND
            if text_control.color == next_color:
                return
            text_control.color = next_color
            if text_control.page is not None:
                text_control.update()

        return _on_hover

    def _build_overlay_step_hit_lane(self, on_click, *, on_hover=None) -> ft.Container:
        return ft.Container(
            content=ft.Container(expand=True),
            expand=1,
            on_click=on_click,
            on_hover=on_hover,
        )

    def _build_overlay_step_visual_lane(
        self, text: str, *, alignment
    ) -> tuple[ft.Container, ft.Text]:
        text_control = ft.Text(
            text,
            size=22,
            font_family=font_for_language(get_locale()),
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        return (
            ft.Container(
                content=text_control,
                expand=1,
                alignment=alignment,
            ),
            text_control,
        )

    def _build_overlay_step_split_layout(
        self,
        *,
        title: ft.Text,
        value_text: ft.Text,
        decrease_text: str,
        increase_text: str,
        on_decrease,
        on_increase,
    ) -> tuple[ft.Stack, ft.Container, ft.Container, ft.Text, ft.Text]:
        decrease_visual, decrease_glyph = self._build_overlay_step_visual_lane(
            decrease_text,
            alignment=ft.alignment.center_right,
        )
        increase_visual, increase_glyph = self._build_overlay_step_visual_lane(
            increase_text,
            alignment=ft.alignment.center_left,
        )
        decrease_lane = self._build_overlay_step_hit_lane(
            on_decrease,
            on_hover=self._make_overlay_step_hover_handler(decrease_glyph),
        )
        increase_lane = self._build_overlay_step_hit_lane(
            on_increase,
            on_hover=self._make_overlay_step_hover_handler(increase_glyph),
        )
        visual_row = ft.Row(
            controls=[
                decrease_visual,
                ft.Container(
                    content=value_text,
                    width=84,
                    alignment=ft.alignment.center,
                ),
                increase_visual,
            ],
            spacing=4,
            expand=1,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        visual_column = ft.Column(
            controls=[
                title,
                ft.Container(
                    content=visual_row,
                    expand=True,
                    alignment=ft.alignment.center,
                ),
            ],
            spacing=0,
            expand=True,
        )
        stack = ft.Stack(
            controls=[
                ft.Row(
                    controls=[decrease_lane, increase_lane],
                    spacing=0,
                    expand=1,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                ft.TransparentPointer(content=visual_column),
            ],
            fit=ft.StackFit.EXPAND,
            expand=True,
            alignment=ft.alignment.center,
        )
        return stack, decrease_lane, increase_lane, decrease_glyph, increase_glyph

    def _get_button_style(
        self,
        font_family: str,
        *,
        size: int = 20,
        default_color: str = COLOR_NEUTRAL,
        disabled_color: str | None = None,
    ) -> ft.ButtonStyle:
        """Create a complete ButtonStyle with the specified font."""
        color = {
            ft.ControlState.HOVERED: COLOR_PRIMARY,
            ft.ControlState.DEFAULT: default_color,
        }
        if disabled_color is not None:
            color[ft.ControlState.DISABLED] = disabled_color
        return ft.ButtonStyle(
            color=color,
            icon_color=color,
            text_style=ft.TextStyle(
                size=size,
                font_family=font_family,
            ),
            overlay_color=ft.Colors.TRANSPARENT,
            animation_duration=0,
        )

    def _settings_subtab_label(self, key: str) -> str:
        return t(f"settings.subtab.{key}")

    def _build_settings_subtab_shell(
        self, tab_rows: dict[str, list[ft.Control]]
    ) -> TextSubtabShell:
        return TextSubtabShell(
            tabs=[
                TextSubtab(key, self._settings_subtab_label(key), tuple(tab_rows[key]))
                for key in _SETTINGS_SUBTAB_ORDER
            ],
            font_family=font_for_language(get_locale()),
            initial_key=_SETTINGS_SUBTAB_ORDER[0],
            subtab_bar_position="bottom",
        )

    def _build_setting_action_row(self, label: ft.Text, action: ft.Control) -> ft.Row:
        return ft.Row(
            controls=[label, ft.Container(expand=True), action],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _emit_runtime_basic(self, message: str, *, level: int = logging.INFO) -> None:
        runtime_log_basic = getattr(self, "runtime_log_basic", None)
        if runtime_log_basic is not None:
            runtime_log_basic(message, level=level)
            return
        logger.log(level, message)

    def _emit_runtime_detailed(self, message: str, *, level: int = logging.INFO) -> None:
        runtime_log_detailed = getattr(self, "runtime_log_detailed", None)
        if runtime_log_detailed is not None:
            runtime_log_detailed(message, level=level)
            return
        logger.log(level, message)

    def _build_action_button(
        self,
        text: str,
        on_click,
        *,
        size: int = 20,
        default_color: str = COLOR_NEUTRAL,
        disabled_color: str | None = None,
        width: float | int | None = None,
        height: float | int | None = None,
    ) -> ft.TextButton:
        return _make_text_button(
            text,
            style=self._get_button_style(
                font_for_language(get_locale()),
                size=size,
                default_color=default_color,
                disabled_color=disabled_color,
            ),
            on_click=on_click,
            width=width,
            height=height,
        )

    def _build_integrated_context_unit_card(self) -> SettingsUnitCard:
        self._integrated_context_label = ft.Text(
            t("settings.integrated_context"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._integrated_context_button = self._build_clickable_text(
            t("settings.context.local"),
            self._on_integrated_context_click,
        )
        self._integrated_context_hint = ft.Text("", size=13, color=COLOR_NEUTRAL)

        self._integrated_context_card = self._wrap_unit_card(
            title=self._integrated_context_label,
            value=self._integrated_context_button,
        )
        return self._integrated_context_card

    def _build_overlay_calibration_field(
        self,
        *,
        value: float,
        on_blur,
    ) -> ft.TextField:
        return ft.TextField(
            value=self._format_overlay_calibration_number(value),
            text_size=14,
            width=120,
            border_radius=10,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            on_blur=on_blur,
        )

    def _build_numeric_setting_field(
        self,
        *,
        label: str,
        value: str,
        on_change_end,
    ) -> ft.TextField:
        return ft.TextField(
            label=label,
            value=value,
            dense=True,
            expand=True,
            text_align=ft.TextAlign.CENTER,
            border_radius=10,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            on_blur=on_change_end,
            on_submit=on_change_end,
        )

    def _build_overlay_calibration_column(
        self,
        *,
        label: ft.Text,
        control: ft.Control,
    ) -> ft.Column:
        return ft.Column(
            controls=[label, control],
            spacing=6,
            expand=True,
        )

    def _format_overlay_calibration_number(self, value: float) -> str:
        return f"{value:.2f}"

    def _overlay_anchor_label_for(self, anchor: str) -> str:
        return t(f"settings.overlay.calibration.anchor.{anchor}")

    def _overlay_text_scale_label_for(self, value: float) -> str:
        return t(
            f"settings.overlay.calibration.text_scale.{self._overlay_text_scale_preset_key_for(value)}"
        )

    def _overlay_text_scale_preset_key_for(self, value: float) -> str:
        return min(
            _OVERLAY_TEXT_SCALE_PRESETS,
            key=lambda preset: abs(preset[1] - value),
        )[0]

    def _overlay_text_scale_value_for(self, preset_key: str) -> float:
        for key, scale in _OVERLAY_TEXT_SCALE_PRESETS:
            if key == preset_key:
                return scale
        try:
            return float(preset_key)
        except (TypeError, ValueError):
            return 1.0

    def _parse_setting_float(
        self,
        raw_value: str,
        *,
        fallback: float,
        minimum: float,
        maximum: float | None = None,
    ) -> float:
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            parsed = fallback
        if parsed < minimum:
            parsed = minimum
        if maximum is not None and parsed > maximum:
            parsed = maximum
        return parsed

    def _parse_setting_int(
        self,
        raw_value: str,
        *,
        fallback: int,
        minimum: int,
    ) -> int:
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            parsed = fallback
        return max(minimum, parsed)

    def _build_ui(self) -> None:
        """Build the settings UI with Bento grid layout."""
        # === API provider surfaces: Self STT + Peer STT + Shared Translation ===
        self._stt_text = self._build_clickable_text(
            provider_label(STTProviderName.LOCAL_QWEN.value),
            self._on_stt_click,
        )
        self._stt_compute_label = ft.Text(
            t("settings.compute.label"), size=14, color=COLOR_ON_BACKGROUND
        )
        self._stt_compute_gpu_btn = ft.Container(
            content=ft.Text("GPU", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
            bgcolor=COLOR_PRIMARY,
            border=ft.border.all(1, COLOR_PRIMARY),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_stt_compute_gpu_click,
        )
        self._stt_compute_cpu_btn = ft.Container(
            content=ft.Text("CPU", size=14, color=COLOR_ON_BACKGROUND),
            bgcolor=COLOR_SURFACE,
            border=ft.border.all(1, COLOR_DIVIDER),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_stt_compute_cpu_click,
        )
        self._stt_compute_row = ft.Row(
            [self._stt_compute_label, self._stt_compute_gpu_btn, self._stt_compute_cpu_btn],
            spacing=8,
            visible=False,
        )
        self._stt_quant_label = ft.Text(
            t("settings.quant.label", default="Quant:"), size=14, color=COLOR_ON_BACKGROUND
        )
        self._stt_quant_q8_btn = self._make_quant_button("Q8_0", lambda e: self._apply_stt_quant("q8_0"))
        self._stt_quant_q6k_btn = self._make_quant_button("Q6_K", lambda e: self._apply_stt_quant("q6_k"))
        self._stt_quant_f16_btn = self._make_quant_button("F16", lambda e: self._apply_stt_quant("f16"))
        self._stt_quant_int8_btn = self._make_quant_button("int8", lambda e: self._apply_stt_quant("int8"))
        # Set initial quant button state based on loaded settings
        _init_stt = self._initial_settings.provider.stt if self._initial_settings else STTProviderName.LOCAL_QWEN
        _init_quant = self._initial_settings.provider.stt_quant if self._initial_settings else "auto"
        _init_available = self._INITIAL_QUANTS_FOR_PROVIDER.get(_init_stt, ["int8"])
        _init_active = _init_quant if _init_quant in _init_available else (_init_available[0] if _init_available else "auto")
        for _q, _btn in [("q8_0", self._stt_quant_q8_btn), ("q6_k", self._stt_quant_q6k_btn), ("f16", self._stt_quant_f16_btn), ("int8", self._stt_quant_int8_btn)]:
            _btn.visible = _q in _init_available
            if _q == _init_active:
                _btn.bgcolor = COLOR_PRIMARY
                _btn.border = ft.border.all(1, COLOR_PRIMARY)
                _btn.content.color = ft.Colors.WHITE
                _btn.content.weight = ft.FontWeight.BOLD
        self._stt_quant_row = ft.Row(
            [self._stt_quant_label, self._stt_quant_q8_btn, self._stt_quant_q6k_btn, self._stt_quant_f16_btn, self._stt_quant_int8_btn],
            spacing=8,
        )
        self._stt_backend_label = ft.Text(
            t("settings.backend.label", default="Backend:"), size=14, color=COLOR_ON_BACKGROUND
        )
        # Set initial backend button state based on loaded settings
        _init_backend = self._initial_settings.provider.stt_backend if self._initial_settings else "onnx"
        _init_is_onnx = _init_backend == "onnx"
        self._stt_backend_onnx_btn = ft.Container(
            content=ft.Text("DirectML", size=14, weight=ft.FontWeight.BOLD if _init_is_onnx else ft.FontWeight.NORMAL, color=ft.Colors.WHITE if _init_is_onnx else COLOR_ON_BACKGROUND),
            bgcolor=COLOR_PRIMARY if _init_is_onnx else COLOR_SURFACE,
            border=ft.border.all(1, COLOR_PRIMARY if _init_is_onnx else COLOR_DIVIDER),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_stt_backend_onnx_click,
        )
        self._stt_backend_gguf_btn = ft.Container(
            content=ft.Text("Vulkan", size=14, weight=ft.FontWeight.BOLD if not _init_is_onnx else ft.FontWeight.NORMAL, color=ft.Colors.WHITE if not _init_is_onnx else COLOR_ON_BACKGROUND),
            bgcolor=COLOR_PRIMARY if not _init_is_onnx else COLOR_SURFACE,
            border=ft.border.all(1, COLOR_PRIMARY if not _init_is_onnx else COLOR_DIVIDER),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_stt_backend_gguf_click,
        )
        self._stt_backend_row = ft.Row(
            [self._stt_backend_label, self._stt_backend_onnx_btn, self._stt_backend_gguf_btn],
            spacing=8,
            visible=False,
        )
        self._stt_title = ft.Text(
            t("settings.section.stt"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        self._stt_provider_label = ft.Text(
            t("settings.self_stt_provider"), size=16, color=COLOR_ON_BACKGROUND
        )
        stt_card = self._wrap_unit_card(
            title=self._stt_title,
            value=self._stt_text,
        )

        self._llm_text = self._build_clickable_text(
            t("provider.gemini3_flash"),
            self._on_llm_click,
        )
        self._trans_title = ft.Text(
            t("settings.section.translation"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._translation_provider_label = ft.Text(
            t("settings.shared_translation_provider"), size=16, color=COLOR_ON_BACKGROUND
        )
        trans_card = self._wrap_unit_card(
            title=self._trans_title,
            value=self._llm_text,
        )

        # === Row 2: API Keys (2x1) ===
        # Qwen region selection button (in header)
        self._qwen_region_btn = _make_text_button(
            f"{t('settings.qwen_region')} {t('region.beijing')}",
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                text_style=ft.TextStyle(
                    size=20,
                    font_family=font_for_language(get_locale()),
                ),
                overlay_color=ft.Colors.TRANSPARENT,
                animation_duration=0,
            ),
            on_click=self._on_qwen_region_click,
            visible=False,  # Hidden by default, updated by visibility logic
        )

        # API Key fields
        self._openai_compatible_key = ApiKeyField(
            "settings.openai_compatible_api_key",
            "openai_compatible_api_key",
            "openai_compatible",
            on_verify=self._verify_key,
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
        )

        self._api_keys_column = ft.Column(
            [
                self._openai_compatible_key,
            ],
            spacing=12,
        )

        self._api_title = ft.Text(
            t("settings.section.api_keys"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        self._api_credentials_helper_text = ft.Text(
            t("settings.api_credentials_helper"),
            size=16,
            color=COLOR_NEUTRAL,
        )
        # Header row with title and region button
        api_header = ft.Row(
            controls=[
                self._api_title,
                ft.Container(expand=True),
                self._qwen_region_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        api_card = self._wrap_card(
            ft.Column(
                [
                    api_header,
                    ft.Container(height=16),
                    self._api_keys_column,
                ],
                spacing=0,
            ),
            height=None,
        )
        api_keys_row = api_card

        # === General Tab Row 1: UI / Include Original / Integrated Context ===
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

        self._audio_settings = AudioSettings(on_change=self._on_audio_change)
        self._chatbox_source_text = self._build_clickable_text(
            t("settings.chatbox_source.on"),
            self._on_chatbox_source_click,
        )
        self._chatbox_source_title = ft.Text(
            t("settings.chatbox_include_source"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        chatbox_source_card = self._wrap_unit_card(
            title=self._chatbox_source_title,
            value=self._chatbox_source_text,
        )

        self._clipboard_auto_translate_text = self._build_clickable_text(
            t("settings.clipboard_auto_translate.off"),
            self._on_clipboard_auto_translate_click,
        )
        self._clipboard_auto_translate_title = ft.Text(
            t("settings.clipboard_auto_translate"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        clipboard_auto_translate_card = self._wrap_unit_card(
            title=self._clipboard_auto_translate_title,
            value=self._clipboard_auto_translate_text,
        )

        self._vrc_mic_text = self._build_clickable_text(
            t("settings.vrc_mic.on"),
            self._on_vrc_mic_click,
        )
        self._vrc_mic_title = ft.Text(
            t("settings.vrc_mic_intercept"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        vrc_mic_card = self._wrap_unit_card(
            title=self._vrc_mic_title,
            value=self._vrc_mic_text,
        )

        self._microphone_test_text = self._build_clickable_text(
            t("settings.microphone_test.action"),
            self._on_microphone_test_click,
        )
        self._microphone_test_title = ft.Text(
            t("settings.microphone_test"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        microphone_test_card = self._wrap_unit_card(
            title=self._microphone_test_title,
            value=self._microphone_test_text,
        )

        integrated_context_card = self._build_integrated_context_unit_card()

        general_primary_row = ft.Container(
            content=ft.Row(
                [
                    ui_card,
                    chatbox_source_card,
                    integrated_context_card,
                ],
                spacing=16,
                expand=True,
            ),
        )

        # === General Tab Row 2: Host API / Microphone Audio / Loopback Audio ===
        self._mic_audio_text = self._build_clickable_text(
            t("settings.default_option"),
            self._on_mic_audio_click,
        )
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
        self._mic_audio_title = ft.Text(
            t("settings.section.microphone_audio"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        mic_audio_card = self._wrap_unit_card(
            title=self._mic_audio_title,
            value=self._mic_audio_text,
        )

        self._loopback_audio_text = self._build_clickable_text(
            t("settings.default_option"),
            self._on_loopback_audio_click,
        )
        self._loopback_audio_title = ft.Text(
            t("settings.section.loopback_audio"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        loopback_audio_card = self._wrap_unit_card(
            title=self._loopback_audio_title,
            value=self._loopback_audio_text,
        )
        general_audio_row = ft.Container(
            content=ft.Row(
                [host_api_card, mic_audio_card, loopback_audio_card],
                spacing=16,
                expand=True,
            ),
        )

        # === API Tab Row 2: Response Mode / Routing / Fallback ===
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

        # === General Tab Row 3: VRChat Mute Sync / Self VAD / Peer VAD ===
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
        general_vad_row = ft.Container(
            content=ft.Row(
                [microphone_test_card, self._self_vad_card, self._peer_vad_card],
                spacing=16,
                expand=True,
            ),
        )
        general_clipboard_row = ft.Container(
            content=ft.Row(
                [
                    clipboard_auto_translate_card,
                    vrc_mic_card,
                ],
                spacing=16,
                expand=True,
            ),
        )

        # === Peer STT card ===
        self._peer_provider_title = ft.Text(
            t("settings.section.peer_stt"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._dashboard_language_redirect_text = ft.Text(
            t("settings.dashboard_language_redirect"),
            size=16,
            color=COLOR_NEUTRAL,
        )
        self._peer_stt_text = self._build_clickable_text(
            provider_label(STTProviderName.LOCAL_QWEN.value),
            self._on_peer_stt_click,
        )
        self._peer_stt_compute_label = ft.Text(
            t("settings.compute.label"), size=14, color=COLOR_ON_BACKGROUND
        )
        self._peer_stt_compute_gpu_btn = ft.Container(
            content=ft.Text("GPU", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
            bgcolor=COLOR_PRIMARY,
            border=ft.border.all(1, COLOR_PRIMARY),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_peer_stt_compute_gpu_click,
        )
        self._peer_stt_compute_cpu_btn = ft.Container(
            content=ft.Text("CPU", size=14, color=COLOR_ON_BACKGROUND),
            bgcolor=COLOR_SURFACE,
            border=ft.border.all(1, COLOR_DIVIDER),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_peer_stt_compute_cpu_click,
        )
        self._peer_stt_compute_row = ft.Row(
            [self._peer_stt_compute_label, self._peer_stt_compute_gpu_btn, self._peer_stt_compute_cpu_btn],
            spacing=8,
            visible=False,
        )
        self._peer_quant_label = ft.Text(
            t("settings.quant.label", default="Quant:"), size=14, color=COLOR_ON_BACKGROUND
        )
        self._peer_quant_q8_btn = self._make_quant_button("Q8_0", lambda e: self._apply_peer_quant("q8_0"))
        self._peer_quant_q6k_btn = self._make_quant_button("Q6_K", lambda e: self._apply_peer_quant("q6_k"))
        self._peer_quant_f16_btn = self._make_quant_button("F16", lambda e: self._apply_peer_quant("f16"))
        self._peer_quant_int8_btn = self._make_quant_button("int8", lambda e: self._apply_peer_quant("int8"))
        # Set initial PEER quant button state based on loaded settings
        _init_peer = self._initial_settings.provider.peer_stt if self._initial_settings else STTProviderName.LOCAL_QWEN
        _init_peer_quant = self._initial_settings.provider.peer_stt_quant if self._initial_settings else "auto"
        _init_peer_available = self._INITIAL_QUANTS_FOR_PROVIDER.get(_init_peer, ["int8"])
        _init_peer_active = _init_peer_quant if _init_peer_quant in _init_peer_available else (_init_peer_available[0] if _init_peer_available else "auto")
        for _q, _btn in [("q8_0", self._peer_quant_q8_btn), ("q6_k", self._peer_quant_q6k_btn), ("f16", self._peer_quant_f16_btn), ("int8", self._peer_quant_int8_btn)]:
            _btn.visible = _q in _init_peer_available
            if _q == _init_peer_active:
                _btn.bgcolor = COLOR_PRIMARY
                _btn.border = ft.border.all(1, COLOR_PRIMARY)
                _btn.content.color = ft.Colors.WHITE
                _btn.content.weight = ft.FontWeight.BOLD
        self._peer_quant_row = ft.Row(
            [self._peer_quant_label, self._peer_quant_q8_btn, self._peer_quant_q6k_btn, self._peer_quant_f16_btn, self._peer_quant_int8_btn],
            spacing=8,
        )
        self._peer_stt_backend_label = ft.Text(
            t("settings.backend.label", default="Backend:"), size=14, color=COLOR_ON_BACKGROUND
        )
        # Set initial PEER backend button state based on loaded settings
        _init_peer_backend = self._initial_settings.provider.peer_stt_backend if self._initial_settings else "onnx"
        _init_peer_is_onnx = _init_peer_backend == "onnx"
        self._peer_stt_backend_onnx_btn = ft.Container(
            content=ft.Text("DirectML", size=14, weight=ft.FontWeight.BOLD if _init_peer_is_onnx else ft.FontWeight.NORMAL, color=ft.Colors.WHITE if _init_peer_is_onnx else COLOR_ON_BACKGROUND),
            bgcolor=COLOR_PRIMARY if _init_peer_is_onnx else COLOR_SURFACE,
            border=ft.border.all(1, COLOR_PRIMARY if _init_peer_is_onnx else COLOR_DIVIDER),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_peer_stt_backend_onnx_click,
        )
        self._peer_stt_backend_gguf_btn = ft.Container(
            content=ft.Text("Vulkan", size=14, weight=ft.FontWeight.BOLD if not _init_peer_is_onnx else ft.FontWeight.NORMAL, color=ft.Colors.WHITE if not _init_peer_is_onnx else COLOR_ON_BACKGROUND),
            bgcolor=COLOR_PRIMARY if not _init_peer_is_onnx else COLOR_SURFACE,
            border=ft.border.all(1, COLOR_PRIMARY if not _init_peer_is_onnx else COLOR_DIVIDER),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_peer_stt_backend_gguf_click,
        )
        self._peer_stt_backend_row = ft.Row(
            [self._peer_stt_backend_label, self._peer_stt_backend_onnx_btn, self._peer_stt_backend_gguf_btn],
            spacing=8,
            visible=False,
        )
        self._peer_stt_label = ft.Text(
            t("settings.peer_stt_provider"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        peer_stt_card = self._wrap_unit_card(
            title=self._peer_provider_title,
            value=self._peer_stt_text,
        )
        self._trans_compute_spacer = ft.Container(height=32, visible=False)
        row1 = ft.Container(
            content=ft.Column([
                ft.Row(
                    [
                        ft.Column([self._stt_backend_row, self._stt_compute_row, self._stt_quant_row, stt_card], spacing=4, expand=True),
                        ft.Column([self._peer_stt_backend_row, self._peer_stt_compute_row, self._peer_quant_row, peer_stt_card], spacing=4, expand=True),
                        ft.Column([self._trans_compute_spacer, trans_card], spacing=4, expand=True),
                    ],
                    spacing=16,
                    expand=True,
                ),
            ], spacing=6),
        )

        self._overlay_translation_title = ft.Text(
            t("settings.overlay.show_translation"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_translation_button = self._build_clickable_text(
            t("settings.option.on"),
            self._on_overlay_translation_click,
        )
        self._overlay_translation_card = self._wrap_unit_card(
            title=self._overlay_translation_title,
            value=self._overlay_translation_button,
        )

        self._overlay_peer_original_title = ft.Text(
            t("settings.overlay.show_peer_original"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_peer_original_button = self._build_clickable_text(
            t("settings.option.on"),
            self._on_overlay_peer_original_click,
        )
        self._overlay_peer_original_card = self._wrap_unit_card(
            title=self._overlay_peer_original_title,
            value=self._overlay_peer_original_button,
        )

        self._overlay_target_title = ft.Text(
            t("settings.overlay.caption_location"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_target_button = self._build_clickable_text(
            self._overlay_target_label_for(OVERLAY_TARGET_STEAMVR),
            self._on_overlay_target_click,
            size=28,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._overlay_target_card = self._wrap_unit_card(
            title=self._overlay_target_title,
            value=self._overlay_target_button,
        )

        self._overlay_anchor_title = ft.Text(
            t("settings.overlay.calibration.anchor"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_anchor_button = self._build_clickable_text(
            self._overlay_anchor_label_for(self._overlay_calibration.anchor),
            self._on_overlay_anchor_click,
        )
        self._overlay_anchor_card = self._wrap_unit_card(
            title=self._overlay_anchor_title,
            value=self._overlay_anchor_button,
        )

        self._overlay_distance_title = ft.Text(
            t("settings.overlay.calibration.distance"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_distance_value_text = ft.Text(
            self._format_overlay_calibration_number(self._overlay_calibration.distance),
            size=28,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        (
            self._overlay_distance_card_content,
            self._overlay_distance_decrease_button,
            self._overlay_distance_increase_button,
            self._overlay_distance_decrease_glyph,
            self._overlay_distance_increase_glyph,
        ) = self._build_overlay_step_split_layout(
            title=self._overlay_distance_title,
            value_text=self._overlay_distance_value_text,
            decrease_text="－",
            increase_text="＋",
            on_decrease=lambda _e: self._on_overlay_distance_step(-_OVERLAY_OFFSET_STEP),
            on_increase=lambda _e: self._on_overlay_distance_step(_OVERLAY_OFFSET_STEP),
        )
        self._overlay_distance_card = self._wrap_card(
            self._overlay_distance_card_content,
            expand=True,
            height=SettingsUnitCard.DEFAULT_HEIGHT,
        )

        self._overlay_offset_x_title = ft.Text(
            t("settings.overlay.calibration.offset_x"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_offset_x_value_text = ft.Text(
            self._format_overlay_calibration_number(self._overlay_calibration.offset_x),
            size=28,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        (
            self._overlay_offset_x_card_content,
            self._overlay_offset_x_decrease_button,
            self._overlay_offset_x_increase_button,
            self._overlay_offset_x_decrease_glyph,
            self._overlay_offset_x_increase_glyph,
        ) = self._build_overlay_step_split_layout(
            title=self._overlay_offset_x_title,
            value_text=self._overlay_offset_x_value_text,
            decrease_text="◀",
            increase_text="▶",
            on_decrease=lambda _e: self._on_overlay_offset_x_step(-_OVERLAY_OFFSET_STEP),
            on_increase=lambda _e: self._on_overlay_offset_x_step(_OVERLAY_OFFSET_STEP),
        )
        self._overlay_offset_x_card = self._wrap_card(
            self._overlay_offset_x_card_content,
            expand=True,
            height=SettingsUnitCard.DEFAULT_HEIGHT,
        )

        self._overlay_offset_y_title = ft.Text(
            t("settings.overlay.calibration.offset_y"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_offset_y_value_text = ft.Text(
            self._format_overlay_calibration_number(self._overlay_calibration.offset_y),
            size=28,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        (
            self._overlay_offset_y_card_content,
            self._overlay_offset_y_decrease_button,
            self._overlay_offset_y_increase_button,
            self._overlay_offset_y_decrease_glyph,
            self._overlay_offset_y_increase_glyph,
        ) = self._build_overlay_step_split_layout(
            title=self._overlay_offset_y_title,
            value_text=self._overlay_offset_y_value_text,
            decrease_text="▲",
            increase_text="▼",
            on_decrease=lambda _e: self._on_overlay_offset_y_step(-_OVERLAY_OFFSET_STEP),
            on_increase=lambda _e: self._on_overlay_offset_y_step(_OVERLAY_OFFSET_STEP),
        )
        self._overlay_offset_y_card = self._wrap_card(
            self._overlay_offset_y_card_content,
            expand=True,
            height=SettingsUnitCard.DEFAULT_HEIGHT,
        )

        self._overlay_text_scale_title = ft.Text(
            t("settings.overlay.calibration.text_scale"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_text_scale_text = self._build_clickable_text(
            self._overlay_text_scale_label_for(self._overlay_calibration.text_scale),
            self._on_overlay_text_scale_click,
        )
        self._overlay_text_scale_card = self._wrap_unit_card(
            title=self._overlay_text_scale_title,
            value=self._overlay_text_scale_text,
        )

        self._overlay_vr_reset_title = ft.Text(
            t("settings.overlay.position_reset.vr.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_vr_reset_button = self._build_clickable_text(
            t("settings.overlay.position_reset.action.vr"),
            self._on_overlay_position_reset,
            height=72,
            expand=False,
        )
        self._overlay_vr_reset_card = self._wrap_unit_card(
            title=self._overlay_vr_reset_title,
            value=self._overlay_vr_reset_button,
        )

        self._overlay_desktop_reset_title = ft.Text(
            t("settings.overlay.position_reset.desktop.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_desktop_reset_button = self._build_clickable_text(
            t("settings.overlay.position_reset.action.desktop"),
            self._on_desktop_overlay_position_reset,
            height=72,
            expand=False,
        )
        self._overlay_desktop_reset_card = self._wrap_unit_card(
            title=self._overlay_desktop_reset_title,
            value=self._overlay_desktop_reset_button,
        )
        self._overlay_reset_title = self._overlay_vr_reset_title

        self._desktop_overlay_size_title = ft.Text(
            t("settings.overlay.desktop.size.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._desktop_overlay_size_button = self._build_clickable_text(
            self._desktop_overlay_size_label_for("medium"),
            self._on_desktop_overlay_size_click,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._desktop_overlay_size_card = self._wrap_unit_card(
            title=self._desktop_overlay_size_title,
            value=self._desktop_overlay_size_button,
        )

        self._desktop_overlay_background_alpha_title = ft.Text(
            t("settings.overlay.desktop.background_alpha.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._desktop_overlay_background_alpha_value_text = ft.Text(
            "40%",
            size=28,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        (
            self._desktop_overlay_background_alpha_card_content,
            self._desktop_overlay_background_alpha_decrease_button,
            self._desktop_overlay_background_alpha_increase_button,
            self._desktop_overlay_background_alpha_decrease_glyph,
            self._desktop_overlay_background_alpha_increase_glyph,
        ) = self._build_overlay_step_split_layout(
            title=self._desktop_overlay_background_alpha_title,
            value_text=self._desktop_overlay_background_alpha_value_text,
            decrease_text="－",
            increase_text="＋",
            on_decrease=lambda _e: self._on_desktop_overlay_background_alpha_step(
                -_DESKTOP_OVERLAY_BACKGROUND_ALPHA_STEP
            ),
            on_increase=lambda _e: self._on_desktop_overlay_background_alpha_step(
                _DESKTOP_OVERLAY_BACKGROUND_ALPHA_STEP
            ),
        )
        self._desktop_overlay_background_alpha_card = self._wrap_card(
            self._desktop_overlay_background_alpha_card_content,
            expand=True,
            height=SettingsUnitCard.DEFAULT_HEIGHT,
        )

        self._desktop_overlay_lock_title = ft.Text(
            t("settings.overlay.desktop.lock.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._desktop_overlay_lock_button = self._build_clickable_text(
            self._desktop_overlay_lock_label_for(False),
            self._on_desktop_overlay_lock_click,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._desktop_overlay_lock_card = self._wrap_unit_card(
            title=self._desktop_overlay_lock_title,
            value=self._desktop_overlay_lock_button,
        )

        self._desktop_overlay_status_title = ft.Text(
            t("settings.overlay.status.off"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._desktop_overlay_reason_text = ft.Text(
            "",
            size=15,
            color=COLOR_NEUTRAL,
            text_align=ft.TextAlign.CENTER,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            visible=False,
        )
        self._desktop_overlay_helper_text = ft.Text(
            "",
            size=14,
            color=COLOR_NEUTRAL,
            text_align=ft.TextAlign.CENTER,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            visible=False,
        )
        self._desktop_overlay_primary_action = self._build_clickable_text(
            "",
            self._on_desktop_overlay_primary_action,
            size=20,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._desktop_overlay_primary_action.visible = False
        self._desktop_overlay_view_logs_action = self._build_clickable_text(
            t("settings.overlay.desktop.recovery.action.view_details"),
            self._on_desktop_overlay_view_logs,
            size=16,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._desktop_overlay_view_logs_action.visible = False
        self._desktop_overlay_status_body = ft.Column(
            [
                self._desktop_overlay_reason_text,
                self._desktop_overlay_primary_action,
                self._desktop_overlay_view_logs_action,
                self._desktop_overlay_helper_text,
            ],
            spacing=6,
            expand=True,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._desktop_overlay_status_card = self._wrap_unit_card(
            title=self._desktop_overlay_status_title,
            value=self._desktop_overlay_status_body,
        )
        self._overlay_empty_card = self._wrap_empty_unit_card()
        self._overlay_desktop_reset_spacer_a = self._wrap_empty_unit_card()
        self._overlay_desktop_reset_spacer_b = self._wrap_empty_unit_card()

        overlay_row1 = ft.Container(
            content=ft.Row(
                [
                    self._overlay_target_card,
                    self._overlay_translation_card,
                    self._overlay_peer_original_card,
                ],
                spacing=16,
                expand=True,
            ),
        )
        overlay_row2 = ft.Container(
            content=ft.Row(
                [
                    self._overlay_anchor_card,
                    self._overlay_distance_card,
                    self._overlay_offset_x_card,
                ],
                spacing=16,
                expand=True,
            ),
        )
        overlay_row3 = ft.Container(
            content=ft.Row(
                [
                    self._overlay_offset_y_card,
                    self._overlay_text_scale_card,
                    self._overlay_vr_reset_card,
                ],
                spacing=16,
                expand=True,
            ),
        )
        overlay_row4 = ft.Container(
            content=ft.Row(
                [
                    self._desktop_overlay_size_card,
                    self._desktop_overlay_lock_card,
                    self._desktop_overlay_background_alpha_card,
                ],
                spacing=16,
                expand=True,
            ),
        )
        overlay_row5 = ft.Container(
            content=ft.Row(
                [
                    self._overlay_desktop_reset_card,
                    self._overlay_desktop_reset_spacer_a,
                    self._overlay_desktop_reset_spacer_b,
                ],
                spacing=16,
                expand=True,
            ),
        )
        overlay_row6 = ft.Container(
            content=ft.Row(
                [
                    self._desktop_overlay_status_card,
                    self._wrap_empty_unit_card(),
                    self._overlay_empty_card,
                ],
                spacing=16,
                expand=True,
            ),
            visible=False,
        )
        self._overlay_vr_rows = (overlay_row2, overlay_row3)
        self._overlay_desktop_rows = (overlay_row4, overlay_row5)
        self._desktop_overlay_controls_row = overlay_row4
        self._desktop_overlay_recovery_row = overlay_row6
        self._sync_overlay_target_specific_visibility()

        # === Row 7: Response Mode / Translation Connection / Fallback ===
        self._translation_connection_title = ft.Text(
            t("settings.translation_connection"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._translation_connection_text = self._build_clickable_text(
            t("settings.translation_connection.openrouter"),
            self._on_translation_connection_click,
        )
        self._translation_connection_card = self._wrap_unit_card(
            title=self._translation_connection_title,
            value=self._translation_connection_text,
        )
        self._openrouter_fallback_title = ft.Text(
            t("settings.fallback"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._openrouter_fallback_text = self._build_clickable_text(
            t("provider.deepseek_v4_flash_fallback"),
            self._on_openrouter_fallback_click,
        )
        self._openrouter_fallback_helper_text = ft.Text(
            t("settings.fallback.inactive_helper"),
            size=16,
            color=COLOR_NEUTRAL,
        )
        self._openrouter_fallback_card = self._wrap_unit_card(
            title=self._openrouter_fallback_title,
            value=self._openrouter_fallback_text,
        )
        self._translation_connection_row = ft.Container(
            content=ft.Row(
                [
                    self._low_latency_card,
                    self._translation_connection_card,
                    self._openrouter_fallback_card,
                ],
                spacing=16,
                expand=True,
            ),
            visible=True,
        )
        self._openrouter_routing_row = self._translation_connection_row

        self._local_llm_connection_title = ft.Text(
            t("settings.local_llm.connection"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._local_llm_base_url = ft.TextField(
            label=t("settings.local_llm.base_url"),
            value="http://127.0.0.1:11434/v1",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_local_llm_field_change,
            on_blur=self._on_local_llm_base_url_change_end,
            on_submit=self._on_local_llm_base_url_change_end,
        )
        self._local_llm_model = ft.TextField(
            label=t("settings.local_llm.model"),
            value="llama3.1:8b",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_local_llm_field_change,
            on_blur=self._on_local_llm_model_change_end,
            on_submit=self._on_local_llm_model_change_end,
        )
        self._local_llm_api_key = ApiKeyField(
            "settings.local_llm.api_key",
            "local_llm_api_key",
            "local_llm",
            on_verify=None,
            on_save=self._on_local_llm_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
            show_status=False,
        )
        local_llm_api_key_description = t("settings.local_llm.api_key.description")
        self._local_llm_api_key_helper = ft.Text(
            local_llm_api_key_description,
            size=15,
            color=COLOR_NEUTRAL,
            visible=bool(local_llm_api_key_description.strip()),
        )
        self._local_llm_extra_body = ft.TextField(
            label=t("settings.local_llm.extra_body"),
            value=json.dumps({"reasoning_effort": "none"}, ensure_ascii=False, indent=2),
            multiline=True,
            min_lines=3,
            max_lines=6,
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_local_llm_field_change,
            on_blur=self._on_local_llm_extra_body_change_end,
            on_submit=self._on_local_llm_extra_body_change_end,
        )
        self._local_llm_extra_body_helper = ft.Text(
            t("settings.local_llm.extra_body.description"),
            size=15,
            color=COLOR_NEUTRAL,
        )
        self._local_llm_extra_body_error = ft.Text(
            "",
            size=13,
            color=ft.Colors.RED_600,
            visible=False,
        )
        self._local_llm_extra_body_error_key = ""
        self._local_llm_extra_body_error_kwargs: dict[str, object] = {}
        self._local_llm_connection_card = self._wrap_card(
            ft.Column(
                [
                    self._local_llm_connection_title,
                    ft.Container(height=4),
                    self._local_llm_extra_body_helper,
                    self._local_llm_base_url,
                    self._local_llm_model,
                    self._local_llm_api_key,
                    self._local_llm_api_key_helper,
                    self._local_llm_extra_body,
                    self._local_llm_extra_body_error,
                ],
                spacing=8,
            ),
            height=None,
        )
        self._local_llm_connection_card.visible = False

        # OpenAI Compatible provider card
        self._openai_compatible_title = ft.Text(
            t("settings.openai_compatible.connection", default="OpenAI Compatible Settings"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._openai_compatible_base_url = ft.TextField(
            label=t("settings.openai_compatible.base_url", default="Base URL"),
            value="https://api.openai.com/v1",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_openai_compatible_field_change,
            on_blur=self._on_openai_compatible_base_url_change_end,
            on_submit=self._on_openai_compatible_base_url_change_end,
        )
        self._openai_compatible_model = ft.TextField(
            label=t("settings.openai_compatible.model", default="Model"),
            value="gpt-4o-mini",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_openai_compatible_field_change,
            on_blur=self._on_openai_compatible_model_change_end,
            on_submit=self._on_openai_compatible_model_change_end,
        )
        self._openai_compatible_card = self._wrap_card(
            ft.Column(
                [
                    self._openai_compatible_title,
                    ft.Container(height=4),
                    self._openai_compatible_base_url,
                    self._openai_compatible_model,
                ],
                spacing=8,
            ),
            height=None,
        )
        self._openai_compatible_card.visible = False

        # === Row 8: Persona (2x2) - Licenses style ===
        self._prompt_editor = PromptEditor(
            on_change=self._on_prompt_change,
            on_commit=self._on_prompt_commit,
        )
        self._prompt_mode = "single"  # "single" or "dual"
        self._prompt_single_btn = self._make_quant_button(
            t("settings.prompt_mode.single", default="Single"),
            self._on_prompt_mode_single,
        )
        self._prompt_dual_btn = self._make_quant_button(
            t("settings.prompt_mode.dual", default="Dual"),
            self._on_prompt_mode_dual,
        )
        self._prompt_single_btn.bgcolor = COLOR_PRIMARY
        self._prompt_single_btn.border = ft.border.all(1, COLOR_PRIMARY)
        self._prompt_single_btn.content.color = ft.Colors.WHITE
        self._prompt_single_btn.content.weight = ft.FontWeight.BOLD
        self._prompt_mode_row = ft.Row(
            [self._prompt_single_btn, self._prompt_dual_btn],
            spacing=4,
        )
        self._persona_title = ft.Text(
            t("settings.section.persona"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        self._prompt_for_text = ft.Text(
            self._prompt_provider_copy(),
            size=16,
            color=COLOR_NEUTRAL,
        )

        # Reset button (matches Persona title color, hover -> primary)
        self._reset_prompt_btn = _make_text_button(
            t("settings.reset_prompt"),
            icon=ft.Icons.REFRESH_ROUNDED,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                icon_color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                text_style=ft.TextStyle(
                    size=20,
                    font_family=font_for_language(get_locale()),
                ),
                overlay_color=ft.Colors.TRANSPARENT,
                animation_duration=0,
            ),
            on_click=self._on_reset_prompt,
        )

        # Header row with title, prompt mode toggle, and reset button
        persona_header = ft.Row(
            controls=[self._persona_title, self._prompt_mode_row, ft.Container(expand=True), self._reset_prompt_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Simple container like Licenses (no border, no internal scroll)
        prompt_container = ft.Container(
            content=self._prompt_editor,
            width=float("inf"),
        )

        persona_card = SharedCardWrapper(
            ft.Column(
                [
                    persona_header,
                    ft.Container(height=16),
                    prompt_container,
                ],
                spacing=0,
            ),
            height=None,
            expand=False,
        )
        # === Row 9: Custom Vocabulary (2x1) ===
        self._custom_vocab_title = ft.Text(
            t("settings.section.custom_vocabulary"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._custom_vocab_description_text = ft.Text(
            t("settings.custom_vocabulary.description"),
            size=16,
            color=COLOR_NEUTRAL,
        )
        self._custom_vocab_tag_editor = CustomVocabularyTagEditor(
            on_add_terms=self._on_custom_vocabulary_add_terms,
            on_remove_term=self._on_custom_vocabulary_remove_term,
        )
        self._apply_custom_vocabulary_tag_editor_locale()
        row7 = SharedCardWrapper(
            ft.Column(
                [
                    self._custom_vocab_title,
                    ft.Container(height=6),
                    self._custom_vocab_description_text,
                    ft.Container(height=12),
                    self._custom_vocab_tag_editor,
                ],
                spacing=0,
            ),
            height=None,
            expand=False,
        )

        self._settings_subtab_shell = self._build_settings_subtab_shell(
            {
                "api": [
                    row1,
                    self._translation_connection_row,
                    self._local_llm_connection_card,
                    self._openai_compatible_card,
                    api_keys_row,
                ],
                "general": [
                    general_primary_row,
                    general_audio_row,
                    general_vad_row,
                    general_clipboard_row,
                ],
                "prompt": [row7, persona_card],
                "overlay": [
                    overlay_row1,
                    overlay_row2,
                    overlay_row3,
                    overlay_row4,
                    overlay_row5,
                    overlay_row6,
                ],
            }
        )
        self.controls = [self._settings_subtab_shell]

    def _populate_host_apis(self) -> None:
        """Legacy hook for tests; host APIs are handled by AudioSettings."""
        return None

    def _refresh_microphones(self) -> None:
        """Legacy hook for tests; microphone list is handled by AudioSettings."""
        return None

    def _build_locale_options(self) -> list[ft.dropdown.Option]:
        """Build locale dropdown options."""
        return [
            ft.dropdown.Option(key=code, text=native_locale_label(code)) for code in available_locales()
        ]

    def _get_llm_modal_value(self, settings: AppSettings) -> str:
        return settings.translation.model.value

    def _translation_model_display_label(self, model: TranslationModel) -> str:
        return t(_TRANSLATION_MODEL_LABEL_KEYS[model])

    def _translation_connection_display_label(self, connection: TranslationConnection) -> str:
        return t(_TRANSLATION_CONNECTION_LABEL_KEYS[connection])

    def _translation_connection_display_description(self, connection: TranslationConnection) -> str:
        return t(_TRANSLATION_CONNECTION_DESCRIPTION_KEYS[connection], default="")

    def _translation_connection_only_supported_description(self) -> str:
        return t(_TRANSLATION_CONNECTION_ONLY_SUPPORTED_KEY, default="")

    def _set_translation_connection_text(self, text: str) -> None:
        text_control = self._translation_connection_text.content
        text_control.value = text
        text_control.size = 28

    def _translation_fallback_display_label(
        self,
        alias: TranslationFallbackSelectionAlias,
    ) -> str:
        return t(_TRANSLATION_FALLBACK_LABEL_KEYS[alias])

    def _effective_translation_fallback_modal_value(
        self,
        settings: AppSettings | None,
    ) -> str:
        if settings is None:
            return TranslationFallbackSelectionAlias.NONE.value
        if settings.translation.fallback_selection_alias != TranslationFallbackSelectionAlias.NONE:
            return settings.translation.fallback_selection_alias.value
        return TranslationFallbackSelectionAlias.NONE.value

    def _get_llm_display_label(self, settings: AppSettings) -> str:
        return self._translation_model_display_label(settings.translation.model)

    def _get_translation_connection_display_label(self, settings: AppSettings | None) -> str:
        if settings is None:
            return self._translation_connection_display_label(TranslationConnection.OPENAI_COMPATIBLE)
        return self._translation_connection_display_label(settings.translation.connection)

    def _active_prompt_key_for_settings(self, settings: AppSettings | None) -> str:
        if settings is None:
            return "openai_compatible"
        if settings.provider.llm == LLMProviderName.LOCAL_LLM:
            return "local_llm"
        if settings.provider.llm == LLMProviderName.OPENAI_COMPATIBLE:
            return "openai_compatible"
        return "openai_compatible"

    def _active_prompt_key(self) -> str:
        return self._active_prompt_key_for_settings(self._build_settings_with_provider_draft())

    def _ensure_provider_prompt_value(self, settings: AppSettings, provider_name: str) -> str:
        prompt = settings.system_prompt
        if prompt.strip():
            settings.system_prompts = {}
            return prompt
        prompt = load_prompt_for_provider(provider_name)
        settings.system_prompt = prompt
        settings.system_prompts = {}
        return prompt

    def _current_source_language(self) -> str:
        if not self._settings:
            return "en"
        return self._settings.languages.source_language

    def _prompt_provider_copy(self) -> str:
        return t(
            "settings.prompt_for",
            provider=provider_label(self._active_prompt_key()),
        )

    def _custom_vocabulary_description_copy(self) -> str:
        return t("settings.custom_vocabulary.description")

    def _apply_custom_vocabulary_tag_editor_locale(self) -> None:
        self._custom_vocab_tag_editor.set_placeholder(
            t("settings.custom_vocabulary.add_placeholder")
        )
        self._custom_vocab_tag_editor.set_add_label(t("settings.custom_vocabulary.add_action"))
        self._custom_vocab_tag_editor.set_empty_text(t("settings.custom_vocabulary.empty"))
        self._custom_vocab_tag_editor.set_remove_label_template(
            t("settings.custom_vocabulary.remove_hint")
        )

    def _sync_prompt_tab_copy(self) -> None:
        self._prompt_for_text.value = self._prompt_provider_copy()
        self._custom_vocab_description_text.value = self._custom_vocabulary_description_copy()
        self._apply_custom_vocabulary_tag_editor_locale()
        if self.page:
            for control in (self._prompt_for_text, self._custom_vocab_description_text):
                with contextlib.suppress(Exception):
                    control.update()

    def _sync_custom_vocabulary_editor_from_settings(self) -> None:
        if not self._settings:
            self._custom_vocab_tag_editor.set_terms([])
            self._custom_vocab_tag_editor.clear_input()
            return

        source_language = self._current_source_language()
        self._custom_vocab_tag_editor.set_terms(
            list(self._settings.stt.custom_terms.get(source_language, []))
        )
        self._custom_vocab_tag_editor.clear_input()

    def _normalize_custom_vocabulary_submitted_terms(self, raw_terms: list[str]) -> list[str]:
        terms: list[str] = []
        for raw_term in raw_terms:
            for part in _CUSTOM_VOCAB_DELIMITER_RE.split(str(raw_term)):
                normalized = part.strip()
                if normalized:
                    terms.append(normalized)
        return terms

    def _copy_provider_draft_fields(self, source: AppSettings, target: AppSettings) -> None:
        target.provider.stt = source.provider.stt
        target.provider.peer_stt = source.provider.peer_stt
        target.provider.llm = source.provider.llm
        target.provider.stt_compute = source.provider.stt_compute
        target.provider.peer_stt_compute = source.provider.peer_stt_compute
        target.provider.stt_backend = source.provider.stt_backend
        target.provider.peer_stt_backend = source.provider.peer_stt_backend
        target.provider.stt_quant = source.provider.stt_quant
        target.provider.peer_stt_quant = source.provider.peer_stt_quant
        target.translation = copy.deepcopy(source.translation)
        target.qwen.region = source.qwen.region
        target.local_llm = copy.deepcopy(source.local_llm)
        target.system_prompt = source.system_prompt
        target.system_prompts = {}

    def _build_settings_with_provider_draft(self) -> AppSettings | None:
        if self._settings is None:
            return None
        if self._provider_settings_draft is None:
            return self._settings
        merged = copy.deepcopy(self._settings)
        self._copy_provider_draft_fields(self._provider_settings_draft, merged)
        return merged

    def _ensure_provider_settings_draft(self) -> AppSettings:
        assert self._settings is not None
        if self._provider_settings_draft is None:
            self._provider_settings_draft = copy.deepcopy(self._settings)
        return self._provider_settings_draft

    def _effective_peer_stt_provider(self, settings: AppSettings | None) -> STTProviderName:
        if settings is None:
            return STTProviderName.LOCAL_QWEN
        return settings.provider.peer_stt

    def _peer_stt_option_item(self, provider: STTProviderName) -> OptionItem:
        return OptionItem(
            value=provider.value,
            label=provider_label(provider.value),
            description=t(f"provider.{provider.value}.description", default=""),
        )

    def _local_llm_extra_body_error_message(
        self,
        message_key: str,
        **kwargs: object,
    ) -> str:
        if "key" not in kwargs:
            return t(message_key, **kwargs)
        template = t(message_key)
        with contextlib.suppress(Exception):
            return template.format(**kwargs)
        return template

    def _show_local_llm_extra_body_error(self, message_key: str, **kwargs: object) -> None:
        message = self._local_llm_extra_body_error_message(message_key, **kwargs)
        self._local_llm_extra_body_error_key = message_key
        self._local_llm_extra_body_error_kwargs = dict(kwargs)
        self._local_llm_extra_body_error.value = message
        self._local_llm_extra_body_error.visible = True
        self._local_llm_extra_body.error_text = message
        _update_control_if_mounted(self._local_llm_extra_body)
        _update_control_if_mounted(self._local_llm_extra_body_error)

    def _on_local_llm_field_change(self, e) -> None:
        _ = e
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if current.provider.llm != LLMProviderName.LOCAL_LLM:
            return
        self._ensure_provider_settings_draft()
        self.has_provider_changes = True

    def _clear_local_llm_extra_body_error(self) -> None:
        self._local_llm_extra_body_error_key = ""
        self._local_llm_extra_body_error_kwargs = {}
        self._local_llm_extra_body_error.value = ""
        self._local_llm_extra_body_error.visible = False
        self._local_llm_extra_body.error_text = None
        _update_control_if_mounted(self._local_llm_extra_body)
        _update_control_if_mounted(self._local_llm_extra_body_error)

    def _on_local_llm_base_url_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw_value = self._local_llm_base_url.value or ""
        try:
            normalized = _normalize_local_llm_base_url(raw_value)
        except ValueError:
            self._local_llm_base_url.error_text = t("settings.local_llm.base_url.invalid")
            _update_control_if_mounted(self._local_llm_base_url)
            return

        self._local_llm_base_url.error_text = None
        self._local_llm_base_url.value = normalized
        current = self._provider_settings_draft or self._settings
        if current.local_llm.base_url != normalized:
            draft = self._ensure_provider_settings_draft()
            draft.local_llm.base_url = normalized
            self.has_provider_changes = True
        _update_control_if_mounted(self._local_llm_base_url)

    def _on_local_llm_model_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        model = (self._local_llm_model.value or "").strip()
        if not model:
            self._local_llm_model.error_text = t("settings.local_llm.model.required")
            _update_control_if_mounted(self._local_llm_model)
            return

        self._local_llm_model.error_text = None
        self._local_llm_model.value = model
        current = self._provider_settings_draft or self._settings
        if current.local_llm.model != model:
            draft = self._ensure_provider_settings_draft()
            draft.local_llm.model = model
            self.has_provider_changes = True
        _update_control_if_mounted(self._local_llm_model)

    def _on_local_llm_extra_body_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw = (self._local_llm_extra_body.value or "").strip()
        try:
            parsed = (
                {"reasoning_effort": "none"}
                if not raw
                else json.loads(raw, parse_constant=_reject_json_constant)
            )
        except json.JSONDecodeError:
            self._show_local_llm_extra_body_error("settings.local_llm.extra_body.invalid_json")
            return

        if not isinstance(parsed, dict):
            self._show_local_llm_extra_body_error("settings.local_llm.extra_body.must_be_object")
            return

        lowered = {str(key).lower() for key in parsed}
        reserved = LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS.intersection(lowered)
        if reserved:
            self._show_local_llm_extra_body_error(
                "settings.local_llm.extra_body.reserved_key",
                key=sorted(reserved)[0],
            )
            return

        sensitive = LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS.intersection(lowered)
        if sensitive:
            self._show_local_llm_extra_body_error(
                "settings.local_llm.extra_body.sensitive_key",
                key=sorted(sensitive)[0],
            )
            return

        try:
            json.dumps(parsed, allow_nan=False)
        except (TypeError, ValueError):
            self._show_local_llm_extra_body_error("settings.local_llm.extra_body.not_serializable")
            return

        normalized = copy.deepcopy(parsed)
        current = self._provider_settings_draft or self._settings
        if current.local_llm.extra_body != normalized:
            draft = self._ensure_provider_settings_draft()
            draft.local_llm.extra_body = normalized
            self.has_provider_changes = True
        self._local_llm_extra_body.value = json.dumps(
            normalized,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        self._clear_local_llm_extra_body_error()
        _update_control_if_mounted(self._local_llm_extra_body)

    def _commit_local_llm_fields_from_controls(self) -> None:
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if current.provider.llm != LLMProviderName.LOCAL_LLM:
            return
        self._on_local_llm_base_url_change_end(None)
        self._on_local_llm_model_change_end(None)
        self._on_local_llm_extra_body_change_end(None)

    def _on_openai_compatible_field_change(self, e) -> None:
        _ = e
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if current.provider.llm != LLMProviderName.OPENAI_COMPATIBLE:
            return
        self._ensure_provider_settings_draft()
        self.has_provider_changes = True

    def _on_openai_compatible_base_url_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw_value = (self._openai_compatible_base_url.value or "").strip()
        if not raw_value:
            self._openai_compatible_base_url.error_text = t(
                "settings.openai_compatible.base_url.required", default="Base URL is required"
            )
            _update_control_if_mounted(self._openai_compatible_base_url)
            return

        self._openai_compatible_base_url.error_text = None
        self._openai_compatible_base_url.value = raw_value
        current = self._provider_settings_draft or self._settings
        if current.provider.openai_compatible.base_url != raw_value:
            draft = self._ensure_provider_settings_draft()
            draft.provider.openai_compatible.base_url = raw_value
            self.has_provider_changes = True
        _update_control_if_mounted(self._openai_compatible_base_url)

    def _on_openai_compatible_model_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        model = (self._openai_compatible_model.value or "").strip()
        if not model:
            self._openai_compatible_model.error_text = t(
                "settings.openai_compatible.model.required", default="Model is required"
            )
            _update_control_if_mounted(self._openai_compatible_model)
            return

        self._openai_compatible_model.error_text = None
        self._openai_compatible_model.value = model
        current = self._provider_settings_draft or self._settings
        if current.provider.openai_compatible.model != model:
            draft = self._ensure_provider_settings_draft()
            draft.provider.openai_compatible.model = model
            self.has_provider_changes = True
        _update_control_if_mounted(self._openai_compatible_model)

    def _commit_openai_compatible_fields_from_controls(self) -> None:
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if current.provider.llm != LLMProviderName.OPENAI_COMPATIBLE:
            return
        self._on_openai_compatible_base_url_change_end(None)
        self._on_openai_compatible_model_change_end(None)

    def _settings_with_desktop_overlay_runtime_state(
        self,
        settings: AppSettings | None,
    ) -> AppSettings | None:
        if settings is None:
            return None
        pending_position_reset = getattr(self, "_desktop_overlay_pending_position_reset", False)
        desktop_settings = settings.overlay.desktop_flet
        size_preset = self._current_desktop_overlay_size_preset()
        needs_copy = desktop_settings.size_preset != size_preset or pending_position_reset
        if not needs_copy:
            return settings

        updated = copy.deepcopy(settings)
        updated_desktop = updated.overlay.desktop_flet
        updated_desktop.size_preset = size_preset
        if pending_position_reset:
            updated_desktop.position.x = None
            updated_desktop.position.y = None
            updated_desktop.locked = False
        updated_desktop.validate()
        return updated

    def _sanitize_provider_apply_settings(self, settings: AppSettings | None) -> AppSettings | None:
        if settings is not None:
            settings.system_prompts = {}
        return settings

    def _stage_prompt_draft(self, value: str) -> None:
        if not self._settings:
            return
        committed_prompt = self._committed_prompt_value()
        draft = self._ensure_provider_settings_draft()
        draft.system_prompt = value
        draft.system_prompts = {}
        self.has_pending_prompt_changes = value != committed_prompt
        if not self.has_pending_prompt_changes and not self.has_provider_changes:
            self._provider_settings_draft = None

    def _committed_prompt_value(self) -> str:
        if not self._settings:
            return ""
        return self._settings.system_prompt

    def build_provider_apply_settings(self) -> AppSettings | None:
        self._commit_local_llm_fields_from_controls()
        self._commit_openai_compatible_fields_from_controls()
        return self._sanitize_provider_apply_settings(
            self._settings_with_desktop_overlay_runtime_state(
                self._build_settings_with_provider_draft()
            )
        )

    def consume_provider_apply_settings(self) -> AppSettings | None:
        settings = self.build_provider_apply_settings()
        if settings is None:
            return None
        self._settings = settings
        self._provider_settings_draft = None
        self.has_provider_changes = False
        self.has_pending_prompt_changes = False
        return settings

    def consume_prompt_apply_settings(self) -> AppSettings | None:
        if not self.has_pending_prompt_changes:
            return None
        settings = self._sanitize_provider_apply_settings(
            self._settings_with_desktop_overlay_runtime_state(
                self._build_settings_with_provider_draft()
            )
        )
        if settings is None:
            return None
        self._settings = settings
        self.has_pending_prompt_changes = False
        if not self.has_provider_changes:
            self._provider_settings_draft = None
        return settings

    # --- Load Settings ---
    def load_from_settings(
        self,
        settings: AppSettings,
        *,
        config_path: Path,
        preserve_custom_vocab_draft: bool = False,
    ) -> None:
        """Load current settings into the UI."""
        self._settings = settings
        self._provider_settings_draft = None
        self._config_path = config_path
        self.has_provider_changes = False
        self.has_pending_prompt_changes = False
        self._desktop_overlay_pending_size_preset = None
        self._desktop_overlay_pending_position_reset = False
        self._desktop_overlay_pending_locked = None
        self._desktop_overlay_captions_locked = False
        if self._overlay_state == "off":
            self._overlay_runtime_target = self._current_overlay_target()
        self._sync_clickable_text_control_fonts(font_for_language(settings.ui.locale))

        # UI Language
        self._ui_text.content.value = locale_label(settings.ui.locale)

        # STT Provider
        self._set_unit_card_value_text(
            self._stt_text,
            provider_label(settings.provider.stt.value),
        )
        self._set_unit_card_value_text(
            self._peer_stt_text,
            provider_label(self._effective_peer_stt_provider(settings).value),
        )
        self._update_api_visibility(settings)

        # LLM Provider
        self._set_unit_card_value_text(
            self._llm_text,
            self._get_llm_display_label(settings),
        )
        self._set_translation_connection_text(
            self._get_translation_connection_display_label(settings),
        )
        self._local_llm_base_url.value = settings.local_llm.base_url
        self._local_llm_base_url.error_text = None
        self._local_llm_model.value = settings.local_llm.model
        self._local_llm_model.error_text = None
        self._local_llm_extra_body.value = json.dumps(
            settings.local_llm.extra_body,
            ensure_ascii=False,
            indent=2,
        )
        self._clear_local_llm_extra_body_error()

        # OpenAI Compatible
        self._openai_compatible_base_url.value = settings.provider.openai_compatible.base_url
        self._openai_compatible_base_url.error_text = None
        self._openai_compatible_model.value = settings.provider.openai_compatible.model
        self._openai_compatible_model.error_text = None

        # Qwen Region
        region_label = t(f"region.{settings.qwen.region.value}")
        _set_text_button_label(self._qwen_region_btn, f"{t('settings.qwen_region')} {region_label}")

        # Audio Settings
        self._audio_settings.host_api = settings.audio.input_host_api
        self._audio_settings.microphone = settings.audio.input_device
        self._audio_settings.desktop_output_device = settings.desktop_audio.output_device
        self._sync_general_audio_card_texts()

        # VAD
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
        # --- 新增：读取 VRChat 同步开关状态 ---
        self._vrc_mic_text.content.value = t(
            "settings.vrc_mic.on" if settings.osc.vrc_mic_intercept else "settings.vrc_mic.off"
        )
        self._chatbox_source_text.content.value = t(
            "settings.chatbox_source.on"
            if settings.osc.chatbox_include_source
            else "settings.chatbox_source.off"
        )
        self._clipboard_auto_translate_text.content.value = t(
            "settings.clipboard_auto_translate.on"
            if settings.ui.clipboard_auto_translate_enabled
            else "settings.clipboard_auto_translate.off"
        )
        # Prompt
        provider_name = self._active_prompt_key()
        self._prompt_editor.set_provider(provider_name)
        settings.system_prompts = {}
        if settings.system_prompt.strip():
            self._prompt_editor.value = settings.system_prompt
        else:
            self._prompt_editor.load_default_prompt(emit_change=False)
            settings.system_prompt = self._prompt_editor.value

        _ = preserve_custom_vocab_draft
        self._sync_custom_vocabulary_editor_from_settings()
        self._sync_prompt_tab_copy()
        self._overlay_peer_contract = None
        self._sync_overlay_controls()
        self.set_overlay_calibration(
            settings.overlay.calibration,
            preserve_draft=self._overlay_calibration_session_active,
        )

        # Load secrets
        self._load_secrets(settings, config_path)

        if self.page:
            self.update()

    def _load_secrets(self, settings: AppSettings, config_path: Path) -> None:
        """Load secret values into fields."""
        try:
            store = create_secret_store(settings.secrets, config_path=config_path)
        except Exception as exc:
            self._emit_runtime_basic(f"Failed to load secrets: {exc}", level=logging.WARNING)
            return

        self._openai_compatible_key.value = store.get("openai_compatible_api_key") or ""
        self._local_llm_api_key.value = store.get("local_llm_api_key") or ""

        # Restore verification status icons from saved settings
        self._restore_api_key_icons(settings)

    def _restore_api_key_icons(self, settings: AppSettings) -> None:
        """Restore API key field icons based on saved verification status."""
        verified = settings.api_key_verified

        field_map = [
            (self._openai_compatible_key, self._openai_compatible_key.value, verified.openai_compatible),
        ]

        for field, has_key, is_verified in field_map:
            if not has_key:
                field._set_status("idle")
                field._last_verified_hash = ""
            elif is_verified:
                field._set_status("success")
                field._last_verified_hash = field._get_key_hash(has_key)
            else:
                field._set_status("error")
                field._last_verified_hash = ""

    # --- Visibility Updates ---
    def _update_api_visibility(self, settings: AppSettings | None = None) -> None:
        """Update API key field visibility based on selected providers."""
        if settings is None:
            settings = self._build_settings_with_provider_draft()
        if settings is None:
            return

        stt = settings.provider.stt
        llm = settings.provider.llm
        peer_stt = self._effective_peer_stt_provider(settings)
        fallback_alias = settings.translation.fallback_selection_alias

        self._translation_connection_row.visible = True
        self._local_llm_connection_card.visible = llm == LLMProviderName.LOCAL_LLM
        self._openai_compatible_key.visible = llm == LLMProviderName.OPENAI_COMPATIBLE
        self._openai_compatible_card.visible = llm == LLMProviderName.OPENAI_COMPATIBLE

        stt_compute_visible = self._is_local_stt(stt)
        peer_compute_visible = self._is_local_stt(peer_stt)
        self._stt_compute_row.visible = stt_compute_visible
        self._peer_stt_compute_row.visible = peer_compute_visible
        self._trans_compute_spacer.visible = stt_compute_visible or peer_compute_visible
        if stt_compute_visible:
            self._sync_stt_compute_buttons(settings.provider.stt_compute)
        if peer_compute_visible:
            self._sync_peer_stt_compute_buttons(settings.provider.peer_stt_compute)
        self._sync_stt_quant_buttons(stt, settings.provider.stt_quant)
        self._sync_peer_quant_buttons(peer_stt, settings.provider.peer_stt_quant)
        _update_control_if_mounted(self._stt_compute_row)
        _update_control_if_mounted(self._peer_stt_compute_row)
        _update_control_if_mounted(self._trans_compute_spacer)

        _DUAL_BACKEND_MODELS = {
            STTProviderName.LOCAL_GIGAAM_RNNT, STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT, STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B, STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        stt_backend_visible = stt in _DUAL_BACKEND_MODELS
        peer_backend_visible = peer_stt in _DUAL_BACKEND_MODELS
        self._stt_backend_row.visible = stt_backend_visible
        self._peer_stt_backend_row.visible = peer_backend_visible
        if stt_backend_visible:
            self._sync_stt_backend_buttons(settings.provider.stt_backend)
        if peer_backend_visible:
            self._sync_peer_stt_backend_buttons(settings.provider.peer_stt_backend)
        _update_control_if_mounted(self._stt_backend_row)
        _update_control_if_mounted(self._peer_stt_backend_row)

    # --- Event Handlers ---
    def _on_qwen_region_click(self, e) -> None:
        pass

    def _on_openrouter_fallback_click(self, e) -> None:
        pass

    def _on_stt_click(self, e) -> None:
        """Open STT provider selection modal."""
        if not self.page:
            return
        _VULKAN_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        _DIRECTML_PROVIDERS = {
            STTProviderName.LOCAL_QWEN,
            STTProviderName.LOCAL_QWEN_17B,
            STTProviderName.LOCAL_GIGAAM_RNNT,
            STTProviderName.LOCAL_PARAKEET_TDT,
        }
        display_settings = self._build_settings_with_provider_draft()
        backend = display_settings.provider.stt_backend if display_settings is not None else "onnx"
        if backend == "gguf":
            allowed = _VULKAN_PROVIDERS
        else:
            allowed = _DIRECTML_PROVIDERS
        options = [
            OptionItem(
                value=p.value,
                label=provider_label(p.value),
                description=t(f"provider.{p.value}.description", default=""),
            )
            for p in STTProviderName if p in allowed
        ]
        current = (
            display_settings.provider.stt.value
            if display_settings is not None
            else STTProviderName.LOCAL_QWEN.value
        )
        modal = SettingsModal(
            self.page,
            t("settings.section.stt"),
            options,
            self._on_stt_selected,
            show_description=True,
        )
        modal.open(current)

    def _on_stt_selected(self, value: str) -> None:
        """Handle STT provider selection from modal."""
        if not self._settings:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        provider = STTProviderName(value)
        old_provider = current_settings.provider.stt.value
        if old_provider == provider.value:
            return
        self._emit_runtime_basic(
            f"[Settings] STT provider changed: {old_provider} -> {provider.value}"
        )
        draft = self._ensure_provider_settings_draft()
        draft.provider.stt = provider
        _GGUF_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        if provider in _GGUF_PROVIDERS:
            draft.provider.stt_backend = "gguf"
        else:
            draft.provider.stt_backend = "onnx"
        # Reset quant to first available when provider changes
        available_quants = self._get_quant_options(provider)
        if available_quants and draft.provider.stt_quant not in available_quants:
            draft.provider.stt_quant = available_quants[0]
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        self.has_provider_changes = True

        # Update text
        self._set_unit_card_value_text(self._stt_text, provider_label(provider.value))

        # Check compatibility warning
        source_lang = self._settings.languages.source_language
        warning = get_stt_compatibility_warning(source_lang, provider.value)
        if warning:
            lang_display = language_name(warning.language_code)
            message = t(warning.key, language=lang_display, lang=lang_display)
            if self.show_snackbar:
                self.show_snackbar(message, ft.Colors.ORANGE_700)
            elif self.page:
                self.page.open(
                    ft.SnackBar(
                        ft.Text(
                            message,
                            color=ft.Colors.WHITE,
                        ),
                        bgcolor=ft.Colors.ORANGE_700,
                        duration=4000,
                        behavior=ft.SnackBarBehavior.FLOATING,
                        margin=ft.margin.only(bottom=90),
                        padding=20,
                    )
                )

        if self.page:
            self._qwen_region_btn.update()
            self._api_keys_column.update()
            self._stt_text.update()

    def _on_peer_stt_click(self, e) -> None:
        if not self.page:
            return
        _VULKAN_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        _DIRECTML_PROVIDERS = {
            STTProviderName.LOCAL_QWEN,
            STTProviderName.LOCAL_QWEN_17B,
            STTProviderName.LOCAL_GIGAAM_RNNT,
            STTProviderName.LOCAL_PARAKEET_TDT,
        }
        display_settings = self._build_settings_with_provider_draft()
        backend = display_settings.provider.peer_stt_backend if display_settings is not None else "onnx"
        if backend == "gguf":
            allowed = _VULKAN_PROVIDERS
        else:
            allowed = _DIRECTML_PROVIDERS
        options = [self._peer_stt_option_item(provider) for provider in STTProviderName if provider in allowed]
        current_provider = (
            display_settings.provider.peer_stt
            if display_settings is not None
            else STTProviderName.LOCAL_QWEN
        )
        current = current_provider.value
        SettingsModal(
            self.page,
            t("settings.peer_stt_provider"),
            options,
            self._on_peer_stt_selected,
            show_description=True,
        ).open(current)

    def _on_peer_stt_selected(self, value: str) -> None:
        if not self._settings:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        provider = STTProviderName(value)
        if current_settings.provider.peer_stt == provider:
            return
        draft = self._ensure_provider_settings_draft()
        draft.provider.peer_stt = provider
        _GGUF_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        if provider in _GGUF_PROVIDERS:
            draft.provider.peer_stt_backend = "gguf"
        else:
            draft.provider.peer_stt_backend = "onnx"
        # Reset quant to first available when provider changes
        available_quants = self._get_quant_options(provider)
        if available_quants and draft.provider.peer_stt_quant not in available_quants:
            draft.provider.peer_stt_quant = available_quants[0]
        self._set_unit_card_value_text(self._peer_stt_text, provider_label(value))
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        if self.page:
            self._peer_stt_text.update()
            self._qwen_region_btn.update()
            self._api_keys_column.update()
        self.has_provider_changes = True

    def _is_local_stt(self, provider: STTProviderName) -> bool:
        return provider in (STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B, STTProviderName.LOCAL_GIGAAM_RNNT, STTProviderName.LOCAL_PARAKEET_TDT, STTProviderName.LOCAL_GIGAAM_RNNT_GGUF, STTProviderName.LOCAL_PARAKEET_TDT_GGUF, STTProviderName.LOCAL_QWEN3_ASR_GGUF, STTProviderName.LOCAL_QWEN_17B_GGUF)

    def _make_quant_button(self, label: str, on_click) -> ft.Container:
        return ft.Container(
            content=ft.Text(label, size=14, color=COLOR_ON_BACKGROUND),
            bgcolor=COLOR_SURFACE,
            border=ft.border.all(1, COLOR_DIVIDER),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=on_click,
        )

    def _sync_quant_buttons(
        self,
        active_quant: str,
        available_quants: list[str],
        label: ft.Text,
        buttons: dict[str, ft.Container],
    ) -> None:
        label.visible = bool(available_quants)
        for quant, btn in buttons.items():
            btn.visible = quant in available_quants
            is_active = quant == active_quant
            btn.bgcolor = COLOR_PRIMARY if is_active else COLOR_SURFACE
            btn.border = ft.border.all(1, COLOR_PRIMARY if is_active else COLOR_DIVIDER)
            btn.content.color = ft.Colors.WHITE if is_active else COLOR_ON_BACKGROUND
            btn.content.weight = ft.FontWeight.BOLD if is_active else ft.FontWeight.NORMAL
            _update_control_if_mounted(btn)
        _update_control_if_mounted(label)

    _ONNX_QUANTS = ["int8"]  # extend here when fp16/fp32 are available
    _GGUF_QUANTS = ["q8_0", "q6_k", "f16"]
    _INITIAL_QUANTS_FOR_PROVIDER: dict[STTProviderName, list[str]] = {
        STTProviderName.LOCAL_QWEN: ["int8"],
        STTProviderName.LOCAL_QWEN_17B: ["int8"],
        STTProviderName.LOCAL_GIGAAM_RNNT: ["int8"],
        STTProviderName.LOCAL_PARAKEET_TDT: ["int8"],
        STTProviderName.LOCAL_GIGAAM_RNNT_GGUF: ["q8_0", "q6_k", "f16"],
        STTProviderName.LOCAL_PARAKEET_TDT_GGUF: ["q8_0", "q6_k", "f16"],
        STTProviderName.LOCAL_QWEN3_ASR_GGUF: ["q8_0", "q6_k", "f16"],
        STTProviderName.LOCAL_QWEN_17B_GGUF: ["q8_0", "q6_k", "f16"],
    }

    def _get_quant_options(self, provider: STTProviderName) -> list[str]:
        _ONNX_PROVIDERS = {
            STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B,
            STTProviderName.LOCAL_GIGAAM_RNNT, STTProviderName.LOCAL_PARAKEET_TDT,
        }
        _GGUF_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF, STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF, STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        if provider in _ONNX_PROVIDERS:
            return list(self._ONNX_QUANTS)
        if provider in _GGUF_PROVIDERS:
            return list(self._GGUF_QUANTS)
        return []

    def _sync_stt_quant_buttons(self, provider: STTProviderName, quant: str) -> None:
        available = self._get_quant_options(provider)
        resolved = quant if quant in available else (available[0] if available else quant)
        self._stt_quant_row.visible = bool(available)
        self._sync_quant_buttons(
            resolved, available,
            self._stt_quant_label,
            {"q8_0": self._stt_quant_q8_btn, "q6_k": self._stt_quant_q6k_btn, "f16": self._stt_quant_f16_btn, "int8": self._stt_quant_int8_btn},
        )
        _update_control_if_mounted(self._stt_quant_row)

    def _sync_peer_quant_buttons(self, provider: STTProviderName, quant: str) -> None:
        available = self._get_quant_options(provider)
        resolved = quant if quant in available else (available[0] if available else quant)
        self._peer_quant_row.visible = bool(available)
        self._sync_quant_buttons(
            resolved, available,
            self._peer_quant_label,
            {"q8_0": self._peer_quant_q8_btn, "q6_k": self._peer_quant_q6k_btn, "f16": self._peer_quant_f16_btn, "int8": self._peer_quant_int8_btn},
        )
        _update_control_if_mounted(self._peer_quant_row)

    def _apply_stt_quant(self, quant: str) -> None:
        if not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        draft.provider.stt_quant = quant
        self._sync_stt_quant_buttons(draft.provider.stt, quant)
        self.has_provider_changes = True

    def _apply_peer_quant(self, quant: str) -> None:
        if not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        draft.provider.peer_stt_quant = quant
        self._sync_peer_quant_buttons(draft.provider.peer_stt, quant)
        self.has_provider_changes = True

    def _sync_stt_compute_buttons(self, compute: str) -> None:
        is_gpu = compute == "gpu"
        self._stt_compute_gpu_btn.bgcolor = COLOR_PRIMARY if is_gpu else COLOR_SURFACE
        self._stt_compute_gpu_btn.border = ft.border.all(1, COLOR_PRIMARY if is_gpu else COLOR_DIVIDER)
        self._stt_compute_gpu_btn.content.color = ft.Colors.WHITE if is_gpu else COLOR_ON_BACKGROUND
        self._stt_compute_gpu_btn.content.weight = ft.FontWeight.BOLD if is_gpu else ft.FontWeight.NORMAL
        self._stt_compute_cpu_btn.bgcolor = COLOR_PRIMARY if not is_gpu else COLOR_SURFACE
        self._stt_compute_cpu_btn.border = ft.border.all(1, COLOR_PRIMARY if not is_gpu else COLOR_DIVIDER)
        self._stt_compute_cpu_btn.content.color = ft.Colors.WHITE if not is_gpu else COLOR_ON_BACKGROUND
        self._stt_compute_cpu_btn.content.weight = ft.FontWeight.BOLD if not is_gpu else ft.FontWeight.NORMAL
        _update_control_if_mounted(self._stt_compute_gpu_btn)
        _update_control_if_mounted(self._stt_compute_cpu_btn)

    def _sync_peer_stt_compute_buttons(self, compute: str) -> None:
        is_gpu = compute == "gpu"
        self._peer_stt_compute_gpu_btn.bgcolor = COLOR_PRIMARY if is_gpu else COLOR_SURFACE
        self._peer_stt_compute_gpu_btn.border = ft.border.all(1, COLOR_PRIMARY if is_gpu else COLOR_DIVIDER)
        self._peer_stt_compute_gpu_btn.content.color = ft.Colors.WHITE if is_gpu else COLOR_ON_BACKGROUND
        self._peer_stt_compute_gpu_btn.content.weight = ft.FontWeight.BOLD if is_gpu else ft.FontWeight.NORMAL
        self._peer_stt_compute_cpu_btn.bgcolor = COLOR_PRIMARY if not is_gpu else COLOR_SURFACE
        self._peer_stt_compute_cpu_btn.border = ft.border.all(1, COLOR_PRIMARY if not is_gpu else COLOR_DIVIDER)
        self._peer_stt_compute_cpu_btn.content.color = ft.Colors.WHITE if not is_gpu else COLOR_ON_BACKGROUND
        self._peer_stt_compute_cpu_btn.content.weight = ft.FontWeight.BOLD if not is_gpu else ft.FontWeight.NORMAL
        _update_control_if_mounted(self._peer_stt_compute_gpu_btn)
        _update_control_if_mounted(self._peer_stt_compute_cpu_btn)

    def _on_stt_compute_gpu_click(self, e) -> None:
        self._apply_stt_compute("gpu")

    def _on_stt_compute_cpu_click(self, e) -> None:
        self._apply_stt_compute("cpu")

    def _sync_stt_backend_buttons(self, backend: str) -> None:
        is_onnx = backend == "onnx"
        self._stt_backend_onnx_btn.bgcolor = COLOR_PRIMARY if is_onnx else COLOR_SURFACE
        self._stt_backend_onnx_btn.border = ft.border.all(1, COLOR_PRIMARY if is_onnx else COLOR_DIVIDER)
        self._stt_backend_onnx_btn.content.color = ft.Colors.WHITE if is_onnx else COLOR_ON_BACKGROUND
        self._stt_backend_onnx_btn.content.weight = ft.FontWeight.BOLD if is_onnx else ft.FontWeight.NORMAL
        self._stt_backend_gguf_btn.bgcolor = COLOR_PRIMARY if not is_onnx else COLOR_SURFACE
        self._stt_backend_gguf_btn.border = ft.border.all(1, COLOR_PRIMARY if not is_onnx else COLOR_DIVIDER)
        self._stt_backend_gguf_btn.content.color = ft.Colors.WHITE if not is_onnx else COLOR_ON_BACKGROUND
        self._stt_backend_gguf_btn.content.weight = ft.FontWeight.BOLD if not is_onnx else ft.FontWeight.NORMAL
        _update_control_if_mounted(self._stt_backend_onnx_btn)
        _update_control_if_mounted(self._stt_backend_gguf_btn)

    def _sync_peer_stt_backend_buttons(self, backend: str) -> None:
        is_onnx = backend == "onnx"
        self._peer_stt_backend_onnx_btn.bgcolor = COLOR_PRIMARY if is_onnx else COLOR_SURFACE
        self._peer_stt_backend_onnx_btn.border = ft.border.all(1, COLOR_PRIMARY if is_onnx else COLOR_DIVIDER)
        self._peer_stt_backend_onnx_btn.content.color = ft.Colors.WHITE if is_onnx else COLOR_ON_BACKGROUND
        self._peer_stt_backend_onnx_btn.content.weight = ft.FontWeight.BOLD if is_onnx else ft.FontWeight.NORMAL
        self._peer_stt_backend_gguf_btn.bgcolor = COLOR_PRIMARY if not is_onnx else COLOR_SURFACE
        self._peer_stt_backend_gguf_btn.border = ft.border.all(1, COLOR_PRIMARY if not is_onnx else COLOR_DIVIDER)
        self._peer_stt_backend_gguf_btn.content.color = ft.Colors.WHITE if not is_onnx else COLOR_ON_BACKGROUND
        self._peer_stt_backend_gguf_btn.content.weight = ft.FontWeight.BOLD if not is_onnx else ft.FontWeight.NORMAL
        _update_control_if_mounted(self._peer_stt_backend_onnx_btn)
        _update_control_if_mounted(self._peer_stt_backend_gguf_btn)

    def _on_stt_backend_onnx_click(self, e) -> None:
        self._apply_stt_backend("onnx")

    def _on_stt_backend_gguf_click(self, e) -> None:
        self._apply_stt_backend("gguf")

    def _on_peer_stt_backend_onnx_click(self, e) -> None:
        self._apply_peer_stt_backend("onnx")

    def _on_peer_stt_backend_gguf_click(self, e) -> None:
        self._apply_peer_stt_backend("gguf")

    def _apply_stt_backend(self, value: str) -> None:
        if not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        if draft.provider.stt_backend == value:
            return
        draft.provider.stt_backend = value
        _ONNX_TO_GGUF = {
            STTProviderName.LOCAL_GIGAAM_RNNT: STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT: STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN: STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B: STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        _GGUF_TO_ONNX = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF: STTProviderName.LOCAL_GIGAAM_RNNT,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF: STTProviderName.LOCAL_PARAKEET_TDT,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF: STTProviderName.LOCAL_QWEN,
            STTProviderName.LOCAL_QWEN_17B_GGUF: STTProviderName.LOCAL_QWEN_17B,
        }
        current = draft.provider.stt
        if value == "gguf" and current in _ONNX_TO_GGUF:
            draft.provider.stt = _ONNX_TO_GGUF[current]
            self._set_unit_card_value_text(self._stt_text, provider_label(draft.provider.stt.value))
        elif value == "onnx" and current in _GGUF_TO_ONNX:
            draft.provider.stt = _GGUF_TO_ONNX[current]
            self._set_unit_card_value_text(self._stt_text, provider_label(draft.provider.stt.value))
        # Reset quant when backend changes (ONNX→GGUF or GGUF→ONNX)
        available_quants = self._get_quant_options(draft.provider.stt)
        if available_quants and draft.provider.stt_quant not in available_quants:
            draft.provider.stt_quant = available_quants[0]
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        self.has_provider_changes = True

    def _apply_peer_stt_backend(self, value: str) -> None:
        if not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        if draft.provider.peer_stt_backend == value:
            return
        draft.provider.peer_stt_backend = value
        _ONNX_TO_GGUF = {
            STTProviderName.LOCAL_GIGAAM_RNNT: STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT: STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN: STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B: STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        _GGUF_TO_ONNX = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF: STTProviderName.LOCAL_GIGAAM_RNNT,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF: STTProviderName.LOCAL_PARAKEET_TDT,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF: STTProviderName.LOCAL_QWEN,
            STTProviderName.LOCAL_QWEN_17B_GGUF: STTProviderName.LOCAL_QWEN_17B,
        }
        current = draft.provider.peer_stt
        if value == "gguf" and current in _ONNX_TO_GGUF:
            draft.provider.peer_stt = _ONNX_TO_GGUF[current]
            self._set_unit_card_value_text(self._peer_stt_text, provider_label(draft.provider.peer_stt.value))
        elif value == "onnx" and current in _GGUF_TO_ONNX:
            draft.provider.peer_stt = _GGUF_TO_ONNX[current]
            self._set_unit_card_value_text(self._peer_stt_text, provider_label(draft.provider.peer_stt.value))
        available_quants = self._get_quant_options(draft.provider.peer_stt)
        if available_quants and draft.provider.peer_stt_quant not in available_quants:
            draft.provider.peer_stt_quant = available_quants[0]
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        self.has_provider_changes = True

    def _apply_stt_compute(self, value: str) -> None:
        if not self._settings:
            return
        if self._settings.provider.stt_compute == value:
            return
        self._settings.provider.stt_compute = value
        self._sync_stt_compute_buttons(value)
        self.has_provider_changes = True
        self._emit_runtime_basic(f"[Settings] STT compute changed: {value}")

    def _on_peer_stt_compute_gpu_click(self, e) -> None:
        self._apply_peer_stt_compute("gpu")

    def _on_peer_stt_compute_cpu_click(self, e) -> None:
        self._apply_peer_stt_compute("cpu")

    def _apply_peer_stt_compute(self, value: str) -> None:
        if not self._settings:
            return
        if self._settings.provider.peer_stt_compute == value:
            return
        self._settings.provider.peer_stt_compute = value
        self._sync_peer_stt_compute_buttons(value)
        self.has_provider_changes = True
        self._emit_runtime_basic(f"[Settings] Peer STT compute changed: {value}")

    def _on_llm_click(self, e) -> None:
        """Open LLM provider selection modal."""
        if not self.page:
            return
        recommended_section = t("settings.translation_model.section.recommended")
        others_section = t("settings.translation_model.section.others")
        model_sections = (
            (TranslationModel.GEMMA4, recommended_section),
            (TranslationModel.DEEPSEEK_V4_FLASH, recommended_section),
            (TranslationModel.GEMMA4_31B_CEREBRAS, others_section),
            (TranslationModel.LOCAL_LLM, others_section),
            (TranslationModel.OPENAI_COMPATIBLE, others_section),
            (TranslationModel.DEEPSEEK_V4_PRO, others_section),
            (TranslationModel.GEMINI_3_FLASH, others_section),
            (TranslationModel.GEMINI_31_FLASH_LITE, others_section),
            (TranslationModel.QWEN_35_PLUS, others_section),
        )
        options = [
            OptionItem(
                value=model.value,
                label=self._translation_model_display_label(model),
                description=t(f"settings.translation_model.{model.value}.description", default=""),
                section=section,
            )
            for model, section in model_sections
        ]
        display_settings = self._build_settings_with_provider_draft()
        current = (
            self._get_llm_modal_value(display_settings)
            if display_settings is not None
            else TranslationModel.GEMMA4.value
        )
        modal = SettingsModal(
            self.page,
            t("settings.section.translation"),
            options,
            self._on_llm_selected,
            show_description=True,
        )
        modal.open(current)

    def _restore_translation_connection_for_model(
        self,
        model: TranslationModel,
        history: dict[str, TranslationConnection],
    ) -> TranslationConnection:
        connection = history.get(model.value)
        if not isinstance(connection, TranslationConnection):
            try:
                connection = TranslationConnection(str(connection))
            except (TypeError, ValueError):
                connection = None
        if connection in supported_translation_connections(model):
            return connection
        return default_translation_connection(model)

    def _sync_translation_selection_controls(self, settings: AppSettings) -> None:
        self._set_unit_card_value_text(
            self._llm_text,
            self._get_llm_display_label(settings),
        )
        self._set_translation_connection_text(
            self._get_translation_connection_display_label(settings),
        )

    def _apply_translation_selection(
        self,
        model: TranslationModel,
        connection: TranslationConnection,
    ) -> None:
        if not self._settings:
            return
        if connection not in supported_translation_connections(model):
            return

        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        old_model = current_settings.translation.model
        old_connection = current_settings.translation.connection
        old_provider = current_settings.provider.llm
        if old_model == model and old_connection == connection:
            return

        draft = self._ensure_provider_settings_draft()
        draft.translation = copy.deepcopy(current_settings.translation)
        draft.translation.model = model
        draft.translation.connection = connection
        draft.translation.connection_history = copy.deepcopy(
            current_settings.translation.connection_history
        )
        draft.translation.connection_history[model.value] = connection
        materialize_translation_settings(draft)
        new_provider = draft.provider.llm

        changes: list[str] = []
        if old_model != model:
            changes.append(f"model={old_model.value}->{model.value}")
        if old_connection != connection:
            changes.append(f"connection={old_connection.value}->{connection.value}")
        if old_provider != new_provider:
            changes.append(f"provider={old_provider.value}->{new_provider.value}")
            self._emit_runtime_basic(
                f"[Settings] LLM provider changed: {old_provider.value} -> {new_provider.value}"
            )
        if changes:
            self._emit_runtime_detailed(
                f"[Settings] Translation selection changed: {', '.join(changes)}"
            )

        self.has_provider_changes = True
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)

        display_settings = merged
        self._sync_translation_selection_controls(display_settings)

        if old_provider != display_settings.provider.llm:
            provider_name = self._active_prompt_key()
            self._prompt_editor.set_provider(provider_name)
            next_prompt = self._ensure_provider_prompt_value(draft, provider_name)
            self._prompt_editor.value = next_prompt
            draft.system_prompt = next_prompt
        self._sync_prompt_tab_copy()

        if self.page:
            self._qwen_region_btn.update()
            self._llm_text.update()
            self._translation_connection_row.update()
            self._local_llm_connection_card.update()
            self._api_keys_column.update()

    def _on_llm_selected(self, value: str) -> None:
        """Handle LLM provider selection from modal."""
        if not self._settings:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        try:
            model = TranslationModel(value)
        except (TypeError, ValueError):
            return

        if current_settings.translation.model == model:
            return
        history = copy.deepcopy(current_settings.translation.connection_history)
        connection = self._restore_translation_connection_for_model(model, history)
        self._apply_translation_selection(model, connection)

    def _on_translation_connection_click(self, e) -> None:
        if not self.page:
            return
        display_settings = self._build_settings_with_provider_draft()
        model = (
            display_settings.translation.model
            if display_settings is not None
            else TranslationModel.GEMMA4
        )
        connections = supported_translation_connections(model)
        options = [
            OptionItem(
                value=connection.value,
                label=self._translation_connection_display_label(connection),
            )
            for connection in connections
        ]
        current = (
            display_settings.translation.connection.value
            if display_settings is not None
            else default_translation_connection(model).value
        )
        modal = SettingsModal(
            self.page,
            t("settings.translation_connection"),
            options,
            self._on_translation_connection_selected,
            show_description=False,
        )
        modal.open(current)

    def _on_translation_connection_selected(self, value: str) -> None:
        if not self._settings:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        model = current_settings.translation.model
        try:
            connection = TranslationConnection(value)
        except (TypeError, ValueError):
            return
        if connection not in supported_translation_connections(model):
            return
        self._apply_translation_selection(model, connection)

    def _on_ui_click(self, e) -> None:
        """Open UI language selection modal."""
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
        """Handle UI language selection from modal."""
        if not self._settings:
            return
        old_locale = self._settings.ui.locale
        self._emit_runtime_basic(f"[Settings] Language changed: {old_locale} -> {value}")
        self._settings.ui.locale = value

        # Update text
        self._ui_text.content.value = locale_label(value)
        if self.page:
            self._ui_text.update()
        self._emit_settings_changed()

    def _write_secret_value(self, key: str, value: str) -> bool:
        if not self._settings or not self._config_path:
            return False

        try:
            store = create_secret_store(self._settings.secrets, config_path=self._config_path)
            if value:
                store.set(key, value)
            else:
                store.delete(key)
            return True
        except Exception as exc:
            self._emit_runtime_basic(
                f"Failed to update secret {key}: {type(exc).__name__}",
                level=logging.WARNING,
            )
            return False

    def _on_local_llm_secret_change(self, key: str, value: str) -> None:
        if key != "local_llm_api_key":
            return
        stripped = value.strip()
        if not self._write_secret_value(key, stripped):
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.api_key.save_failed"), ft.Colors.RED_400)
            return
        self._local_llm_api_key.value = stripped
        if self.on_local_llm_secret_changed:
            self.on_local_llm_secret_changed()

    def _on_secret_change(self, key: str, value: str) -> None:
        if not self._settings or not self._config_path:
            return

        if not self._write_secret_value(key, value):
            return
        if not value and self.on_secret_cleared:
            with contextlib.suppress(Exception):
                self.on_secret_cleared(key)

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

        self._settings.audio.input_host_api = new_host
        self._settings.audio.input_device = new_device
        self._settings.desktop_audio.output_device = new_desktop_output
        self._emit_settings_changed()

    def _on_mic_host_api_click(self, e) -> None:
        if not self.page:
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
        if self.page:
            self._mic_audio_text.update()
            self._audio_host_api_text.update()
        self._on_audio_change()

    def _on_mic_audio_click(self, e) -> None:
        if not self.page:
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
        if self.page:
            self._mic_audio_text.update()
        self._on_audio_change()

    def _on_loopback_audio_click(self, e) -> None:
        if not self.page:
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
        if self.page:
            self._loopback_audio_text.update()
        self._on_audio_change()

    def _normalized_overlay_target(self, value: object) -> str:
        return OVERLAY_TARGET_DESKTOP if value == OVERLAY_TARGET_DESKTOP else OVERLAY_TARGET_STEAMVR

    def _current_overlay_target(self) -> str:
        if self._settings is None:
            return OVERLAY_TARGET_STEAMVR
        return self._normalized_overlay_target(self._settings.overlay.target)

    def _overlay_target_label_for(self, target: object) -> str:
        normalized_target = self._normalized_overlay_target(target)
        return t(f"settings.overlay.target.{normalized_target}")

    def _sync_overlay_target_control(self) -> None:
        self._set_unit_card_value_text(
            self._overlay_target_button,
            self._overlay_target_label_for(self._current_overlay_target()),
            size=28,
        )
        self._overlay_target_button.disabled = self._settings is None

    def _sync_overlay_target_specific_visibility(self) -> None:
        desktop_selected = self._current_overlay_target() == OVERLAY_TARGET_DESKTOP
        for row in getattr(self, "_overlay_vr_rows", ()):
            row.visible = not desktop_selected
        for row in getattr(self, "_overlay_desktop_rows", ()):
            row.visible = desktop_selected

    @staticmethod
    def _normalize_desktop_overlay_size_preset(value: object) -> str:
        if isinstance(value, str) and value in DESKTOP_FLET_SIZE_PRESET_ORDER:
            return value
        return "medium"

    @staticmethod
    def _normalize_desktop_overlay_background_alpha(value: object) -> float:
        if isinstance(value, bool):
            return DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
        try:
            alpha = float(value)
        except (TypeError, ValueError):
            return DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
        if not math.isfinite(alpha):
            return DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
        return max(0.0, min(1.0, alpha))

    def _desktop_overlay_background_alpha_label_for(self, value: object) -> str:
        alpha = self._normalize_desktop_overlay_background_alpha(value)
        transparency = 1.0 - alpha
        return f"{int(round(transparency * 100))}%"

    def _desktop_overlay_size_label_for(self, size_preset: object) -> str:
        normalized = self._normalize_desktop_overlay_size_preset(size_preset)
        return t(f"settings.overlay.desktop.size.option.{normalized}")

    def _current_desktop_overlay_size_preset(self) -> str:
        pending_size_preset = getattr(self, "_desktop_overlay_pending_size_preset", None)
        if pending_size_preset is not None:
            return pending_size_preset
        if self._settings is None:
            return "medium"
        return self._normalize_desktop_overlay_size_preset(
            self._settings.overlay.desktop_flet.size_preset
        )

    def _current_desktop_overlay_background_alpha(self) -> float:
        if self._settings is None:
            return DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
        return self._normalize_desktop_overlay_background_alpha(
            self._settings.overlay.desktop_flet.visual.background_alpha
        )

    def _desktop_overlay_lock_label_for(self, locked: bool) -> str:
        return t(
            "settings.overlay.desktop.lock.value.locked"
            if locked
            else "settings.overlay.desktop.lock.value.move"
        )

    def _current_desktop_overlay_locked(self) -> bool:
        if self._settings is None:
            return False
        if getattr(self, "_desktop_overlay_pending_position_reset", False):
            return False
        if not self._desktop_overlay_runtime_lock_applies():
            return False
        pending_locked = getattr(self, "_desktop_overlay_pending_locked", None)
        if pending_locked is not None:
            return bool(pending_locked)
        return bool(getattr(self, "_desktop_overlay_captions_locked", False))

    def _desktop_overlay_runtime_lock_applies(self) -> bool:
        if getattr(self, "_overlay_state", "off") not in {"connected", "running"}:
            return False
        return (
            self._normalized_overlay_target(
                getattr(self, "_overlay_runtime_target", OVERLAY_TARGET_STEAMVR)
            )
            == OVERLAY_TARGET_DESKTOP
        )

    def _sync_desktop_overlay_main_controls(self) -> None:
        self._set_unit_card_value_text(
            self._desktop_overlay_size_button,
            self._desktop_overlay_size_label_for(self._current_desktop_overlay_size_preset()),
        )
        self._set_unit_card_value_text(
            self._desktop_overlay_lock_button,
            self._desktop_overlay_lock_label_for(self._current_desktop_overlay_locked()),
        )
        self._desktop_overlay_background_alpha_value_text.value = (
            self._desktop_overlay_background_alpha_label_for(
                self._current_desktop_overlay_background_alpha()
            )
        )
        disabled = self._settings is None
        self._desktop_overlay_size_button.disabled = disabled
        self._desktop_overlay_background_alpha_decrease_button.disabled = disabled
        self._desktop_overlay_background_alpha_increase_button.disabled = disabled
        self._desktop_overlay_lock_button.disabled = disabled
        self._overlay_vr_reset_button.disabled = disabled
        self._overlay_desktop_reset_button.disabled = disabled

    def _desktop_overlay_status_is_visible(self) -> bool:
        return bool(
            self._current_overlay_target() == OVERLAY_TARGET_DESKTOP
            or self._normalized_overlay_target(self._overlay_runtime_target)
            == OVERLAY_TARGET_DESKTOP
        )

    def _desktop_overlay_failure_action_kind(self) -> str:
        if self._overlay_failure_reason in _DESKTOP_OVERLAY_REOPEN_FAILURE_REASONS:
            return "reopen"
        return "retry"

    def _set_desktop_overlay_primary_action(
        self,
        *,
        label_key: str | None,
        action_kind: str | None,
        visible: bool,
    ) -> None:
        self._set_unit_card_value_text(
            self._desktop_overlay_primary_action,
            t(label_key) if label_key else "",
            size=20,
        )
        self._desktop_overlay_primary_action_kind = action_kind
        self._desktop_overlay_primary_action.visible = visible

    def _sync_desktop_overlay_status_control(self) -> None:
        state = self._overlay_state
        desktop_status_visible = self._desktop_overlay_status_is_visible() and state == "failed"
        self._desktop_overlay_status_card.visible = desktop_status_visible
        self._desktop_overlay_recovery_row.visible = desktop_status_visible
        self._desktop_overlay_reason_text.visible = False
        self._desktop_overlay_reason_text.value = ""
        self._desktop_overlay_helper_text.visible = False
        self._desktop_overlay_helper_text.value = ""
        self._desktop_overlay_view_logs_action.visible = False
        self._desktop_overlay_view_logs_action.disabled = False

        if state == "failed":
            self._desktop_overlay_status_title.value = t("settings.overlay.desktop.status.failed")
            action_kind = self._desktop_overlay_failure_action_kind()
            self._desktop_overlay_reason_text.value = t(
                f"settings.overlay.desktop.recovery.message.{action_kind}",
                default=t("settings.overlay.desktop.recovery.message.retry"),
            )
            self._desktop_overlay_reason_text.visible = True
            action_key = (
                "settings.overlay.desktop.recovery.action.reopen"
                if action_kind == "reopen"
                else "settings.overlay.desktop.recovery.action.retry"
            )
            self._set_desktop_overlay_primary_action(
                label_key=action_key,
                action_kind=action_kind,
                visible=True,
            )
            self._desktop_overlay_view_logs_action.visible = True
        else:
            self._desktop_overlay_status_title.value = t(
                "settings.overlay.status.stopping"
                if state == "stopping"
                else "settings.overlay.status.off"
            )
            self._set_desktop_overlay_primary_action(
                label_key=None,
                action_kind=None,
                visible=False,
            )

    def _on_overlay_target_click(self, e) -> None:
        _ = e
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(
                value=OVERLAY_TARGET_STEAMVR,
                label=self._overlay_target_label_for(OVERLAY_TARGET_STEAMVR),
            ),
            OptionItem(
                value=OVERLAY_TARGET_DESKTOP,
                label=self._overlay_target_label_for(OVERLAY_TARGET_DESKTOP),
            ),
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.caption_location"),
            options,
            self._on_overlay_target_selected,
            show_description=True,
        )
        modal.open(self._current_overlay_target())

    def _on_overlay_target_selected(self, value: str) -> None:
        if not self._settings:
            return
        target = self._normalized_overlay_target(value)
        if self._current_overlay_target() == target:
            return
        self._settings.overlay.target = target
        if self._overlay_state == "off":
            self._overlay_runtime_target = target
        self._sync_overlay_controls()
        self._emit_settings_changed()

    def _on_desktop_overlay_size_click(self, e) -> None:
        _ = e
        if not self.page or not self._settings or self._desktop_overlay_size_button.disabled:
            return
        options = [
            OptionItem(
                value=preset,
                label=self._desktop_overlay_size_label_for(preset),
            )
            for preset in DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.desktop.size.title"),
            options,
            self._on_desktop_overlay_size_selected,
            show_description=False,
        )
        modal.open(self._current_desktop_overlay_size_preset())

    def _on_desktop_overlay_size_selected(self, value: str) -> None:
        if not self._settings:
            return
        size_preset = self._normalize_desktop_overlay_size_preset(value)
        if self._current_desktop_overlay_size_preset() == size_preset:
            return
        if self.on_desktop_overlay_size_change:
            self._desktop_overlay_pending_size_preset = size_preset
            self._sync_desktop_overlay_main_controls()
            self.on_desktop_overlay_size_change(size_preset)
            return
        self._settings.overlay.desktop_flet.size_preset = size_preset
        self._desktop_overlay_pending_size_preset = None
        self._sync_desktop_overlay_main_controls()
        self._emit_settings_changed()

    def _on_desktop_overlay_lock_click(self, e) -> None:
        _ = e
        if not self._settings or self._desktop_overlay_lock_button.disabled:
            return
        next_value = "move" if self._current_desktop_overlay_locked() else "locked"
        self._on_desktop_overlay_lock_selected(next_value)

    def _on_desktop_overlay_lock_selected(self, value: str) -> None:
        if not self._settings:
            return
        locked = value == "locked"
        if self._current_desktop_overlay_locked() == locked:
            return
        if not self._desktop_overlay_runtime_lock_applies():
            self._sync_desktop_overlay_main_controls()
            return
        if self.on_desktop_overlay_lock_change:
            self._desktop_overlay_pending_locked = locked
            self._desktop_overlay_captions_locked = locked
            self._sync_desktop_overlay_main_controls()
            self.on_desktop_overlay_lock_change(locked)
            return
        self._desktop_overlay_pending_locked = locked
        self._desktop_overlay_captions_locked = locked
        self._sync_desktop_overlay_main_controls()

    def _on_desktop_overlay_background_alpha_step(self, delta: float) -> None:
        if not self._settings or self._desktop_overlay_background_alpha_decrease_button.disabled:
            return
        current = self._current_desktop_overlay_background_alpha()
        current_transparency = 1.0 - current
        next_transparency = self._normalize_desktop_overlay_background_alpha(
            round(current_transparency + delta, 2)
        )
        next_alpha = self._normalize_desktop_overlay_background_alpha(
            round(1.0 - next_transparency, 2)
        )
        if current == next_alpha:
            self._sync_desktop_overlay_main_controls()
            if self.page:
                self.update()
            return
        updated = copy.deepcopy(self._settings)
        desktop_visual = updated.overlay.desktop_flet.visual
        desktop_visual.background_alpha = next_alpha
        desktop_visual.validate()
        self._settings = updated
        self._sync_desktop_overlay_main_controls()
        if self.page:
            self.update()
        self._emit_settings_changed()

    def _on_desktop_overlay_primary_action(self, e) -> None:
        _ = e
        action_kind = self._desktop_overlay_primary_action_kind
        if action_kind == "lock" and self.on_desktop_overlay_lock_change:
            self.on_desktop_overlay_lock_change(True)
        elif action_kind == "edit" and self.on_desktop_overlay_lock_change:
            self.on_desktop_overlay_lock_change(False)
        elif action_kind in {"retry", "reopen"} and self.on_desktop_overlay_recovery_action:
            self.on_desktop_overlay_recovery_action(action_kind)

    def _on_desktop_overlay_view_logs(self, e) -> None:
        _ = e
        if self.on_view_logs:
            self.on_view_logs()

    def set_overlay_calibration(
        self,
        calibration: OverlayCalibration,
        *,
        preserve_draft: bool = False,
    ) -> None:
        calibration.validate()
        self._overlay_calibration = calibration.copy()

        if preserve_draft and self._overlay_calibration_session_active:
            self._sync_overlay_calibration_controls(self._overlay_calibration_draft)
            return

        self._overlay_calibration_draft = calibration.copy()
        self._overlay_calibration_session_active = False
        self._sync_overlay_calibration_controls(self._overlay_calibration)

    def _sync_overlay_calibration_controls(
        self,
        calibration: OverlayCalibration | None = None,
    ) -> None:
        current = (calibration or self._overlay_calibration).copy()
        self._set_unit_card_value_text(
            self._overlay_anchor_button,
            self._overlay_anchor_label_for(current.anchor),
        )
        self._overlay_distance_value_text.value = self._format_overlay_calibration_number(
            current.distance
        )
        self._overlay_offset_x_value_text.value = self._format_overlay_calibration_number(
            current.offset_x
        )
        self._overlay_offset_y_value_text.value = self._format_overlay_calibration_number(
            current.offset_y
        )
        self._overlay_text_scale_text.content.value = self._overlay_text_scale_label_for(
            current.text_scale
        )

    def _begin_overlay_calibration_session(self) -> OverlayCalibration:
        if self._overlay_calibration_session_active:
            return self._overlay_calibration_draft.copy()

        if self.on_overlay_calibration_begin:
            calibration = self.on_overlay_calibration_begin()
        else:
            calibration = self._overlay_calibration.copy()

        calibration.validate()
        self._overlay_calibration_draft = calibration.copy()
        self._overlay_calibration_session_active = True
        self._sync_overlay_calibration_controls(self._overlay_calibration_draft)
        return self._overlay_calibration_draft.copy()

    def _update_overlay_calibration_draft(
        self,
        field_name: str,
        value: object,
    ) -> OverlayCalibration:
        self._begin_overlay_calibration_session()

        if self.on_overlay_calibration_change:
            calibration = self.on_overlay_calibration_change(field_name, value)
            calibration.validate()
            self._overlay_calibration_draft = calibration.copy()
        else:
            if field_name == "anchor":
                setattr(self._overlay_calibration_draft, field_name, str(value))
            else:
                setattr(self._overlay_calibration_draft, field_name, float(value))
            self._overlay_calibration_draft.validate()

        self._sync_overlay_calibration_controls(self._overlay_calibration_draft)
        return self._overlay_calibration_draft.copy()

    def _commit_overlay_calibration_draft(self) -> OverlayCalibration:
        if self.on_overlay_calibration_apply:
            calibration = self.on_overlay_calibration_apply()
            calibration.validate()
        else:
            if not self._overlay_calibration_session_active:
                self._begin_overlay_calibration_session()
            calibration = self._overlay_calibration_draft.copy()

        self._overlay_calibration = calibration.copy()
        self._overlay_calibration_draft = calibration.copy()
        self._overlay_calibration_session_active = False
        if self._settings is not None:
            self._settings.overlay.calibration = calibration.copy()
        self._sync_overlay_calibration_controls(self._overlay_calibration)

        if self.page:
            self.update()

        if self.on_overlay_calibration_apply is None:
            self._emit_settings_changed()

        return calibration.copy()

    def _apply_overlay_calibration_field_immediately(
        self,
        field_name: str,
        value: object,
    ) -> OverlayCalibration | None:
        try:
            self._update_overlay_calibration_draft(field_name, value)
        except ValueError:
            self._sync_overlay_calibration_controls(self._overlay_calibration)
            return None

        return self._commit_overlay_calibration_draft()

    def _on_overlay_distance_step(self, delta: float) -> None:
        current = self._overlay_calibration.distance
        next_value = max(_OVERLAY_DISTANCE_MIN, min(_OVERLAY_DISTANCE_MAX, current + delta))
        self._apply_overlay_calibration_field_immediately("distance", round(next_value, 2))

    def _on_overlay_anchor_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(value=anchor, label=t(f"settings.overlay.calibration.anchor.{anchor}"))
            for anchor in OVERLAY_CALIBRATION_ANCHORS
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.calibration.anchor"),
            options,
            self._on_overlay_anchor_selected,
            show_description=False,
        )
        modal.open(self._overlay_calibration.anchor)

    def _on_overlay_anchor_selected(self, value: str) -> None:
        self._apply_overlay_calibration_field_immediately("anchor", value)

    def _on_overlay_offset_x_step(self, delta: float) -> None:
        current = self._overlay_calibration.offset_x
        self._apply_overlay_calibration_field_immediately("offset_x", current + delta)

    def _on_overlay_offset_y_step(self, delta: float) -> None:
        current = self._overlay_calibration.offset_y
        self._apply_overlay_calibration_field_immediately("offset_y", current + delta)

    def _on_overlay_text_scale_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(
                value=key,
                label=t(f"settings.overlay.calibration.text_scale.{key}"),
            )
            for key, _scale in _OVERLAY_TEXT_SCALE_PRESETS
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.calibration.text_scale"),
            options,
            self._on_overlay_text_scale_selected,
            show_description=False,
        )
        modal.open(self._overlay_text_scale_preset_key_for(self._overlay_calibration.text_scale))

    def _on_overlay_text_scale_selected(self, value: str) -> None:
        self._apply_overlay_calibration_field_immediately(
            "text_scale", self._overlay_text_scale_value_for(value)
        )

    def _on_overlay_position_reset(self, e) -> None:
        _ = e
        defaults = OverlayCalibration()
        for field_name in OverlayCalibration.__dataclass_fields__:
            self._update_overlay_calibration_draft(field_name, getattr(defaults, field_name))
        self._commit_overlay_calibration_draft()

    def _on_desktop_overlay_position_reset(self, e) -> None:
        _ = e
        if not self._settings or self._overlay_desktop_reset_button.disabled:
            return
        if self.on_desktop_overlay_position_reset:
            self._desktop_overlay_pending_position_reset = True
            self._desktop_overlay_captions_locked = False
            self._sync_desktop_overlay_main_controls()
            self.on_desktop_overlay_position_reset()
            return
        desktop_settings = self._settings.overlay.desktop_flet
        desktop_settings.position.x = None
        desktop_settings.position.y = None
        desktop_settings.locked = False
        desktop_settings.validate()
        self._desktop_overlay_captions_locked = False
        self._desktop_overlay_pending_position_reset = False
        self._sync_desktop_overlay_main_controls()
        self._emit_settings_changed()

    def sync_desktop_overlay_settings(self, settings: AppSettings) -> None:
        self._settings = settings
        self._desktop_overlay_pending_size_preset = None
        self._desktop_overlay_pending_position_reset = False
        self._desktop_overlay_pending_locked = None
        self._desktop_overlay_captions_locked = False
        if self._overlay_state == "off":
            self._overlay_runtime_target = self._current_overlay_target()
        self._sync_overlay_controls()

    def set_overlay_peer_contract(self, contract: OverlayPeerConsumerContract) -> None:
        self._overlay_peer_contract = contract
        if self._settings is not None:
            self._settings.ui.overlay_enabled = contract.overlay.intent_enabled
            self._settings.ui.peer_translation_enabled = contract.peer.intent_enabled
            self._update_api_visibility()
            if self.page:
                self._api_keys_column.update()
        self._sync_overlay_controls()

    def _sync_overlay_controls(self) -> None:
        overlay_translation_enabled = bool(
            self._settings and self._settings.overlay.show_translation
        )
        overlay_peer_original_enabled = bool(
            self._settings and self._settings.overlay.show_peer_original
        )
        integrated_context_enabled = bool(
            self._settings and self._settings.ui.integrated_context_enabled
        )

        self._set_unit_card_value_text(
            self._overlay_translation_button,
            t("settings.option.on" if overlay_translation_enabled else "settings.option.off"),
        )
        self._set_unit_card_value_text(
            self._overlay_peer_original_button,
            t("settings.option.on" if overlay_peer_original_enabled else "settings.option.off"),
        )
        self._set_unit_card_value_text(
            self._integrated_context_button,
            t(
                "settings.context.integrated"
                if integrated_context_enabled
                else "settings.context.local"
            ),
        )
        self._sync_overlay_target_control()
        self._sync_overlay_target_specific_visibility()
        self._sync_desktop_overlay_main_controls()
        self._sync_desktop_overlay_status_control()

        self._overlay_translation_button.disabled = self._settings is None
        self._overlay_peer_original_button.disabled = self._settings is None
        self._overlay_target_button.disabled = self._settings is None
        self._overlay_anchor_button.disabled = self._settings is None
        self._overlay_distance_decrease_button.disabled = self._settings is None
        self._overlay_distance_increase_button.disabled = self._settings is None
        self._overlay_offset_x_decrease_button.disabled = self._settings is None
        self._overlay_offset_x_increase_button.disabled = self._settings is None
        self._overlay_offset_y_decrease_button.disabled = self._settings is None
        self._overlay_offset_y_increase_button.disabled = self._settings is None
        self._desktop_overlay_background_alpha_decrease_button.disabled = self._settings is None
        self._desktop_overlay_background_alpha_increase_button.disabled = self._settings is None
        self._overlay_vr_reset_button.disabled = self._settings is None
        self._overlay_desktop_reset_button.disabled = self._settings is None
        self._integrated_context_button.disabled = self._settings is None
        self._integrated_context_hint.value = ""

        if self.page:
            self.update()

    def set_overlay_runtime_state(
        self,
        state: str,
        *,
        failure_reason: str | None = None,
        overlay_target: str | None = None,
        desktop_captions_locked: bool | None = None,
    ) -> None:
        self._overlay_state = state
        self._overlay_failure_reason = failure_reason
        if overlay_target is not None:
            self._overlay_runtime_target = self._normalized_overlay_target(overlay_target)
        elif state == "off":
            self._overlay_runtime_target = self._current_overlay_target()
        if desktop_captions_locked is not None:
            if self._desktop_overlay_runtime_lock_applies():
                self._desktop_overlay_pending_locked = None
                self._desktop_overlay_captions_locked = bool(desktop_captions_locked)
            else:
                self._desktop_overlay_pending_locked = None
                self._desktop_overlay_captions_locked = False
        self._sync_overlay_controls()

    def _on_overlay_calibration_reset(self, e) -> None:
        _ = e
        self._begin_overlay_calibration_session()
        self._overlay_calibration_draft = OverlayCalibration()
        self._sync_overlay_calibration_controls(self._overlay_calibration_draft)

        if self.page:
            self.update()

    def _on_overlay_translation_click(self, e) -> None:
        if not self._settings or self._overlay_translation_button.disabled:
            return
        next_value = "off" if self._settings.overlay.show_translation else "on"
        self._on_overlay_translation_selected(next_value)

    def _on_overlay_translation_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.overlay.show_translation = value == "on"
        self._sync_overlay_controls()
        self._emit_settings_changed()

    def _on_overlay_peer_original_click(self, e) -> None:
        if not self._settings or self._overlay_peer_original_button.disabled:
            return
        next_value = "off" if self._settings.overlay.show_peer_original else "on"
        self._on_overlay_peer_original_selected(next_value)

    def _on_overlay_peer_original_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.overlay.show_peer_original = value == "on"
        self._sync_overlay_controls()
        self._emit_settings_changed()

    def _on_integrated_context_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(value="off", label=t("settings.context.local")),
            OptionItem(
                value="on",
                label=t("settings.context.integrated"),
                description=t("settings.context.integrated_modal_helper"),
            ),
        ]
        modal = SettingsModal(
            self.page,
            t("settings.integrated_context"),
            options,
            self._on_integrated_context_selected,
            show_description=True,
        )
        modal.open("on" if self._settings.ui.integrated_context_enabled else "off")

    def _on_integrated_context_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.ui.integrated_context_enabled = value == "on"
        self._sync_overlay_controls()
        self._emit_settings_changed()

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

        self._settings.stt.vad_speech_threshold = new_vad
        self._emit_settings_changed()

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

        self._settings.desktop_audio.vad_speech_threshold = new_vad
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

        self._settings.desktop_audio.vad_speech_threshold = new_value
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

        self._settings.desktop_audio.vad_hangover_ms = new_value
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

        self._settings.desktop_audio.vad_pre_roll_ms = new_value
        self._peer_pre_roll_field.value = str(new_value)
        _update_control_if_mounted(self._peer_pre_roll_field)
        self._emit_settings_changed()

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

    def _on_clipboard_auto_translate_click(self, e) -> None:
        """Toggle clipboard auto-translate immediately from the unit card."""
        if not self._settings:
            return
        next_value = "off" if self._settings.ui.clipboard_auto_translate_enabled else "on"
        self._on_clipboard_auto_translate_selected(next_value)

    def _on_clipboard_auto_translate_selected(self, value: str) -> None:
        """Handle clipboard auto-translate selection result."""
        if not self._settings:
            return
        new_value = value == "on"
        self._emit_runtime_basic(f"[Settings] Clipboard auto translate toggled: {new_value}")
        self._settings.ui.clipboard_auto_translate_enabled = new_value
        self._clipboard_auto_translate_text.content.value = t(
            "settings.clipboard_auto_translate.on"
            if new_value
            else "settings.clipboard_auto_translate.off"
        )
        if self.page:
            self._clipboard_auto_translate_text.update()
        self._emit_settings_changed()

    def _on_low_latency_click(self, e) -> None:
        """Open low latency mode selection modal."""
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
        """Handle low latency mode selection from modal."""
        if not self._settings:
            return
        new_value = value == "on"
        old_value = self._settings.stt.low_latency_mode
        if new_value != old_value:
            self._emit_runtime_detailed(
                f"[Settings] Low latency mode changed: {old_value} -> {new_value}"
            )
        self._settings.stt.low_latency_mode = new_value

        # Update text
        self._low_latency_text.content.value = t("toggle.on" if new_value else "toggle.off")
        if self.page:
            self._low_latency_text.update()
        self._emit_settings_changed()

    def _on_prompt_change(self, value: str) -> None:
        self._stage_prompt_draft(value)

    def _on_prompt_commit(self, value: str) -> None:
        if not self.has_pending_prompt_changes and value == self._committed_prompt_value():
            return
        self._stage_prompt_draft(value)
        if self.has_provider_changes:
            return
        pending = self.consume_prompt_apply_settings()
        if pending is None:
            return
        self._emit_prompt_apply_settings(pending)

    def _on_reset_prompt(self, e) -> None:
        """Reset prompt to default for current provider."""
        if self._prompt_mode != "single":
            self._prompt_mode = "single"
            self._sync_prompt_mode_buttons()
            if self.page:
                self._prompt_mode_row.update()
        self._prompt_editor.load_default_prompt()
        self._on_prompt_commit(self._prompt_editor.value)

    def _on_prompt_mode_single(self, e) -> None:
        if self._prompt_mode == "single":
            return
        self._prompt_mode = "single"
        self._sync_prompt_mode_buttons()
        self._prompt_editor.load_default_prompt(emit_change=False)
        if self.page:
            self._prompt_mode_row.update()

    def _on_prompt_mode_dual(self, e) -> None:
        if self._prompt_mode == "dual":
            return
        self._prompt_mode = "dual"
        self._sync_prompt_mode_buttons()
        from puripuly_heart.config.prompts import get_dual_translation_prompt_template
        dual = get_dual_translation_prompt_template()
        self._prompt_editor.value = dual if dual else t("settings.prompt_mode.dual_not_found", default="Dual prompt template not found")
        if self.page:
            self._prompt_mode_row.update()

    def _sync_prompt_mode_buttons(self) -> None:
        is_single = self._prompt_mode == "single"
        self._prompt_single_btn.bgcolor = COLOR_PRIMARY if is_single else COLOR_SURFACE
        self._prompt_single_btn.border = ft.border.all(1, COLOR_PRIMARY if is_single else COLOR_DIVIDER)
        self._prompt_single_btn.content.color = ft.Colors.WHITE if is_single else COLOR_ON_BACKGROUND
        self._prompt_single_btn.content.weight = ft.FontWeight.BOLD if is_single else ft.FontWeight.NORMAL
        self._prompt_dual_btn.bgcolor = COLOR_PRIMARY if not is_single else COLOR_SURFACE
        self._prompt_dual_btn.border = ft.border.all(1, COLOR_PRIMARY if not is_single else COLOR_DIVIDER)
        self._prompt_dual_btn.content.color = ft.Colors.WHITE if not is_single else COLOR_ON_BACKGROUND
        self._prompt_dual_btn.content.weight = ft.FontWeight.BOLD if not is_single else ft.FontWeight.NORMAL

    def _show_custom_vocabulary_limit_snackbar(self) -> None:
        if self.show_snackbar:
            self.show_snackbar(
                t(
                    "snackbar.custom_vocabulary_limit",
                    max_terms=MAX_CUSTOM_VOCAB_TERMS,
                ),
                ft.Colors.ORANGE_700,
            )

    def _set_custom_vocabulary_terms_for_current_language(self, next_terms: list[str]) -> None:
        if not self._settings:
            return

        source_language = self._current_source_language()
        updated_terms = dict(self._settings.stt.custom_terms)
        current_terms = list(updated_terms.get(source_language, []))
        applied_terms = list(next_terms)
        updated_terms[source_language] = applied_terms
        next_enabled = any(bool(terms) for terms in updated_terms.values())

        if (
            current_terms == applied_terms
            and self._settings.stt.custom_vocabulary_enabled == next_enabled
        ):
            return

        self._settings.stt.custom_terms = updated_terms
        self._settings.stt.custom_vocabulary_enabled = next_enabled
        self._custom_vocab_tag_editor.set_terms(applied_terms)
        self._emit_runtime_detailed(
            f"[Settings] Custom vocabulary applied: language={source_language}, terms={len(applied_terms)}"
        )
        self._emit_settings_changed()

    def _on_custom_vocabulary_add_terms(self, raw_terms: list[str]) -> None:
        if not self._settings:
            return

        raw_values = [str(term) for term in raw_terms]
        if any(value != "" for value in raw_values):
            self._custom_vocab_tag_editor.clear_input()
        submitted_terms = self._normalize_custom_vocabulary_submitted_terms(raw_values)
        if not submitted_terms:
            return

        source_language = self._current_source_language()
        current_terms = list(self._settings.stt.custom_terms.get(source_language, []))
        next_terms = list(current_terms)
        seen_terms = set(current_terms)
        unique_requested_count = len(current_terms)
        cap_exceeded = False

        for term in submitted_terms:
            if term in seen_terms:
                continue
            seen_terms.add(term)
            unique_requested_count += 1
            if len(next_terms) >= MAX_CUSTOM_VOCAB_TERMS:
                cap_exceeded = True
                continue
            next_terms.append(term)

        updated_terms = dict(self._settings.stt.custom_terms)
        updated_terms[source_language] = list(next_terms)
        next_enabled = any(bool(terms) for terms in updated_terms.values())
        will_change = (
            current_terms != next_terms
            or self._settings.stt.custom_vocabulary_enabled != next_enabled
        )
        if cap_exceeded:
            if will_change:
                self._emit_runtime_detailed(
                    "[Settings] Custom vocabulary capped: "
                    f"language={source_language}, requested={unique_requested_count}, "
                    f"applied={MAX_CUSTOM_VOCAB_TERMS}"
                )
            self._show_custom_vocabulary_limit_snackbar()

        self._set_custom_vocabulary_terms_for_current_language(next_terms)

    def _on_custom_vocabulary_remove_term(self, term: str) -> None:
        if not self._settings:
            return

        source_language = self._current_source_language()
        current_terms = list(self._settings.stt.custom_terms.get(source_language, []))
        try:
            current_terms.remove(term)
        except ValueError:
            return
        self._set_custom_vocabulary_terms_for_current_language(current_terms)

    async def _verify_key(self, provider: str, key: str) -> tuple[bool, str]:
        """Verify API key."""
        if self.on_verify_api_key:
            result = await self.on_verify_api_key(provider, key)
            return result
        return False, "Verification not available"

    def _emit_settings_changed(self) -> None:
        if self._settings and self.on_settings_changed:
            self.on_settings_changed(
                self._sanitize_provider_apply_settings(
                    self._settings_with_desktop_overlay_runtime_state(self._settings)
                )
            )

    def _emit_prompt_apply_settings(self, settings: AppSettings) -> None:
        sanitized = self._sanitize_provider_apply_settings(settings)
        if sanitized is None:
            return
        if self.on_prompt_apply_settings:
            self.on_prompt_apply_settings(sanitized)
            return
        if self.on_settings_changed:
            self.on_settings_changed(sanitized)

    # --- Locale ---
    def apply_locale(self) -> None:
        """Update all labels when locale changes."""
        self._settings_subtab_shell.set_font_family(font_for_language(get_locale()))
        for key in _SETTINGS_SUBTAB_ORDER:
            self._settings_subtab_shell.set_tab_label(key, self._settings_subtab_label(key))

        # STT / PEER backend & quant labels
        self._stt_backend_label.value = t("settings.backend.label", default="Engine:")
        self._stt_quant_label.value = t("settings.quant.label", default="Quality:")
        self._peer_stt_backend_label.value = t("settings.backend.label", default="Engine:")
        self._peer_quant_label.value = t("settings.quant.label", default="Quality:")

        # Section titles
        self._stt_title.value = t("settings.section.stt")
        self._trans_title.value = t("settings.section.translation")
        self._stt_compute_label.value = t("settings.compute.label")
        self._peer_stt_compute_label.value = t("settings.compute.label")
        self._api_title.value = t("settings.section.api_keys")
        self._stt_provider_label.value = t("settings.self_stt_provider")
        self._translation_provider_label.value = t("settings.shared_translation_provider")
        self._api_credentials_helper_text.value = t("settings.api_credentials_helper")
        self._ui_title.value = t("settings.section.ui")
        self._audio_host_api_title.value = t("settings.audio_host_api")
        self._mic_audio_title.value = t("settings.section.microphone_audio")
        self._loopback_audio_title.value = t("settings.section.loopback_audio")
        self._self_vad_title.value = t("settings.section.self_vad_sensitivity")
        self._peer_vad_title.value = t("settings.section.peer_vad_sensitivity")
        self._microphone_test_title.value = t("settings.microphone_test")
        self._peer_vad_field.label = t("settings.vad.peer")
        self._peer_hangover_field.label = t("settings.vad.peer_hangover_ms")
        self._peer_pre_roll_field.label = t("settings.vad.peer_pre_roll_ms")
        self._low_latency_title.value = t("settings.low_latency_mode")
        self._translation_connection_title.value = t("settings.translation_connection")
        self._openrouter_fallback_title.value = t("settings.fallback")
        self._local_llm_connection_title.value = t("settings.local_llm.connection")
        self._local_llm_base_url.label = t("settings.local_llm.base_url")
        self._local_llm_model.label = t("settings.local_llm.model")
        self._local_llm_api_key.apply_locale()
        local_llm_api_key_description = t("settings.local_llm.api_key.description")
        self._local_llm_api_key_helper.value = local_llm_api_key_description
        self._local_llm_api_key_helper.visible = bool(local_llm_api_key_description.strip())
        self._local_llm_extra_body.label = t("settings.local_llm.extra_body")
        self._local_llm_extra_body_helper.value = t("settings.local_llm.extra_body.description")
        if self._local_llm_base_url.error_text:
            self._local_llm_base_url.error_text = t("settings.local_llm.base_url.invalid")
        if self._local_llm_model.error_text:
            self._local_llm_model.error_text = t("settings.local_llm.model.required")
        if self._local_llm_extra_body_error.visible:
            error_key = self._local_llm_extra_body_error_key
            error_kwargs = self._local_llm_extra_body_error_kwargs
            if error_key:
                message = self._local_llm_extra_body_error_message(error_key, **error_kwargs)
                self._local_llm_extra_body_error.value = message
                self._local_llm_extra_body.error_text = message
        self._persona_title.value = t("settings.section.persona")
        self._custom_vocab_title.value = t("settings.section.custom_vocabulary")
        self._vrc_mic_title.value = t("settings.vrc_mic_intercept")
        self._chatbox_source_title.value = t("settings.chatbox_include_source")
        self._clipboard_auto_translate_title.value = t("settings.clipboard_auto_translate")
        self._peer_provider_title.value = t("settings.section.peer_stt")
        self._dashboard_language_redirect_text.value = t("settings.dashboard_language_redirect")
        self._peer_stt_label.value = t("settings.peer_stt_provider")
        self._overlay_target_title.value = t("settings.overlay.caption_location")
        self._overlay_translation_title.value = t("settings.overlay.show_translation")
        self._overlay_peer_original_title.value = t("settings.overlay.show_peer_original")
        self._integrated_context_label.value = t("settings.integrated_context")
        self._audio_settings.apply_locale()
        self._sync_general_audio_card_texts()
        self._overlay_anchor_title.value = t("settings.overlay.calibration.anchor")
        self._overlay_distance_title.value = t("settings.overlay.calibration.distance")
        self._overlay_offset_x_title.value = t("settings.overlay.calibration.offset_x")
        self._overlay_offset_y_title.value = t("settings.overlay.calibration.offset_y")
        self._overlay_text_scale_title.value = t("settings.overlay.calibration.text_scale")
        self._overlay_vr_reset_title.value = t("settings.overlay.position_reset.vr.title")
        self._overlay_desktop_reset_title.value = t("settings.overlay.position_reset.desktop.title")
        self._desktop_overlay_size_title.value = t("settings.overlay.desktop.size.title")
        self._desktop_overlay_background_alpha_title.value = t(
            "settings.overlay.desktop.background_alpha.title"
        )
        self._desktop_overlay_lock_title.value = t("settings.overlay.desktop.lock.title")
        self._set_unit_card_value_text(
            self._overlay_vr_reset_button, t("settings.overlay.position_reset.action.vr")
        )
        self._set_unit_card_value_text(
            self._overlay_desktop_reset_button,
            t("settings.overlay.position_reset.action.desktop"),
        )
        _set_text_button_label(self._reset_prompt_btn, t("settings.reset_prompt"))
        self._sync_prompt_tab_copy()

        # Update dynamic buttons by replacing the entire style object
        ui_font = font_for_language(get_locale())
        display_settings = self._build_settings_with_provider_draft()

        if self._reset_prompt_btn:
            self._reset_prompt_btn.style = self._get_button_style(ui_font)

        if self._qwen_region_btn:
            self._qwen_region_btn.style = self._get_button_style(ui_font)
        self._sync_clickable_text_control_fonts(ui_font)
        for glyph_text in (
            getattr(self, "_overlay_distance_decrease_glyph", None),
            getattr(self, "_overlay_distance_increase_glyph", None),
            getattr(self, "_overlay_offset_x_decrease_glyph", None),
            getattr(self, "_overlay_offset_x_increase_glyph", None),
            getattr(self, "_overlay_offset_y_decrease_glyph", None),
            getattr(self, "_overlay_offset_y_increase_glyph", None),
            getattr(self, "_desktop_overlay_background_alpha_decrease_glyph", None),
            getattr(self, "_desktop_overlay_background_alpha_increase_glyph", None),
        ):
            if glyph_text:
                glyph_text.font_family = ui_font
                glyph_text.size = 22
        # Update text controls with current selection labels
        if display_settings:
            self._set_unit_card_value_text(
                self._stt_text,
                provider_label(display_settings.provider.stt.value),
            )
            self._set_unit_card_value_text(
                self._peer_stt_text,
                provider_label(self._effective_peer_stt_provider(display_settings).value),
            )
            self._set_unit_card_value_text(
                self._llm_text,
                self._get_llm_display_label(display_settings),
            )
            self._set_translation_connection_text(
                self._get_translation_connection_display_label(display_settings),
            )
            self._ui_text.content.value = locale_label(display_settings.ui.locale)
            self._low_latency_text.content.value = t(
                "toggle.on" if display_settings.stt.low_latency_mode else "toggle.off"
            )
            self._vrc_mic_text.content.value = t(
                "settings.vrc_mic.on"
                if display_settings.osc.vrc_mic_intercept
                else "settings.vrc_mic.off"
            )
            self._chatbox_source_text.content.value = t(
                "settings.chatbox_source.on"
                if display_settings.osc.chatbox_include_source
                else "settings.chatbox_source.off"
            )
            self._clipboard_auto_translate_text.content.value = t(
                "settings.clipboard_auto_translate.on"
                if display_settings.ui.clipboard_auto_translate_enabled
                else "settings.clipboard_auto_translate.off"
            )
            self._set_unit_card_value_text(
                self._microphone_test_text,
                t("settings.microphone_test.action"),
            )
            self._sync_overlay_controls()
            self._sync_overlay_calibration_controls()

        # Qwen Region label
        if display_settings:
            region_val = display_settings.qwen.region.value
            _set_text_button_label(
                self._qwen_region_btn,
                f"{t('settings.qwen_region')} {t(f'region.{region_val}')}",
            )

        # Components
        self._audio_settings.apply_locale()
        self._prompt_editor.apply_locale()

        if self.page:
            self.update()

    def refresh_prompt_if_empty(self) -> None:
        """Load default prompt if current is empty."""
        was_empty = not self._prompt_editor.value.strip()
        self._prompt_editor.load_default_if_empty()
        if was_empty and self._prompt_editor.value.strip():
            if self._prompt_editor.value != self._committed_prompt_value():
                self._stage_prompt_draft(self._prompt_editor.value)
