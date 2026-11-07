from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from puripuly_heart.app.language_selection import LanguageSelectionChange
from puripuly_heart.app.ports.settings_view import (
    AlibabaConnectionApplyResult,
    AlibabaConnectionDraftSnapshot,
    GeneralSettingsSnapshot,
    ImmediateSettingsIntent,
    OpenRouterPkceTarget,
    OverlaySettingsSnapshot,
    PromptApplyIntent,
    ProviderApplyIntent,
)
from puripuly_heart.app.ports.ui_models import (
    GpuNoticeAction,
    ManagedGemmaNoticeAction,
    OverlayPeerPresentationState,
)
from puripuly_heart.config.alibaba_connection import AlibabaEndpointMode, AlibabaRegion
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.orchestrator.self_translation_channel import TextSubmissionAdmission


class UiInputRuntimePort(Protocol):
    async def submit_text(self, text: str) -> TextSubmissionAdmission | None: ...

    def set_manual_input_activity(self, has_text: bool) -> None: ...

    async def set_translation_enabled(
        self, enabled: bool, *, allow_authorization: bool = True
    ) -> object: ...

    async def set_stt_enabled(self, enabled: bool) -> object: ...


class UiPeerCaptureRuntimePort(Protocol):
    async def set_peer_translation_enabled(self, enabled: bool) -> object: ...

    async def retry_peer_process_capture(self) -> bool: ...

    async def apply_loopback_capture_option(self, value: str) -> None: ...

    def list_loopback_capture_options(self) -> object: ...

    def list_loopback_process_options(self) -> object: ...

    def list_loopback_device_options(self) -> object: ...

    def current_loopback_capture_option_value(self) -> object: ...

    def loopback_capture_summary(self) -> object: ...

    def overlay_peer_presentation_state(self) -> OverlayPeerPresentationState | None: ...


class UiSettingsRuntimePort(Protocol):
    async def on_dashboard_language_change(
        self,
        change: LanguageSelectionChange,
    ) -> None: ...

    def capture_settings_view_change(self, settings: AppSettingsVNext) -> object: ...

    def merge_settings_view_change_with_current(self, captured: object) -> AppSettingsVNext: ...

    def refresh_settings_projection(
        self,
        *,
        preserve_custom_vocab_draft: bool = False,
    ) -> bool: ...

    def refresh_settings_after_openrouter_pkce_success(self) -> bool: ...

    def merge_settings_tab_apply_with_current_languages(
        self,
        settings: AppSettingsVNext,
    ) -> AppSettingsVNext: ...

    async def apply_settings(
        self,
        settings: AppSettingsVNext,
        *,
        provider_requested: bool = False,
    ) -> object: ...


    async def apply_settings_intent(self, intent: ImmediateSettingsIntent) -> object: ...

    async def retry_settings_runtime(self) -> bool: ...

    async def apply_prompt_intent(self, intent: PromptApplyIntent) -> object: ...

    def materialize_provider_intent(self, intent: ProviderApplyIntent) -> AppSettingsVNext: ...

    def overlay_snapshot(self) -> OverlaySettingsSnapshot | None: ...

    def general_snapshot(self) -> GeneralSettingsSnapshot | None: ...


