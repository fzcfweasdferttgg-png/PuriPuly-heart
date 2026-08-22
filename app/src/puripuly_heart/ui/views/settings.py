"""Settings view - Bento grid layout with SegmentedButton providers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import flet as ft

from puripuly_heart.config.settings import (
    OVERLAY_TARGET_STEAMVR,
    AppSettings,
    LLMProviderName,
    STTProviderName,
)
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.domain.i18n import (
    available_locales,
    get_locale,
    locale_label,
    native_locale_label,
    provider_label,
    t,
)
from puripuly_heart.domain.overlay_calibration import OverlayCalibration
from puripuly_heart.domain.overlay_contract import OverlayPeerConsumerContract
from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL,
)

from puripuly_heart.ui.views.settings_helpers import (
    SettingsHelpersMixin,
    _SETTINGS_SUBTAB_ORDER,
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

if TYPE_CHECKING:
    from puripuly_heart.ports.settings_draft import SettingsDraftServiceProtocol as SettingsDraftService

logger = logging.getLogger(__name__)

# MIXIN OWNERSHIP MAP — who creates which self._* attributes.
#
# __init__ (settings.py):
#   _settings, _provider_settings_draft, _config_path, has_provider_changes,
#   has_pending_prompt_changes, all on_* callbacks,
#   runtime_log_basic/detailed
#
# _build_general_tab (settings.py):
#   _settings_subtab_shell (via _build_ui)
#
# _build_api_tab (settings.py):
#   _translation_connection_row, _openrouter_routing_row,
#   _stub_title, _stub_text, _translation_connection_card,
#   _fallback_status_title, _fallback_status_text, _fallback_status_card,
#   _fallback_openai_title, _fallback_openai_card,
#   _fallback_local_llm_title, _fallback_local_llm_card,
#   _trans_compute_spacer
#
# UiSectionMixin._build_ui_language_widgets:
#   _ui_text, _ui_title
# UiSectionMixin._build_low_latency_widgets:
#   _low_latency_text, _low_latency_title, _low_latency_card
# UiSectionMixin._build_vad_widgets:
#   _self_vad_title, _vad_slider, _self_vad_card,
#   _peer_vad_title, _peer_vad_slider, _peer_vad_field,
#   _peer_hangover_field, _peer_pre_roll_field, _peer_vad_card
#
# OscSectionMixin._build_osc_widgets:
#   _chatbox_source_text/title, _clipboard_auto_translate_text/title,
#   _vrc_mic_text/title, _microphone_test_text/title
#
# AudioSectionMixin._build_audio_widgets:
#   _audio_settings, _audio_host_api_title/text, _mic_audio_title/text,
#   _loopback_audio_title/text
#
# ContextSectionMixin._build_integrated_context_unit_card:
#   _integrated_context_label, _integrated_context_button, _integrated_context_hint,
#   _integrated_context_card
# ContextSectionMixin._build_prompt_widgets:
#   _prompt_editor, _prompt_mode, _prompt_single_btn, _prompt_dual_btn,
#   _prompt_mode_row, _persona_title, _prompt_for_text, _reset_prompt_btn
# ContextSectionMixin._build_vocabulary_widgets:
#   _custom_vocab_title, _custom_vocab_description_text, _custom_vocab_tag_editor
#
# OverlaySectionMixin._build_overlay_widgets:
#   ~30+ _overlay_* / _desktop_overlay_* controls
#
# CalibrationSectionMixin._build_overlay_calibration_widgets:
#   _overlay_anchor_title/button/card, _overlay_distance_*,
#   _overlay_offset_x_*, _overlay_offset_y_*,
#   _overlay_text_scale_*, _overlay_vr_reset_*
#
# SttSectionMixin._build_stt_widgets:
#   _stt_text, _stt_compute_label/gpu_btn/cpu_btn/row,
#   _stt_quant_label/q8_btn/q6k_btn/f16_btn/int8_btn/row,
#   _stt_backend_label/onnx_btn/gguf_btn/row, _stt_title, _stt_provider_label
# SttSectionMixin._build_peer_stt_widgets:
#   _peer_stt_text, _peer_stt_compute_*, _peer_quant_*,
#   _peer_stt_backend_*, _peer_provider_title, _peer_stt_label
#
# LlmSectionMixin._build_llm_widgets:
#   _llm_text, _trans_title, _translation_provider_label,
#   _openai_compatible_key (ApiKeyField)
# LlmSectionMixin._build_local_llm_widgets:
#   _local_llm_connection_title/base_url/model/fetch_btn/test_btn/api_key/...,
#   _local_llm_connection_card
# LlmSectionMixin._build_openai_compat_widgets:
#   _openai_compatible_title/provider/base_url/model/fetch_btn/test_btn,
#   _translation_openai_card
#
# FallbackSectionMixin._init_fallback_openai_controls:
#   _fallback_openai_provider, _fallback_openai_base_url, _fallback_openai_model,
#   _fallback_openai_fetch_btn, _fallback_openai_test_btn, _fallback_api_key
# FallbackLocalLlmSectionMixin._init_fallback_local_llm_controls:
#   _fallback_local_llm_base_url/model/fetch_btn/test_btn/api_key/...,
#   _fallback_local_llm_extra_body/helper/error

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

    def __init__(self, initial_settings: AppSettings | None = None, draft_service: SettingsDraftService | None = None, command_executor=None):
        super().__init__(expand=True, spacing=16)
        self._initial_settings = initial_settings
        if draft_service is None:
            raise RuntimeError("draft_service must be injected by composition root (app.wiring)")
        self._draft_service = draft_service
        self._command_executor = command_executor

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
        self.on_load_secrets: Callable[[Path], dict[str, str]] | None = None
        self.on_write_secret: Callable[[str, str, Path], bool] | None = None
        self.on_fetch_models: Callable[[str, str], list[str]] | None = None
        self.on_test_connection: Callable[[str, str], tuple[int, str]] | None = None
        self.show_snackbar: Callable[[str, str], None] | None = None
        self.runtime_log_basic: Callable[..., None] | None = None
        self.runtime_log_detailed: Callable[..., None] | None = None

        # State
        self._settings: AppSettings | None = None
        self._config_path: Path | None = None
        self._overlay_state: str = "off"
        self._overlay_failure_reason: str | None = None
        self._overlay_runtime_target: str = OVERLAY_TARGET_STEAMVR
        self._desktop_overlay_captions_locked = False
        self._desktop_overlay_pending_locked: bool | None = None
        self._desktop_overlay_primary_action_kind: str | None = None
        # Sync hooks — section mixins register callbacks during _build_*_widgets
        # to participate in _sync_overlay_controls() and _update_api_visibility().
        self._sync_hooks: list[Callable[[], None]] = []
        self._visibility_hooks: list[Callable[[AppSettings], None]] = []
        self._desktop_overlay_pending_size_preset: str | None = None
        self._desktop_overlay_pending_position_reset = False
        self._overlay_calibration = OverlayCalibration()
        self._overlay_calibration_draft = self._overlay_calibration.copy()
        self._overlay_calibration_session_active = False
        self._overlay_peer_contract: OverlayPeerConsumerContract | None = None

        # Build UI components
        try:
            self._build_ui()
        except Exception as exc:
            import traceback
            logger.error("[SettingsView] _build_ui failed: %s\n%s", exc, traceback.format_exc())
            raise

    def _register_sync_hook(self, hook: Callable[[], None]) -> None:
        """Register a callback to be called during _sync_overlay_controls()."""
        self._sync_hooks.append(hook)

    def _register_visibility_hook(self, hook: Callable[[AppSettings], None]) -> None:
        """Register a callback to be called during _update_api_visibility()."""
        self._visibility_hooks.append(hook)

    # --- Draft state delegates (backed by _draft_service) ---
    @property
    def has_provider_changes(self) -> bool:
        return self._draft_service.has_provider_changes

    @has_provider_changes.setter
    def has_provider_changes(self, value: bool) -> None:
        self._draft_service.has_provider_changes = value

    @property
    def has_pending_prompt_changes(self) -> bool:
        return self._draft_service.has_pending_prompt_changes

    @has_pending_prompt_changes.setter
    def has_pending_prompt_changes(self, value: bool) -> None:
        self._draft_service.has_pending_prompt_changes = value

    @property
    def _provider_settings_draft(self) -> AppSettings | None:
        return self._draft_service._provider_settings_draft

    @_provider_settings_draft.setter
    def _provider_settings_draft(self, value: AppSettings | None) -> None:
        self._draft_service._provider_settings_draft = value

    # --- Card Wrapper (About page pattern) ---
    def _build_prompt_tab(self) -> list[ft.Control]:
        import traceback
        try:
            persona_card = self._build_prompt_widgets()
        except Exception as exc:
            logger.error("[SettingsView] _build_prompt_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise
        return [persona_card]

    def _build_overlay_tab(self) -> list[ft.Control]:
        import traceback
        # === Section H: Overlay Cards ===
        try:
            target_card, translation_card, peer_original_card = self._build_overlay_toggle_widgets()
        except Exception as exc:
            logger.error("[SettingsView] _build_overlay_toggle_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise

        # === Section I: Overlay Calibration ===
        try:
            (
                anchor_card,
                distance_card,
                offset_x_card,
                offset_y_card,
                text_scale_card,
                vr_reset_card,
            ) = self._build_overlay_calibration_widgets()
        except Exception as exc:
            logger.error("[SettingsView] _build_overlay_calibration_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise

        # === Section J: Desktop Overlay ===
        try:
            size_card, lock_card, bg_alpha_card = self._build_desktop_overlay_widgets()
        except Exception as exc:
            logger.error("[SettingsView] _build_desktop_overlay_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise

        # === Section K: Overlay Row Layout (cross-cutting) ===
        overlay_row1 = ft.Container(
            content=ft.Row(
                [target_card, translation_card, peer_original_card],
                spacing=16,
                expand=True,
            ),
        )
        overlay_row2 = ft.Container(
            content=ft.Row(
                [anchor_card, distance_card, offset_x_card],
                spacing=16,
                expand=True,
            ),
        )
        overlay_row3 = ft.Container(
            content=ft.Row(
                [offset_y_card, text_scale_card, vr_reset_card],
                spacing=16,
                expand=True,
            ),
        )
        overlay_row4 = ft.Container(
            content=ft.Row(
                [size_card, lock_card, bg_alpha_card],
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

        return [
            overlay_row1,
            overlay_row2,
            overlay_row3,
            overlay_row4,
            overlay_row5,
            overlay_row6,
        ]

    # CROSS-TAB DEPENDENCY — this method creates widgets used by _build_api_tab.
    # Specifically: _low_latency_card (from UiSectionMixin._build_low_latency_widgets)
    # is embedded in _translation_connection_row inside _build_api_tab.
    # General tab also creates _stt_backend_row, _peer_stt_backend_row,
    # _stt_compute_row, _peer_stt_compute_row (from SttSectionMixin._build_stt_widgets)
    # which _update_api_visibility controls.

    def _build_general_tab(self) -> list[ft.Control]:
        import traceback
        # Delegated widget creation (each builder stores attrs on self for handlers)
        try:
            ui_card = self._build_ui_language_widgets()
        except Exception as exc:
            logger.error("[SettingsView] _build_ui_language_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise
        try:
            self._build_low_latency_widgets()  # stores _low_latency_card on self
        except Exception as exc:
            logger.error("[SettingsView] _build_low_latency_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise
        try:
            host_api_card, mic_audio_card, loopback_audio_card = self._build_audio_widgets()
        except Exception as exc:
            logger.error("[SettingsView] _build_audio_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise
        try:
            self_vad_card, peer_vad_card = self._build_vad_widgets()
        except Exception as exc:
            logger.error("[SettingsView] _build_vad_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise
        try:
            chatbox_source_card, clipboard_auto_translate_card, vrc_mic_card, microphone_test_card = (
                self._build_osc_widgets()
            )
        except Exception as exc:
            logger.error("[SettingsView] _build_osc_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise
        try:
            integrated_context_card = self._build_integrated_context_unit_card()
        except Exception as exc:
            logger.error("[SettingsView] _build_integrated_context_unit_card failed: %s\n%s", exc, traceback.format_exc())
            raise

        # Cross-cutting layout — stays in settings.py
        general_primary_row = ft.Container(
            content=ft.Row(
                [ui_card, chatbox_source_card, integrated_context_card],
                spacing=16,
                expand=True,
            ),
        )
        general_audio_row = ft.Container(
            content=ft.Row(
                [host_api_card, mic_audio_card, loopback_audio_card],
                spacing=16,
                expand=True,
            ),
        )
        general_vad_row = ft.Container(
            content=ft.Row(
                [microphone_test_card, self_vad_card, peer_vad_card],
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

        return [
            general_primary_row,
            general_audio_row,
            general_vad_row,
            general_clipboard_row,
        ]

    # ORDER CONSTRAINT — assert at line ~315 guards the _low_latency_card dependency.
    # Section L (translation connection) assembles cards from THREE sources:
    #   - _low_latency_card: from UiSectionMixin (created in _build_general_tab)
    #   - _translation_connection_card / _fallback_status_card: created HERE in settings.py
    #   - _local_llm_connection_card: from LlmSectionMixin._build_local_llm_widgets
    #
    # FALLBACK CARDS OWNERSHIP QUIRK: _fallback_openai_card and _fallback_local_llm_card
    # are CREATED in _build_api_tab (settings.py) but their internal controls
    # (_fallback_openai_provider, _fallback_openai_model, etc.) are created by
    # _init_fallback_openai_controls / _init_fallback_local_llm_controls in the
    # FallbackSectionMixin / FallbackLocalLlmSectionMixin. The cards wrap those controls.

    def _build_api_tab(self) -> list[ft.Control]:
        """Build the API provider tab controls.

        Order constraint: MUST be called AFTER _build_general_tab() —
        uses _low_latency_card created in the General tab's Response Mode section.
        """
        import traceback
        assert hasattr(self, '_low_latency_card'), (
            "_low_latency_card must exist before _build_api_tab — "
            "call _build_general_tab() first"
        )

        # === Section A: Self STT ===
        try:
            stt_card = self._build_stt_widgets()
        except Exception as exc:
            logger.error("[SettingsView] _build_stt_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise

        # === Section B: Translation Provider ===
        try:
            trans_card = self._build_llm_widgets()
        except Exception as exc:
            logger.error("[SettingsView] _build_llm_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise

        # === Section G: Peer STT ===
        try:
            peer_stt_card = self._build_peer_stt_widgets()
        except Exception as exc:
            logger.error("[SettingsView] _build_peer_stt_widgets failed: %s\n%s", exc, traceback.format_exc())
            raise
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

        # === Section L: Translation Connection ===
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

        # === Section M: Local LLM Connection ===
        self._build_local_llm_widgets()

        # === Section N: OpenAI-Compatible ===
        self._build_openai_compat_widgets()

        # === Section O: Fallback OpenAI ===
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

        # === Section P: Fallback Local LLM ===
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

        return [
            row1,
            self._translation_connection_row,
            self._local_llm_connection_card,
            self._translation_openai_card,
            self._fallback_openai_card,
            self._fallback_local_llm_card,
        ]

    # BUILD ORDER CONSTRAINT — this is the single most fragile invariant.
    # _build_general_tab() MUST run before _build_api_tab() because
    # _build_low_latency_widgets() (called from _build_general_tab via UiSectionMixin)
    # creates self._low_latency_card, which _build_api_tab() asserts exists and embeds
    # in _translation_connection_row. Reordering these calls WILL crash at runtime.
    # _build_prompt_tab and _build_overlay_tab are independent of each other
    # and of general/api, but must run after both (prompt uses _prompt_editor
    # created by _build_prompt_widgets via ContextSectionMixin).

    def _build_ui(self) -> None:
        import traceback
        try:
            general_rows = self._build_general_tab()
        except Exception as exc:
            logger.error("[SettingsView] _build_general_tab failed: %s\n%s", exc, traceback.format_exc())
            raise
        try:
            api_rows = self._build_api_tab()
        except Exception as exc:
            logger.error("[SettingsView] _build_api_tab failed: %s\n%s", exc, traceback.format_exc())
            raise

        try:
            prompt_rows = self._build_prompt_tab()
        except Exception as exc:
            logger.error("[SettingsView] _build_prompt_tab failed: %s\n%s", exc, traceback.format_exc())
            raise

        try:
            overlay_rows = self._build_overlay_tab()
        except Exception as exc:
            logger.error("[SettingsView] _build_overlay_tab failed: %s\n%s", exc, traceback.format_exc())
            raise

        try:
            self._settings_subtab_shell = self._build_settings_subtab_shell(
                {
                    "api": api_rows,
                    "general": general_rows,
                    "prompt": prompt_rows,
                    "overlay": overlay_rows,
                }
            )
        except Exception as exc:
            logger.error("[SettingsView] _build_settings_subtab_shell failed: %s\n%s", exc, traceback.format_exc())
            raise
        self.controls = [self._settings_subtab_shell]

    def _build_locale_options(self) -> list[ft.dropdown.Option]:
        return [
            ft.dropdown.Option(key=code, text=native_locale_label(code)) for code in available_locales()
        ]
    def _copy_provider_draft_fields(self, source: AppSettings, target: AppSettings) -> None:
        self._draft_service.copy_provider_draft_fields(source, target)

    # DRAFT MERGE — returns _settings with provider fields overlaid from draft.
    # Used by: _update_api_visibility, apply_locale, _on_*_click (display), build_provider_apply_settings.
    # Cost: one copy.deepcopy per call. Mixin callers often build merged once and pass
    # to _update_api_visibility(merged) to avoid double-deepcopy.

    def _build_settings_with_provider_draft(self) -> AppSettings | None:
        return self._draft_service.build_settings_with_provider_draft()

    # DRAFT GATE — lazily creates _provider_settings_draft on first mutation.
    # All mixin event handlers use this to stage changes before apply/consume.
    # The draft persists until consume_provider_apply_settings() resets it.

    def _ensure_provider_settings_draft(self) -> AppSettings:
        return self._draft_service._ensure_provider_settings_draft()
    def _sanitize_provider_apply_settings(self, settings: AppSettings | None) -> AppSettings | None:
        return self._draft_service.sanitize_provider_apply_settings(settings)

    def _stage_prompt_draft(self, value: str) -> None:
        self._draft_service.stage_prompt_draft(value)

    def _committed_prompt_value(self) -> str:
        return self._draft_service.committed_prompt_value()

    # COMMIT CHAIN — before building final settings, must flush dirty text fields:
    # _commit_local_llm_fields_from_controls (LlmSectionMixin)
    # _commit_openai_compatible_fields_from_controls (LlmSectionMixin)
    # _commit_fallback_fields_from_controls (FallbackSectionMixin)
    # _commit_fallback_local_llm_fields_from_controls (FallbackLocalLlmSectionMixin)
    # Missing any _commit_* call would lose uncommitted text field edits.

    def build_provider_apply_settings(self) -> AppSettings | None:
        self._commit_local_llm_fields_from_controls()
        self._commit_openai_compatible_fields_from_controls()
        self._commit_fallback_fields_from_controls()
        self._commit_fallback_local_llm_fields_from_controls()
        return self._draft_service.build_provider_apply_settings(
            overlay_state_fn=self._settings_with_desktop_overlay_runtime_state
        )

    def consume_provider_apply_settings(self) -> AppSettings | None:
        self._commit_local_llm_fields_from_controls()
        self._commit_openai_compatible_fields_from_controls()
        self._commit_fallback_fields_from_controls()
        self._commit_fallback_local_llm_fields_from_controls()
        return self._draft_service.consume_provider_apply_settings(
            overlay_state_fn=self._settings_with_desktop_overlay_runtime_state
        )

    def consume_prompt_apply_settings(self) -> AppSettings | None:
        return self._draft_service.consume_prompt_apply_settings(
            overlay_state_fn=self._settings_with_desktop_overlay_runtime_state
        )

    # LOAD ORDER — the sequence matters:
    # 1. Reset state flags (_provider_settings_draft=None, has_provider_changes=False, etc.)
    # 2. _load_*_from_settings for each section (populates widget values)
    # 3. _update_api_visibility AFTER all sections loaded (needs provider values to decide visibility)
    # 4. Backup translation status display (reads settings.backup_translation)
    # 5. _load_secrets (reads secret store, sets API key field values)
    #
    # DRAFT PATTERN: _provider_settings_draft is None until user changes a provider field.
    # _ensure_provider_settings_draft() lazily creates it via deepcopy of _settings.
    # Multiple mixin event handlers mutate the draft via this gate (31 call sites).
    # Flet is single-threaded — no race condition between concurrent handlers.
    #
    # DEAD PARAMETER: preserve_custom_vocab_draft (kept for backward compat, body is _ = param).

    # --- Load Settings ---
    def load_from_settings(
        self,
        settings: AppSettings,
        *,
        config_path: Path,
        preserve_custom_vocab_draft: bool = False,
    ) -> None:
        _ = preserve_custom_vocab_draft  # kept for backward compatibility; no longer used
        self._settings = settings
        self._draft_service.set_settings(settings)
        self._config_path = config_path
        self._desktop_overlay_pending_size_preset = None
        self._desktop_overlay_pending_position_reset = False
        self._desktop_overlay_pending_locked = None
        self._desktop_overlay_captions_locked = False
        if self._overlay_state == "off":
            self._overlay_runtime_target = self._current_overlay_target()
        self._sync_clickable_text_control_fonts(font_for_language(settings.ui.locale))

        self._load_ui_from_settings(settings)
        self._load_stt_from_settings(settings)
        self._load_llm_from_settings(settings)
        self._load_fallback_from_settings(settings)
        self._load_fallback_local_llm_from_settings(settings)
        self._load_audio_from_settings(settings)
        self._load_vad_and_latency_from_settings(settings)
        self._load_osc_from_settings(settings)
        self._load_context_from_settings(settings)
        self._load_overlay_from_settings(settings)

        # Update API key field visibility after all sections loaded
        self._update_api_visibility(settings)

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

        # Load secrets
        self._load_secrets(settings, config_path)

        try:
            if self.page:
                self.update()
        except (AssertionError, RuntimeError):
            pass
    # CROSS-CUTTING VISIBILITY COORDINATOR — called from 7+ call sites across
    # stt_section.py, llm_section.py, settings_helpers.py, overlay_section.py.
    # Controls visibility of:
    #   _translation_connection_row (always visible when built)
    #   _local_llm_connection_card (visible when LLM == LOCAL_LLM)
    #   _translation_openai_card (visible when LLM == OPENAI_COMPATIBLE)
    #   _fallback_openai_card (visible when backup enabled + OPENAI_COMPATIBLE)
    #   _fallback_local_llm_card (visible when backup enabled + LOCAL_LLM)
    #   _stt_compute_row / _peer_stt_compute_row (visible for local STT providers)
    #   _stt_backend_row / _peer_stt_backend_row (visible for dual-backend models)
    #
    # Mixin callers (e.g. _on_stt_selected) build merged settings once and pass
    # to avoid redundant deepcopy inside this method.
    #
    # SAFETY: All callers execute after _build_ui() completes (post-build).
    # If a future mixin calls this before widgets are built, it will crash with
    # AttributeError — no hasattr guard exists (by design: fail-fast).

    # --- Visibility Updates ---
    def _update_api_visibility(self, settings: AppSettings | None = None) -> None:
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

        # Execute section-specific visibility hooks
        for hook in self._visibility_hooks:
            hook(settings)

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

    # LOCALE CASCADE — must call section _apply_locale_* methods in order.
    # _apply_locale_llm handles primary translation labels only.
    # Fallback labels handled by dedicated _apply_locale_fallback() / _apply_locale_fallback_local_llm().
    # Each method updates only attributes owned by its own mixin.
    # Intentionally omitted: secrets (no locale-sensitive labels). Fallback handled by dedicated methods.
    # _sync_overlay_controls at the end updates overlay target visibility after locale change.

    # --- Locale ---
    def apply_locale(self) -> None:
        self._settings_subtab_shell.set_font_family(font_for_language(get_locale()))
        for key in _SETTINGS_SUBTAB_ORDER:
            self._settings_subtab_shell.set_tab_label(key, self._settings_subtab_label(key))

        self._apply_locale_stt()
        self._apply_locale_ui()
        self._apply_locale_audio()
        self._apply_locale_osc()
        self._apply_locale_llm()
        self._apply_locale_fallback()
        self._apply_locale_fallback_local_llm()
        if self._settings:
            _bt = self._settings.backup_translation
            if _bt.enabled:
                _fb_label = t("provider.local_llms") if _bt.mode == LLMProviderName.LOCAL_LLM else t("provider.openai_compatible")
            else:
                _fb_label = t("option.disabled")
            self._set_unit_card_value_text(self._fallback_status_text, _fb_label)
        self._apply_locale_context()
        self._apply_locale_overlay()
        self._apply_locale_calibration()

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

        try:
            if self.page:
                self.update()
        except (AssertionError, RuntimeError):
            pass

    def refresh_prompt_if_empty(self) -> None:
        was_empty = not self._prompt_editor.value.strip()
        self._prompt_editor.load_default_if_empty()
        if was_empty and self._prompt_editor.value.strip():
            if self._prompt_editor.value != self._committed_prompt_value():
                self._stage_prompt_draft(self._prompt_editor.value)
