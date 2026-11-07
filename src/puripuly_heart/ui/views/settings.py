"""Settings view - Bento grid layout with SegmentedButton providers."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
import json
import logging
import math
import re
from dataclasses import fields, replace
from pathlib import Path
from typing import Awaitable, Callable, Mapping

import flet as ft

from puripuly_heart.app.ports.settings_secrets import (
    SettingsSecretKey,
    SettingsSecretLoadResult,
    SettingsSecretMutation,
    SettingsSecretSnapshot,
    SettingsSecretsPort,
)
from puripuly_heart.app.ports.settings_view import (
    ActivationNoticeSettingsIntent,
    AudioInputSettingsIntent,
    AudioSettingsIntent,
    ChatboxSourceSettingsIntent,
    ClipboardSettingsIntent,
    CustomSttEndpointEdit,
    CustomSttExtraEdit,
    CustomSttModelEdit,
    CustomVocabularySettingsIntent,
    DesktopAudioOutputSettingsIntent,
    DesktopOverlayBackgroundAlphaIntent,
    DesktopOverlayPositionResetIntent,
    DesktopOverlaySizeIntent,
    DesktopOverlaySwapCaptionLanguagesIntent,
    GeneralSettingsSnapshot,
    ImmediateSettingsIntent,
    LlmGpuDeviceEdit,
    LocaleSettingsIntent,
    LocalLlmBaseUrlEdit,
    LocalLlmExtraBodyEdit,
    LocalLlmModelEdit,
    OpenRouterPkceTarget,
    OscConnectionSettingsIntent,
    OverlayCalibrationSettingsIntent,
    OverlayCalibrationSnapshot,
    OverlayPeerOriginalSettingsIntent,
    OverlaySettingsSnapshot,
    OverlayTargetSettingsIntent,
    OverlayTranslationSettingsIntent,
    PeerExpectedLanguagesIntent,
    PeerSttGpuBackendEdit,
    PeerSttGpuDeviceEdit,
    PeerSttProviderEdit,
    PeerVadSpeechThresholdIntent,
    PromptApplyIntent,
    PromptSettingsSnapshot,
    ProviderApplyIntent,
    ProviderSettingsEdit,
    ProviderSettingsSnapshot,
    QwenBeijingApiHostEdit,
    QwenRegionEdit,
    QwenSingaporeApiHostEdit,
    SelfSttProviderEdit,
    SelfVadSettingsIntent,
    SttGpuBackendEdit,
    SttGpuDeviceEdit,
    SttLocalModelEdit,
    SttSharedEngineEdit,
    OpenAICompatibleProviderKeyEdit,
    OpenAICompatibleBaseUrlEdit,
    OpenAICompatibleModelEdit,
    BackupTranslationEnabledEdit,
    BackupVariantEdit,
    BackupOpenAICompatibleProviderKeyEdit,
    BackupOpenAICompatibleBaseUrlEdit,
    BackupOpenAICompatibleModelEdit,
    BackupLocalLlmBaseUrlEdit,
    BackupLocalLlmModelEdit,
    BackupLocalLlmExtraBodyEdit,
    SystemPromptEdit,
    TranslationHttpExtensionEdit,
    TranslationSelectionEdit,
    TranslationSelectionSnapshot,
    VrcMicInterceptSettingsIntent,
)
from puripuly_heart.app.ports.ui_models import OscControlPresentationState
from puripuly_heart.app.services.http_extension_registry import (
    HttpExtensionRegistryService,
)
from puripuly_heart.app.services.local_asr.local_asr_selection import resolve_local_asr_selection
from puripuly_heart.config.alibaba_connection import workspace_api_host_region
from puripuly_heart.config.desktop_overlay_values import (
    DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
    DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER,
    DESKTOP_FLET_SIZE_PRESET_ORDER,
)
from puripuly_heart.config.overlay_calibration import (
    OVERLAY_CALIBRATION_ANCHORS,
    OverlayCalibration,
)
from puripuly_heart.config.prompts import load_prompt_for_provider
from puripuly_heart.config.provider_values import (
    LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS,
    LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS,
    MAX_CUSTOM_VOCAB_TERMS,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterSelectionAlias,
    QwenRegion,
    STTProviderName,
    display_stt_provider,
    is_custom_stt_provider,
    is_qwen_cloud_stt_provider,
    normalize_local_llm_base_url,
    parse_openrouter_llm_model,
)
from puripuly_heart.config.resolved import (
    OVERLAY_TARGET_DESKTOP,
    OVERLAY_TARGET_STEAMVR,
    VAD_ONSET_MAX,
    VAD_ONSET_MIN,
)
from puripuly_heart.config.translation_values import (
    MANAGED_GEMMA_MODELS,
    TranslationConnection,
    TranslationModel,
    default_translation_connection,
    normalize_translation_connection,
    normalize_translation_model,
    supported_translation_connections,
)
from puripuly_heart.core.http_extensions import http_extension_secret_key
from puripuly_heart.core.language import get_stt_compatibility_warning
from puripuly_heart.core.local_asr.local_stt_assets import LOCAL_STT_MODEL_IDS
from puripuly_heart.core.stt.custom import (
    CustomSTTConfigurationError,
    normalize_custom_stt_extra,
)
from puripuly_heart.ui.components.settings import (
    ApiKeyField,
    AudioSettings,
    CustomVocabularyTagEditor,
    LanguageHintEditor,
    OptionItem,
    OscConnectionModal,
    PromptEditor,
    SettingsModal,
    SettingsUnitCard,
)
from puripuly_heart.core.llm.model_discovery import (
    fetch_models as _fetch_models_default,
    test_connection as _test_connection_default,
)
from puripuly_heart.ui.components.settings.stt_engine_modal import (
    ENGINE_BACKENDS,
    SttEngineModal,
    build_backend_toggle,
)
from puripuly_heart.ui.components.shared_card_wrapper import SharedCardWrapper
from puripuly_heart.ui.components.subtab_shell import TextSubtab, TextSubtabShell
from puripuly_heart.ui.flet_runtime import (
    is_control_mounted,
    is_hover_active,
    update_control_if_mounted,
)
from puripuly_heart.ui.fonts import TypeFace, font_for_language, locale_body_letter_spacing
from puripuly_heart.ui.gpu_device import GpuDeviceOption
from puripuly_heart.ui.i18n import (
    available_locales,
    get_locale,
    language_name,
    locale_label,
    provider_label,
    t,
)
from puripuly_heart.ui.overlay_peer_contract import OverlayPeerConsumerContract
from puripuly_heart.ui.settings.contract import (
    SettingsApiSurfaceSlots,
    SettingsGeneralIntents,
    SettingsGeneralSurfaceSlots,
    SettingsOverlayIntents,
    SettingsOverlaySurfaceSlots,
    SettingsPromptIntents,
    SettingsPromptSurfaceSlots,
    SettingsProviderIntents,
    SettingsSurfaceIntents,
)
from puripuly_heart.ui.settings.renderer import (
    SETTINGS_ROW_SPACING,
    compose_settings_api_surface,
    compose_settings_general_surface,
    compose_settings_overlay_surface,
    compose_settings_prompt_surface,
)
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_ERROR,
    COLOR_NEUTRAL_DARK,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SECONDARY,
    COLOR_WARNING,
    text_field_outline_border,
    text_style,
)

logger = logging.getLogger(__name__)
_DRAFT_PROVIDER_FIELDS: dict[type, str] = {
    SelfSttProviderEdit: "stt_provider",
    PeerSttProviderEdit: "peer_stt_provider",
    SttGpuDeviceEdit: "stt_gpu_device_id",
    SttGpuBackendEdit: "stt_gpu_backend",
    SttSharedEngineEdit: "stt_shared_engine",
    PeerSttGpuDeviceEdit: "peer_stt_gpu_device_id",
    PeerSttGpuBackendEdit: "peer_stt_gpu_backend",
    SttLocalModelEdit: "stt_local_model",
    OpenAICompatibleProviderKeyEdit: "openai_compatible_provider_key",
    OpenAICompatibleBaseUrlEdit: "openai_compatible_base_url",
    OpenAICompatibleModelEdit: "openai_compatible_model",
    BackupTranslationEnabledEdit: "backup_enabled",
    BackupVariantEdit: "backup_variant",
    BackupOpenAICompatibleProviderKeyEdit: "backup_provider_key",
    BackupOpenAICompatibleBaseUrlEdit: "backup_base_url",
    BackupOpenAICompatibleModelEdit: "backup_model",
    BackupLocalLlmBaseUrlEdit: "backup_local_base_url",
    BackupLocalLlmModelEdit: "backup_local_model",
    BackupLocalLlmExtraBodyEdit: "backup_local_extra_body_json",
    QwenRegionEdit: "qwen_region",
    QwenBeijingApiHostEdit: "qwen_api_host_beijing",
    QwenSingaporeApiHostEdit: "qwen_api_host_singapore",
    LocalLlmBaseUrlEdit: "local_llm_base_url",
    LocalLlmModelEdit: "local_llm_model",
    LocalLlmExtraBodyEdit: "local_llm_extra_body_json",
    CustomSttEndpointEdit: "custom_stt_endpoint",
    CustomSttModelEdit: "custom_stt_model",
    CustomSttExtraEdit: "custom_stt_extra_json",
    TranslationSelectionEdit: "translation",
}
_DRAFT_TRANSLATION_FIELDS: dict[type, str] = {
    LlmGpuDeviceEdit: "gpu_device_id",
    TranslationHttpExtensionEdit: "http_extension_id",
}


_CENTER_ALIGNMENT = ft.Alignment(0, 0)
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
_DESKTOP_OVERLAY_REOPEN_FAILURE_REASONS = frozenset(
    {
        "window_configuration_failed",
        "window_reveal_lost",
        "window_visibility_unstable",
    }
)
_CUSTOM_VOCAB_DELIMITER_RE = re.compile(r"\s+")
_STT_UI_PROVIDERS = (
    STTProviderName.LOCAL_QWEN_GPU,
)
_STT_SECTION_ORDER = (
    "settings.stt.section.gpu_inference",
)
_ROLLING_MEMBER_STT_PROVIDERS: frozenset[STTProviderName] = frozenset()
_STT_SECTION_BY_PROVIDER: dict[STTProviderName, str] = {
    STTProviderName.LOCAL_QWEN_GPU: "settings.stt.section.gpu_inference",
}
_TRANSLATION_MODEL_LABEL_KEYS = {
    TranslationModel.LOCAL_LLM: "settings.translation_variant.local_llm",
    TranslationModel.CUSTOM_HTTP: "provider.custom_http",
    TranslationModel.OPENAI_COMPATIBLE: "settings.translation_variant.openai_compatible",
}
_TRANSLATION_MODEL_DESCRIPTION_KEYS = {
    TranslationModel.LOCAL_LLM: "settings.translation_variant.local_llm.description",
    TranslationModel.OPENAI_COMPATIBLE: "settings.translation_variant.openai_compatible.description",
}
_LOCAL_STT_PROVIDERS = frozenset(
    {
        STTProviderName.LOCAL_QWEN_GPU,
    }
)
_TRANSLATION_CONNECTION_LABEL_KEYS = {
    TranslationConnection.OLLAMA: "settings.translation_connection.ollama",
    TranslationConnection.CUSTOM_HTTP: "settings.translation_connection.custom_http",
}
_TRANSLATION_CONNECTION_DESCRIPTION_KEYS: dict[TranslationConnection, str] = {}
_TRANSLATION_MODELS = (
    TranslationModel.LOCAL_LLM,
    TranslationModel.CUSTOM_HTTP,
)
_TRANSLATION_MODEL_SECTION_ORDER = (
    "settings.translation_model.section.user_settings",
)
_TRANSLATION_MODEL_SECTION_BY_MODEL: dict[TranslationModel, str] = {
    TranslationModel.LOCAL_LLM: "settings.translation_model.section.user_settings",
    TranslationModel.CUSTOM_HTTP: "settings.translation_model.section.user_settings",
}

def _make_text_button(label: str, **kwargs) -> ft.TextButton:
    return ft.TextButton(content=label, **kwargs)


def _settings_secondary_text_button_style() -> ft.ButtonStyle:
    return ft.ButtonStyle(
        color={
            ft.ControlState.HOVERED: COLOR_PRIMARY,
            ft.ControlState.DEFAULT: COLOR_SECONDARY,
        },
        icon_color={
            ft.ControlState.HOVERED: COLOR_PRIMARY,
            ft.ControlState.DEFAULT: COLOR_SECONDARY,
        },
        text_style=text_style(size=20),
        overlay_color=ft.Colors.TRANSPARENT,
        animation_duration=0,
    )


def _set_text_button_label(button: ft.TextButton, label: str) -> None:
    button.content = label


def _reject_json_constant(value: str) -> None:
    raise json.JSONDecodeError(f"invalid JSON constant: {value}", value, 0)


def _custom_stt_extra_to_text(extra: Mapping[str, object]) -> str:
    if not extra:
        return "{}"
    return json.dumps(extra, ensure_ascii=False, indent=2)


def _update_control_if_mounted(control: ft.Control) -> None:
    update_control_if_mounted(control)


_VALUE_TEXT_MAX_WIDTH = 232


def _value_char_width(char: str) -> float:
    # Approximate glyph advance widths in em units for the UI font.
    if char == " ":
        return 0.30
    if char in "ijlt.,:;'!|()[]{}-/\\\"`":
        return 0.32
    if char in "mwMW@%&":
        return 0.80
    if ord(char) >= 0x2E80:
        return 1.05
    return 0.62


def _value_word_width(word: str) -> float:
    return sum(_value_char_width(char) for char in word)


def _value_line_count(words: list[str], size: int) -> int:
    lines = 1
    current = 0.0
    for word in words:
        width = _value_word_width(word) * size
        if current and current + 0.30 * size + width > _VALUE_TEXT_MAX_WIDTH:
            lines += 1
            current = width
        else:
            current += (0.30 * size if current else 0.0) + width
    return lines


def _scaled_value_text_size(text: str, base: int = 28, max_lines: int = 1) -> int:
    """Largest size up to base at which the value fits within max_lines lines."""
    words = text.split()
    if not words:
        return base
    widest = max(_value_word_width(word) for word in words)
    for size in range(base, 13, -1):
        if widest * size > _VALUE_TEXT_MAX_WIDTH:
            continue
        if _value_line_count(words, size) <= max_lines:
            return size
    return 14


class SettingsView(ft.Column):
    """Settings view with Bento grid layout."""

    def __init__(
        self,
        http_extension_registry: HttpExtensionRegistryService | None = None,
    ):
        super().__init__(expand=True, spacing=16)

        # Callbacks (assigned by App)
        self.on_settings_changed: Callable[[ImmediateSettingsIntent], None] | None = None
        self.on_prompt_apply_settings: Callable[[PromptApplyIntent], None] | None = None
        self.on_pending_prompt_changed: Callable[[PromptApplyIntent], None] | None = None
        self.on_providers_changed: Callable[[], None] | None = None
        self.on_local_llm_secret_changed: Callable[[], None] | None = None
        self.on_fetch_models = None
        self.on_test_connection = None
        self.on_verify_api_key: Callable[[str, str], object] | None = None
        self.on_provider_secret_change: Callable[[str, str], object] | None = None
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
        self.on_start_audio_test: Callable[[], None] | None = None
        self.on_gpu_discovery_requested: Callable[[], object] | None = None
        self.on_open_model_assets: Callable[[], None] | None = None
        self.on_list_loopback_capture_options: Callable[[], object] | None = None
        self.on_list_loopback_process_options: Callable[[], object] | None = None
        self.on_list_loopback_device_options: Callable[[], object] | None = None
        self.on_current_loopback_capture_option: Callable[[], str] | None = None
        self.on_apply_loopback_capture_option: Callable[[str], None] | None = None
        self.on_loopback_capture_summary: Callable[[], str] | None = None
        self.on_osc_effective_ports: Callable[[], tuple[int | None, int | None]] | None = None
        self.show_snackbar: Callable[[str, str], None] | None = None
        self.runtime_log_basic: Callable[..., None] | None = None
        self._settings_secrets: SettingsSecretsPort | None = None

        self._http_extensions = (
            http_extension_registry
            if http_extension_registry is not None
            else HttpExtensionRegistryService.from_default_directory()
        )
        self._http_extension_secret_fields: dict[str, ft.TextField] = {}
        self._http_extension_secret_dirty: set[str] = set()
        self._http_extension_selected_id: str | None = None
        self._http_extension_snapshot = self._http_extensions.snapshot
        self._http_extension_runtime_reload_pending = False
        self._immediate_provider_apply_intent: ProviderApplyIntent | None = None

        # State
        self._provider_snapshot: ProviderSettingsSnapshot | None = None
        self._provider_draft: ProviderSettingsSnapshot | None = None
        self._provider_edits: dict[type, ProviderSettingsEdit] = {}
        self._general_snapshot: GeneralSettingsSnapshot | None = None
        self._peer_auto_languages_pending: tuple[str, ...] | None = None
        self._prompt_snapshot: PromptSettingsSnapshot | None = None
        self._overlay_snapshot: OverlaySettingsSnapshot | None = None
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
        self._gpu_devices: tuple[GpuDeviceOption, ...] = ()
        self._llm_gpu_devices: tuple[GpuDeviceOption, ...] = ()

        # Build UI components
        self._build_ui()

    def self_stt_control(self) -> ft.Control:
        return self._self_stt_card

    def peer_stt_control(self) -> ft.Control:
        return self._peer_stt_card

    def translation_provider_control(self) -> ft.Control:
        return self._translation_provider_card

    def translation_connection_control(self) -> ft.Control:
        return self._translation_connection_card

    def http_extension_control(self) -> ft.Control:
        return self._http_extension_host

    def set_http_extension_registry(
        self,
        registry: HttpExtensionRegistryService | None,
    ) -> None:
        if registry is None:
            return
        self._http_extensions = registry
        self._http_extension_snapshot = registry.snapshot
        self._sync_http_extension_card(force_credentials=True)

    def gpu_device_control(self) -> ft.Control:
        return self._gpu_device_card

    def peer_gpu_device_control(self) -> ft.Control:
        return self._peer_gpu_device_card

    def shared_engine_control(self) -> ft.Control:
        return self._shared_engine_card

    def stt_model_control(self) -> ft.Control:
        return self._stt_model_card

    def gpu_llm_control(self) -> ft.Control:
        return self._gpu_llm_card

    def model_assets_control(self) -> ft.Control:
        return self._model_assets_card

    def local_llm_connection_control(self) -> ft.Control:
        return self._local_llm_connection_card

    def backup_translation_control(self) -> ft.Control:
        return self._backup_translation_card

    def openai_compatible_config_control(self) -> ft.Control:
        return self._openai_compatible_card

    def backup_translation_config_control(self) -> ft.Control:
        return self._backup_config_card

    def bind_settings_intents(
        self,
        *,
        surface: SettingsSurfaceIntents,
        provider: SettingsProviderIntents,
        general: SettingsGeneralIntents,
        prompt: SettingsPromptIntents,
        overlay: SettingsOverlayIntents,
    ) -> None:
        self.on_settings_changed = surface.settings_changed
        self.show_snackbar = surface.show_snackbar
        if surface.runtime_log_basic is not None:
            self.runtime_log_basic = surface.runtime_log_basic
        self.on_providers_changed = provider.providers_changed
        self.on_verify_api_key = provider.verify_api_key
        self.on_provider_secret_change = provider.provider_secret_change
        self.on_secret_cleared = provider.secret_cleared
        self.on_local_llm_secret_changed = provider.local_llm_secret_changed
        self.on_fetch_models = provider.fetch_models or _fetch_models_default
        self.on_test_connection = provider.test_connection or _test_connection_default
        self.on_gpu_discovery_requested = provider.gpu_discovery_requested
        self.on_open_model_assets = provider.open_model_assets
        self._settings_secrets = provider.settings_secrets
        self.on_start_audio_test = general.start_audio_test
        self.on_list_loopback_capture_options = general.list_loopback_capture_options
        self.on_list_loopback_process_options = general.list_loopback_process_options
        self.on_list_loopback_device_options = general.list_loopback_device_options
        self.on_current_loopback_capture_option = general.current_loopback_capture_option
        self.on_apply_loopback_capture_option = general.apply_loopback_capture_option
        self.on_loopback_capture_summary = general.loopback_capture_summary
        self.on_osc_effective_ports = general.osc_effective_ports
        self.on_prompt_apply_settings = prompt.prompt_apply_settings
        self.on_pending_prompt_changed = prompt.pending_prompt_changed
        self.on_desktop_overlay_lock_change = overlay.desktop_overlay_lock_change
        self.on_desktop_overlay_size_change = overlay.desktop_overlay_size_change
        self.on_desktop_overlay_recovery_action = overlay.desktop_overlay_recovery_action
        self.on_desktop_overlay_position_reset = overlay.desktop_overlay_position_reset
        self.on_view_logs = overlay.view_logs
        if overlay.calibration_begin is not None:
            self.on_overlay_calibration_begin = overlay.calibration_begin
        if overlay.calibration_change is not None:
            self.on_overlay_calibration_change = overlay.calibration_change
        if overlay.calibration_apply is not None:
            self.on_overlay_calibration_apply = overlay.calibration_apply
        if overlay.calibration_cancel is not None:
            self.on_overlay_calibration_cancel = overlay.calibration_cancel

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
        card = self._wrap_card(ft.Container(expand=True), expand=True, height=height)
        card.ignore_interactions = True
        return card

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

    def _build_provider_dropdown(self, on_select) -> ft.Dropdown:
        from puripuly_heart.config.providers_registry import load_provider_presets

        options = [
            ft.dropdown.Option(key=key, text=preset.label)
            for key, preset in load_provider_presets().items()
        ]
        return ft.Dropdown(
            label=t("settings.openai_compatible.provider"),
            options=options,
            value=options[0].key if options else None,
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_select=on_select,
        )

    def _compose_openai_form(
        self,
        *,
        title,
        provider,
        model,
        fetch_btn,
        api_key,
        test_btn,
    ) -> ft.Column:
        items: list[ft.Control] = []
        if title is not None:
            items.append(title)
        items.extend(
            [
                ft.Container(height=4),
                provider,
                ft.Row([model, fetch_btn], spacing=4),
                api_key,
                test_btn,
            ]
        )
        return ft.Column(items, spacing=8)

    def _compose_local_form(
        self,
        *,
        title,
        base_url,
        test_btn,
        model,
        fetch_btn,
        api_key,
        api_key_helper,
        extra_body,
        extra_body_helper,
        extra_body_error,
    ) -> ft.Column:
        items: list[ft.Control] = []
        if title is not None:
            items.append(title)
        items.extend(
            [
                ft.Container(height=4),
                ft.Row([base_url, test_btn], spacing=4),
                ft.Row([model, fetch_btn], spacing=4),
                api_key,
                api_key_helper,
                extra_body,
                extra_body_helper,
                extra_body_error,
            ]
        )
        return ft.Column(items, spacing=8)

    def _build_openai_form_controls(self) -> None:
        """Create the translation OpenAI-compatible form controls."""
        self._openai_compatible_title = ft.Text(
            t("settings.translation.config_title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._openai_provider = self._build_provider_dropdown(self._on_openai_provider_change)
        self._openai_base_url = ft.TextField(
            label=t("settings.openai_compatible.base_url"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            visible=False,
            on_blur=self._on_openai_base_url_change_end,
            on_submit=self._on_openai_base_url_change_end,
        )
        self._openai_model = ft.TextField(
            label=t("settings.openai_compatible.model"),
            hint_text=t("settings.openai_compatible.model_hint"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_blur=self._on_openai_model_change_end,
            on_submit=self._on_openai_model_change_end,
        )
        self._openai_api_key = ApiKeyField(
            "settings.openai_compatible.api_key",
            "openai_compatible_api_key",
            "openai_compatible",
            on_save=self._on_openai_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
            show_status=False,
        )
        self._openai_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            tooltip=t("settings.openai_compatible.fetch_models"),
            on_click=self._on_fetch_openai_models,
        )
        self._openai_test_btn = ft.TextButton(
            content=ft.Text(t("settings.openai_compatible.test_connection")),
            on_click=self._on_test_openai_connection,
        )

    def _build_local_form_controls(self) -> None:
        """Create the translation custom-server form controls."""
        self._local_llm_connection_title = ft.Text(
            t("settings.translation.config_title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
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
        self._local_llm_api_key_helper = ft.Text(
            t("settings.local_llm.api_key.description"),
            size=15,
            color=COLOR_SECONDARY,
        )
        self._local_llm_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            tooltip=t("settings.openai_compatible.fetch_models"),
            on_click=self._on_fetch_local_llm_models,
        )
        self._local_llm_test_btn = ft.TextButton(
            content=ft.Text(t("settings.openai_compatible.test_connection")),
            on_click=self._on_test_local_llm_connection,
        )
        self._local_llm_extra_body = ft.TextField(
            label=t("settings.local_llm.extra_body"),
            value=json.dumps(
                {"reasoning_effort": "none", "temperature": 0.6},
                ensure_ascii=False,
                indent=2,
            ),
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
            color=COLOR_SECONDARY,
        )
        self._local_llm_extra_body_error = ft.Text(
            "",
            size=13,
            color=COLOR_ERROR,
            visible=False,
        )
        self._local_llm_extra_body_error_key = ""
        self._local_llm_extra_body_error_kwargs: dict[str, object] = {}

    def _build_backup_form_controls(self) -> None:
        """Create the backup translation form controls (both variants)."""
        self._backup_config_title = ft.Text(
            t("settings.backup_translation.config_title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._backup_provider = self._build_provider_dropdown(self._on_backup_provider_change)
        self._backup_base_url = ft.TextField(
            label=t("settings.openai_compatible.base_url"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            visible=False,
            on_blur=self._on_backup_base_url_change_end,
            on_submit=self._on_backup_base_url_change_end,
        )
        self._backup_model = ft.TextField(
            label=t("settings.openai_compatible.model"),
            hint_text=t("settings.openai_compatible.model_hint"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_blur=self._on_backup_model_change_end,
            on_submit=self._on_backup_model_change_end,
        )
        self._backup_api_key = ApiKeyField(
            "settings.openai_compatible.api_key",
            "backup_api_key",
            "backup",
            on_save=self._on_backup_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
            show_status=False,
        )
        self._backup_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            tooltip=t("settings.openai_compatible.fetch_models"),
            on_click=self._on_fetch_backup_models,
        )
        self._backup_test_btn = ft.TextButton(
            content=ft.Text(t("settings.openai_compatible.test_connection")),
            on_click=self._on_test_backup_connection,
        )
        self._backup_network_group = self._compose_openai_form(
            title=None,
            provider=self._backup_provider,
            model=self._backup_model,
            fetch_btn=self._backup_fetch_btn,
            api_key=self._backup_api_key,
            test_btn=self._backup_test_btn,
        )
        self._backup_local_base_url = ft.TextField(
            label=t("settings.local_llm.base_url"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_backup_local_field_change,
            on_blur=self._on_backup_local_base_url_change_end,
            on_submit=self._on_backup_local_base_url_change_end,
        )
        self._backup_local_model = ft.TextField(
            label=t("settings.local_llm.model"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_backup_local_field_change,
            on_blur=self._on_backup_local_model_change_end,
            on_submit=self._on_backup_local_model_change_end,
        )
        self._backup_local_api_key = ApiKeyField(
            "settings.local_llm.api_key",
            "fallback_local_llm_api_key",
            "backup_local",
            on_save=self._on_backup_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
            show_status=False,
        )
        self._backup_local_api_key_helper = ft.Text(
            t("settings.local_llm.api_key.description"),
            size=15,
            color=COLOR_SECONDARY,
        )
        self._backup_local_extra_body = ft.TextField(
            label=t("settings.local_llm.extra_body"),
            value="",
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
            on_change=self._on_backup_local_field_change,
            on_blur=self._on_backup_local_extra_body_change_end,
            on_submit=self._on_backup_local_extra_body_change_end,
        )
        self._backup_local_extra_body_helper = ft.Text(
            t("settings.local_llm.extra_body.description"),
            size=15,
            color=COLOR_SECONDARY,
        )
        self._backup_local_extra_body_error = ft.Text(
            "",
            size=13,
            color=COLOR_ERROR,
            visible=False,
        )
        self._backup_local_extra_body_error_key = ""
        self._backup_local_extra_body_error_kwargs: dict[str, object] = {}
        self._backup_local_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            tooltip=t("settings.openai_compatible.fetch_models"),
            on_click=self._on_fetch_backup_local_models,
        )
        self._backup_local_test_btn = ft.TextButton(
            content=ft.Text(t("settings.openai_compatible.test_connection")),
            on_click=self._on_test_backup_local_connection,
        )
        self._backup_local_group = self._compose_local_form(
            title=None,
            base_url=self._backup_local_base_url,
            test_btn=self._backup_local_test_btn,
            model=self._backup_local_model,
            fetch_btn=self._backup_local_fetch_btn,
            api_key=self._backup_local_api_key,
            api_key_helper=self._backup_local_api_key_helper,
            extra_body=self._backup_local_extra_body,
            extra_body_helper=self._backup_local_extra_body_helper,
            extra_body_error=self._backup_local_extra_body_error,
        )

    def _build_provider_settings_gear(self, on_click) -> ft.Container:
        """Framed gear button shown where the inline form used to clutter the card."""
        from puripuly_heart.ui.theme import COLOR_SURFACE

        return ft.Container(
            content=ft.Icon(ft.Icons.SETTINGS_ROUNDED, size=20, color=COLOR_NEUTRAL_DARK),
            width=40,
            height=40,
            alignment=ft.Alignment.CENTER,
            border_radius=10,
            border=ft.Border.all(1, COLOR_DIVIDER),
            bgcolor=COLOR_SURFACE,
            on_click=on_click,
            on_hover=self._on_provider_gear_hover,
        )

    def _build_provider_gear_host(self, gear: ft.Control) -> ft.Control:
        return ft.Column(
            controls=[
                ft.Container(expand=True),
                ft.Row([gear], alignment=ft.MainAxisAlignment.START),
            ],
            expand=True,
        )

    def _on_provider_gear_hover(self, e) -> None:
        from puripuly_heart.ui.theme import COLOR_SURFACE

        container = e.control
        if getattr(e, "data", "") == "true":
            container.bgcolor = COLOR_DIVIDER
        else:
            container.bgcolor = COLOR_SURFACE
        _update_control_if_mounted(container)

    def attach_provider_settings_window(self, window, schedule) -> None:
        """Attach the floating provider settings window and its session scheduler."""
        self._provider_settings_window = window
        self._provider_settings_schedule = schedule

    def _on_openai_gear_click(self, _e) -> None:
        self._open_provider_settings_dialog("translation_openai")

    def _on_local_llm_gear_click(self, _e) -> None:
        self._open_provider_settings_dialog("translation_local")

    def _on_backup_gear_click(self, _e) -> None:
        self._open_provider_settings_dialog("backup")

    def _open_provider_settings_dialog(self, kind: str) -> None:
        window = getattr(self, "_provider_settings_window", None)
        schedule = getattr(self, "_provider_settings_schedule", None)
        if window is None or schedule is None or self._provider_snapshot is None:
            return
        if window.is_open:
            window.open(
                schedule,
                title="",
                content=None,
                on_accept=self._accept_provider_settings_dialog,
                on_close_request=self._revert_provider_settings_dialog,
            )
            return
        self._provider_dialog_edits_before = dict(self._provider_edits)
        self._provider_dialog_draft_before = self._provider_draft
        if kind == "translation_openai":
            self._build_openai_form_controls()
            self._openai_form = self._compose_openai_form(
                title=None,
                provider=self._openai_provider,
                model=self._openai_model,
                fetch_btn=self._openai_fetch_btn,
                api_key=self._openai_api_key,
                test_btn=self._openai_test_btn,
            )
            content = self._openai_form
            title = t("settings.translation.config_title")
        elif kind == "translation_local":
            self._build_local_form_controls()
            self._local_llm_form = self._compose_local_form(
                title=None,
                base_url=self._local_llm_base_url,
                test_btn=self._local_llm_test_btn,
                model=self._local_llm_model,
                fetch_btn=self._local_llm_fetch_btn,
                api_key=self._local_llm_api_key,
                api_key_helper=self._local_llm_api_key_helper,
                extra_body=self._local_llm_extra_body,
                extra_body_helper=self._local_llm_extra_body_helper,
                extra_body_error=self._local_llm_extra_body_error,
            )
            content = self._local_llm_form
            title = t("settings.translation.config_title")
        else:
            self._build_backup_form_controls()
            content = ft.Column(
                [self._backup_network_group, self._backup_local_group],
                spacing=8,
            )
            title = t("settings.backup_translation.config_title")
        display = self._provider_draft or self._provider_snapshot
        self._sync_translation_selection_controls(display)
        self._local_llm_base_url.value = display.local_llm_base_url
        self._local_llm_model.value = display.local_llm_model
        self._local_llm_extra_body.value = display.local_llm_extra_body_json
        self._clear_local_llm_extra_body_error()
        self._clear_backup_local_extra_body_error()
        window.open(
            schedule,
            title=title,
            content=content,
            on_accept=self._accept_provider_settings_dialog,
            on_close_request=self._revert_provider_settings_dialog,
        )

    def _commit_openai_fields_from_controls(self) -> None:
        if self._provider_snapshot is None:
            return
        self._commit_openai_field("base_url", self._openai_base_url.value or "")
        self._commit_openai_field("model", self._openai_model.value or "")

    def _commit_backup_fields_from_controls(self) -> None:
        if self._provider_snapshot is None:
            return
        self._commit_backup_field("base_url", self._backup_base_url.value or "")
        self._commit_backup_field("model", self._backup_model.value or "")

    def _accept_provider_settings_dialog(self) -> None:
        """Accept button: commit staged form values and apply them."""
        self._commit_openai_fields_from_controls()
        self._commit_backup_fields_from_controls()
        self._commit_local_llm_fields_from_controls()
        self._commit_backup_local_fields_from_controls()
        on_providers_changed = getattr(self, "on_providers_changed", None)
        if on_providers_changed is not None:
            on_providers_changed()

    def _revert_provider_settings_dialog(self) -> None:
        """Window closed without Accept: discard edits staged inside the dialog."""
        self._provider_edits = getattr(
            self, "_provider_dialog_edits_before", self._provider_edits
        )
        self._provider_draft = getattr(
            self, "_provider_dialog_draft_before", self._provider_draft
        )
        self.has_provider_changes = any(
            edit_type is not SystemPromptEdit for edit_type in self._provider_edits
        )

    def _set_unit_card_value_text(
        self, control: ft.Container, text: str, *, size: int = 28
    ) -> None:
        text_control = control.content
        text_control.value = text
        text_control.size = _scaled_value_text_size(
            text, size, max_lines=getattr(text_control, "max_lines", None) or 1
        )

    def _iter_locale_sensitive_clickable_text_controls(self) -> tuple[ft.Container, ...]:
        return (
            self._stt_text,
            self._peer_stt_text,
            self._gpu_device_text,
            self._peer_gpu_device_text,
            self._shared_engine_text,
            self._gpu_llm_text,
            self._llm_text,
            self._ui_text,
            self._chatbox_source_text,
            self._osc_connection_text,
            self._clipboard_auto_translate_text,
            self._activation_notice_button,
            self._audio_test_text,
            self._vrc_mic_text,
            self._mic_audio_text,
            self._audio_host_api_text,
            self._loopback_audio_text,
            self._overlay_translation_button,
            self._overlay_peer_original_button,
            self._overlay_target_button,
            self._overlay_anchor_button,
            self._overlay_text_scale_text,
            self._desktop_overlay_size_button,
            self._desktop_overlay_lock_button,
            self._desktop_overlay_swap_caption_languages_button,
            self._overlay_vr_reset_button,
            self._overlay_desktop_reset_button,
            self._desktop_overlay_primary_action,
            self._desktop_overlay_view_logs_action,
            self._translation_connection_text,
            self._http_extension_text,
            self._http_extension_path_text,
            self._backup_translation_text,
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
        loopback_summary = (
            self.on_loopback_capture_summary()
            if callable(getattr(self, "on_loopback_capture_summary", None))
            else (self._audio_settings.desktop_output_device or default_label)
        )
        self._set_unit_card_value_text(
            self._loopback_audio_text,
            loopback_summary or default_label,
        )

    def _sync_osc_connection_card(self, settings: GeneralSettingsSnapshot) -> None:
        mode = settings.osc_connection_mode
        if mode not in {"automatic", "manual", "off"}:
            mode = "automatic"
        self._set_unit_card_value_text(
            self._osc_connection_text,
            t(f"settings.osc.mode.{mode}"),
        )

    def refresh_loopback_capture_target(self, settings: GeneralSettingsSnapshot) -> None:
        self._general_snapshot = settings
        self._audio_settings.desktop_output_device = settings.output_device
        summary = (
            self.on_loopback_capture_summary()
            if callable(getattr(self, "on_loopback_capture_summary", None))
            else (self._audio_settings.desktop_output_device or t("settings.default_option"))
        )
        self._set_unit_card_value_text(
            self._loopback_audio_text,
            summary or t("settings.default_option"),
        )
        if is_control_mounted(self._loopback_audio_text):
            self._loopback_audio_text.update()

    def _on_text_hover(self, e: ft.ControlEvent) -> None:
        """Handle hover effect on clickable text."""
        container = e.control
        text_control = container.content
        next_color = COLOR_PRIMARY if is_hover_active(e) else COLOR_ON_BACKGROUND
        if text_control.color == next_color:
            return
        text_control.color = next_color
        container.update()

    def _make_overlay_step_hover_handler(self, text_control: ft.Text):
        def _on_hover(e: ft.ControlEvent) -> None:
            next_color = COLOR_PRIMARY if is_hover_active(e) else COLOR_ON_BACKGROUND
            if text_control.color == next_color:
                return
            text_control.color = next_color
            if is_control_mounted(text_control):
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
            alignment=ft.Alignment.CENTER_RIGHT,
        )
        increase_visual, increase_glyph = self._build_overlay_step_visual_lane(
            increase_text,
            alignment=ft.Alignment.CENTER_LEFT,
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
                    alignment=ft.Alignment.CENTER,
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
                    alignment=ft.Alignment.CENTER,
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
            alignment=ft.Alignment.CENTER,
        )
        return stack, decrease_lane, increase_lane, decrease_glyph, increase_glyph

    def _get_button_style(
        self,
        font_family: str | None,
        *,
        size: int = 20,
        default_color: str = COLOR_SECONDARY,
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
            text_style=text_style(
                TypeFace(font_family, locale_body_letter_spacing(get_locale())),
                size=size,
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

    def _emit_runtime_basic(self, message: str, *, level: int = logging.INFO) -> None:
        runtime_log_basic = getattr(self, "runtime_log_basic", None)
        if runtime_log_basic is not None:
            runtime_log_basic(message, level=level)
            return
        logger.log(level, message)

    def _build_action_button(
        self,
        text: str,
        on_click,
        *,
        size: int = 20,
        default_color: str = COLOR_SECONDARY,
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
        except TypeError, ValueError:
            return 1.0

    def _build_ui(self) -> None:
        """Build the settings UI with Bento grid layout."""
        # === API provider surfaces: Self STT + Peer STT + Shared Translation ===
        self._stt_text = self._build_clickable_text(
            t("settings.not_selected"),
            self._on_stt_click,
        )
        self._stt_title = ft.Text(
            t("settings.section.stt"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_SECONDARY
        )
        self._stt_backend_toggle_host = ft.Container(
            content=build_backend_toggle(
                "vulkan", {}, self._on_stt_engine_selected, compact=True
            ),
            alignment=ft.Alignment.CENTER,
            visible=False,
        )
        self._self_stt_card = self._wrap_unit_card(
            title=self._stt_title,
            value=self._stt_text,
            extra_controls=(self._stt_backend_toggle_host,),
        )

        self._llm_text = self._build_clickable_text(
            t("provider.gemini_flash"),
            self._on_llm_click,
            no_wrap=False,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._trans_title = ft.Text(
            t("settings.section.translation"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._translation_provider_card = self._wrap_unit_card(
            title=self._trans_title,
            value=self._llm_text,
        )

        self._peer_auto_languages_title = ft.Text(
            t("settings.peer_auto_languages.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._peer_auto_languages_editor = LanguageHintEditor(
            on_add=self._on_peer_auto_languages_add,
            on_remove=self._on_peer_auto_languages_remove,
        )
        self._peer_auto_languages_card = self._wrap_card(
            ft.Column(
                [
                    self._peer_auto_languages_title,
                    self._peer_auto_languages_editor,
                ],
                spacing=12,
            ),
            height=None,
            expand=False,
        )

        # === General Tab Row 1 ===
        self._ui_text = self._build_clickable_text(
            locale_label(get_locale()),
            self._on_ui_click,
        )
        self._ui_title = ft.Text(
            t("settings.section.ui"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_SECONDARY
        )
        ui_card = self._wrap_unit_card(
            title=self._ui_title,
            value=self._ui_text,
        )

        self._audio_settings = AudioSettings()
        self._chatbox_source_text = self._build_clickable_text(
            t("settings.chatbox_source.on"),
            self._on_chatbox_source_click,
        )
        self._chatbox_source_title = ft.Text(
            t("settings.chatbox_include_source"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        chatbox_source_card = self._wrap_unit_card(
            title=self._chatbox_source_title,
            value=self._chatbox_source_text,
        )

        self._osc_connection_text = self._build_clickable_text(
            t("settings.osc.mode.automatic"),
            self._on_osc_connection_click,
        )
        self._osc_connection_title = ft.Text(
            t("settings.osc.connection.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._vrchat_osc_card = self._wrap_unit_card(
            title=self._osc_connection_title,
            value=self._osc_connection_text,
        )

        self._clipboard_auto_translate_text = self._build_clickable_text(
            t("settings.clipboard_auto_translate.off"),
            self._on_clipboard_auto_translate_click,
        )
        self._clipboard_auto_translate_title = ft.Text(
            t("settings.clipboard_auto_translate"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        clipboard_auto_translate_card = self._wrap_unit_card(
            title=self._clipboard_auto_translate_title,
            value=self._clipboard_auto_translate_text,
        )

        self._activation_notice_title = ft.Text(
            t("settings.activation_notice.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._activation_notice_button = self._build_clickable_text(
            t("settings.option.on"),
            self._on_activation_notice_click,
        )
        self._activation_notice_button.disabled = True
        self._activation_notice_card = self._wrap_unit_card(
            title=self._activation_notice_title,
            value=self._activation_notice_button,
        )

        self._vrc_mic_text = self._build_clickable_text(
            t("settings.vrc_mic.on"),
            self._on_vrc_mic_click,
        )
        self._vrc_mic_title = ft.Text(
            t("settings.vrc_mic_intercept"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        vrc_mic_card = self._wrap_unit_card(
            title=self._vrc_mic_title,
            value=self._vrc_mic_text,
        )

        self._audio_test_text = self._build_clickable_text(
            t("settings.audio_test.action"),
            self._on_audio_test_click,
        )
        self._audio_test_title = ft.Text(
            t("settings.audio_test"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        audio_test_card = self._wrap_unit_card(
            title=self._audio_test_title,
            value=self._audio_test_text,
        )

        self._general_ui_card = ui_card
        self._general_chatbox_source_card = chatbox_source_card

        # === General Tab Row 2: Host API / Microphone Audio / Loopback Audio ===
        self._mic_audio_text = self._build_clickable_text(
            t("settings.default_option"),
            self._on_mic_audio_click,
        )
        self._audio_host_api_title = ft.Text(
            t("settings.audio_host_api"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
        )
        loopback_audio_card = self._wrap_unit_card(
            title=self._loopback_audio_title,
            value=self._loopback_audio_text,
        )
        self._general_host_api_card = host_api_card
        self._general_mic_audio_card = mic_audio_card
        self._general_loopback_audio_card = loopback_audio_card

        # === General Tab Row 3: VRChat Mute Sync / Self VAD / Peer VAD ===
        self._self_vad_title = ft.Text(
            t("settings.section.self_vad_sensitivity"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._vad_slider = ft.Slider(
            min=VAD_ONSET_MIN,
            max=VAD_ONSET_MAX,
            divisions=18,
            value=0.4,
            label="0.40",
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
            color=COLOR_SECONDARY,
        )
        self._peer_vad_slider = ft.Slider(
            min=VAD_ONSET_MIN,
            max=VAD_ONSET_MAX,
            divisions=18,
            value=0.5,
            label="0.50",
            active_color=COLOR_PRIMARY,
            on_change=self._handle_peer_vad_visual_change,
            on_change_end=self._handle_peer_vad_change,
        )
        self._peer_vad_card = self._wrap_unit_card(
            title=self._peer_vad_title,
            value=ft.Container(
                content=self._peer_vad_slider,
                alignment=_CENTER_ALIGNMENT,
                expand=True,
            ),
        )
        self._general_surface = compose_settings_general_surface(
            SettingsGeneralSurfaceSlots(
                ui=self._general_ui_card,
                chatbox_source=self._general_chatbox_source_card,
                audio_host_api=self._general_host_api_card,
                microphone=self._general_mic_audio_card,
                loopback=self._general_loopback_audio_card,
                audio_test=audio_test_card,
                self_vad=self._self_vad_card,
                peer_vad=self._peer_vad_card,
                clipboard_auto_translate=clipboard_auto_translate_card,
                vrchat_mic_intercept=vrc_mic_card,
                activation_notice=self._activation_notice_card,
                activation_notice_middle=self._wrap_empty_unit_card(),
                activation_notice_trailing=self._wrap_empty_unit_card(),
            ),
            placeholder_factory=lambda: self._vrchat_osc_card,
        )

        # === Peer STT card ===
        self._peer_provider_title = ft.Text(
            t("settings.section.peer_stt"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._peer_stt_text = self._build_clickable_text(
            t("settings.not_selected"),
            self._on_peer_stt_click,
        )
        self._peer_backend_toggle_host = ft.Container(
            content=build_backend_toggle(
                "vulkan", {}, self._on_peer_stt_engine_selected, compact=True
            ),
            alignment=ft.Alignment.CENTER,
            visible=False,
        )
        self._peer_stt_card = self._wrap_unit_card(
            title=self._peer_provider_title,
            value=self._peer_stt_text,
            extra_controls=(self._peer_backend_toggle_host,),
        )

        self._gpu_device_title = ft.Text(
            t("settings.gpu_device.asr"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._gpu_device_text = self._build_clickable_text(
            t("settings.gpu_device.auto"),
            self._on_gpu_device_click,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._gpu_device_card = self._wrap_unit_card(
            title=self._gpu_device_title,
            value=self._gpu_device_text,
        )
        self._peer_gpu_device_title = ft.Text(
            t("settings.gpu_device.peer_asr"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._peer_gpu_device_text = self._build_clickable_text(
            t("settings.gpu_device.auto"),
            self._on_peer_gpu_device_click,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._peer_gpu_device_card = self._wrap_unit_card(
            title=self._peer_gpu_device_title,
            value=self._peer_gpu_device_text,
        )
        self._shared_engine_title = ft.Text(
            t("settings.shared_engine.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._shared_engine_text = self._build_clickable_text(
            t("settings.option.off"),
            self._on_shared_engine_click,
        )
        self._shared_engine_card = self._wrap_unit_card(
            title=self._shared_engine_title,
            value=self._shared_engine_text,
        )
        self._stt_model_title = ft.Text(
            t("settings.stt_model.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._stt_model_text = self._build_clickable_text(
            t("settings.not_selected"),
            self._on_stt_model_click,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._stt_model_card = self._wrap_unit_card(
            title=self._stt_model_title,
            value=self._stt_model_text,
        )
        self._gpu_llm_title = ft.Text(
            t("settings.gpu_device.llm"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._gpu_llm_text = self._build_clickable_text(
            t("settings.gpu_device.auto"),
            self._on_llm_gpu_device_click,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._gpu_llm_card = self._wrap_unit_card(
            title=self._gpu_llm_title,
            value=self._gpu_llm_text,
        )
        self._model_assets_title = ft.Text(
            t("settings.model_assets"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._model_assets_text = self._build_clickable_text(
            t("settings.model_assets.action"),
            self._on_model_assets_click,
        )
        self._model_assets_card = self._wrap_unit_card(
            title=self._model_assets_title,
            value=self._model_assets_text,
        )
        self._model_assets_card.expand = False
        self._model_assets_card.width = 280
        for card in (
            self._gpu_device_card,
            self._peer_gpu_device_card,
            self._shared_engine_card,
            self._stt_model_card,
            self._gpu_llm_card,
        ):
            card.visible = False

        self._overlay_translation_title = ft.Text(
            t("settings.overlay.show_translation"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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
            color=COLOR_SECONDARY,
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

        self._desktop_overlay_swap_caption_languages_title = ft.Text(
            t("settings.overlay.desktop.swap_caption_languages.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._desktop_overlay_swap_caption_languages_button = self._build_clickable_text(
            t("settings.option.off"),
            self._on_desktop_overlay_swap_caption_languages_click,
        )
        self._desktop_overlay_swap_caption_languages_card = self._wrap_unit_card(
            title=self._desktop_overlay_swap_caption_languages_title,
            value=self._desktop_overlay_swap_caption_languages_button,
        )

        self._desktop_overlay_status_title = ft.Text(
            "",
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._desktop_overlay_reason_text = ft.Text(
            "",
            size=15,
            color=COLOR_SECONDARY,
            text_align=ft.TextAlign.CENTER,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            visible=False,
        )
        self._desktop_overlay_helper_text = ft.Text(
            "",
            size=14,
            color=COLOR_SECONDARY,
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
        self._overlay_desktop_reset_spacer = self._wrap_empty_unit_card()

        self._overlay_surface = compose_settings_overlay_surface(
            SettingsOverlaySurfaceSlots(
                overlay_target=self._overlay_target_card,
                overlay_translation=self._overlay_translation_card,
                overlay_peer_original=self._overlay_peer_original_card,
                anchor=self._overlay_anchor_card,
                distance=self._overlay_distance_card,
                offset_x=self._overlay_offset_x_card,
                offset_y=self._overlay_offset_y_card,
                text_scale=self._overlay_text_scale_card,
                vr_reset=self._overlay_vr_reset_card,
                desktop_size=self._desktop_overlay_size_card,
                desktop_lock=self._desktop_overlay_lock_card,
                desktop_background_alpha=self._desktop_overlay_background_alpha_card,
                desktop_swap_caption_languages=self._desktop_overlay_swap_caption_languages_card,
                desktop_reset=self._overlay_desktop_reset_card,
                desktop_reset_spacer=self._overlay_desktop_reset_spacer,
                desktop_status=self._desktop_overlay_status_card,
                desktop_status_trailing=self._overlay_empty_card,
            ),
            placeholder_factory=self._wrap_empty_unit_card,
        )
        self._overlay_vr_rows = self._overlay_surface.vr_rows
        self._overlay_desktop_rows = self._overlay_surface.desktop_rows
        self._desktop_overlay_controls_row = self._overlay_surface.desktop_controls_row
        self._desktop_overlay_recovery_row = self._overlay_surface.recovery_row
        self._sync_overlay_target_specific_visibility()

        self._translation_connection_title = ft.Text(
            t("settings.translation_connection"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._translation_connection_text = self._build_clickable_text(
            t("settings.translation_connection.managed"),
            self._on_translation_connection_click,
        )
        self._translation_connection_card = self._wrap_unit_card(
            title=self._translation_connection_title,
            value=self._translation_connection_text,
        )

        self._build_local_form_controls()
        self._local_llm_form = self._compose_local_form(
            title=None,
            base_url=self._local_llm_base_url,
            test_btn=self._local_llm_test_btn,
            model=self._local_llm_model,
            fetch_btn=self._local_llm_fetch_btn,
            api_key=self._local_llm_api_key,
            api_key_helper=self._local_llm_api_key_helper,
            extra_body=self._local_llm_extra_body,
            extra_body_helper=self._local_llm_extra_body_helper,
            extra_body_error=self._local_llm_extra_body_error,
        )
        self._local_llm_gear = self._build_provider_settings_gear(
            self._on_local_llm_gear_click
        )
        self._local_llm_connection_card = self._wrap_card(
            self._build_provider_gear_host(self._local_llm_gear),
            height=96,
        )
        self._local_llm_connection_card.visible = False
        self._backup_translation_title = ft.Text(
            t("settings.backup_translation.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._backup_translation_text = self._build_clickable_text(
            t("settings.not_selected"),
            self._on_backup_translation_click,
            no_wrap=False,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._backup_translation_card = self._wrap_unit_card(
            title=self._backup_translation_title,
            value=self._backup_translation_text,
        )

        self._build_openai_form_controls()
        self._openai_form = self._compose_openai_form(
            title=None,
            provider=self._openai_provider,
            model=self._openai_model,
            fetch_btn=self._openai_fetch_btn,
            api_key=self._openai_api_key,
            test_btn=self._openai_test_btn,
        )
        self._openai_gear = self._build_provider_settings_gear(
            self._on_openai_gear_click
        )
        self._openai_compatible_card = self._wrap_card(
            self._build_provider_gear_host(self._openai_gear),
            height=96,
        )
        self._openai_compatible_card.visible = False

        self._build_backup_form_controls()
        self._backup_gear = self._build_provider_settings_gear(
            self._on_backup_gear_click
        )
        self._backup_config_card = self._wrap_card(
            self._build_provider_gear_host(self._backup_gear),
            height=96,
        )
        self._backup_config_card.visible = False

        self._http_extension_title = ft.Text(
            t("settings.http_extension.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._http_extension_text = self._build_clickable_text(
            t("settings.http_extension.none"),
            self._on_http_extension_click,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._http_extension_selection_card = self._wrap_unit_card(
            title=self._http_extension_title,
            value=self._http_extension_text,
        )

        self._http_extension_path_title = ft.Text(
            t("settings.http_extension.path"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._http_extension_path_text = self._build_clickable_text(
            t("settings.http_extension.open"),
            self._on_http_extension_open_folder,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._http_extension_path_card = self._wrap_unit_card(
            title=self._http_extension_path_title,
            value=self._http_extension_path_text,
        )

        self._http_extension_refresh_title = ft.Text(
            t("settings.http_extension.refresh"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )
        self._http_extension_refresh_icon = ft.Container(
            content=ft.Icon(
                ft.Icons.REFRESH_ROUNDED,
                size=44,
                color=COLOR_ON_BACKGROUND,
            ),
            alignment=_CENTER_ALIGNMENT,
            expand=True,
            on_click=self._on_http_extension_reload,
            on_hover=self._on_text_hover,
        )
        self._http_extension_refresh_card = self._wrap_unit_card(
            title=self._http_extension_refresh_title,
            value=self._http_extension_refresh_icon,
        )

        self._http_extension_credentials = ft.Column([], spacing=12, visible=False)

        self._http_extension_row = ft.Row(
            [
                self._http_extension_selection_card,
                self._http_extension_path_card,
                self._http_extension_refresh_card,
            ],
            spacing=SETTINGS_ROW_SPACING,
            expand=True,
        )
        self._http_extension_host = ft.Container(
            content=ft.Column(
                [self._http_extension_row, self._http_extension_credentials],
                spacing=12,
            ),
            visible=False,
        )

        # === Row 8: Persona (2x2) - Licenses style ===
        self._prompt_editor = PromptEditor(
            on_change=self._on_prompt_change,
            on_commit=self._on_prompt_commit,
        )
        self._persona_title = ft.Text(
            t("settings.section.persona"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_SECONDARY
        )

        # Reset button (matches Persona title color, hover -> primary)
        self._reset_prompt_btn = _make_text_button(
            t("settings.reset_prompt"),
            icon=ft.Icons.REFRESH_ROUNDED,
            style=_settings_secondary_text_button_style(),
            on_click=self._on_reset_prompt,
        )

        # Header row with title and reset button
        persona_header = ft.Row(
            controls=[self._persona_title, ft.Container(expand=True), self._reset_prompt_btn],
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
            color=COLOR_SECONDARY,
        )
        self._custom_vocab_description_text = ft.Text(
            t("settings.custom_vocabulary.description"),
            size=20,
            color=COLOR_SECONDARY,
            text_align=ft.TextAlign.RIGHT,
        )
        self._custom_vocab_header = ft.Row(
            controls=[
                self._custom_vocab_title,
                ft.Container(expand=True),
                self._custom_vocab_description_text,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        )
        self._custom_vocab_tag_editor = CustomVocabularyTagEditor(
            on_add_terms=self._on_custom_vocabulary_add_terms,
            on_remove_term=self._on_custom_vocabulary_remove_term,
        )
        row7 = SharedCardWrapper(
            ft.Column(
                [
                    self._custom_vocab_header,
                    ft.Container(height=12),
                    self._custom_vocab_tag_editor,
                ],
                spacing=0,
            ),
            height=None,
            expand=False,
        )

        self._api_surface = compose_settings_api_surface(
            SettingsApiSurfaceSlots.from_slot_provider(self),
        )
        self._translation_connection_row = self._api_surface.translation_connection_row
        self._gpu_device_row = self._api_surface.gpu_device_row

        self._prompt_surface = compose_settings_prompt_surface(
            SettingsPromptSurfaceSlots(
                custom_vocabulary=row7,
                peer_expected_language=self._peer_auto_languages_card,
                persona=persona_card,
            )
        )

        self._settings_subtab_shell = self._build_settings_subtab_shell(
            {
                "api": list(self._api_surface.rows),
                "general": list(self._general_surface.rows),
                "prompt": list(self._prompt_surface.rows),
                "overlay": list(self._overlay_surface.rows),
            }
        )
        self.controls = [self._settings_subtab_shell]

    def _gpu_llm_selected(self, settings: ProviderSettingsSnapshot) -> bool:
        model = settings.translation.model
        return (
            model in MANAGED_GEMMA_MODELS
            and settings.translation.connection == TranslationConnection.GPU
        )

    def _local_model_selected(self, settings: ProviderSettingsSnapshot | None = None) -> bool:
        current = settings or self._build_settings_with_provider_draft()
        return bool(
            current is not None
            and (
                current.stt_provider in _LOCAL_STT_PROVIDERS
                or current.peer_stt_provider in _LOCAL_STT_PROVIDERS
                or current.translation.model in MANAGED_GEMMA_MODELS
            )
        )

    def _gpu_column_label(
        self,
        selected: str,
        devices: tuple[GpuDeviceOption, ...],
    ) -> str:
        if selected == "auto":
            return t("settings.gpu_device.auto")
        if selected == "cpu":
            return t("settings.gpu_device.cpu")
        selected_device = next(
            (device for device in devices if device.device_id == selected),
            None,
        )
        if selected_device is not None:
            return selected_device.display_name
        return t("settings.gpu_device.unavailable", device=selected)

    def _sync_gpu_device_card(self) -> None:
        if not hasattr(self, "_gpu_device_text"):
            return
        settings = self._build_settings_with_provider_draft()
        asr_selected = settings.stt_gpu_device_id if settings is not None else "auto"
        peer_selected = settings.peer_stt_gpu_device_id if settings is not None else "auto"
        llm_selected = settings.translation.gpu_device_id if settings is not None else "auto"
        asr_label = self._gpu_column_label(asr_selected, getattr(self, "_gpu_devices", ()))
        peer_label = self._gpu_column_label(peer_selected, getattr(self, "_gpu_devices", ()))
        llm_label = self._gpu_column_label(llm_selected, getattr(self, "_llm_gpu_devices", ()))
        self._set_unit_card_value_text(self._gpu_device_text, asr_label)
        self._set_unit_card_value_text(self._peer_gpu_device_text, peer_label)
        self._set_unit_card_value_text(self._gpu_llm_text, llm_label)
        self._sync_stt_model_card()
        visible = self._local_model_selected(settings)
        self_local = settings is not None and settings.stt_provider in _LOCAL_STT_PROVIDERS
        peer_local = settings is not None and settings.peer_stt_provider in _LOCAL_STT_PROVIDERS
        shared_engine = settings is not None and settings.stt_shared_engine
        self._gpu_device_card.visible = self_local and settings.stt_gpu_backend != "cpu"
        self._peer_gpu_device_card.visible = (
            peer_local and not shared_engine and settings.peer_stt_gpu_backend != "cpu"
        )
        self._shared_engine_card.visible = self_local or peer_local
        self._stt_model_card.visible = visible
        self._gpu_llm_card.visible = visible
        self._apply_shared_engine_grey(shared_engine)
        self._sync_stt_backend_toggles()
        _update_control_if_mounted(self._gpu_device_row)

    def _apply_shared_engine_grey(self, shared_engine: bool) -> None:
        for card in (self._peer_stt_card, self._peer_gpu_device_card):
            card.opacity = 0.5 if shared_engine else 1.0
            card.ignore_interactions = shared_engine
        self._set_unit_card_value_text(
            self._shared_engine_text,
            t("settings.option.on") if shared_engine else t("settings.option.off"),
        )

    def _sync_stt_backend_toggles(self) -> None:
        if not hasattr(self, "_stt_backend_toggle_host"):
            return
        settings = self._build_settings_with_provider_draft()
        if settings is None:
            return
        available = self._engine_backend_availability()
        self_local = settings.stt_provider in _LOCAL_STT_PROVIDERS
        peer_local = settings.peer_stt_provider in _LOCAL_STT_PROVIDERS
        shared_engine = settings.stt_shared_engine
        self._stt_backend_toggle_host.content = build_backend_toggle(
            settings.stt_gpu_backend,
            available,
            self._on_stt_engine_selected,
            compact=True,
        )
        self._stt_backend_toggle_host.visible = self_local
        self._peer_backend_toggle_host.content = build_backend_toggle(
            settings.peer_stt_gpu_backend,
            available,
            self._on_peer_stt_engine_selected,
            compact=True,
        )
        self._peer_backend_toggle_host.visible = peer_local and not shared_engine
        _update_control_if_mounted(self._stt_backend_toggle_host)
        _update_control_if_mounted(self._peer_backend_toggle_host)

    def set_gpu_devices(
        self,
        *,
        devices: tuple[GpuDeviceOption, ...] | None = None,
        llm_devices: tuple[GpuDeviceOption, ...] | None = None,
    ) -> None:
        if devices is not None:
            self._gpu_devices = devices
        if llm_devices is not None:
            self._llm_gpu_devices = llm_devices
        self._sync_gpu_device_card()

    @staticmethod
    def _gpu_backend_label(name: str) -> str:
        match = re.fullmatch(r"Vulkan\s*(\d+)", name.strip(), flags=re.IGNORECASE)
        if match is not None:
            return f"Vulkan {match.group(1)}"
        return name.strip()

    def _gpu_column_options(
        self,
        selected: str,
        devices: tuple[GpuDeviceOption, ...],
    ) -> list[OptionItem]:
        options = [
            OptionItem(
                value="auto",
                label=t("settings.gpu_device.auto"),
            ),
            OptionItem(
                value="cpu",
                label=t("settings.gpu_device.cpu"),
            ),
        ]
        options.extend(
            OptionItem(
                value=device.device_id,
                label=device.display_name,
                description=self._gpu_backend_label(device.backend_name),
            )
            for device in devices
        )
        if (
            selected not in {"auto", "cpu"}
            and all(device.device_id != selected for device in devices)
        ):
            options.append(
                OptionItem(
                    value=selected,
                    label=t("settings.gpu_device.unavailable", device=selected),
                )
            )
        return options

    def _open_gpu_column_modal(
        self,
        *,
        title: str,
        selected: str,
        devices: tuple[GpuDeviceOption, ...],
        on_select,
    ) -> None:
        if self.on_gpu_discovery_requested is not None:
            self.on_gpu_discovery_requested()
        SettingsModal(
            self.page,
            title,
            self._gpu_column_options(selected, devices),
            on_select,
            show_description=True,
        ).open(selected)

    def _stt_device_options(
        self,
        selected: str,
        devices: tuple[GpuDeviceOption, ...],
        backend: str,
    ) -> list[OptionItem]:
        options = [
            OptionItem(
                value="auto",
                label=t("settings.gpu_device.auto"),
            )
        ]
        options.extend(
            OptionItem(
                value=device.device_id,
                label=device.display_name,
                description=self._gpu_backend_label(device.backend_name),
            )
            for device in devices
            if device.kind in ("", backend)
        )
        if (
            selected not in {"auto", "cpu"}
            and all(device.device_id != selected for device in devices)
        ):
            options.append(
                OptionItem(
                    value=selected,
                    label=t("settings.gpu_device.unavailable", device=selected),
                )
            )
        return options

    def _open_stt_device_modal(
        self,
        *,
        title: str,
        selected: str,
        backend: str,
        on_select,
    ) -> None:
        if self.on_gpu_discovery_requested is not None:
            self.on_gpu_discovery_requested()
        SettingsModal(
            self.page,
            title,
            self._stt_device_options(selected, self._gpu_devices, backend),
            on_select,
            show_description=True,
        ).open(selected)

    def _on_gpu_device_click(self, _event) -> None:
        if not is_control_mounted(self):
            return
        settings = self._build_settings_with_provider_draft()
        selected = settings.stt_gpu_device_id if settings is not None else "auto"
        backend = settings.stt_gpu_backend if settings is not None else "vulkan"
        self._open_stt_device_modal(
            title=t("settings.gpu_device.asr"),
            selected=selected,
            backend=backend,
            on_select=self._on_gpu_device_selected,
        )

    def _on_peer_gpu_device_click(self, _event) -> None:
        if not is_control_mounted(self):
            return
        settings = self._build_settings_with_provider_draft()
        if settings is not None and settings.stt_shared_engine:
            return
        selected = settings.peer_stt_gpu_device_id if settings is not None else "auto"
        backend = settings.peer_stt_gpu_backend if settings is not None else "vulkan"
        self._open_stt_device_modal(
            title=t("settings.gpu_device.peer_asr"),
            selected=selected,
            backend=backend,
            on_select=self._on_peer_gpu_device_selected,
        )

    def _on_llm_gpu_device_click(self, _event) -> None:
        if not is_control_mounted(self):
            return
        settings = self._build_settings_with_provider_draft()
        selected = settings.translation.gpu_device_id if settings is not None else "auto"
        self._open_gpu_column_modal(
            title=t("settings.gpu_device.llm"),
            selected=selected,
            devices=self._llm_gpu_devices,
            on_select=self._on_llm_gpu_device_selected,
        )

    def _on_model_assets_click(self, _event) -> None:
        if self.on_open_model_assets is not None:
            self.on_open_model_assets()

    def _on_gpu_device_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        draft = self._ensure_provider_settings_draft()
        self._provider_draft = replace(draft, stt_gpu_device_id=value or "auto")
        self._record_provider_edit(SttGpuDeviceEdit(self._provider_draft.stt_gpu_device_id))
        self.has_provider_changes = True
        self._sync_gpu_device_card()

    def _on_peer_gpu_device_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        draft = self._ensure_provider_settings_draft()
        self._provider_draft = replace(draft, peer_stt_gpu_device_id=value or "auto")
        self._record_provider_edit(
            PeerSttGpuDeviceEdit(self._provider_draft.peer_stt_gpu_device_id)
        )
        self.has_provider_changes = True
        self._sync_gpu_device_card()

    def _on_llm_gpu_device_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        draft = self._ensure_provider_settings_draft()
        translation = replace(draft.translation, gpu_device_id=value or "auto")
        self._provider_draft = replace(draft, translation=translation)
        self._record_provider_edit(LlmGpuDeviceEdit(translation.gpu_device_id))
        self.has_provider_changes = True
        self._sync_gpu_device_card()

    @staticmethod
    def _stt_model_label(model_id: str) -> str:
        family, _, quant = model_id.partition("-gguf-")
        if not quant:
            return model_id
        return f"{family} · {quant.upper()}"

    def _stt_model_options(self) -> list[OptionItem]:
        options = [OptionItem(value="", label=t("settings.not_selected"))]
        options.extend(
            OptionItem(
                value=model_id,
                label=self._stt_model_label(model_id),
            )
            for model_id in LOCAL_STT_MODEL_IDS
        )
        return options

    def _sync_stt_model_card(self) -> None:
        if not hasattr(self, "_stt_model_text"):
            return
        settings = self._build_settings_with_provider_draft()
        model = settings.stt_local_model if settings is not None else ""
        label = self._stt_model_label(model) if model else t("settings.not_selected")
        self._set_unit_card_value_text(self._stt_model_text, label)

    def _on_stt_model_click(self, _event) -> None:
        if not is_control_mounted(self):
            return
        settings = self._build_settings_with_provider_draft()
        selected = settings.stt_local_model if settings is not None else ""
        SettingsModal(
            self.page,
            t("settings.stt_model.title"),
            self._stt_model_options(),
            self._on_stt_model_selected,
            show_description=False,
        ).open(selected)

    def _on_stt_model_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        draft = self._ensure_provider_settings_draft()
        self._provider_draft = replace(draft, stt_local_model=value)
        self._record_provider_edit(SttLocalModelEdit(self._provider_draft.stt_local_model))
        self.has_provider_changes = True
        self._sync_stt_model_card()

    def _http_extension_modal_options(self) -> list[OptionItem]:
        options = [OptionItem(value="", label=t("settings.http_extension.none"))]
        options.extend(
            OptionItem(
                value=loaded.definition.id,
                label=loaded.definition.name,
                description=loaded.definition.description or None,
            )
            for loaded in self._http_extension_snapshot.extensions
        )
        return options

    def _sync_http_extension_credentials(self, extension) -> None:
        self._http_extension_secret_fields = {}
        self._http_extension_secret_dirty.clear()
        secret_values: dict[str, str] = {}
        if extension is not None and self._settings_secrets is not None:
            keys = tuple(
                http_extension_secret_key(extension.id, secret.id) for secret in extension.secrets
            )
            result = self._settings_secrets.load_values(keys)
            if result.error_message is not None:
                self._emit_runtime_basic(result.error_message, level=logging.WARNING)
            if result.snapshot is not None:
                secret_values = {
                    secret.id: result.snapshot.get(
                        http_extension_secret_key(extension.id, secret.id)
                    )
                    or ""
                    for secret in extension.secrets
                }
        controls: list[ft.Control] = []
        if extension is not None:
            for secret in extension.secrets:
                field = ft.TextField(
                    label=t("settings.http_extension.api_key"),
                    value=secret_values.get(secret.id, ""),
                    password=True,
                    can_reveal_password=False,
                    border_radius=12,
                    border_color=COLOR_DIVIDER,
                    focused_border_color=COLOR_PRIMARY,
                    expand=True,
                    text_size=24,
                    color=COLOR_NEUTRAL_DARK,
                    label_style=ft.TextStyle(
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        color=COLOR_NEUTRAL_DARK,
                    ),
                    on_change=lambda _event, secret_id=secret.id: (
                        self._http_extension_secret_dirty.add(secret_id)
                    ),
                    on_blur=lambda _event, secret_id=secret.id: self._on_http_extension_secret_blur(
                        secret_id
                    ),
                )
                reveal_button = ft.IconButton(
                    icon=ft.Icons.VISIBILITY_OFF_ROUNDED,
                    icon_color=COLOR_DIVIDER,
                    icon_size=24,
                )

                def _on_toggle_secret_reveal(
                    _event,
                    field: ft.TextField = field,
                    button: ft.IconButton = reveal_button,
                ) -> None:
                    field.password = not field.password
                    button.icon = (
                        ft.Icons.VISIBILITY_OFF_ROUNDED
                        if field.password
                        else ft.Icons.VISIBILITY_ROUNDED
                    )
                    update_control_if_mounted(field)
                    update_control_if_mounted(button)

                reveal_button.on_click = _on_toggle_secret_reveal
                field.suffix = reveal_button
                self._http_extension_secret_fields[secret.id] = field
                controls.append(field)
        self._http_extension_credentials.controls = controls
        _update_control_if_mounted(self._http_extension_credentials)

    def _sync_http_extension_card(
        self,
        settings: ProviderSettingsSnapshot | None = None,
        *,
        force_credentials: bool = False,
    ) -> None:
        if not hasattr(self, "_http_extension_row"):
            return
        if settings is None:
            settings = self._build_settings_with_provider_draft()
        if settings is None:
            return
        is_custom = settings.translation.model == TranslationModel.CUSTOM_HTTP
        self._http_extension_row.visible = is_custom
        self._http_extension_host.visible = is_custom
        self._http_extension_credentials.visible = is_custom
        if not is_custom:
            _update_control_if_mounted(self._http_extension_host)
            return
        selected_id = settings.translation.http_extension_id
        loaded = self._http_extension_snapshot.get(selected_id)
        selected_changed = selected_id != self._http_extension_selected_id
        self._set_unit_card_value_text(
            self._http_extension_text,
            loaded.definition.name if loaded else t("settings.http_extension.none"),
        )
        if selected_changed or force_credentials:
            self._sync_http_extension_credentials(loaded.definition if loaded else None)
            self._http_extension_selected_id = selected_id
        _update_control_if_mounted(self._http_extension_host)
        _update_control_if_mounted(self._http_extension_credentials)

    def _on_http_extension_click(self, _event) -> None:
        if not is_control_mounted(self):
            return
        settings = self._build_settings_with_provider_draft()
        selected = settings.translation.http_extension_id if settings is not None else ""
        SettingsModal(
            self.page,
            t("settings.http_extension.title"),
            self._http_extension_modal_options(),
            self._on_http_extension_selected,
            show_description=True,
        ).open(selected)

    def _on_http_extension_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        draft = self._ensure_provider_settings_draft()
        translation = replace(draft.translation, http_extension_id=value or "")
        self._provider_draft = replace(draft, translation=translation)
        self._record_provider_edit(TranslationHttpExtensionEdit(translation.http_extension_id))
        self.has_provider_changes = True
        self._sync_http_extension_card(self._provider_draft, force_credentials=True)

    def _on_http_extension_secret_blur(self, secret_id: str) -> None:
        http_extension_id = self._http_extension_selected_id
        field = self._http_extension_secret_fields.get(secret_id)
        if (
            not http_extension_id
            or field is None
            or secret_id not in self._http_extension_secret_dirty
        ):
            return
        value = (field.value or "").strip()
        self._http_extension_secret_dirty.discard(secret_id)
        result = self._on_secret_change(
            http_extension_secret_key(http_extension_id, secret_id),
            value,
        )
        if inspect.isawaitable(result):
            self._schedule_page_task(self._finish_http_extension_secret_save, result)

    async def _finish_http_extension_secret_save(self, result) -> None:
        succeeded = await result
        if succeeded is False and self.show_snackbar is not None:
            self.show_snackbar(
                t("settings.http_extension.credential_save_failed"),
                COLOR_ERROR,
            )

    def _schedule_page_task(self, callback: Callable[..., object], *args: object) -> None:
        page = getattr(self, "page", None)
        if page is not None:
            page.run_task(callback, *args)

    def _on_http_extension_open_folder(self, _event) -> None:
        try:
            self._http_extensions.open_directory()
        except Exception:
            if self.show_snackbar is not None:
                self.show_snackbar(
                    t("settings.http_extension.open_folder_failed"),
                    COLOR_ERROR,
                )

    def _on_http_extension_reload(self, _event) -> None:
        settings = self._build_settings_with_provider_draft()
        previous_snapshot = self._http_extension_snapshot
        active_settings = self._provider_snapshot
        selected_id = (
            active_settings.translation.http_extension_id
            if active_settings is not None
            and active_settings.translation.model == TranslationModel.CUSTOM_HTTP
            else None
        )
        previous_selected = previous_snapshot.get(selected_id) if selected_id else None
        self._http_extension_snapshot = self._http_extensions.reload()
        current_selected = self._http_extension_snapshot.get(selected_id) if selected_id else None
        self._sync_http_extension_card(settings, force_credentials=True)
        if self._http_extension_snapshot.errors and self.show_snackbar is not None:
            self.show_snackbar(
                t(
                    "settings.http_extension.reload_errors",
                    count=len(self._http_extension_snapshot.errors),
                ),
                COLOR_WARNING,
            )
        if (
            active_settings is not None
            and active_settings.translation.model == TranslationModel.CUSTOM_HTTP
            and (
                previous_selected is None
                and current_selected is not None
                or previous_selected is not None
                and current_selected is None
                or previous_selected is not None
                and current_selected is not None
                and previous_selected.fingerprint != current_selected.fingerprint
            )
            and self.on_providers_changed is not None
        ):
            self._http_extension_runtime_reload_pending = True
            self.on_providers_changed()

    def consume_http_extension_runtime_reload(self) -> bool:
        pending = self._http_extension_runtime_reload_pending
        self._http_extension_runtime_reload_pending = False
        return pending

    def _get_llm_modal_value(self, settings: ProviderSettingsSnapshot) -> str:
        return settings.translation.model.value

    def _translation_model_display_label(self, model: TranslationModel) -> str:
        if model == TranslationModel.NONE:
            return t("settings.not_selected")
        return t(_TRANSLATION_MODEL_LABEL_KEYS[model])

    def _translation_model_display_description(self, model: TranslationModel) -> str:
        key = _TRANSLATION_MODEL_DESCRIPTION_KEYS.get(model)
        return t(key) if key else ""

    def _translation_connection_display_label(self, connection: TranslationConnection) -> str:
        if connection == TranslationConnection.NONE:
            return t("settings.not_selected")
        return t(_TRANSLATION_CONNECTION_LABEL_KEYS[connection])
        key = _TRANSLATION_CONNECTION_DESCRIPTION_KEYS.get(connection)
        return t(key) if key else ""

    def _backup_display_label(self, settings: ProviderSettingsSnapshot | None) -> str:
        if settings is None or not settings.backup_enabled:
            return t("settings.not_selected")
        variant = normalize_translation_model(settings.backup_variant)
        if variant == TranslationModel.NONE:
            return t("settings.not_selected")
        return self._translation_model_display_label(variant)

    def _set_translation_connection_text(self, text: str) -> None:
        text_control = self._translation_connection_text.content
        text_control.value = text
        text_control.size = 28

    def _sync_translation_connection_title(self, settings: ProviderSettingsSnapshot) -> None:
        title = getattr(self, "_translation_connection_title", None)
        if title is None:
            return
        title.value = t("settings.translation_connection")

    def _get_llm_display_label(self, settings: ProviderSettingsSnapshot) -> str:
        return self._translation_model_display_label(settings.translation.model)

    def _get_translation_connection_display_label(
        self,
        settings: ProviderSettingsSnapshot | None,
    ) -> str:
        if settings is None:
            return t("settings.not_selected")
        return self._translation_connection_display_label(settings.translation.connection)

    def _sync_activation_notice_card(self) -> None:
        settings = self._general_snapshot
        enabled = settings.activation_notice_enabled if settings is not None else True
        self._set_unit_card_value_text(
            self._activation_notice_button,
            t("settings.option.on" if enabled else "settings.option.off"),
        )
        self._activation_notice_button.disabled = settings is None
        _update_control_if_mounted(self._activation_notice_button)

    def _active_prompt_key_for_settings(
        self,
        settings: ProviderSettingsSnapshot | None,
    ) -> str:
        if settings is not None:
            if settings.llm_provider == LLMProviderName.LOCAL_LLM:
                return "local_llm"
            if settings.llm_provider == LLMProviderName.MANAGED_GEMMA:
                return "managed_gemma"
        return "qwen"

    def _active_prompt_key(self) -> str:
        return self._active_prompt_key_for_settings(self._build_settings_with_provider_draft())

    def _ensure_provider_prompt_value(
        self,
        settings: ProviderSettingsSnapshot,
        provider_name: str,
    ) -> str:
        prompt = self._prompt_editor.value
        if prompt.strip():
            return prompt
        prompt = load_prompt_for_provider(provider_name)
        return prompt

    def _current_source_language(self) -> str:
        if self._prompt_snapshot is None:
            return "en"
        return self._prompt_snapshot.source_language

    def _sync_prompt_tab_copy(self) -> None:
        self._custom_vocab_description_text.value = t("settings.custom_vocabulary.description")
        peer_auto_languages_title = getattr(self, "_peer_auto_languages_title", None)
        if peer_auto_languages_title is not None:
            peer_auto_languages_title.value = t("settings.peer_auto_languages.title")
        peer_auto_languages_editor = getattr(self, "_peer_auto_languages_editor", None)
        if peer_auto_languages_editor is not None and hasattr(
            peer_auto_languages_editor, "apply_locale"
        ):
            peer_auto_languages_editor.apply_locale()
        if is_control_mounted(self):
            for control in (
                self._custom_vocab_description_text,
                peer_auto_languages_title,
            ):
                if control is not None:
                    with contextlib.suppress(Exception):
                        control.update()

    def _sync_custom_vocabulary_editor_from_settings(self, *, preserve_input: bool = False) -> None:
        if self._prompt_snapshot is None:
            self._custom_vocab_tag_editor.set_terms([])
            self._custom_vocab_tag_editor.clear_input()
            return

        self._custom_vocab_tag_editor.set_terms(list(self._prompt_snapshot.custom_vocabulary_terms))
        if not preserve_input:
            self._custom_vocab_tag_editor.clear_input()

    def bind_recent_language_queue(self, dashboard: object) -> None:
        """Point the hint editor at the dashboard's shared recent-language queue."""
        codes = getattr(dashboard, "recent_language_codes", None)
        remember = getattr(dashboard, "remember_recent_language", None)
        editor = getattr(self, "_peer_auto_languages_editor", None)
        if editor is None or not callable(codes) or not callable(remember):
            return
        editor.bind_recent_languages(codes=codes, remember=remember)

    def _sync_peer_auto_languages_editor(
        self,
        settings: GeneralSettingsSnapshot | None = None,
    ) -> None:
        if not hasattr(self, "_peer_auto_languages_editor"):
            return
        settings = settings or self._general_snapshot
        languages = () if settings is None else tuple(settings.peer_expected_languages)
        pending = self._peer_auto_languages_pending
        if pending is not None and languages != pending:
            if (
                self._general_snapshot is not None
                and self._general_snapshot.peer_expected_languages != pending
            ):
                self._general_snapshot = replace(
                    self._general_snapshot,
                    peer_expected_languages=pending,
                )
            self._peer_auto_languages_editor.set_terms(list(pending))
            return
        self._peer_auto_languages_pending = None
        self._peer_auto_languages_editor.set_terms(list(languages))

    def _set_peer_auto_languages(self, languages: list[str]) -> None:
        if self._general_snapshot is None:
            return
        normalized = tuple(
            dict.fromkeys(language.strip() for language in languages if language.strip())
        )
        if self._general_snapshot.peer_expected_languages == normalized:
            return
        self._peer_auto_languages_pending = normalized
        self._general_snapshot = replace(
            self._general_snapshot,
            peer_expected_languages=normalized,
        )
        self._peer_auto_languages_editor.set_terms(list(normalized))
        recent_languages = self._peer_auto_languages_editor.recent_language_codes()
        self._emit_settings_changed(PeerExpectedLanguagesIntent(normalized, recent_languages))

    def _on_peer_auto_languages_add(self, language: str) -> None:
        if self._general_snapshot is None:
            return
        self._set_peer_auto_languages([*self._general_snapshot.peer_expected_languages, language])

    def _on_peer_auto_languages_remove(self, language: str) -> None:
        if self._general_snapshot is None:
            return
        self._set_peer_auto_languages(
            [
                current
                for current in self._general_snapshot.peer_expected_languages
                if current != language
            ]
        )

    def _normalize_custom_vocabulary_submitted_terms(self, raw_terms: list[str]) -> list[str]:
        terms: list[str] = []
        for raw_term in raw_terms:
            for part in _CUSTOM_VOCAB_DELIMITER_RE.split(str(raw_term)):
                normalized = part.strip()
                if normalized:
                    terms.append(normalized)
        return terms

    def _build_settings_with_provider_draft(self) -> ProviderSettingsSnapshot | None:
        return self._provider_draft or self._provider_snapshot

    def _ensure_provider_settings_draft(self) -> ProviderSettingsSnapshot:
        assert self._provider_snapshot is not None
        if self._provider_draft is None:
            self._provider_draft = self._provider_snapshot
        return self._provider_draft

    def _record_provider_edit(self, edit: ProviderSettingsEdit) -> None:
        self._provider_edits[type(edit)] = edit

    def _translation_selection_edit(
        self,
        selection: TranslationSelectionSnapshot,
    ) -> TranslationSelectionEdit:
        committed_history = (
            {}
            if self._provider_snapshot is None
            else dict(self._provider_snapshot.translation.connection_history)
        )
        return TranslationSelectionEdit(
            selection,
            tuple(
                (model, connection)
                for model, connection in selection.connection_history
                if committed_history.get(model) != connection
            ),
        )

    def _stt_provider_display_label(
        self,
        provider: STTProviderName,
        *,
        custom_mode: str = "offline",
    ) -> str:
        if provider == STTProviderName.NONE:
            return t("settings.not_selected")
        return provider_label(
            display_stt_provider(
                provider,
                custom_mode=custom_mode,
            ).value
        )

    def _display_stt_choice(
        self,
        settings: ProviderSettingsSnapshot,
        *,
        peer: bool = False,
    ) -> STTProviderName:
        provider = self._effective_peer_stt_provider(settings) if peer else settings.stt_provider
        return display_stt_provider(
            provider,
            custom_mode=settings.custom_stt_mode,
        )

    def _normalized_peer_stt_provider(self, provider: STTProviderName) -> STTProviderName:
        return provider

    def _effective_peer_stt_provider(
        self,
        settings: ProviderSettingsSnapshot | None,
    ) -> STTProviderName:
        if settings is None:
            return STTProviderName.NONE
        return self._normalized_peer_stt_provider(settings.peer_stt_provider)

    def _peer_stt_option_item(self, provider: STTProviderName) -> OptionItem:
        return self._stt_option_item(provider)

    def _classified_peer_stt_option_item(self, provider: STTProviderName) -> OptionItem:
        item = self._peer_stt_option_item(provider)
        section_key = _STT_SECTION_BY_PROVIDER.get(provider, "")
        section = t(section_key) if section_key else ""
        return OptionItem(
            value=item.value,
            label=item.label,
            description=item.description,
            disabled=item.disabled,
            section=section,
        )

    def _stt_option_item(self, provider: STTProviderName) -> OptionItem:
        description = t(f"provider.{provider.value}.description", default="")
        return OptionItem(
            value=provider.value,
            label=provider_label(provider.value).replace("\n", " "),
            description=description,
        )

    def _classified_stt_option_item(self, provider: STTProviderName) -> OptionItem:
        item = self._stt_option_item(provider)
        section_key = _STT_SECTION_BY_PROVIDER.get(provider, "")
        section = t(section_key) if section_key else ""
        return OptionItem(
            value=item.value,
            label=item.label,
            description=item.description,
            disabled=item.disabled,
            section=section,
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
        self._local_llm_extra_body.error = message
        _update_control_if_mounted(self._local_llm_extra_body)
        _update_control_if_mounted(self._local_llm_extra_body_error)

    def _on_local_llm_field_change(self, e) -> None:
        _ = e
        if self._provider_snapshot is None:
            return
        current = self._provider_draft or self._provider_snapshot
        if current.llm_provider != LLMProviderName.LOCAL_LLM:
            return
        self._ensure_provider_settings_draft()
        self.has_provider_changes = True

    def _clear_local_llm_extra_body_error(self) -> None:
        self._local_llm_extra_body_error_key = ""
        self._local_llm_extra_body_error_kwargs = {}
        self._local_llm_extra_body_error.value = ""
        self._local_llm_extra_body_error.visible = False
        self._local_llm_extra_body.error = None
        _update_control_if_mounted(self._local_llm_extra_body)
        _update_control_if_mounted(self._local_llm_extra_body_error)

    def _on_local_llm_base_url_change_end(self, e) -> None:
        _ = e
        if self._provider_snapshot is None:
            return
        raw_value = self._local_llm_base_url.value or ""
        try:
            normalized = normalize_local_llm_base_url(raw_value)
        except ValueError:
            self._local_llm_base_url.error = t("settings.local_llm.base_url.invalid")
            _update_control_if_mounted(self._local_llm_base_url)
            return

        self._local_llm_base_url.error = None
        self._local_llm_base_url.value = normalized
        current = self._provider_draft or self._provider_snapshot
        if current.local_llm_base_url != normalized:
            draft = self._ensure_provider_settings_draft()
            self._provider_draft = replace(draft, local_llm_base_url=normalized)
            self._record_provider_edit(LocalLlmBaseUrlEdit(self._provider_draft.local_llm_base_url))
            self.has_provider_changes = True
        _update_control_if_mounted(self._local_llm_base_url)

    def _on_local_llm_model_change_end(self, e) -> None:
        _ = e
        if self._provider_snapshot is None:
            return
        model = (self._local_llm_model.value or "").strip()
        if not model:
            self._local_llm_model.error = t("settings.local_llm.model.required")
            _update_control_if_mounted(self._local_llm_model)
            return

        self._local_llm_model.error = None
        self._local_llm_model.value = model
        current = self._provider_draft or self._provider_snapshot
        if current.local_llm_model != model:
            draft = self._ensure_provider_settings_draft()
            self._provider_draft = replace(draft, local_llm_model=model)
            self._record_provider_edit(LocalLlmModelEdit(self._provider_draft.local_llm_model))
            self.has_provider_changes = True
        _update_control_if_mounted(self._local_llm_model)

    def _on_local_llm_extra_body_change_end(self, e) -> None:
        _ = e
        if self._provider_snapshot is None:
            return
        raw = (self._local_llm_extra_body.value or "").strip()
        try:
            parsed = (
                {"reasoning_effort": "none", "temperature": 0.6}
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
        except TypeError, ValueError:
            self._show_local_llm_extra_body_error("settings.local_llm.extra_body.not_serializable")
            return

        normalized = copy.deepcopy(parsed)
        normalized_text = json.dumps(
            normalized,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        current = self._provider_draft or self._provider_snapshot
        if current.local_llm_extra_body_json != normalized_text:
            draft = self._ensure_provider_settings_draft()
            self._provider_draft = replace(draft, local_llm_extra_body_json=normalized_text)
            self._record_provider_edit(
                LocalLlmExtraBodyEdit(self._provider_draft.local_llm_extra_body_json)
            )
            self.has_provider_changes = True
        self._local_llm_extra_body.value = normalized_text
        self._clear_local_llm_extra_body_error()
        _update_control_if_mounted(self._local_llm_extra_body)

    def _commit_local_llm_fields_from_controls(self) -> None:
        if self._provider_snapshot is None:
            return
        current = self._provider_draft or self._provider_snapshot
        if current.llm_provider != LLMProviderName.LOCAL_LLM:
            return
        self._on_local_llm_base_url_change_end(None)
        self._on_local_llm_model_change_end(None)
        self._on_local_llm_extra_body_change_end(None)

    def _stage_prompt_draft(self, value: str) -> None:
        if self._prompt_snapshot is None:
            return
        committed_prompt = self._committed_prompt_value()
        self.has_pending_prompt_changes = value != committed_prompt
        if self.has_pending_prompt_changes:
            self._record_provider_edit(SystemPromptEdit(value))
        else:
            self._provider_edits.pop(SystemPromptEdit, None)

    def _committed_prompt_value(self) -> str:
        if self._prompt_snapshot is None:
            return ""
        return self._prompt_snapshot.system_prompt

    def _build_provider_apply_intent(self) -> ProviderApplyIntent | None:
        self._commit_local_llm_fields_from_controls()
        self._commit_backup_local_fields_from_controls()
        if self._provider_snapshot is None:
            return None
        return ProviderApplyIntent(tuple(self._provider_edits.values()))

    def consume_immediate_provider_apply_settings(self) -> ProviderApplyIntent | None:
        intent = self._immediate_provider_apply_intent
        self._immediate_provider_apply_intent = None
        return intent

    def build_provider_apply_settings(self) -> ProviderApplyIntent | None:
        return self._build_provider_apply_intent()

    def acknowledge_provider_apply_settings(self, intent: ProviderApplyIntent) -> None:
        snapshot = self._provider_snapshot
        if snapshot is None:
            return
        for edit in intent.edits:
            edit_type = type(edit)
            field_name = _DRAFT_PROVIDER_FIELDS.get(edit_type)
            translation_field = _DRAFT_TRANSLATION_FIELDS.get(edit_type)
            if edit_type is TranslationSelectionEdit:
                selection = edit.selection
                history = dict(snapshot.translation.connection_history)
                history.update(edit.history_updates)
                translation = replace(
                    snapshot.translation,
                    model=selection.model,
                    connection=selection.connection,
                    connection_history=tuple(history.items()),
                    previous_llm_model=selection.previous_llm_model,
                )
                snapshot = self._provider_snapshot_with_translation(snapshot, translation)
            elif field_name is not None:
                value = getattr(edit, fields(edit)[0].name)
                snapshot = replace(snapshot, **{field_name: value})
            elif translation_field is not None:
                value = getattr(edit, fields(edit)[0].name)
                snapshot = replace(
                    snapshot,
                    translation=replace(snapshot.translation, **{translation_field: value}),
                )
            elif edit_type is SystemPromptEdit and self._prompt_snapshot is not None:
                self._prompt_snapshot = replace(self._prompt_snapshot, system_prompt=edit.value)
            if self._provider_edits.get(edit_type) == edit:
                del self._provider_edits[edit_type]
        self._provider_snapshot = snapshot
        self.has_provider_changes = any(
            edit_type is not SystemPromptEdit for edit_type in self._provider_edits
        )
        self.has_pending_prompt_changes = (
            self._prompt_snapshot is not None
            and self._prompt_editor.value != self._prompt_snapshot.system_prompt
        )
        if not self.has_provider_changes:
            self._provider_draft = None

    def acknowledge_prompt_apply_settings(self, intent: PromptApplyIntent) -> None:
        if self._prompt_snapshot is None:
            return
        self._prompt_snapshot = replace(self._prompt_snapshot, system_prompt=intent.value)
        self._stage_prompt_draft(self._prompt_editor.value)

    def _rebase_provider_draft(self, provider: ProviderSettingsSnapshot) -> None:
        if self._provider_draft is None:
            return
        updates: dict[str, object] = {}
        translation_updates: dict[str, object] = {}
        for edit_type, edit in self._provider_edits.items():
            field_name = _DRAFT_PROVIDER_FIELDS.get(edit_type)
            if edit_type is TranslationSelectionEdit:
                selection = edit.selection
                history = dict(provider.translation.connection_history)
                history.update(edit.history_updates)
                translation_updates.update(
                    model=selection.model,
                    connection=selection.connection,
                    connection_history=tuple(history.items()),
                    previous_llm_model=selection.previous_llm_model,
                )
            elif field_name is not None:
                updates[field_name] = getattr(edit, fields(edit)[0].name)
            translation_field = _DRAFT_TRANSLATION_FIELDS.get(edit_type)
            if translation_field is not None:
                translation_updates[translation_field] = getattr(edit, fields(edit)[0].name)
        if translation_updates:
            updates["translation"] = replace(provider.translation, **translation_updates)
        self._provider_draft = replace(provider, **updates)
        if TranslationSelectionEdit in self._provider_edits:
            self._provider_draft = self._provider_snapshot_with_translation(
                self._provider_draft, self._provider_draft.translation
            )

    # --- Load Settings ---
    def load_from_settings(
        self,
        *,
        provider: ProviderSettingsSnapshot,
        general: GeneralSettingsSnapshot,
        prompt: PromptSettingsSnapshot,
        overlay: OverlaySettingsSnapshot,
        config_path: Path,
        preserve_custom_vocab_draft: bool = False,
    ) -> None:
        """Load current settings into the UI."""
        previous = self._build_settings_with_provider_draft()
        selection_changed = (
            self._provider_snapshot is not None
            and self._provider_snapshot.translation != provider.translation
        )
        raw_drafts = []
        if selection_changed and previous is not None:
            for control, field_name in (
                (self._local_llm_base_url, "local_llm_base_url"),
                (self._local_llm_model, "local_llm_model"),
                (self._local_llm_extra_body, "local_llm_extra_body_json"),
                (self._backup_local_base_url, "backup_local_base_url"),
                (self._backup_local_model, "backup_local_model"),
                (self._backup_local_extra_body, "backup_local_extra_body_json"),
                (self._custom_stt_endpoint, "custom_stt_endpoint"),
                (self._custom_stt_model, "custom_stt_model"),
                (self._custom_stt_extra, "custom_stt_extra_json"),
            ):
                if control.value != getattr(previous, field_name):
                    raw_drafts.append((control, control.value, control.error))
        preserve_vocabulary_input = (
            selection_changed
            and self._prompt_snapshot is not None
            and self._prompt_snapshot.source_language == prompt.source_language
        )
        self._rebase_provider_draft(provider)
        draft = self._provider_draft
        prompt_draft = self._prompt_editor.value if self.has_pending_prompt_changes else None
        self._provider_snapshot = provider
        self._general_snapshot = general
        self._prompt_snapshot = prompt
        self._overlay_snapshot = overlay
        self._config_path = config_path
        self._http_extension_runtime_reload_pending = False
        display_provider = draft or provider
        self._desktop_overlay_pending_size_preset = None
        self._desktop_overlay_pending_position_reset = False
        self._desktop_overlay_pending_locked = None
        self._desktop_overlay_captions_locked = False
        if self._overlay_state == "off":
            self._overlay_runtime_target = self._current_overlay_target()
        self._sync_clickable_text_control_fonts(font_for_language(general.locale))

        # UI Language
        self._ui_text.content.value = locale_label(general.locale)

        # STT Provider
        self._set_unit_card_value_text(
            self._stt_text,
            self._stt_provider_display_label(
                display_provider.stt_provider,
                custom_mode=display_provider.custom_stt_mode,
            ),
        )
        self._set_unit_card_value_text(
            self._peer_stt_text,
            self._stt_provider_display_label(
                self._effective_peer_stt_provider(display_provider),
                custom_mode=display_provider.custom_stt_mode,
            ),
        )
        self._update_api_visibility()
        self._sync_gpu_device_card()

        # LLM Provider
        self._set_unit_card_value_text(
            self._llm_text,
            self._get_llm_display_label(display_provider),
        )
        self._set_translation_connection_text(
            self._get_translation_connection_display_label(display_provider),
        )
        self._sync_translation_connection_title(display_provider)
        self._set_unit_card_value_text(
            self._backup_translation_text,
            self._backup_display_label(display_provider),
        )
        self._local_llm_base_url.value = display_provider.local_llm_base_url
        self._local_llm_base_url.error = None
        self._local_llm_model.value = display_provider.local_llm_model
        self._local_llm_model.error = None
        self._local_llm_extra_body.value = display_provider.local_llm_extra_body_json
        self._clear_local_llm_extra_body_error()

        # Audio Settings
        self._audio_settings.host_api = general.input_host_api
        self._audio_settings.microphone = general.input_device
        self._audio_settings.desktop_output_device = general.output_device
        self._sync_general_audio_card_texts()

        # VAD
        self._vad_slider.value = general.self_vad_speech_threshold
        self._vad_slider.label = f"{general.self_vad_speech_threshold:.2f}"
        self._peer_vad_slider.value = general.peer_vad_speech_threshold
        self._peer_vad_slider.label = f"{general.peer_vad_speech_threshold:.2f}"
        self._vrc_mic_text.content.value = t(
            "settings.vrc_mic.on" if general.vrc_mic_intercept else "settings.vrc_mic.off"
        )
        self._sync_osc_connection_card(general)
        self._chatbox_source_text.content.value = t(
            "settings.chatbox_source.on"
            if general.chatbox_include_source
            else "settings.chatbox_source.off"
        )
        self._clipboard_auto_translate_text.content.value = t(
            "settings.clipboard_auto_translate.on"
            if general.clipboard_auto_translate_enabled
            else "settings.clipboard_auto_translate.off"
        )
        self._sync_activation_notice_card()
        # Prompt
        provider_name = self._active_prompt_key()
        self._prompt_editor.set_provider(provider_name)
        if prompt_draft is not None:
            self._prompt_editor.value = prompt_draft
        elif prompt.system_prompt.strip():
            self._prompt_editor.value = prompt.system_prompt
        else:
            self._prompt_editor.load_default_prompt(emit_change=False)
            self._prompt_snapshot = replace(prompt, system_prompt=self._prompt_editor.value)

        _ = preserve_custom_vocab_draft
        self._sync_custom_vocabulary_editor_from_settings(preserve_input=preserve_vocabulary_input)
        self._sync_peer_auto_languages_editor()
        self._sync_prompt_tab_copy()
        self._overlay_peer_contract = None
        self._sync_overlay_controls()
        self.set_overlay_calibration(
            OverlayCalibration(
                anchor=overlay.calibration.anchor,
                distance=overlay.calibration.distance,
                offset_x=overlay.calibration.offset_x,
                offset_y=overlay.calibration.offset_y,
                text_scale=overlay.calibration.text_scale,
            ),
            preserve_draft=self._overlay_calibration_session_active,
        )

        # Load secrets
        self._load_secrets(provider, config_path)
        for control, value, error in raw_drafts:
            control.value = value
            control.error = error

        if is_control_mounted(self):
            self.update()

    def refresh_after_openrouter_pkce_success(
        self,
        *,
        provider: ProviderSettingsSnapshot,
        prompt: PromptSettingsSnapshot,
        config_path: Path,
    ) -> None:
        self._provider_snapshot = provider
        self._provider_draft = None
        self._provider_edits.clear()
        self._prompt_snapshot = prompt
        self._config_path = config_path
        self.has_provider_changes = False
        self.has_pending_prompt_changes = False
        self._desktop_overlay_pending_size_preset = None
        self._desktop_overlay_pending_position_reset = False
        self._desktop_overlay_pending_locked = None
        self._desktop_overlay_captions_locked = False

        self._set_unit_card_value_text(
            self._llm_text,
            self._get_llm_display_label(provider),
        )
        self._set_translation_connection_text(
            self._get_translation_connection_display_label(provider),
        )
        self._update_api_visibility()

        provider_name = self._active_prompt_key()
        self._prompt_editor.set_provider(provider_name)
        if prompt.system_prompt.strip():
            self._prompt_editor.value = prompt.system_prompt
        else:
            self._prompt_editor.load_default_prompt(emit_change=False)
            self._prompt_snapshot = replace(prompt, system_prompt=self._prompt_editor.value)
        self._sync_custom_vocabulary_editor_from_settings()
        self._sync_peer_auto_languages_editor()
        self._sync_prompt_tab_copy()

        if is_control_mounted(self):
            self.update()

    def project_osc_control_state(self, state: OscControlPresentationState) -> None:
        control = state.changed_control
        if control in {"PuriPuly_Talk", "PuriPuly_Trans"}:
            return

        if control == "PuriPuly_SelfSrcLang" and self._prompt_snapshot is not None:
            self._prompt_snapshot = replace(
                self._prompt_snapshot,
                source_language=state.self_source_language,
                custom_vocabulary_enabled=state.custom_vocabulary_enabled,
                custom_vocabulary_terms=state.custom_vocabulary_terms,
                custom_vocabulary_other_languages_have_terms=(
                    state.custom_vocabulary_other_languages_have_terms
                ),
            )
            self._custom_vocab_tag_editor.set_terms(list(state.custom_vocabulary_terms))
        if control in {"PuriPuly_PeerAuto", "PuriPuly_PeerSrcLang"}:
            if self._general_snapshot is not None:
                self._general_snapshot = replace(
                    self._general_snapshot,
                    effective_peer_source_language=state.peer_source_language,
                )
        if control in {
            "PuriPuly_PeerAuto",
            "PuriPuly_SelfSrcLang",
            "PuriPuly_SelfDstLang",
            "PuriPuly_SelfDstLang2",
            "PuriPuly_PeerSrcLang",
            "PuriPuly_PeerDstLang",
        }:
            return

        if self._provider_snapshot is not None:
            self._provider_snapshot = self._project_osc_provider_snapshot(
                self._provider_snapshot,
                state,
            )
        if self._provider_draft is not None:
            self._provider_draft = self._project_osc_provider_snapshot(
                self._provider_draft,
                state,
            )
            self._rebase_provider_edits_after_osc(control)
        if self._general_snapshot is not None:
            if control == "PuriPuly_MuteSync":
                self._general_snapshot = replace(
                    self._general_snapshot,
                    vrc_mic_intercept=state.mute_sync,
                )
            elif control == "PuriPuly_ChatboxSource":
                self._general_snapshot = replace(
                    self._general_snapshot,
                    chatbox_include_source=state.chatbox_source,
                )

        display_settings = self._build_settings_with_provider_draft()
        if display_settings is None:
            return
        if control in {"PuriPuly_SelfASR", "PuriPuly_PeerASR"}:
            self._sync_stt_provider_labels(display_settings)
            self._update_api_visibility()
            _update_control_if_mounted(self._stt_text)
            _update_control_if_mounted(self._peer_stt_text)
        elif control == "PuriPuly_Translator":
            self._set_unit_card_value_text(
                self._llm_text,
                self._get_llm_display_label(display_settings),
            )
            self._set_translation_connection_text(
                self._get_translation_connection_display_label(display_settings)
            )
            self._sync_translation_connection_title(display_settings)
            self._update_api_visibility()
            _update_control_if_mounted(self._llm_text)
            _update_control_if_mounted(self._translation_connection_row)
        elif control == "PuriPuly_MuteSync":
            self._vrc_mic_text.content.value = t(
                "settings.vrc_mic.on" if state.mute_sync else "settings.vrc_mic.off"
            )
            _update_control_if_mounted(self._vrc_mic_text)
        elif control == "PuriPuly_ChatboxSource":
            self._chatbox_source_text.content.value = t(
                "settings.chatbox_source.on"
                if state.chatbox_source
                else "settings.chatbox_source.off"
            )
            _update_control_if_mounted(self._chatbox_source_text)
        elif control in {"PuriPuly_Listen", "PuriPuly_Captions"}:
            self._sync_overlay_controls()

    @staticmethod
    def _project_osc_provider_snapshot(
        snapshot: ProviderSettingsSnapshot,
        state: OscControlPresentationState,
    ) -> ProviderSettingsSnapshot:
        control = state.changed_control
        if control in {"PuriPuly_SelfASR", "PuriPuly_PeerASR"}:
            if control == "PuriPuly_SelfASR":
                return replace(
                    snapshot,
                    stt_provider=STTProviderName(state.self_asr_setting),
                    custom_stt_mode=state.custom_stt_mode,
                    custom_stt_compatibility=state.custom_stt_compatibility,
                )
            return replace(
                snapshot,
                peer_stt_provider=STTProviderName(state.peer_asr_setting),
                custom_stt_mode=state.custom_stt_mode,
                custom_stt_compatibility=state.custom_stt_compatibility,
            )
        if control == "PuriPuly_Translator":
            translation = replace(
                snapshot.translation,
                model=TranslationModel(state.translation_model),
                connection=TranslationConnection(state.translation_connection),
                connection_history=tuple(
                    (TranslationModel(model), TranslationConnection(connection))
                    for model, connection in state.translation_connection_history
                ),
                http_extension_id=state.translation_http_extension_id,
                previous_llm_model=(
                    None
                    if state.translation_previous_model is None
                    else TranslationModel(state.translation_previous_model)
                ),
            )
            return replace(
                snapshot,
                llm_provider=LLMProviderName(state.llm_provider),
                translation=translation,
                openrouter_llm_model=parse_openrouter_llm_model(state.openrouter_llm_model),
                openrouter_selected_source=OpenRouterCredentialSource(
                    state.openrouter_selected_source
                ),
                openrouter_selection_alias=(
                    None
                    if state.openrouter_selection_alias is None
                    else OpenRouterSelectionAlias(state.openrouter_selection_alias)
                ),
            )
        return snapshot

    def _rebase_provider_edits_after_osc(self, control: str) -> None:
        draft = self._provider_draft
        if draft is None:
            return
        if control == "PuriPuly_SelfASR" and SelfSttProviderEdit in self._provider_edits:
            self._record_provider_edit(SelfSttProviderEdit(draft.stt_provider))
        if control == "PuriPuly_PeerASR" and PeerSttProviderEdit in self._provider_edits:
            self._record_provider_edit(PeerSttProviderEdit(draft.peer_stt_provider))
        if control == "PuriPuly_Translator" and (TranslationSelectionEdit in self._provider_edits):
            self._record_provider_edit(self._translation_selection_edit(draft.translation))

    def _load_secrets(self, settings: ProviderSettingsSnapshot, config_path: Path) -> None:
        """Load secret values into fields."""
        _ = config_path
        result = self._load_secret_snapshot()
        if result is None or result.snapshot is None:
            return
        snapshot = result.snapshot

        if snapshot.local_llm_api_key is not None:
            self._local_llm_api_key.value = snapshot.local_llm_api_key

    def _load_secret_snapshot(
        self,
    ) -> SettingsSecretLoadResult[SettingsSecretSnapshot] | None:
        if self._settings_secrets is None:
            return None
        result = self._settings_secrets.load()
        if result.error_message is not None:
            self._emit_runtime_basic(result.error_message, level=logging.WARNING)
        return result

    # --- Visibility Updates ---
    def _update_api_visibility(self) -> None:
        """Update remaining provider surface visibility."""
        settings = self._build_settings_with_provider_draft()
        if settings is None:
            return

        is_custom_http = settings.translation.model == TranslationModel.CUSTOM_HTTP
        if hasattr(self, "_http_extension_credentials"):
            self._http_extension_credentials.visible = is_custom_http
            _update_control_if_mounted(self._http_extension_credentials)
        # Removed from the surface for now: the future model folds the
        # connection choice into the translation / backup-translation cards.
        self._translation_connection_row.visible = False
        translation_model = normalize_translation_model(settings.translation.model)
        self._local_llm_connection_card.visible = (
            translation_model == TranslationModel.LOCAL_LLM
        )
        self._openai_compatible_card.visible = (
            translation_model == TranslationModel.OPENAI_COMPATIBLE
        )
        self._backup_config_card.visible = settings.backup_enabled
        backup_variant = normalize_translation_model(settings.backup_variant)
        self._backup_network_group.visible = (
            backup_variant == TranslationModel.OPENAI_COMPATIBLE
        )
        self._backup_local_group.visible = backup_variant == TranslationModel.LOCAL_LLM
        self._sync_http_extension_card(settings)
        for control in (
            self._openai_compatible_card,
            self._local_llm_connection_card,
            self._backup_config_card,
            self._backup_network_group,
            self._backup_local_group,
        ):
            _update_control_if_mounted(control)

    # --- Event Handlers ---
    @staticmethod
    def _engine_backend_label(backend: str) -> str:
        return dict(ENGINE_BACKENDS).get(backend, backend)

    def _engine_backend_availability(self) -> dict[str, bool]:
        devices = getattr(self, "_gpu_devices", ())
        known = {device.kind for device in devices if device.kind}
        if not known:
            return {"cpu": True, "vulkan": True, "cuda": True}
        return {
            "cpu": True,
            "vulkan": "vulkan" in known,
            "cuda": "cuda" in known,
        }

    def _open_stt_engine_modal(
        self,
        *,
        title: str,
        current_engine: str,
        current_backend: str,
        on_select_none,
        on_select_engine,
    ) -> None:
        if self.on_gpu_discovery_requested is not None:
            self.on_gpu_discovery_requested()
        SttEngineModal(
            self.page,
            title,
            none_label=t("settings.not_selected"),
            engine_label=provider_label(STTProviderName.LOCAL_QWEN_GPU.value),
            engine_description=t("provider.local_qwen_gpu.description", default=""),
            current_engine=current_engine,
            current_backend=current_backend,
            backend_available=self._engine_backend_availability(),
            on_select_none=on_select_none,
            on_select_engine=on_select_engine,
        ).open()

    def _on_stt_click(self, e) -> None:
        """Open the recognition engine selection modal."""
        if not is_control_mounted(self):
            return
        display_settings = self._build_settings_with_provider_draft()
        if display_settings is None:
            return
        self._open_stt_engine_modal(
            title=t("settings.section.stt"),
            current_engine=display_settings.stt_provider.value,
            current_backend=display_settings.stt_gpu_backend,
            on_select_none=lambda: self._on_stt_selected(STTProviderName.NONE.value),
            on_select_engine=self._on_stt_engine_selected,
        )

    def _on_stt_engine_selected(self, backend: str) -> None:
        if self._provider_snapshot is None:
            return
        current_settings = self._build_settings_with_provider_draft()
        if current_settings is None:
            return
        if current_settings.stt_gpu_backend != backend:
            draft = self._ensure_provider_settings_draft()
            if draft.stt_gpu_device_id != "auto":
                self._provider_draft = replace(draft, stt_gpu_device_id="auto")
                self._record_provider_edit(SttGpuDeviceEdit("auto"))
                draft = self._provider_draft
            self._provider_draft = replace(draft, stt_gpu_backend=backend)
            self._record_provider_edit(SttGpuBackendEdit(backend))
            self.has_provider_changes = True
        if current_settings.stt_provider != STTProviderName.LOCAL_QWEN_GPU:
            self._on_stt_selected(STTProviderName.LOCAL_QWEN_GPU.value)
            return
        self._sync_stt_provider_labels(self._build_settings_with_provider_draft())
        self._sync_gpu_device_card()

    def _on_stt_selected(self, value: str) -> None:
        """Handle STT provider selection from modal."""
        if self._provider_snapshot is None:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        selected = STTProviderName(value)
        if self._display_stt_choice(current_settings) == selected:
            return
        old_provider = current_settings.stt_provider.value
        self._emit_runtime_basic(
            f"[Settings] STT provider draft changed: {old_provider} -> {selected.value}"
        )
        draft = self._ensure_provider_settings_draft()
        self._provider_draft = replace(
            draft,
            stt_provider=selected,
        )
        if selected != current_settings.stt_provider:
            self._record_provider_edit(SelfSttProviderEdit(selected))
        if (
            selected == STTProviderName.LOCAL_QWEN_GPU
            and self.on_gpu_discovery_requested is not None
        ):
            self.on_gpu_discovery_requested()
        self._update_api_visibility()
        self._sync_gpu_device_card()
        self.has_provider_changes = True

        self._sync_stt_provider_labels(self._provider_draft)

        source_lang = self._current_source_language()
        selection = resolve_local_asr_selection(selected.value, source_lang)
        if selected != STTProviderName.NONE and not selection.supported:
            self._show_stt_selection_notice(t("local_stt.language_unsupported"))
        warning = (
            None
            if selected == STTProviderName.NONE or not selection.supported
            else get_stt_compatibility_warning(
                source_lang,
                selected.value,
            )
        )
        if warning:
            message = t(warning.key, language=language_name(warning.language_code))
            self._show_stt_selection_notice(message)

        if is_control_mounted(self):
            self._stt_text.update()
            self._peer_stt_text.update()

    def _on_peer_stt_click(self, e) -> None:
        if not is_control_mounted(self):
            return
        display_settings = self._build_settings_with_provider_draft()
        if display_settings is None:
            return
        if display_settings.stt_shared_engine:
            return
        self._open_stt_engine_modal(
            title=t("settings.section.peer_stt"),
            current_engine=display_settings.peer_stt_provider.value,
            current_backend=display_settings.peer_stt_gpu_backend,
            on_select_none=lambda: self._on_peer_stt_selected(STTProviderName.NONE.value),
            on_select_engine=self._on_peer_stt_engine_selected,
        )

    def _on_peer_stt_engine_selected(self, backend: str) -> None:
        if self._provider_snapshot is None:
            return
        current_settings = self._build_settings_with_provider_draft()
        if current_settings is None:
            return
        if current_settings.peer_stt_gpu_backend != backend:
            draft = self._ensure_provider_settings_draft()
            if draft.peer_stt_gpu_device_id != "auto":
                self._provider_draft = replace(draft, peer_stt_gpu_device_id="auto")
                self._record_provider_edit(PeerSttGpuDeviceEdit("auto"))
                draft = self._provider_draft
            self._provider_draft = replace(draft, peer_stt_gpu_backend=backend)
            self._record_provider_edit(PeerSttGpuBackendEdit(backend))
            self.has_provider_changes = True
        if current_settings.peer_stt_provider != STTProviderName.LOCAL_QWEN_GPU:
            self._on_peer_stt_selected(STTProviderName.LOCAL_QWEN_GPU.value)
            return
        self._sync_stt_provider_labels(self._build_settings_with_provider_draft())
        self._sync_gpu_device_card()

    def _on_shared_engine_click(self, _event) -> None:
        if not is_control_mounted(self):
            return
        settings = self._build_settings_with_provider_draft()
        current = (
            "on" if settings is not None and settings.stt_shared_engine else "off"
        )
        SettingsModal(
            self.page,
            t("settings.shared_engine.title"),
            [
                OptionItem(value="on", label=t("settings.option.on")),
                OptionItem(value="off", label=t("settings.option.off")),
            ],
            self._on_shared_engine_selected,
        ).open(current)

    def _on_shared_engine_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        enabled = value == "on"
        settings = self._build_settings_with_provider_draft()
        if settings is not None and settings.stt_shared_engine == enabled:
            return
        draft = self._ensure_provider_settings_draft()
        self._provider_draft = replace(draft, stt_shared_engine=enabled)
        self._record_provider_edit(SttSharedEngineEdit(enabled))
        self.has_provider_changes = True
        self._sync_stt_provider_labels(self._build_settings_with_provider_draft())
        self._update_api_visibility()
        self._sync_gpu_device_card()

    def _on_peer_stt_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        selected = STTProviderName(value)
        if self._display_stt_choice(current_settings, peer=True) == selected:
            return
        draft = self._ensure_provider_settings_draft()
        self._provider_draft = replace(
            draft,
            peer_stt_provider=selected,
        )
        if selected != current_settings.peer_stt_provider:
            self._record_provider_edit(PeerSttProviderEdit(selected))
        if (
            selected == STTProviderName.LOCAL_QWEN_GPU
            and self.on_gpu_discovery_requested is not None
        ):
            self.on_gpu_discovery_requested()
        selection = resolve_local_asr_selection(
            selected.value,
            (
                self._general_snapshot.effective_peer_source_language
                if self._general_snapshot is not None
                else "en"
            ),
        )
        if selected != STTProviderName.NONE and not selection.supported:
            self._show_stt_selection_notice(t("local_stt.language_unsupported"))
        self._sync_stt_provider_labels(self._provider_draft)
        self._update_api_visibility()
        self._sync_gpu_device_card()
        if is_control_mounted(self):
            self._peer_stt_text.update()
            self._stt_text.update()
        self.has_provider_changes = True

    def _stt_engine_value_label(
        self,
        settings: ProviderSettingsSnapshot,
        *,
        peer: bool,
    ) -> str:
        if peer and settings.stt_shared_engine:
            provider = settings.stt_provider
        elif peer:
            provider = settings.peer_stt_provider
        else:
            provider = settings.stt_provider
        return self._stt_provider_display_label(
            provider,
            custom_mode=settings.custom_stt_mode,
        )

    def _sync_stt_provider_labels(self, settings: ProviderSettingsSnapshot) -> None:
        self._set_unit_card_value_text(
            self._stt_text,
            self._stt_engine_value_label(settings, peer=False),
        )
        self._set_unit_card_value_text(
            self._peer_stt_text,
            self._stt_engine_value_label(settings, peer=True),
        )

    def _show_stt_selection_notice(self, message: str) -> None:
        if self.show_snackbar:
            self.show_snackbar(message, COLOR_WARNING)
        elif is_control_mounted(self):
            self.page.show_dialog(
                ft.SnackBar(
                    ft.Text(message, color=ft.Colors.WHITE),
                    bgcolor=COLOR_WARNING,
                    duration=4000,
                    behavior=ft.SnackBarBehavior.FLOATING,
                    elevation=0,
                    margin=ft.Margin.only(bottom=90),
                    padding=20,
                )
            )

    def _translation_picker_options(self) -> list[OptionItem]:
        options: list[OptionItem] = [
            OptionItem(
                value=TranslationModel.NONE.value,
                label=t("settings.not_selected"),
            )
        ]
        for model in (TranslationModel.OPENAI_COMPATIBLE, TranslationModel.LOCAL_LLM):
            options.append(
                OptionItem(
                    value=model.value,
                    label=self._translation_model_display_label(model),
                    description=self._translation_model_display_description(model),
                )
            )
        return options

    def _backup_picker_options(self) -> list[OptionItem]:
        options: list[OptionItem] = [
            OptionItem(value="off", label=t("settings.not_selected")),
        ]
        for model in (TranslationModel.OPENAI_COMPATIBLE, TranslationModel.LOCAL_LLM):
            options.append(
                OptionItem(
                    value=model.value,
                    label=self._translation_model_display_label(model),
                    description=self._translation_model_display_description(model),
                )
            )
        return options

    def _on_llm_click(self, e) -> None:
        """Open translation model selection modal."""
        if not is_control_mounted(self):
            return
        options = self._translation_picker_options()
        display_settings = self._build_settings_with_provider_draft()
        current = (
            self._get_llm_modal_value(display_settings)
            if display_settings is not None
            else TranslationModel.NONE.value
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
        history: tuple[tuple[TranslationModel, TranslationConnection], ...],
    ) -> TranslationConnection:
        connection = dict(history).get(model)
        if connection in supported_translation_connections(model):
            return connection
        return default_translation_connection(model)

    def _sync_translation_selection_controls(self, settings: ProviderSettingsSnapshot) -> None:
        self._set_unit_card_value_text(
            self._llm_text,
            self._get_llm_display_label(settings),
        )
        self._set_translation_connection_text(
            self._get_translation_connection_display_label(settings),
        )
        self._sync_translation_connection_title(settings)
        self._set_unit_card_value_text(
            self._backup_translation_text,
            self._backup_display_label(settings),
        )
        self._openai_base_url.value = settings.openai_compatible_base_url
        self._openai_model.value = settings.openai_compatible_model
        self._openai_provider.value = self._provider_dropdown_value(
            settings.openai_compatible_provider_key,
            settings.openai_compatible_base_url,
        )
        self._backup_base_url.value = settings.backup_base_url
        self._backup_model.value = settings.backup_model
        self._backup_provider.value = self._provider_dropdown_value(
            settings.backup_provider_key,
            settings.backup_base_url,
        )
        self._backup_local_base_url.value = settings.backup_local_base_url
        self._backup_local_model.value = settings.backup_local_model
        self._backup_local_extra_body.value = settings.backup_local_extra_body_json
        self._clear_backup_local_extra_body_error()

    def _provider_snapshot_with_translation(
        self,
        settings: ProviderSettingsSnapshot,
        selection: TranslationSelectionSnapshot,
    ) -> ProviderSettingsSnapshot:
        llm_provider = settings.llm_provider
        if selection.model == TranslationModel.LOCAL_LLM:
            llm_provider = LLMProviderName.LOCAL_LLM
        elif selection.model in MANAGED_GEMMA_MODELS:
            llm_provider = LLMProviderName.MANAGED_GEMMA
        return replace(
            settings,
            llm_provider=llm_provider,
            translation=selection,
        )

    def _apply_translation_selection(
        self,
        model: TranslationModel,
        connection: TranslationConnection,
    ) -> None:
        if self._provider_snapshot is None:
            return
        if connection not in supported_translation_connections(model):
            return

        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        old_model = current_settings.translation.model
        old_connection = current_settings.translation.connection
        old_provider = current_settings.llm_provider
        if old_model == model and old_connection == connection:
            return

        draft = self._ensure_provider_settings_draft()
        history = dict(current_settings.translation.connection_history)
        history[model] = connection
        previous_llm_model = current_settings.translation.previous_llm_model
        if model == TranslationModel.CUSTOM_HTTP and old_model != TranslationModel.CUSTOM_HTTP:
            previous_llm_model = old_model
        elif model != TranslationModel.CUSTOM_HTTP:
            previous_llm_model = None
        selection = replace(
            current_settings.translation,
            model=model,
            connection=connection,
            connection_history=tuple(
                (candidate, history[candidate])
                for candidate in TranslationModel
                if candidate in history
            ),
            previous_llm_model=previous_llm_model,
        )
        self._provider_draft = self._provider_snapshot_with_translation(draft, selection)
        self._record_provider_edit(self._translation_selection_edit(selection))
        new_provider = self._provider_draft.llm_provider

        if old_provider != new_provider:
            self._emit_runtime_basic(
                f"[Settings] LLM provider changed: {old_provider.value} -> {new_provider.value}"
            )

        self.has_provider_changes = True
        self._update_api_visibility()
        if (
            self._gpu_llm_selected(self._provider_draft)
            and not self._gpu_llm_selected(current_settings)
            and self.on_gpu_discovery_requested is not None
        ):
            self.on_gpu_discovery_requested()
        self._sync_gpu_device_card()

        if (
            model == TranslationModel.INDEX_TRANSLATE_35B
            or old_model == TranslationModel.INDEX_TRANSLATE_35B
            or connection in (TranslationConnection.MANAGED, TranslationConnection.MANAGED_CHINA)
            or model in MANAGED_GEMMA_MODELS
            or old_model in MANAGED_GEMMA_MODELS
        ) and getattr(self, "on_providers_changed", None) is not None:
            if (
                model == TranslationModel.INDEX_TRANSLATE_35B
                or old_model == TranslationModel.INDEX_TRANSLATE_35B
            ):
                self._immediate_provider_apply_intent = ProviderApplyIntent(
                    (self._translation_selection_edit(selection),)
                )
            self.on_providers_changed()

        display_settings = self._build_settings_with_provider_draft()
        assert display_settings is not None
        self._sync_translation_selection_controls(display_settings)

        if old_provider != display_settings.llm_provider:
            provider_name = self._active_prompt_key()
            self._prompt_editor.set_provider(provider_name)
            next_prompt = self._ensure_provider_prompt_value(self._provider_draft, provider_name)
            self._prompt_editor.value = next_prompt
            self._stage_prompt_draft(next_prompt)
        self._sync_prompt_tab_copy()

        if is_control_mounted(self):
            self._llm_text.update()
            self._translation_connection_row.update()
            self._local_llm_connection_card.update()
            http_extension_host = getattr(self, "_http_extension_host", None)
            if http_extension_host is not None:
                http_extension_host.update()

    def _on_llm_selected(self, value: str) -> None:
        """Handle translation model selection from modal."""
        if self._provider_snapshot is None:
            return
        model = normalize_translation_model(value)
        connection = default_translation_connection(model)
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        if (
            current_settings.translation.model == model
            and current_settings.translation.connection == connection
        ):
            self._update_api_visibility()
            return
        self._apply_translation_selection(model, connection)

    def _on_translation_connection_click(self, e) -> None:
        if not is_control_mounted(self):
            return
        display_settings = self._build_settings_with_provider_draft()
        model = (
            display_settings.translation.model
            if display_settings is not None
            else TranslationModel.NONE
        )
        if model == TranslationModel.NONE:
            return
        connections = supported_translation_connections(model)
        options = [
            OptionItem(
                value=connection.value,
                label=self._translation_connection_display_label(connection),
                description=self._translation_connection_display_description(connection),
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
            show_description=True,
        )
        modal.open(current)

    def _on_translation_connection_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        model = current_settings.translation.model
        try:
            connection = TranslationConnection(value)
        except TypeError, ValueError:
            return
        if connection not in supported_translation_connections(model):
            return
        self._apply_translation_selection(model, connection)

    def _on_ui_click(self, e) -> None:
        """Open UI language selection modal."""
        if not is_control_mounted(self):
            return
        options = [OptionItem(value=code, label=locale_label(code)) for code in available_locales()]
        current = self._general_snapshot.locale if self._general_snapshot else "en"
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
        if self._general_snapshot is None:
            return
        old_locale = self._general_snapshot.locale
        self._emit_runtime_basic(f"[Settings] Language changed: {old_locale} -> {value}")
        self._general_snapshot = replace(self._general_snapshot, locale=value)

        # Update text
        self._ui_text.content.value = locale_label(value)
        if is_control_mounted(self):
            self._ui_text.update()
        self._emit_settings_changed(LocaleSettingsIntent(value))

    def _write_secret_value(self, key: str, value: str) -> bool:
        if (
            self._provider_snapshot is None
            or not self._config_path
            or self._settings_secrets is None
        ):
            return False

        try:
            secret_key = SettingsSecretKey(key)
        except ValueError:
            return False
        result = self._settings_secrets.mutate(SettingsSecretMutation(key=secret_key, value=value))
        if not result.succeeded:
            self._emit_runtime_basic(
                f"Failed to update secret {key}: {result.error_type or 'unknown'}",
                level=logging.WARNING,
            )
            return False
        return True

    def _on_local_llm_secret_change(self, key: str, value: str) -> None:
        if key != "local_llm_api_key":
            return
        stripped = value.strip()
        if not self._write_secret_value(key, stripped):
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.api_key.save_failed"), COLOR_ERROR)
            return
        self._local_llm_api_key.value = stripped
        if self.on_local_llm_secret_changed:
            self.on_local_llm_secret_changed()

    def _commit_openai_field(self, field: str, value: str) -> None:
        if self._provider_snapshot is None:
            return
        normalized = value.strip()
        draft = self._ensure_provider_settings_draft()
        attr = f"openai_compatible_{field}"
        if getattr(draft, attr) == normalized:
            return
        self._provider_draft = replace(draft, **{attr: normalized})
        edit_type = {
            "provider_key": OpenAICompatibleProviderKeyEdit,
            "base_url": OpenAICompatibleBaseUrlEdit,
            "model": OpenAICompatibleModelEdit,
        }[field]
        self._record_provider_edit(edit_type(normalized))
        self.has_provider_changes = True

    def _commit_backup_field(self, field: str, value: str) -> None:
        if self._provider_snapshot is None:
            return
        normalized = value.strip()
        draft = self._ensure_provider_settings_draft()
        attr = f"backup_{field}"
        if getattr(draft, attr) == normalized:
            return
        self._provider_draft = replace(draft, **{attr: normalized})
        edit_type = {
            "provider_key": BackupOpenAICompatibleProviderKeyEdit,
            "base_url": BackupOpenAICompatibleBaseUrlEdit,
            "model": BackupOpenAICompatibleModelEdit,
            "local_base_url": BackupLocalLlmBaseUrlEdit,
            "local_model": BackupLocalLlmModelEdit,
            "local_extra_body_json": BackupLocalLlmExtraBodyEdit,
        }[field]
        self._record_provider_edit(edit_type(normalized))
        self.has_provider_changes = True

    def _on_openai_base_url_change_end(self, e) -> None:
        self._commit_openai_field("base_url", getattr(e.control, "value", ""))

    def _on_openai_model_change_end(self, e) -> None:
        self._commit_openai_field("model", getattr(e.control, "value", ""))

    def _on_backup_base_url_change_end(self, e) -> None:
        self._commit_backup_field("base_url", getattr(e.control, "value", ""))

    def _on_backup_model_change_end(self, e) -> None:
        self._commit_backup_field("model", getattr(e.control, "value", ""))

    def _on_backup_local_base_url_change_end(self, e) -> None:
        _ = e
        if self._provider_snapshot is None:
            return
        raw_value = self._backup_local_base_url.value or ""
        try:
            normalized = normalize_local_llm_base_url(raw_value)
        except ValueError:
            self._backup_local_base_url.error = t("settings.local_llm.base_url.invalid")
            _update_control_if_mounted(self._backup_local_base_url)
            return
        self._backup_local_base_url.error = None
        self._backup_local_base_url.value = normalized
        self._commit_backup_field("local_base_url", normalized)
        _update_control_if_mounted(self._backup_local_base_url)

    def _on_backup_local_model_change_end(self, e) -> None:
        _ = e
        if self._provider_snapshot is None:
            return
        model = (self._backup_local_model.value or "").strip()
        if not model:
            self._backup_local_model.error = t("settings.local_llm.model.required")
            _update_control_if_mounted(self._backup_local_model)
            return
        self._backup_local_model.error = None
        self._backup_local_model.value = model
        self._commit_backup_field("local_model", model)
        _update_control_if_mounted(self._backup_local_model)

    def _on_backup_local_field_change(self, e) -> None:
        _ = e
        if self._provider_snapshot is None:
            return
        current = self._provider_draft or self._provider_snapshot
        if not current.backup_enabled:
            return
        if normalize_translation_model(current.backup_variant) != TranslationModel.LOCAL_LLM:
            return
        self._ensure_provider_settings_draft()
        self.has_provider_changes = True

    def _show_backup_local_extra_body_error(self, message_key: str, **kwargs: object) -> None:
        message = self._local_llm_extra_body_error_message(message_key, **kwargs)
        self._backup_local_extra_body_error_key = message_key
        self._backup_local_extra_body_error_kwargs = dict(kwargs)
        self._backup_local_extra_body_error.value = message
        self._backup_local_extra_body_error.visible = True
        self._backup_local_extra_body.error = message
        _update_control_if_mounted(self._backup_local_extra_body)
        _update_control_if_mounted(self._backup_local_extra_body_error)

    def _clear_backup_local_extra_body_error(self) -> None:
        self._backup_local_extra_body_error_key = ""
        self._backup_local_extra_body_error_kwargs = {}
        self._backup_local_extra_body_error.value = ""
        self._backup_local_extra_body_error.visible = False
        self._backup_local_extra_body.error = None
        _update_control_if_mounted(self._backup_local_extra_body)
        _update_control_if_mounted(self._backup_local_extra_body_error)

    def _on_backup_local_extra_body_change_end(self, e) -> None:
        _ = e
        if self._provider_snapshot is None:
            return
        raw = (self._backup_local_extra_body.value or "").strip()
        try:
            parsed = (
                {"reasoning_effort": "none", "temperature": 0.6}
                if not raw
                else json.loads(raw, parse_constant=_reject_json_constant)
            )
        except json.JSONDecodeError:
            self._show_backup_local_extra_body_error("settings.local_llm.extra_body.invalid_json")
            return
        if not isinstance(parsed, dict):
            self._show_backup_local_extra_body_error("settings.local_llm.extra_body.must_be_object")
            return
        lowered = {str(key).lower() for key in parsed}
        reserved = LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS.intersection(lowered)
        if reserved:
            self._show_backup_local_extra_body_error(
                "settings.local_llm.extra_body.reserved_key",
                key=sorted(reserved)[0],
            )
            return
        sensitive = LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS.intersection(lowered)
        if sensitive:
            self._show_backup_local_extra_body_error(
                "settings.local_llm.extra_body.sensitive_key",
                key=sorted(sensitive)[0],
            )
            return
        try:
            json.dumps(parsed, allow_nan=False)
        except (TypeError, ValueError):
            self._show_backup_local_extra_body_error("settings.local_llm.extra_body.not_serializable")
            return
        normalized = copy.deepcopy(parsed)
        normalized_text = json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False)
        current = self._provider_draft or self._provider_snapshot
        if current.backup_local_extra_body_json != normalized_text:
            draft = self._ensure_provider_settings_draft()
            self._provider_draft = replace(draft, backup_local_extra_body_json=normalized_text)
            self._record_provider_edit(
                BackupLocalLlmExtraBodyEdit(self._provider_draft.backup_local_extra_body_json)
            )
            self.has_provider_changes = True
        self._backup_local_extra_body.value = normalized_text
        self._clear_backup_local_extra_body_error()
        _update_control_if_mounted(self._backup_local_extra_body)

    def _commit_backup_local_fields_from_controls(self) -> None:
        if self._provider_snapshot is None:
            return
        current = self._provider_draft or self._provider_snapshot
        if not current.backup_enabled:
            return
        if normalize_translation_model(current.backup_variant) != TranslationModel.LOCAL_LLM:
            return
        self._on_backup_local_base_url_change_end(None)
        self._on_backup_local_model_change_end(None)
        self._on_backup_local_extra_body_change_end(None)

    def _on_openai_secret_change(self, key: str, value: str) -> None:
        if key != "openai_compatible_api_key":
            return
        stripped = value.strip()
        if not self._write_secret_value(key, stripped):
            if self.show_snackbar:
                self.show_snackbar(t("settings.openai_compatible.save_failed"), COLOR_ERROR)
            return
        self._openai_api_key.value = stripped
        if self.on_local_llm_secret_changed:
            self.on_local_llm_secret_changed()

    def _on_backup_secret_change(self, key: str, value: str) -> None:
        if key not in {"backup_api_key", "fallback_local_llm_api_key"}:
            return
        stripped = value.strip()
        if not self._write_secret_value(key, stripped):
            if self.show_snackbar:
                self.show_snackbar(t("settings.openai_compatible.save_failed"), COLOR_ERROR)
            return
        if key == "backup_api_key":
            self._backup_api_key.value = stripped
        else:
            self._backup_local_api_key.value = stripped
        if self.on_local_llm_secret_changed:
            self.on_local_llm_secret_changed()

    def _provider_dropdown_value(self, provider_key: str, base_url: str) -> str | None:
        from puripuly_heart.config.providers_registry import load_provider_presets

        presets = load_provider_presets()
        if provider_key in presets:
            return provider_key
        for key, preset in presets.items():
            if preset.base_url == base_url:
                return key
        return next(iter(presets), None)

    def _on_openai_provider_change(self, e) -> None:
        value = getattr(e, "data", None) or getattr(getattr(e, "control", None), "value", None)
        if value is None:
            return
        self._on_openai_provider_selected(value)

    def _on_openai_provider_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        self._commit_openai_field("provider_key", value)
        from puripuly_heart.config.providers_registry import load_provider_presets

        preset = load_provider_presets().get(value)
        if preset is not None:
            self._openai_base_url.value = preset.base_url
            _update_control_if_mounted(self._openai_base_url)
            current = self._provider_draft or self._provider_snapshot
            if current.openai_compatible_base_url != preset.base_url:
                self._commit_openai_field("base_url", preset.base_url)
                self._commit_openai_field("model", "")
            self._openai_model.value = ""
            _update_control_if_mounted(self._openai_model)
        self._openai_provider.value = value
        _update_control_if_mounted(self._openai_provider)

    def _on_backup_provider_change(self, e) -> None:
        value = getattr(e, "data", None) or getattr(getattr(e, "control", None), "value", None)
        if value is None:
            return
        self._on_backup_provider_selected(value)

    def _on_backup_provider_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        self._commit_backup_field("provider_key", value)
        from puripuly_heart.config.providers_registry import load_provider_presets

        preset = load_provider_presets().get(value)
        if preset is not None:
            self._backup_base_url.value = preset.base_url
            _update_control_if_mounted(self._backup_base_url)
            current = self._provider_draft or self._provider_snapshot
            if current.backup_base_url != preset.base_url:
                self._commit_backup_field("base_url", preset.base_url)
                self._commit_backup_field("model", "")
            self._backup_model.value = ""
            _update_control_if_mounted(self._backup_model)
        self._backup_provider.value = value
        _update_control_if_mounted(self._backup_provider)

    async def _do_fetch_models(self, *, base_url: str, api_key: str, on_model_picked) -> None:
        if self.on_fetch_models is None or not base_url:
            return
        try:
            model_ids = await self.on_fetch_models(base_url, api_key)
        except Exception:
            model_ids = []
        if not model_ids:
            if self.show_snackbar:
                self.show_snackbar(t("settings.openai_compatible.fetch_failed"), COLOR_WARNING)
            return
        if len(model_ids) == 1:
            on_model_picked(model_ids[0])
            return
        options = [OptionItem(value=m, label=m) for m in model_ids]
        SettingsModal(
            self.page,
            t("settings.openai_compatible.select_model"),
            options,
            on_model_picked,
            searchable=True,
            search_hint=t("settings.filter"),
        ).open("")

    async def _do_test_connection(self, *, base_url: str, api_key: str) -> None:
        if self.on_test_connection is None or not base_url:
            return
        status, _body = await self.on_test_connection(base_url, api_key)
        if self.show_snackbar:
            if status == 200:
                self.show_snackbar(t("settings.openai_compatible.test_ok"), COLOR_SECONDARY)
            else:
                self.show_snackbar(t("settings.openai_compatible.test_failed"), COLOR_WARNING)

    async def _on_fetch_openai_models(self, e) -> None:
        await self._do_fetch_models(
            base_url=(self._openai_base_url.value or "").strip(),
            api_key=(self._openai_api_key.value or "").strip(),
            on_model_picked=self._pick_openai_model,
        )

    def _pick_openai_model(self, value: str) -> None:
        self._openai_model.value = value
        self._commit_openai_field("model", value)
        _update_control_if_mounted(self._openai_model)

    async def _on_test_openai_connection(self, e) -> None:
        await self._do_test_connection(
            base_url=(self._openai_base_url.value or "").strip(),
            api_key=(self._openai_api_key.value or "").strip(),
        )

    async def _on_fetch_backup_models(self, e) -> None:
        await self._do_fetch_models(
            base_url=(self._backup_base_url.value or "").strip(),
            api_key=(self._backup_api_key.value or "").strip(),
            on_model_picked=self._pick_backup_model,
        )

    def _pick_backup_model(self, value: str) -> None:
        self._backup_model.value = value
        self._commit_backup_field("model", value)
        _update_control_if_mounted(self._backup_model)

    async def _on_test_backup_connection(self, e) -> None:
        await self._do_test_connection(
            base_url=(self._backup_base_url.value or "").strip(),
            api_key=(self._backup_api_key.value or "").strip(),
        )

    async def _on_fetch_backup_local_models(self, e) -> None:
        await self._do_fetch_models(
            base_url=(self._backup_local_base_url.value or "").strip(),
            api_key=(self._backup_local_api_key.value or "").strip(),
            on_model_picked=self._pick_backup_local_model,
        )

    def _pick_backup_local_model(self, value: str) -> None:
        self._backup_local_model.value = value
        self._commit_backup_field("local_model", value)
        _update_control_if_mounted(self._backup_local_model)

    async def _on_test_backup_local_connection(self, e) -> None:
        await self._do_test_connection(
            base_url=(self._backup_local_base_url.value or "").strip(),
            api_key=(self._backup_local_api_key.value or "").strip(),
        )

    async def _on_fetch_local_llm_models(self, e) -> None:
        await self._do_fetch_models(
            base_url=(self._local_llm_base_url.value or "").strip(),
            api_key=(self._local_llm_api_key.value or "").strip(),
            on_model_picked=self._pick_local_llm_model,
        )

    def _pick_local_llm_model(self, value: str) -> None:
        self._local_llm_model.value = value
        draft = self._ensure_provider_settings_draft()
        self._provider_draft = replace(draft, local_llm_model=value)
        self._record_provider_edit(LocalLlmModelEdit(value))
        self.has_provider_changes = True
        _update_control_if_mounted(self._local_llm_model)

    async def _on_test_local_llm_connection(self, e) -> None:
        await self._do_test_connection(
            base_url=(self._local_llm_base_url.value or "").strip(),
            api_key=(self._local_llm_api_key.value or "").strip(),
        )

    def _on_backup_translation_click(self, e) -> None:
        if not is_control_mounted(self):
            return
        settings = self._build_settings_with_provider_draft()
        current = "off"
        if settings is not None and settings.backup_enabled and settings.backup_variant:
            current = settings.backup_variant
        options = self._backup_picker_options()
        SettingsModal(
            self.page,
            t("settings.backup_translation.title"),
            options,
            self._on_backup_variant_selected,
            show_description=True,
        ).open(current)

    def _on_backup_variant_selected(self, value: str) -> None:
        if self._provider_snapshot is None:
            return
        enabled = value != "off"
        variant = value if enabled else ""
        draft = self._ensure_provider_settings_draft()
        if draft.backup_enabled != enabled:
            self._provider_draft = replace(draft, backup_enabled=enabled)
            self._record_provider_edit(BackupTranslationEnabledEdit(enabled))
            draft = self._provider_draft
        if draft.backup_variant != variant:
            self._provider_draft = replace(draft, backup_variant=variant)
            self._record_provider_edit(BackupVariantEdit(variant))
        self.has_provider_changes = True
        self._sync_translation_selection_controls(self._build_settings_with_provider_draft())
        self._update_api_visibility()

    def _on_secret_change(self, key: str, value: str) -> object:
        if self._provider_snapshot is None or not self._config_path:
            return False
        if self.on_provider_secret_change is not None:
            return self.on_provider_secret_change(key, value)

        if not self._write_secret_value(key, value):
            return False
        if not value and self.on_secret_cleared:
            with contextlib.suppress(Exception):
                self.on_secret_cleared(key)
        return True

    def _on_audio_change(self) -> None:
        if self._general_snapshot is None:
            return

        new_host = self._audio_settings.host_api
        new_device = self._audio_settings.microphone
        new_desktop_output = self._audio_settings.desktop_output_device
        old_host = self._general_snapshot.input_host_api
        old_device = self._general_snapshot.input_device
        old_desktop_output = self._general_snapshot.output_device

        if old_host != new_host:
            self._emit_runtime_basic(f"[Settings] Audio Host changed: {old_host} -> {new_host}")
        if old_device != new_device:
            self._emit_runtime_basic(f"[Settings] Microphone changed: {old_device} -> {new_device}")
        if old_desktop_output != new_desktop_output:
            self._emit_runtime_basic(
                f"[Settings] Desktop loopback output changed: {old_desktop_output} -> {new_desktop_output}"
            )

        self._general_snapshot = replace(
            self._general_snapshot,
            input_host_api=new_host,
            input_device=new_device,
            output_device=new_desktop_output,
        )
        changes = []
        if old_host != new_host or old_device != new_device:
            changes.append(AudioInputSettingsIntent(new_host, new_device))
        if old_desktop_output != new_desktop_output:
            changes.append(DesktopAudioOutputSettingsIntent(new_desktop_output))
        if changes:
            self._emit_settings_changed(AudioSettingsIntent(tuple(changes)))

    def _on_mic_host_api_click(self, e) -> None:
        if not is_control_mounted(self):
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
        if is_control_mounted(self):
            self._mic_audio_text.update()
            self._audio_host_api_text.update()
        self._on_audio_change()

    def _on_mic_audio_click(self, e) -> None:
        if not is_control_mounted(self):
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
        if is_control_mounted(self):
            self._mic_audio_text.update()
        self._on_audio_change()

    def _on_loopback_audio_click(self, e) -> None:
        if not is_control_mounted(self):
            return
        list_process_options = getattr(self, "on_list_loopback_process_options", None)
        list_device_options = getattr(self, "on_list_loopback_device_options", None)
        list_options = getattr(self, "on_list_loopback_capture_options", None)
        if callable(list_options):
            current = (
                self.on_current_loopback_capture_option()
                if callable(getattr(self, "on_current_loopback_capture_option", None))
                else "device:"
            )
            if callable(list_device_options) and callable(list_process_options):
                device_options = list_device_options()
                process_section = t("settings.desktop_audio.section.process")
                initial_options: list[OptionItem] = [
                    OptionItem(value="", label="", section=process_section),
                    *device_options,
                ]
                modal = SettingsModal(
                    self.page,
                    t("settings.section.loopback_audio"),
                    initial_options,
                    self._on_loopback_audio_selected,
                    show_description=False,
                    two_column=True,
                )
                modal.open(current, loading_section=process_section)
                self._schedule_page_task(self._load_process_capture_options, modal, current)
            else:
                options = list_options()
                modal = SettingsModal(
                    self.page,
                    t("settings.section.loopback_audio"),
                    options,
                    self._on_loopback_audio_selected,
                    show_description=False,
                    two_column=True,
                )
                modal.open(current)
        else:
            options = self._audio_settings._get_desktop_output_options()
            current = self._audio_settings.desktop_output_device
            modal = SettingsModal(
                self.page,
                t("settings.section.loopback_audio"),
                options,
                self._on_loopback_audio_selected,
                show_description=False,
            )
            modal.open(current)

    async def _load_process_capture_options(self, modal: SettingsModal, current: str) -> None:
        list_process_options = getattr(self, "on_list_loopback_process_options", None)
        list_device_options = getattr(self, "on_list_loopback_device_options", None)
        if not callable(list_process_options) or not callable(list_device_options):
            return
        process_options = await asyncio.to_thread(list_process_options)
        device_options = await asyncio.to_thread(list_device_options)
        full_options: list[OptionItem] = [*process_options, *device_options]
        modal.replace_options(full_options)

    def _on_loopback_audio_selected(self, value: str) -> None:
        apply_option = getattr(self, "on_apply_loopback_capture_option", None)
        if callable(apply_option):
            apply_option(value)
            summary = (
                self.on_loopback_capture_summary()
                if callable(getattr(self, "on_loopback_capture_summary", None))
                else value
            )
            if value.startswith("device:"):
                self._audio_settings.desktop_output_device = value[len("device:") :]
            self._set_unit_card_value_text(
                self._loopback_audio_text,
                summary or t("settings.default_option"),
            )
            if is_control_mounted(self):
                self._loopback_audio_text.update()
            return
        self._audio_settings.desktop_output_device = value
        self._sync_general_audio_card_texts()
        if is_control_mounted(self):
            self._loopback_audio_text.update()
        self._on_audio_change()

    def _normalized_overlay_target(self, value: object) -> str:
        return OVERLAY_TARGET_DESKTOP if value == OVERLAY_TARGET_DESKTOP else OVERLAY_TARGET_STEAMVR

    def _current_overlay_target(self) -> str:
        if self._overlay_snapshot is None:
            return OVERLAY_TARGET_STEAMVR
        return self._normalized_overlay_target(self._overlay_snapshot.target)

    def _overlay_target_label_for(self, target: object) -> str:
        normalized_target = self._normalized_overlay_target(target)
        return t(f"settings.overlay.target.{normalized_target}")

    def _sync_overlay_target_control(self) -> None:
        self._set_unit_card_value_text(
            self._overlay_target_button,
            self._overlay_target_label_for(self._current_overlay_target()),
            size=28,
        )
        self._overlay_target_button.disabled = self._overlay_snapshot is None

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
        except TypeError, ValueError:
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
        if self._overlay_snapshot is None:
            return "medium"
        return self._normalize_desktop_overlay_size_preset(
            self._overlay_snapshot.desktop_size_preset
        )

    def _current_desktop_overlay_background_alpha(self) -> float:
        if self._overlay_snapshot is None:
            return DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
        return self._normalize_desktop_overlay_background_alpha(
            self._overlay_snapshot.desktop_background_alpha
        )

    def _desktop_overlay_lock_label_for(self, locked: bool) -> str:
        return t(
            "settings.overlay.desktop.lock.value.locked"
            if locked
            else "settings.overlay.desktop.lock.value.move"
        )

    def _current_desktop_overlay_locked(self) -> bool:
        if self._overlay_snapshot is None:
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
        self._set_unit_card_value_text(
            self._desktop_overlay_swap_caption_languages_button,
            t(
                "settings.option.on"
                if self._current_desktop_overlay_swap_caption_languages()
                else "settings.option.off"
            ),
        )
        self._desktop_overlay_background_alpha_value_text.value = (
            self._desktop_overlay_background_alpha_label_for(
                self._current_desktop_overlay_background_alpha()
            )
        )
        disabled = self._overlay_snapshot is None
        self._desktop_overlay_size_button.disabled = disabled
        self._desktop_overlay_background_alpha_decrease_button.disabled = disabled
        self._desktop_overlay_background_alpha_increase_button.disabled = disabled
        self._desktop_overlay_lock_button.disabled = disabled
        self._desktop_overlay_swap_caption_languages_button.disabled = disabled
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
            self._set_desktop_overlay_primary_action(
                label_key=None,
                action_kind=None,
                visible=False,
            )

    def _on_overlay_target_click(self, e) -> None:
        _ = e
        if not is_control_mounted(self) or self._overlay_snapshot is None:
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
        if self._overlay_snapshot is None:
            return
        target = self._normalized_overlay_target(value)
        if self._current_overlay_target() == target:
            return
        self._overlay_snapshot = replace(self._overlay_snapshot, target=target)
        if self._overlay_state == "off":
            self._overlay_runtime_target = target
        self._sync_overlay_controls()
        self._emit_settings_changed(OverlayTargetSettingsIntent(self._overlay_snapshot.target))

    def _on_desktop_overlay_size_click(self, e) -> None:
        _ = e
        if (
            not is_control_mounted(self)
            or self._overlay_snapshot is None
            or self._desktop_overlay_size_button.disabled
        ):
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
        if self._overlay_snapshot is None:
            return
        size_preset = self._normalize_desktop_overlay_size_preset(value)
        if self._current_desktop_overlay_size_preset() == size_preset:
            return
        if self.on_desktop_overlay_size_change:
            self._desktop_overlay_pending_size_preset = size_preset
            self._sync_desktop_overlay_main_controls()
            self.on_desktop_overlay_size_change(size_preset)
            return
        self._overlay_snapshot = replace(
            self._overlay_snapshot,
            desktop_size_preset=size_preset,
        )
        self._desktop_overlay_pending_size_preset = None
        self._sync_desktop_overlay_main_controls()
        self._emit_settings_changed(DesktopOverlaySizeIntent(size_preset))

    def _current_desktop_overlay_swap_caption_languages(self) -> bool:
        if self._overlay_snapshot is None:
            return False
        return self._overlay_snapshot.desktop_swap_caption_languages

    def _on_desktop_overlay_swap_caption_languages_click(self, e) -> None:
        if (
            self._overlay_snapshot is None
            or self._desktop_overlay_swap_caption_languages_button.disabled
        ):
            return
        next_value = "off" if self._current_desktop_overlay_swap_caption_languages() else "on"
        self._on_desktop_overlay_swap_caption_languages_selected(next_value)

    def _on_desktop_overlay_swap_caption_languages_selected(self, value: str) -> None:
        if self._overlay_snapshot is None:
            return
        enabled = value == "on"
        if self._current_desktop_overlay_swap_caption_languages() == enabled:
            self._sync_desktop_overlay_main_controls()
            return
        self._overlay_snapshot = replace(
            self._overlay_snapshot,
            desktop_swap_caption_languages=enabled,
        )
        self._sync_desktop_overlay_main_controls()
        self._emit_settings_changed(DesktopOverlaySwapCaptionLanguagesIntent(enabled))

    def _on_desktop_overlay_lock_click(self, e) -> None:
        _ = e
        if self._overlay_snapshot is None or self._desktop_overlay_lock_button.disabled:
            return
        next_value = "move" if self._current_desktop_overlay_locked() else "locked"
        self._on_desktop_overlay_lock_selected(next_value)

    def _on_desktop_overlay_lock_selected(self, value: str) -> None:
        if self._overlay_snapshot is None:
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
        if (
            self._overlay_snapshot is None
            or self._desktop_overlay_background_alpha_decrease_button.disabled
        ):
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
            if is_control_mounted(self):
                self.update()
            return
        self._overlay_snapshot = replace(
            self._overlay_snapshot,
            desktop_background_alpha=next_alpha,
        )
        self._sync_desktop_overlay_main_controls()
        if is_control_mounted(self):
            self.update()
        self._emit_settings_changed(DesktopOverlayBackgroundAlphaIntent(next_alpha))

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
        if self._overlay_snapshot is not None:
            self._overlay_snapshot = replace(
                self._overlay_snapshot,
                calibration=OverlayCalibrationSnapshot(
                    anchor=calibration.anchor,
                    distance=calibration.distance,
                    offset_x=calibration.offset_x,
                    offset_y=calibration.offset_y,
                    text_scale=calibration.text_scale,
                ),
            )

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
        calibration_snapshot = OverlayCalibrationSnapshot(
            anchor=calibration.anchor,
            distance=calibration.distance,
            offset_x=calibration.offset_x,
            offset_y=calibration.offset_y,
            text_scale=calibration.text_scale,
        )
        if self._overlay_snapshot is not None:
            self._overlay_snapshot = replace(
                self._overlay_snapshot,
                calibration=calibration_snapshot,
            )
        self._sync_overlay_calibration_controls(self._overlay_calibration)

        if is_control_mounted(self):
            self.update()

        if self.on_overlay_calibration_apply is None:
            self._emit_settings_changed(OverlayCalibrationSettingsIntent(calibration_snapshot))

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
        if not is_control_mounted(self) or self._overlay_snapshot is None:
            return
        options = [
            OptionItem(
                value=anchor,
                label=t(f"settings.overlay.calibration.anchor.{anchor}"),
                description=t(
                    f"settings.overlay.calibration.anchor.{anchor}.description",
                    default="",
                ),
            )
            for anchor in OVERLAY_CALIBRATION_ANCHORS
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.calibration.anchor"),
            options,
            self._on_overlay_anchor_selected,
            show_description=True,
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
        if not is_control_mounted(self) or self._overlay_snapshot is None:
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
        if self._overlay_snapshot is None or self._overlay_desktop_reset_button.disabled:
            return
        if self.on_desktop_overlay_position_reset:
            self._desktop_overlay_pending_position_reset = True
            self._desktop_overlay_captions_locked = False
            self._sync_desktop_overlay_main_controls()
            self.on_desktop_overlay_position_reset()
            return
        self._desktop_overlay_captions_locked = False
        self._desktop_overlay_pending_position_reset = False
        self._sync_desktop_overlay_main_controls()
        self._emit_settings_changed(DesktopOverlayPositionResetIntent())

    def sync_desktop_overlay_settings(self, settings: OverlaySettingsSnapshot) -> None:
        self._overlay_snapshot = settings
        self._desktop_overlay_pending_size_preset = None
        self._desktop_overlay_pending_position_reset = False
        self._desktop_overlay_pending_locked = None
        self._desktop_overlay_captions_locked = False
        if self._overlay_state == "off":
            self._overlay_runtime_target = self._current_overlay_target()
        self._sync_overlay_controls()

    def set_overlay_peer_contract(self, contract: OverlayPeerConsumerContract) -> None:
        self._overlay_peer_contract = contract
        if self._provider_snapshot is not None:
            self._update_api_visibility()
        self._sync_overlay_controls()

    def _sync_overlay_controls(self) -> None:
        overlay_translation_enabled = bool(
            self._overlay_snapshot and self._overlay_snapshot.show_translation
        )
        overlay_peer_original_enabled = bool(
            self._overlay_snapshot and self._overlay_snapshot.show_peer_original
        )
        self._set_unit_card_value_text(
            self._overlay_translation_button,
            t("settings.option.on" if overlay_translation_enabled else "settings.option.off"),
        )
        self._set_unit_card_value_text(
            self._overlay_peer_original_button,
            t("settings.option.on" if overlay_peer_original_enabled else "settings.option.off"),
        )
        self._sync_overlay_target_control()
        self._sync_overlay_target_specific_visibility()
        self._sync_desktop_overlay_main_controls()
        self._sync_desktop_overlay_status_control()

        disabled = self._overlay_snapshot is None
        self._overlay_translation_button.disabled = disabled
        self._overlay_peer_original_button.disabled = disabled
        self._overlay_target_button.disabled = disabled
        self._overlay_anchor_button.disabled = disabled
        self._overlay_distance_decrease_button.disabled = disabled
        self._overlay_distance_increase_button.disabled = disabled
        self._overlay_offset_x_decrease_button.disabled = disabled
        self._overlay_offset_x_increase_button.disabled = disabled
        self._overlay_offset_y_decrease_button.disabled = disabled
        self._overlay_offset_y_increase_button.disabled = disabled
        self._desktop_overlay_background_alpha_decrease_button.disabled = disabled
        self._desktop_overlay_background_alpha_increase_button.disabled = disabled
        self._overlay_vr_reset_button.disabled = disabled
        self._overlay_desktop_reset_button.disabled = disabled
        if is_control_mounted(self):
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

    def _on_overlay_translation_click(self, e) -> None:
        if self._overlay_snapshot is None or self._overlay_translation_button.disabled:
            return
        next_value = "off" if self._overlay_snapshot.show_translation else "on"
        self._on_overlay_translation_selected(next_value)

    def _on_overlay_translation_selected(self, value: str) -> None:
        if self._overlay_snapshot is None:
            return
        self._overlay_snapshot = replace(
            self._overlay_snapshot,
            show_translation=value == "on",
        )
        self._sync_overlay_controls()
        self._emit_settings_changed(
            OverlayTranslationSettingsIntent(self._overlay_snapshot.show_translation)
        )

    def _on_overlay_peer_original_click(self, e) -> None:
        if self._overlay_snapshot is None or self._overlay_peer_original_button.disabled:
            return
        next_value = "off" if self._overlay_snapshot.show_peer_original else "on"
        self._on_overlay_peer_original_selected(next_value)

    def _on_overlay_peer_original_selected(self, value: str) -> None:
        if self._overlay_snapshot is None:
            return
        self._overlay_snapshot = replace(
            self._overlay_snapshot,
            show_peer_original=value == "on",
        )
        self._sync_overlay_controls()
        self._emit_settings_changed(
            OverlayPeerOriginalSettingsIntent(self._overlay_snapshot.show_peer_original)
        )

    def _handle_vad_visual_change(self, e) -> None:
        self._vad_slider.label = f"{float(e.control.value):.2f}"
        _update_control_if_mounted(self._vad_slider)

    def _handle_vad_change(self, e) -> None:
        self.apply_vad_threshold("self", float(e.control.value))

    def _handle_peer_vad_visual_change(self, e) -> None:
        self._peer_vad_slider.label = f"{float(e.control.value):.2f}"
        _update_control_if_mounted(self._peer_vad_slider)

    def _handle_peer_vad_change(self, e) -> None:
        self.apply_vad_threshold("peer", float(e.control.value))

    def vad_thresholds(self) -> dict[str, float] | None:
        if self._general_snapshot is None:
            return None
        return {
            "self": self._general_snapshot.self_vad_speech_threshold,
            "peer": self._general_snapshot.peer_vad_speech_threshold,
        }

    def apply_vad_threshold(self, channel: str, value: float) -> None:
        if self._general_snapshot is None:
            return
        new_vad = float(value)
        if channel == "peer":
            self._general_snapshot = replace(
                self._general_snapshot,
                peer_vad_speech_threshold=new_vad,
            )
            slider = self._peer_vad_slider
            intent = PeerVadSpeechThresholdIntent(new_vad)
        else:
            self._general_snapshot = replace(
                self._general_snapshot,
                self_vad_speech_threshold=new_vad,
            )
            slider = self._vad_slider
            intent = SelfVadSettingsIntent(new_vad)
        slider.value = new_vad
        slider.label = f"{new_vad:.2f}"
        _update_control_if_mounted(slider)
        self._emit_settings_changed(intent)

    def _on_vrc_mic_click(self, e) -> None:
        """Toggle VRC mic intercept immediately from the unit card."""
        if self._general_snapshot is None:
            return
        next_value = "off" if self._general_snapshot.vrc_mic_intercept else "on"
        self._on_vrc_mic_selected(next_value)

    def _on_audio_test_click(self, e) -> None:
        """Request the app/controller-owned audio-test lifecycle."""
        _ = e
        if self.on_start_audio_test is not None:
            self.on_start_audio_test()

    def _on_vrc_mic_selected(self, value: str) -> None:
        """处理选项卡的选择结果

        Handle VRC mic intercept selection result.
        """
        if self._general_snapshot is None:
            return
        new_value = value == "on"
        self._emit_runtime_basic(f"[Settings] VRC mic intercept toggled: {new_value}")
        self._general_snapshot = replace(
            self._general_snapshot,
            vrc_mic_intercept=new_value,
        )

        self._vrc_mic_text.content.value = t(
            "settings.vrc_mic.on" if new_value else "settings.vrc_mic.off"
        )
        if is_control_mounted(self):
            self._vrc_mic_text.update()
        self._emit_settings_changed(VrcMicInterceptSettingsIntent(new_value))

    def _on_chatbox_source_click(self, e) -> None:
        """Open chatbox source inclusion selection modal."""
        if not is_control_mounted(self):
            return
        options = [
            OptionItem(value="on", label=t("settings.chatbox_source.on")),
            OptionItem(value="off", label=t("settings.chatbox_source.off")),
        ]
        if self._general_snapshot is None:
            return
        current = "on" if self._general_snapshot.chatbox_include_source else "off"
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
        if self._general_snapshot is None:
            return
        new_value = value == "on"
        self._emit_runtime_basic(f"[Settings] Chatbox include source toggled: {new_value}")
        self._general_snapshot = replace(
            self._general_snapshot,
            chatbox_include_source=new_value,
        )

        self._chatbox_source_text.content.value = t(
            "settings.chatbox_source.on" if new_value else "settings.chatbox_source.off"
        )
        if is_control_mounted(self):
            self._chatbox_source_text.update()
        self._emit_settings_changed(ChatboxSourceSettingsIntent(new_value))

    def _on_osc_connection_click(self, e) -> None:
        _ = e
        if self._general_snapshot is None or not is_control_mounted(self):
            return
        self._osc_connection_modal = OscConnectionModal(
            self.page,
            self._on_osc_connection_selected,
            effective_ports_provider=self.on_osc_effective_ports,
        )
        self._osc_connection_modal.open(
            self._general_snapshot.osc_connection_mode,
            int(self._general_snapshot.osc_send_port or self._general_snapshot.osc_port),
            int(self._general_snapshot.osc_receive_port),
        )

    def _on_osc_connection_selected(self, mode: str, send_port: int, receive_port: int) -> None:
        if self._general_snapshot is None:
            return
        if mode not in {"automatic", "manual", "off"}:
            return
        try:
            send_port = int(send_port)
            receive_port = int(receive_port)
        except TypeError, ValueError:
            return
        if not 1 <= send_port <= 65535 or not 1 <= receive_port <= 65535:
            return
        self._general_snapshot = replace(
            self._general_snapshot,
            osc_connection_mode=mode,
            osc_send_port=send_port,
            osc_receive_port=receive_port,
        )
        self._sync_osc_connection_card(self._general_snapshot)
        if is_control_mounted(self):
            self._osc_connection_text.update()
        self._emit_settings_changed(
            OscConnectionSettingsIntent(
                connection_mode=self._general_snapshot.osc_connection_mode,
                send_port=self._general_snapshot.osc_send_port,
                receive_port=self._general_snapshot.osc_receive_port,
            )
        )

    def _on_clipboard_auto_translate_click(self, e) -> None:
        """Toggle clipboard auto-translate immediately from the unit card."""
        if self._general_snapshot is None:
            return
        next_value = "off" if self._general_snapshot.clipboard_auto_translate_enabled else "on"
        self._on_clipboard_auto_translate_selected(next_value)

    def _on_clipboard_auto_translate_selected(self, value: str) -> None:
        """Handle clipboard auto-translate selection result."""
        if self._general_snapshot is None:
            return
        new_value = value == "on"
        self._emit_runtime_basic(f"[Settings] Clipboard auto translate toggled: {new_value}")
        self._general_snapshot = replace(
            self._general_snapshot,
            clipboard_auto_translate_enabled=new_value,
        )
        self._clipboard_auto_translate_text.content.value = t(
            "settings.clipboard_auto_translate.on"
            if new_value
            else "settings.clipboard_auto_translate.off"
        )
        if is_control_mounted(self):
            self._clipboard_auto_translate_text.update()
        self._emit_settings_changed(ClipboardSettingsIntent(new_value))

    def _on_activation_notice_click(self, e) -> None:
        if self._general_snapshot is None or self._activation_notice_button.disabled:
            return
        enabled = not self._general_snapshot.activation_notice_enabled
        self._general_snapshot = replace(
            self._general_snapshot,
            activation_notice_enabled=enabled,
        )
        self._sync_activation_notice_card()
        self._emit_settings_changed(ActivationNoticeSettingsIntent(enabled))

    def _on_prompt_change(self, value: str) -> None:
        self._prompt_editor.value = value
        self._stage_prompt_draft(value)

    def _on_prompt_commit(self, value: str) -> None:
        self._stage_prompt_draft(value)
        if self.on_pending_prompt_changed is not None:
            self.on_pending_prompt_changed(PromptApplyIntent(value))
        if not self.has_pending_prompt_changes and value == self._committed_prompt_value():
            return
        if self.has_provider_changes:
            return
        if self.has_pending_prompt_changes:
            self._emit_prompt_apply_settings(PromptApplyIntent(value))

    def _on_reset_prompt(self, e) -> None:
        """Reset prompt to default for current provider."""
        self._prompt_editor.load_default_prompt()
        self._on_prompt_commit(self._prompt_editor.value)

    def _show_custom_vocabulary_limit_snackbar(self) -> None:
        if self.show_snackbar:
            self.show_snackbar(
                t(
                    "snackbar.custom_vocabulary_limit",
                    max_terms=MAX_CUSTOM_VOCAB_TERMS,
                ),
                COLOR_WARNING,
            )

    def _set_custom_vocabulary_terms_for_current_language(self, next_terms: list[str]) -> None:
        if self._prompt_snapshot is None:
            return

        source_language = self._current_source_language()
        current_terms = list(self._prompt_snapshot.custom_vocabulary_terms)
        applied_terms = list(next_terms)
        next_enabled = bool(applied_terms) or (
            self._prompt_snapshot.custom_vocabulary_other_languages_have_terms
        )

        if (
            current_terms == applied_terms
            and self._prompt_snapshot.custom_vocabulary_enabled == next_enabled
        ):
            return

        self._prompt_snapshot = replace(
            self._prompt_snapshot,
            custom_vocabulary_enabled=next_enabled,
            custom_vocabulary_terms=tuple(applied_terms),
        )
        self._custom_vocab_tag_editor.set_terms(applied_terms)
        self._emit_settings_changed(
            CustomVocabularySettingsIntent(
                source_language=source_language,
                terms=tuple(applied_terms),
            )
        )

    def _on_custom_vocabulary_add_terms(self, raw_terms: list[str]) -> None:
        if self._prompt_snapshot is None:
            return

        raw_values = [str(term) for term in raw_terms]
        if any(value != "" for value in raw_values):
            self._custom_vocab_tag_editor.clear_input()
        submitted_terms = self._normalize_custom_vocabulary_submitted_terms(raw_values)
        if not submitted_terms:
            return

        current_terms = list(self._prompt_snapshot.custom_vocabulary_terms)
        next_terms = list(current_terms)
        seen_terms = set(current_terms)
        cap_exceeded = False

        for term in submitted_terms:
            if term in seen_terms:
                continue
            seen_terms.add(term)
            if len(next_terms) >= MAX_CUSTOM_VOCAB_TERMS:
                cap_exceeded = True
                continue
            next_terms.append(term)

        if cap_exceeded:
            self._show_custom_vocabulary_limit_snackbar()

        self._set_custom_vocabulary_terms_for_current_language(next_terms)

    def _on_custom_vocabulary_remove_term(self, term: str) -> None:
        if self._prompt_snapshot is None:
            return

        current_terms = list(self._prompt_snapshot.custom_vocabulary_terms)
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

    def _emit_settings_changed(self, intent: ImmediateSettingsIntent) -> None:
        if self.on_settings_changed:
            self.on_settings_changed(intent)

    def _emit_prompt_apply_settings(self, intent: PromptApplyIntent) -> None:
        if self.on_prompt_apply_settings:
            self.on_prompt_apply_settings(intent)

    # --- Locale ---
    def apply_locale(self) -> None:
        """Update all labels when locale changes."""
        self._settings_subtab_shell.set_font_family(font_for_language(get_locale()))
        for key in _SETTINGS_SUBTAB_ORDER:
            self._settings_subtab_shell.set_tab_label(key, self._settings_subtab_label(key))

        # Section titles
        self._stt_title.value = t("settings.section.stt")
        self._trans_title.value = t("settings.section.translation")
        self._ui_title.value = t("settings.section.ui")
        self._audio_host_api_title.value = t("settings.audio_host_api")
        self._mic_audio_title.value = t("settings.section.microphone_audio")
        self._loopback_audio_title.value = t("settings.section.loopback_audio")
        self._self_vad_title.value = t("settings.section.self_vad_sensitivity")
        self._peer_vad_title.value = t("settings.section.peer_vad_sensitivity")
        self._audio_test_title.value = t("settings.audio_test")
        self._translation_connection_title.value = t("settings.translation_connection")
        self._local_llm_connection_title.value = t("settings.translation.config_title")
        self._backup_translation_title.value = t("settings.backup_translation.title")
        self._openai_compatible_title.value = t("settings.translation.config_title")
        self._backup_config_title.value = t("settings.backup_translation.config_title")
        self._openai_provider.label = t("settings.openai_compatible.provider")
        self._openai_base_url.label = t("settings.openai_compatible.base_url")
        self._openai_model.label = t("settings.openai_compatible.model")
        self._openai_model.hint_text = t("settings.openai_compatible.model_hint")
        self._openai_api_key.apply_locale()
        self._openai_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models")
        self._openai_test_btn.content.value = t("settings.openai_compatible.test_connection")
        self._backup_provider.label = t("settings.openai_compatible.provider")
        self._backup_base_url.label = t("settings.openai_compatible.base_url")
        self._backup_model.label = t("settings.openai_compatible.model")
        self._backup_model.hint_text = t("settings.openai_compatible.model_hint")
        self._backup_api_key.apply_locale()
        self._backup_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models")
        self._backup_test_btn.content.value = t("settings.openai_compatible.test_connection")
        self._backup_local_base_url.label = t("settings.local_llm.base_url")
        self._backup_local_model.label = t("settings.local_llm.model")
        self._backup_local_api_key.apply_locale()
        self._backup_local_api_key_helper.value = t("settings.local_llm.api_key.description")
        self._backup_local_extra_body.label = t("settings.local_llm.extra_body")
        self._backup_local_extra_body_helper.value = t("settings.local_llm.extra_body.description")
        self._backup_local_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models")
        self._backup_local_test_btn.content.value = t("settings.openai_compatible.test_connection")
        self._local_llm_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models")
        self._local_llm_test_btn.content.value = t("settings.openai_compatible.test_connection")
        self._local_llm_api_key_helper.value = t("settings.local_llm.api_key.description")
        self._http_extension_title.value = t("settings.http_extension.title")
        self._http_extension_path_title.value = t("settings.http_extension.path")
        self._http_extension_refresh_title.value = t("settings.http_extension.refresh")
        self._set_unit_card_value_text(
            self._http_extension_path_text,
            t("settings.http_extension.open"),
        )
        self._sync_http_extension_card()
        self._local_llm_base_url.label = t("settings.local_llm.base_url")
        self._local_llm_model.label = t("settings.local_llm.model")
        self._local_llm_api_key.apply_locale()
        self._local_llm_extra_body.label = t("settings.local_llm.extra_body")
        self._local_llm_extra_body_helper.value = t("settings.local_llm.extra_body.description")
        if self._local_llm_base_url.error:
            self._local_llm_base_url.error = t("settings.local_llm.base_url.invalid")
        if self._local_llm_model.error:
            self._local_llm_model.error = t("settings.local_llm.model.required")
        if self._local_llm_extra_body_error.visible:
            error_key = self._local_llm_extra_body_error_key
            error_kwargs = self._local_llm_extra_body_error_kwargs
            if error_key:
                message = self._local_llm_extra_body_error_message(error_key, **error_kwargs)
                self._local_llm_extra_body_error.value = message
                self._local_llm_extra_body.error = message
        if self._backup_local_base_url.error:
            self._backup_local_base_url.error = t("settings.local_llm.base_url.invalid")
        if self._backup_local_model.error:
            self._backup_local_model.error = t("settings.local_llm.model.required")
        if self._backup_local_extra_body_error.visible:
            backup_error_key = self._backup_local_extra_body_error_key
            backup_error_kwargs = self._backup_local_extra_body_error_kwargs
            if backup_error_key:
                backup_message = self._local_llm_extra_body_error_message(
                    backup_error_key, **backup_error_kwargs
                )
                self._backup_local_extra_body_error.value = backup_message
                self._backup_local_extra_body.error = backup_message
        self._persona_title.value = t("settings.section.persona")
        self._custom_vocab_title.value = t("settings.section.custom_vocabulary")
        self._vrc_mic_title.value = t("settings.vrc_mic_intercept")
        self._osc_connection_title.value = t("settings.osc.connection.title")
        self._chatbox_source_title.value = t("settings.chatbox_include_source")
        self._clipboard_auto_translate_title.value = t("settings.clipboard_auto_translate")
        self._activation_notice_title.value = t("settings.activation_notice.title")
        self._sync_activation_notice_card()
        self._peer_provider_title.value = t("settings.section.peer_stt")
        self._gpu_device_title.value = t("settings.gpu_device.asr")
        self._stt_model_title.value = t("settings.stt_model.title")
        self._gpu_llm_title.value = t("settings.gpu_device.llm")
        self._model_assets_title.value = t("settings.model_assets")
        self._set_unit_card_value_text(
            self._model_assets_text,
            t("settings.model_assets.action"),
        )
        self._overlay_target_title.value = t("settings.overlay.caption_location")
        self._overlay_translation_title.value = t("settings.overlay.show_translation")
        self._overlay_peer_original_title.value = t("settings.overlay.show_peer_original")
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
        self._desktop_overlay_swap_caption_languages_title.value = t(
            "settings.overlay.desktop.swap_caption_languages.title"
        )
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
        self._set_unit_card_value_text(
            self._backup_translation_text,
            self._backup_display_label(display_settings),
        )

        if self._reset_prompt_btn:
            self._reset_prompt_btn.style = self._get_button_style(ui_font)

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
            self._sync_stt_provider_labels(display_settings)
            self._set_unit_card_value_text(
                self._llm_text,
                self._get_llm_display_label(display_settings),
            )
            self._set_translation_connection_text(
                self._get_translation_connection_display_label(display_settings),
            )
            self._sync_translation_connection_title(display_settings)
            self._sync_http_extension_card(display_settings, force_credentials=True)
            if self._general_snapshot is not None:
                self._ui_text.content.value = locale_label(self._general_snapshot.locale)
            self._vrc_mic_text.content.value = t(
                "settings.vrc_mic.on"
                if self._general_snapshot is not None and self._general_snapshot.vrc_mic_intercept
                else "settings.vrc_mic.off"
            )
            if self._general_snapshot is not None:
                self._sync_osc_connection_card(self._general_snapshot)
            self._chatbox_source_text.content.value = t(
                "settings.chatbox_source.on"
                if self._general_snapshot is not None
                and self._general_snapshot.chatbox_include_source
                else "settings.chatbox_source.off"
            )
            self._clipboard_auto_translate_text.content.value = t(
                "settings.clipboard_auto_translate.on"
                if self._general_snapshot is not None
                and self._general_snapshot.clipboard_auto_translate_enabled
                else "settings.clipboard_auto_translate.off"
            )
            self._set_unit_card_value_text(
                self._audio_test_text,
                t("settings.audio_test.action"),
            )
            self._sync_overlay_controls()
            self._sync_overlay_calibration_controls()

        # Components
        self._audio_settings.apply_locale()
        self._prompt_editor.apply_locale()
        self._sync_gpu_device_card()

        if is_control_mounted(self):
            self.update()

    def refresh_prompt_if_empty(self) -> None:
        """Load default prompt if current is empty."""
        self._prompt_editor.load_default_if_empty()
