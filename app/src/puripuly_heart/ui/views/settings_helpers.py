"""Settings helpers mixin — UI builders, parsers, locale helpers."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.ui.components.settings import (
    SettingsUnitCard,
    OptionItem,
    SettingsModal,
)
from puripuly_heart.ui.components.shared_card_wrapper import SharedCardWrapper
from puripuly_heart.ui.components.subtab_shell import TextSubtab, TextSubtabShell
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.domain.i18n import (
    get_locale,
    provider_label,
    t,
)
from puripuly_heart.domain.overlay_calibration import (
    OVERLAY_CALIBRATION_ANCHORS,
)
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
)

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings

logger = logging.getLogger(__name__)

# ── Module constants (used only by helpers) ──────────────────────────────

_CJK_START = 0x3000
_CENTER_ALIGNMENT = ft.alignment.Alignment(0, 0)
_CENTER_RIGHT_ALIGNMENT = ft.alignment.Alignment(1, 0)
_SETTINGS_SUBTAB_ORDER = ("api", "general", "prompt", "overlay")
_CUSTOM_VOCAB_DELIMITER_RE = re.compile(r"\s+")


# ── Module-level helpers ──────────────────────────────────────────────────

def _make_text_button(label: str, **kwargs) -> ft.TextButton:
    return ft.TextButton(text=label, **kwargs)


def _set_text_button_label(button: ft.TextButton, label: str) -> None:
    button.text = label


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


# ── Mixin class ───────────────────────────────────────────────────────────

# SHARED INFRASTRUCTURE — provides UI primitives used by ALL other mixins:
#   _wrap_card, _wrap_unit_card, _wrap_empty_unit_card (card wrappers)
#   _build_clickable_text, _set_unit_card_value_text (text controls)
#   _build_overlay_step_split_layout (overlay calibration step controls)
#   _build_settings_subtab_shell (4-tab navigation)
#   _emit_runtime_basic/detailed (logging)
#   _sync_general_audio_card_texts (audio section display)
#   _sync_prompt_tab_copy (prompt tab label sync)
#   _sync_custom_vocabulary_editor_from_settings (vocab editor sync)
#   _on_fallback_status_click/selected (backup translation mode toggle)
#
# MRO POSITION: SettingsHelpersMixin is FIRST in the class hierarchy of SettingsView.
# Its methods are available to all other mixins via self.* because Python MRO
# resolves to the first class in the MRO that defines a method.

class SettingsHelpersMixin:
    """UI builder and helper methods."""

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

    # --- Quant-style toggle button ---
    def _make_quant_button(self, label: str, on_click) -> ft.Container:
        return ft.Container(
            content=ft.Text(label, size=14, color=COLOR_ON_BACKGROUND),
            bgcolor=COLOR_SURFACE,
            border=ft.border.all(1, COLOR_DIVIDER),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=on_click,
        )

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
        """Aggregate locale-sensitive controls from all section mixins.

        Each section mixin defines _locale_sensitive_controls() returning its
        own controls.  Since all mixins share one class via MRO, we call each
        mixin's implementation explicitly by class name.
        """
        from puripuly_heart.ui.views.stt_section import SttSectionMixin
        from puripuly_heart.ui.views.llm_section import LlmSectionMixin
        from puripuly_heart.ui.views.ui_section import UiSectionMixin
        from puripuly_heart.ui.views.osc_section import OscSectionMixin
        from puripuly_heart.ui.views.audio_section import AudioSectionMixin
        from puripuly_heart.ui.views.context_section import ContextSectionMixin
        from puripuly_heart.ui.views.overlay_section import OverlaySectionMixin
        from puripuly_heart.ui.views.calibration_section import CalibrationSectionMixin

        controls: list[ft.Container] = []
        for mixin_cls in (
            ContextSectionMixin,
            SttSectionMixin,
            LlmSectionMixin,
            UiSectionMixin,
            OscSectionMixin,
            AudioSectionMixin,
            OverlaySectionMixin,
            CalibrationSectionMixin,
        ):
            controls.extend(mixin_cls._locale_sensitive_controls(self))
        return tuple(controls)

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

    def _on_stub_click(self, e) -> None:
        if not self.page:
            return
        modal = SettingsModal(
            self.page,
            "Stub",
            [OptionItem(value="stub", label="Stub")],
            lambda value: None,
            show_description=False,
        )
        modal.open("stub")

    def _on_fallback_status_click(self, e) -> None:
        if not self.page:
            return
        from puripuly_heart.domain.providers import LLMProviderName
        display_settings = self._build_settings_with_provider_draft()
        bt = display_settings.backup_translation if display_settings else None
        if bt and bt.enabled:
            current_key = bt.mode.value
        else:
            current_key = "_disabled"
        options = [
            OptionItem(value="_disabled", label=t("option.disabled")),
            OptionItem(
                value="local_llm",
                label=t("provider.local_llms"),
                description=t("settings.translation_model.local_llm.description", default=""),
            ),
            OptionItem(
                value="openai_compatible",
                label=t("provider.openai_compatible"),
                description=t("settings.translation_model.openai_compatible.description", default=""),
            ),
        ]
        modal = SettingsModal(
            self.page,
            t("settings.backup_translation"),
            options,
            self._on_fallback_status_selected,
            show_description=True,
        )
        modal.open(current_key)

    def _on_fallback_status_selected(self, value: str) -> None:
        if not self._settings:
            return
        from puripuly_heart.app.services.settings_manager import load_providers
        from puripuly_heart.domain.providers import LLMProviderName
        # Commit current fallback fields before switching mode
        self._commit_fallback_fields_from_controls()
        self._commit_fallback_local_llm_fields_from_controls()
        draft = self._ensure_provider_settings_draft()
        if value == "_disabled":
            draft.backup_translation.enabled = False
            self._set_unit_card_value_text(self._fallback_status_text, t("option.disabled"))
        elif value == "local_llm":
            draft.backup_translation.enabled = True
            draft.backup_translation.mode = LLMProviderName.LOCAL_LLM
            # Sync fallback local_llm card controls
            self._fallback_local_llm_base_url.value = draft.backup_translation.local_llm.base_url
            self._fallback_local_llm_base_url.error_text = None
            self._fallback_local_llm_model.value = draft.backup_translation.local_llm.model or ""
            self._fallback_local_llm_model.error_text = None
            self._fallback_local_llm_extra_body.value = (
                json.dumps(draft.backup_translation.local_llm.extra_body, ensure_ascii=False, indent=2)
                if draft.backup_translation.local_llm.extra_body else ""
            )
            self._fallback_local_llm_extra_body_error.visible = False
            self._set_unit_card_value_text(self._fallback_status_text, t("provider.local_llms"))
        else:
            providers = load_providers()
            draft.backup_translation.enabled = True
            draft.backup_translation.mode = LLMProviderName.OPENAI_COMPATIBLE
            if not draft.backup_translation.openai_compatible.base_url:
                first = next(iter(providers.values()), None)
                if first:
                    draft.backup_translation.openai_compatible.base_url = first.get("base_url", "")
            # Sync fallback card controls from draft
            self._fallback_openai_base_url.value = draft.backup_translation.openai_compatible.base_url
            self._fallback_openai_base_url.error_text = None
            self._fallback_openai_model.value = draft.backup_translation.openai_compatible.model or ""
            _fb_opts = self._fallback_openai_provider.options or []
            _fb_matched = _fb_opts[0].key if _fb_opts else None
            for _pk, _pi in providers.items():
                if _pi.get("base_url") == draft.backup_translation.openai_compatible.base_url:
                    _fb_matched = _pk
                    break
            self._fallback_openai_provider.value = _fb_matched
            self._set_unit_card_value_text(self._fallback_status_text, t("provider.openai_compatible"))
        self.has_provider_changes = True
        _update_control_if_mounted(self._fallback_status_card)
        self._update_api_visibility(draft)
