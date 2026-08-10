"""Settings view - Bento grid layout with SegmentedButton providers."""

from __future__ import annotations

import contextlib
import copy
import json
import logging
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
    _normalize_local_llm_base_url,
)
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
from puripuly_heart.domain.overlay_calibration import (
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
)

from puripuly_heart.ui.views.settings_helpers import (
    SettingsHelpersMixin,
    _CENTER_ALIGNMENT,
    _SETTINGS_SUBTAB_ORDER,
    _make_text_button,
    _set_text_button_label,
    _update_control_if_mounted,
)
from puripuly_heart.ui.views.stt_section import SttSectionMixin
from puripuly_heart.ui.views.llm_section import LlmSectionMixin
from puripuly_heart.ui.views.fallback_section import FallbackSectionMixin
from puripuly_heart.ui.views.fallback_local_llm_section import FallbackLocalLlmSectionMixin
from puripuly_heart.ui.views.overlay_section import OverlaySectionMixin
from puripuly_heart.ui.views.calibration_section import CalibrationSectionMixin
from puripuly_heart.ui.views.audio_section import AudioSectionMixin
from puripuly_heart.ui.views.ui_section import UiSectionMixin
from puripuly_heart.ui.views.osc_section import OscSectionMixin
from puripuly_heart.ui.views.context_section import ContextSectionMixin
from puripuly_heart.ui.views.secrets_section import SecretsSectionMixin

logger = logging.getLogger(__name__)