class UiProviderRuntimePort(Protocol):
    def model_asset_entries(self) -> tuple[object, ...]: ...

    async def refresh_model_assets(self) -> tuple[object, ...]: ...

    async def download_model_asset(self, model_id: str, *, source: str | None = None) -> object: ...

    async def import_model_asset(self, model_id: str, directory: Path) -> object: ...

    async def cancel_model_asset(self, model_id: str) -> bool: ...

    async def delete_model_asset(self, model_id: str) -> object: ...
    async def apply_providers(
        self,
        settings: AppSettingsVNext | None = None,
        *,
        force_rebuild_llm: bool = False,
        persist_settings: bool = True,
        refresh_ui: bool = True,
    ) -> object: ...

    async def install_selected_gpu_model_if_needed(self) -> None: ...

    async def ensure_gpu_device_discovery(self) -> None: ...

    async def begin_alibaba_connection_draft(self) -> AlibabaConnectionDraftSnapshot: ...
    async def alibaba_connection_draft(self) -> AlibabaConnectionDraftSnapshot: ...
    async def alibaba_active_connection(self) -> AlibabaConnectionDraftSnapshot: ...
    async def edit_alibaba_connection_draft(
        self,
        *,
        token: str,
        region: AlibabaRegion | None = None,
        endpoint_mode: AlibabaEndpointMode | None = None,
        api_host: str | None = None,
    ) -> AlibabaConnectionDraftSnapshot: ...
    async def verify_alibaba_connection_draft(
        self,
        *,
        token: str,
        capability: Literal["asr", "translation", "both"],
        api_key: str | None = None,
    ) -> AlibabaConnectionDraftSnapshot: ...
    async def apply_alibaba_connection_draft(
        self, *, token: str
    ) -> AlibabaConnectionApplyResult: ...
    def cancel_alibaba_connection_draft(self, *, token: str) -> None: ...

    async def verify_api_key(self, provider: str, key: str) -> tuple[bool, str]: ...

    def persist_api_key_verification(
        self,
        provider: str,
        key: str,
        success: bool,
    ) -> None: ...

    async def persist_provider_secret_change(self, key: str, value: str) -> bool: ...

    def clear_provider_verification(self, provider: str) -> None: ...

    def handle_gpu_notice_action(self, action: GpuNoticeAction) -> object: ...

    async def handle_managed_gemma_notice_action(
        self,
        action: ManagedGemmaNoticeAction,
    ) -> object: ...


class UiAudioTestRuntimePort(Protocol):
    async def start_audio_test(
        self,
        *,
        observation_callback: Callable[[str, tuple[object, ...]], object] | None = None,
    ) -> bool: ...

    def audio_test_snapshot(self) -> dict[str, object]: ...

    async def wait_audio_test_ready(self) -> bool: ...

    async def stop_audio_test(self) -> None: ...

    def release_audio_test_capture_restore(self, channel: str) -> None: ...


class UiOverlayRuntimePort(Protocol):
    async def set_overlay_enabled(self, enabled: bool) -> object: ...

    async def wait_overlay_transition(self) -> dict[str, object]: ...

    async def set_desktop_overlay_captions_locked(self, locked: bool) -> dict[str, str]: ...

    async def set_desktop_overlay_size_preset(self, size_preset: str) -> dict[str, str]: ...

    async def reset_desktop_overlay_position(self) -> dict[str, str]: ...

    def begin_overlay_calibration(self) -> object: ...

    def set_overlay_calibration_field(
        self,
        field_name: str,
        value: object,
    ) -> object: ...

    def apply_overlay_calibration(self) -> object: ...

    def cancel_overlay_calibration(self) -> object: ...


class UiEngagementRuntimePort(Protocol):
    def get_event_language_codes(self) -> tuple[str | None, str | None]: ...

    def schedule_github_star_prompt_translation_success_observed(self) -> None: ...


    def should_show_github_star_prompt(self) -> bool: ...

    async def persist_github_star_prompt_eligible_launch(self) -> bool: ...

    async def prepare_runtime_after_launch(self) -> None: ...

    async def persist_github_star_prompt_opened(
        self,
        *,
        should_open: Callable[[], bool] | None = None,
    ) -> bool: ...

    async def persist_github_star_prompt_clicked(self) -> None: ...


class UiDiagnosticsRuntimePort(Protocol):
    def cycle_debug_capture_fault_profile(self) -> str: ...

    def cycle_debug_stt_fault_profile(self) -> str: ...

    def clear_debug_audio_fault_profiles(self) -> None: ...


__all__ = [
    "UiAudioTestRuntimePort",
    "UiDiagnosticsRuntimePort",
    "UiEngagementRuntimePort",
    "UiInputRuntimePort",
    "UiOverlayRuntimePort",
    "UiPeerCaptureRuntimePort",
    "UiProviderRuntimePort",
    "UiSettingsRuntimePort",
]