class SettingsView(
    ft.Column,
    SettingsHelpersMixin,
    SttSectionMixin,
    LlmSectionMixin,
    FallbackSectionMixin,
    FallbackLocalLlmSectionMixin,
    OverlaySectionMixin,
    CalibrationSectionMixin,
    AudioSectionMixin,
    UiSectionMixin,
    OscSectionMixin,
    ContextSectionMixin,
    SecretsSectionMixin,
):
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
        self.model_discovery: object | None = None
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
        _init_quant = self._initial_settings.provider.stt_quant if self._initial_settings else ""
        _init_available = self._INITIAL_QUANTS_FOR_PROVIDER.get(_init_stt, ["int8"])
        _init_active = _init_quant if _init_quant in _init_available else ""
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

        # API Key for Translation card (inline)
        self._openai_compatible_key = ApiKeyField(
            "settings.openai_compatible_api_key",
            "openai_compatible_api_key",
            "openai_compatible",
            on_verify=self._verify_key,
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
            base_url_getter=lambda: self._openai_compatible_base_url.value,
        )

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
        _init_peer_quant = self._initial_settings.provider.peer_stt_quant if self._initial_settings else ""
        _init_peer_available = self._INITIAL_QUANTS_FOR_PROVIDER.get(_init_peer, ["int8"])
        _init_peer_active = _init_peer_quant if _init_peer_quant in _init_peer_available else ""
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
        self._stub_title = ft.Text(
            "Stub",
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._stub_text = self._build_clickable_text(
            "Stub",
            self._on_stub_click,
        )
        self._translation_connection_card = self._wrap_unit_card(
            title=self._stub_title,
            value=self._stub_text,
        )
        self._fallback_status_title = ft.Text(
            t("settings.backup_translation.connection", default="Backup Translation Settings"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._fallback_status_text = self._build_clickable_text(
            t("option.disabled"),
            self._on_fallback_status_click,
        )
        self._fallback_status_card = self._wrap_unit_card(
            title=self._fallback_status_title,
            value=self._fallback_status_text,
        )
        self._translation_connection_row = ft.Container(
            content=ft.Row(
                [
                    self._low_latency_card,
                    self._translation_connection_card,
                    self._fallback_status_card,
                ],
                spacing=16,
                expand=True,
            ),
            visible=True,
        )
        self._openrouter_routing_row = self._translation_connection_row

        self._local_llm_connection_title = ft.Text(
            t("settings.openai_compatible.connection", default="Translation Settings"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._local_llm_base_url = ft.TextField(
            label=t("settings.local_llm.base_url"),
            value="",
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
            value="",
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
        self._local_llm_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip=t("settings.openai_compatible.fetch_models", default="Fetch models from API"),
            on_click=self._fetch_local_llm_models,
        )
        self._local_llm_test_btn = ft.TextButton(
            text=t("settings.local_llm.test_connection", default="Test connection"),
            on_click=self._test_local_llm_connection,
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
                    ft.Row([self._local_llm_base_url, self._local_llm_test_btn], spacing=4),
                    ft.Row([self._local_llm_model, self._local_llm_fetch_btn], spacing=4),
                    self._local_llm_api_key,
                    self._local_llm_api_key_helper,
                    self._local_llm_extra_body,
                    self._local_llm_extra_body_helper,
                    self._local_llm_extra_body_error,
                ],
                spacing=8,
            ),
            height=None,
        )
        self._local_llm_connection_card.visible = False

        # Translation OpenAI-compatible provider card
        self._openai_compatible_title = ft.Text(
            t("settings.openai_compatible.connection", default="Translation Provider Settings"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        from puripuly_heart.config.providers import load_providers
        _providers = load_providers()
        _provider_options = []
        for key, info in _providers.items():
            _provider_options.append(ft.dropdown.Option(key=key, text=info.get("label", key)))
        self._openai_compatible_provider = ft.Dropdown(
            label=t("settings.openai_compatible.provider", default="Provider"),
            options=_provider_options,
            value=_provider_options[0].key if _provider_options else None,
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_openai_compatible_provider_change,
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
            hint_text=t("settings.openai_compatible.model.hint", default="Enter model name or click refresh"),
            value="",
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
        self._openai_compatible_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip=t("settings.openai_compatible.fetch_models", default="Fetch models from API"),
            on_click=self._fetch_models,
        )
        self._openai_compatible_test_btn = ft.TextButton(
            text=t("settings.local_llm.test_connection", default="Test connection"),
            on_click=self._test_openai_compatible_connection,
        )
        self._translation_openai_card = self._wrap_card(
            ft.Column(
                [
                    self._openai_compatible_title,
                    ft.Container(height=4),
                    self._openai_compatible_provider,
                    ft.Row([self._openai_compatible_model, self._openai_compatible_fetch_btn], spacing=4),
                    self._openai_compatible_key,
                    self._openai_compatible_test_btn,
                ],
                spacing=8,
            ),
            height=None,
        )
        self._translation_openai_card.visible = False

        # Fallback OpenAI-compatible card
        self._init_fallback_openai_controls(
            on_verify=self._verify_key,
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
        )
        self._fallback_openai_title = ft.Text(
            t("settings.backup_translation.connection", default="Backup Translation Settings"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._fallback_openai_card = self._wrap_card(
            ft.Column(
                [
                    self._fallback_openai_title,
                    ft.Container(height=4),
                    self._fallback_openai_provider,
                    ft.Row([self._fallback_openai_model, self._fallback_openai_fetch_btn], spacing=4),
                    self._fallback_api_key,
                    self._fallback_openai_test_btn,
                ],
                spacing=8,
            ),
            height=None,
        )
        self._fallback_openai_card.visible = False

        # Fallback local LLM card
        self._init_fallback_local_llm_controls(
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
        )
        self._fallback_local_llm_title = ft.Text(
            t("settings.backup_translation.connection", default="Backup Translation Settings"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._fallback_local_llm_card = self._wrap_card(
            ft.Column(
                [
                    self._fallback_local_llm_title,
                    ft.Container(height=4),
                    ft.Row([self._fallback_local_llm_base_url, self._fallback_local_llm_test_btn], spacing=4),
                    ft.Row([self._fallback_local_llm_model, self._fallback_local_llm_fetch_btn], spacing=4),
                    self._fallback_local_llm_api_key,
                    self._fallback_local_llm_api_key_helper,
                    self._fallback_local_llm_extra_body,
                    self._fallback_local_llm_extra_body_helper,
                    self._fallback_local_llm_extra_body_error,
                ],
                spacing=8,
            ),
            height=None,
        )
        self._fallback_local_llm_card.visible = False

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
                    self._translation_openai_card,
                    self._fallback_openai_card,
                    self._fallback_local_llm_card,
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
        target.provider.openai_compatible = copy.deepcopy(source.provider.openai_compatible)
        target.translation = copy.deepcopy(source.translation)
        target.local_llm = copy.deepcopy(source.local_llm)
        target.backup_translation = copy.deepcopy(source.backup_translation)
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
        self._commit_fallback_fields_from_controls()
        self._commit_fallback_local_llm_fields_from_controls()
        return self._sanitize_provider_apply_settings(
            self._settings_with_desktop_overlay_runtime_state(
                self._build_settings_with_provider_draft()
            )
        )

    def consume_provider_apply_settings(self) -> AppSettings | None:
        import logging
        logger = logging.getLogger(__name__)
        settings = self.build_provider_apply_settings()
        if settings is None:
            logger.warning("[Settings] consume: build returned None")
            return None
        logger.info(
            "[Settings] consume: backup.enabled=%s backup.mode=%s backup.oc.base_url=%s backup.oc.model=%s backup.llm.base_url=%s backup.llm.model=%s has_changes=%s",
            settings.backup_translation.enabled,
            settings.backup_translation.mode.value,
            settings.backup_translation.openai_compatible.base_url,
            settings.backup_translation.openai_compatible.model,
            settings.backup_translation.local_llm.base_url,
            settings.backup_translation.local_llm.model,
            self.has_provider_changes,
        )
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
        self._openai_compatible_model.value = settings.provider.openai_compatible.model or ""
        # Sync provider dropdown from base_url
        from puripuly_heart.config.providers import load_providers
        _loaded_providers = load_providers()
        _opts = self._openai_compatible_provider.options or []
        _matched = _opts[0].key if _opts else None
        for _pk, _pi in _loaded_providers.items():
            if _pi.get("base_url") == settings.provider.openai_compatible.base_url:
                _matched = _pk
                break
        self._openai_compatible_provider.value = _matched

        # Fallback OpenAI Compatible
        bt = settings.backup_translation
        if bt.enabled and bt.mode == LLMProviderName.OPENAI_COMPATIBLE:
            self._fallback_openai_base_url.value = bt.openai_compatible.base_url
            self._fallback_openai_base_url.error_text = None
            self._fallback_openai_model.value = bt.openai_compatible.model or ""
            _fb_opts = self._fallback_openai_provider.options or []
            _fb_matched = _fb_opts[0].key if _fb_opts else None
            for _pk, _pi in _loaded_providers.items():
                if _pi.get("base_url") == bt.openai_compatible.base_url:
                    _fb_matched = _pk
                    break
            self._fallback_openai_provider.value = _fb_matched

        # Fallback local LLM
        if bt.enabled and bt.mode == LLMProviderName.LOCAL_LLM:
            self._fallback_local_llm_base_url.value = bt.local_llm.base_url
            self._fallback_local_llm_base_url.error_text = None
            self._fallback_local_llm_model.value = bt.local_llm.model or ""
            self._fallback_local_llm_model.error_text = None
            self._fallback_local_llm_extra_body.value = (
                json.dumps(bt.local_llm.extra_body, ensure_ascii=False, indent=2)
                if bt.local_llm.extra_body else ""
            )
            self._fallback_local_llm_extra_body_error.visible = False

        # Backup translation status display
        bt = settings.backup_translation
        if bt.enabled:
            if bt.mode == LLMProviderName.LOCAL_LLM:
                _fb_label = t("provider.local_llms")
            else:
                _fb_label = t("provider.openai_compatible")
            self._set_unit_card_value_text(self._fallback_status_text, _fb_label)
        else:
            self._set_unit_card_value_text(self._fallback_status_text, t("option.disabled"))

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

        self._translation_connection_row.visible = True
        self._local_llm_connection_card.visible = llm == LLMProviderName.LOCAL_LLM
        self._translation_openai_card.visible = llm == LLMProviderName.OPENAI_COMPATIBLE
        self._fallback_openai_card.visible = (
            settings.backup_translation.enabled
            and settings.backup_translation.mode == LLMProviderName.OPENAI_COMPATIBLE
        )
        self._fallback_local_llm_card.visible = (
            settings.backup_translation.enabled
            and settings.backup_translation.mode == LLMProviderName.LOCAL_LLM
        )
        _update_control_if_mounted(self._local_llm_connection_card)
        _update_control_if_mounted(self._translation_openai_card)
        _update_control_if_mounted(self._fallback_openai_card)
        _update_control_if_mounted(self._fallback_local_llm_card)

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
        self._stt_provider_label.value = t("settings.self_stt_provider")
        self._translation_provider_label.value = t("settings.shared_translation_provider")
        self._fallback_openai_title.value = t("settings.backup_translation.connection", default="Backup Translation Settings")
        self._fallback_openai_test_btn.text = t("settings.local_llm.test_connection", default="Test connection")
        self._fallback_local_llm_title.value = t("settings.backup_translation.connection", default="Backup Translation Settings")
        self._fallback_local_llm_test_btn.text = t("settings.local_llm.test_connection", default="Test connection")
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
        self._local_llm_connection_title.value = t("settings.openai_compatible.connection", default="Translation Settings")
        self._local_llm_base_url.label = t("settings.local_llm.base_url")
        self._local_llm_model.label = t("settings.local_llm.model")
        self._local_llm_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models", default="Fetch models from API")
        self._local_llm_test_btn.text = t("settings.local_llm.test_connection", default="Test connection")
        self._openai_compatible_test_btn.text = t("settings.local_llm.test_connection", default="Test connection")
        self._local_llm_api_key.apply_locale()
        self._fallback_api_key.apply_locale()
        self._fallback_local_llm_api_key.apply_locale()
        local_llm_api_key_description = t("settings.local_llm.api_key.description")
        self._local_llm_api_key_helper.value = local_llm_api_key_description
        self._local_llm_api_key_helper.visible = bool(local_llm_api_key_description.strip())
        self._local_llm_extra_body.label = t("settings.local_llm.extra_body")
        self._local_llm_extra_body_helper.value = t("settings.local_llm.extra_body.description")
        # Translation OpenAI-compatible labels
        self._openai_compatible_title.value = t("settings.openai_compatible.connection", default="Translation Provider Settings")
        self._openai_compatible_provider.label = t("settings.openai_compatible.provider", default="Provider")
        self._openai_compatible_base_url.label = t("settings.openai_compatible.base_url", default="Base URL")
        self._openai_compatible_model.label = t("settings.openai_compatible.model", default="Model")
        self._openai_compatible_model.hint_text = t("settings.openai_compatible.model.hint", default="Enter model name or click refresh")
        self._openai_compatible_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models", default="Fetch models from API")
        self._openai_compatible_key.apply_locale()
        # Fallback OpenAI labels
        self._fallback_openai_provider.label = t("settings.openai_compatible.provider", default="Provider")
        self._fallback_openai_base_url.label = t("settings.openai_compatible.base_url", default="Base URL")
        self._fallback_openai_model.label = t("settings.openai_compatible.model", default="Model")
        self._fallback_openai_model.hint_text = t("settings.openai_compatible.model.hint", default="Enter model name or click refresh")
        # Fallback Local LLM labels
        self._fallback_local_llm_base_url.label = t("settings.local_llm.base_url", default="Base URL")
        self._fallback_local_llm_model.label = t("settings.local_llm.model", default="Model")
        self._fallback_local_llm_extra_body.label = t("settings.local_llm.extra_body", default="Extra Body")
        self._fallback_local_llm_extra_body_helper.value = t("settings.local_llm.extra_body.description", default="")
        self._fallback_openai_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models", default="Fetch models from API")
        self._fallback_local_llm_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models", default="Fetch models from API")
        _fb_helper = t("settings.local_llm.api_key.description", default="")
        self._fallback_local_llm_api_key_helper.value = _fb_helper
        self._fallback_local_llm_api_key_helper.visible = bool(_fb_helper.strip())
        # Fallback status card
        self._fallback_status_title.value = t("settings.backup_translation.connection", default="Backup Translation Settings")
        if self._settings:
            _bt = self._settings.backup_translation
            if _bt.enabled:
                _fb_label = t("provider.local_llms") if _bt.mode == LLMProviderName.LOCAL_LLM else t("provider.openai_compatible")
            else:
                _fb_label = t("option.disabled")
            self._set_unit_card_value_text(self._fallback_status_text, _fb_label)
        if self._local_llm_base_url.error_text:
            self._local_llm_base_url.error_text = t("settings.local_llm.base_url.invalid")
        if self._local_llm_model.error_text:
            self._local_llm_model.error_text = t("settings.local_llm.model.required")
        if self._fallback_local_llm_base_url.error_text:
            self._fallback_local_llm_base_url.error_text = t("settings.local_llm.base_url.invalid")
        if self._fallback_local_llm_model.error_text:
            self._fallback_local_llm_model.error_text = t("settings.local_llm.model.required")
        if self._fallback_openai_base_url.error_text:
            self._fallback_openai_base_url.error_text = t(
                "settings.openai_compatible.base_url.required", default="Base URL is required"
            )
        if self._local_llm_extra_body_error.visible:
            error_key = self._local_llm_extra_body_error_key
            error_kwargs = self._local_llm_extra_body_error_kwargs
            if error_key:
                message = self._local_llm_extra_body_error_message(error_key, **error_kwargs)
                self._local_llm_extra_body_error.value = message
                self._local_llm_extra_body.error_text = message
        if self._fallback_local_llm_extra_body_error.visible:
            fb_error_key = self._fallback_local_llm_extra_body_error_key
            fb_error_kwargs = self._fallback_local_llm_extra_body_error_kwargs
            if fb_error_key:
                if "key" not in fb_error_kwargs:
                    fb_msg = t(fb_error_key, default="")
                else:
                    template = t(fb_error_key, default="")
                    try:
                        fb_msg = template.format(**fb_error_kwargs)
                    except Exception:
                        fb_msg = template
                self._fallback_local_llm_extra_body_error.value = fb_msg
                self._fallback_local_llm_extra_body.error_text = fb_msg
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
        # Prompt mode buttons
        self._prompt_single_btn.content.value = t("settings.prompt_mode.single", default="Single")
        self._prompt_dual_btn.content.value = t("settings.prompt_mode.dual", default="Dual")
        # Desktop overlay view logs
        self._desktop_overlay_view_logs_action.content.value = t("settings.overlay.desktop.recovery.action.view_details")

        # Update dynamic buttons by replacing the entire style object
        ui_font = font_for_language(get_locale())
        display_settings = self._build_settings_with_provider_draft()

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
