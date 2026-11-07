from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import flet as ft

from puripuly_heart.app.ports.settings_secrets import SettingsSecretsPort
from puripuly_heart.app.ports.settings_view import (
    GeneralSettingsSnapshot,
    ImmediateSettingsIntent,
    OverlaySettingsSnapshot,
    PromptApplyIntent,
    PromptSettingsSnapshot,
    ProviderApplyIntent,
    ProviderSettingsSnapshot,
)
from puripuly_heart.ui.gpu_device import GpuDeviceOption


@dataclass(frozen=True, slots=True)
class SettingsSurfaceIntents:
    settings_changed: Callable[[ImmediateSettingsIntent], None]
    show_snackbar: Callable[[str, str], None]
    runtime_log_basic: Callable[..., None] | None = None


@dataclass(frozen=True, slots=True)
class SettingsProviderIntents:
    providers_changed: Callable[[], None]
    verify_api_key: Callable[[str, str], object]
    provider_secret_change: Callable[[str, str], object]
    secret_cleared: Callable[[str], None]
    local_llm_secret_changed: Callable[[], None]
    gpu_discovery_requested: Callable[[], object]
    settings_secrets: SettingsSecretsPort
    fetch_models: Callable[[str, str], object] | None = None
    test_connection: Callable[[str, str], object] | None = None


@dataclass(frozen=True, slots=True)
class SettingsGeneralIntents:
    start_audio_test: Callable[[], None]
    list_loopback_capture_options: Callable[[], object]
    list_loopback_process_options: Callable[[], object]
    list_loopback_device_options: Callable[[], object]
    current_loopback_capture_option: Callable[[], str]
    apply_loopback_capture_option: Callable[[str], None]
    loopback_capture_summary: Callable[[], str]
    osc_effective_ports: Callable[[], tuple[int | None, int | None]] | None = None


@dataclass(frozen=True, slots=True)
class SettingsPromptIntents:
    prompt_apply_settings: Callable[[PromptApplyIntent], None]
    pending_prompt_changed: Callable[[PromptApplyIntent], None] | None = None


@dataclass(frozen=True, slots=True)
class SettingsOverlayIntents:
    desktop_overlay_lock_change: Callable[[bool], None]
    desktop_overlay_size_change: Callable[[str], None]
    desktop_overlay_recovery_action: Callable[[str], None]
    desktop_overlay_position_reset: Callable[[], None]
    view_logs: Callable[[], None]
    calibration_begin: Callable[[], object] | None = None
    calibration_change: Callable[[str, object], object] | None = None
    calibration_apply: Callable[[], object] | None = None
    calibration_cancel: Callable[[], object] | None = None


class SettingsIntentConsumer(Protocol):
    def bind_settings_intents(
        self,
        *,
        surface: SettingsSurfaceIntents,
        provider: SettingsProviderIntents,
        general: SettingsGeneralIntents,
        prompt: SettingsPromptIntents,
        overlay: SettingsOverlayIntents,
    ) -> None: ...


class SettingsProviderStateSink(Protocol):
    has_provider_changes: bool

    def load_from_settings(
        self,
        *,
        provider: ProviderSettingsSnapshot,
        general: GeneralSettingsSnapshot,
        prompt: PromptSettingsSnapshot,
        overlay: OverlaySettingsSnapshot,
        config_path: Path,
        preserve_custom_vocab_draft: bool = False,
    ) -> None: ...

    def refresh_after_openrouter_pkce_success(
        self,
        *,
        provider: ProviderSettingsSnapshot,
        prompt: PromptSettingsSnapshot,
        config_path: Path,
    ) -> None: ...

    def set_gpu_devices(
        self,
        *,
        devices: tuple[GpuDeviceOption, ...] | None = None,
        llm_devices: tuple[GpuDeviceOption, ...] | None = None,
    ) -> None: ...

    def acknowledge_provider_apply_settings(self, intent: ProviderApplyIntent) -> None: ...

    def apply_locale(self) -> None: ...


class SettingsApiSlotProvider(Protocol):
    def self_stt_control(self) -> ft.Control: ...

    def peer_stt_control(self) -> ft.Control: ...

    def translation_provider_control(self) -> ft.Control: ...

    def translation_connection_control(self) -> ft.Control: ...

    def http_extension_control(self) -> ft.Control: ...

    def gpu_device_control(self) -> ft.Control: ...

    def stt_model_control(self) -> ft.Control: ...

    def gpu_llm_control(self) -> ft.Control: ...

    def local_llm_connection_control(self) -> ft.Control: ...


@dataclass(frozen=True, slots=True)
class SettingsApiSurfaceSlots:
    self_stt: ft.Control
    peer_stt: ft.Control
    translation_provider: ft.Control
    translation_connection: ft.Control
    gpu_device: ft.Control
    stt_model: ft.Control
    gpu_llm: ft.Control
    http_extension: ft.Control | None = None
    peer_gpu_device: ft.Control | None = None
    shared_engine: ft.Control | None = None
    backup_translation: ft.Control | None = None
    openai_compatible_config: ft.Control | None = None
    backup_translation_config: ft.Control | None = None
    local_llm_connection: ft.Control | None = None

    @classmethod
    def from_slot_provider(cls, provider: SettingsApiSlotProvider) -> SettingsApiSurfaceSlots:
        extension_factory = getattr(provider, "http_extension_control", None)
        peer_gpu_factory = getattr(provider, "peer_gpu_device_control", None)
        shared_engine_factory = getattr(provider, "shared_engine_control", None)
        backup_translation_factory = getattr(provider, "backup_translation_control", None)
        openai_config_factory = getattr(provider, "openai_compatible_config_control", None)
        backup_config_factory = getattr(provider, "backup_translation_config_control", None)
        return cls(
            self_stt=provider.self_stt_control(),
            peer_stt=provider.peer_stt_control(),
            translation_provider=provider.translation_provider_control(),
            translation_connection=provider.translation_connection_control(),
            gpu_device=provider.gpu_device_control(),
            stt_model=provider.stt_model_control(),
            gpu_llm=provider.gpu_llm_control(),
            local_llm_connection=provider.local_llm_connection_control(),
            http_extension=(extension_factory() if callable(extension_factory) else None),
            peer_gpu_device=(peer_gpu_factory() if callable(peer_gpu_factory) else None),
            shared_engine=(shared_engine_factory() if callable(shared_engine_factory) else None),
            backup_translation=(
                backup_translation_factory() if callable(backup_translation_factory) else None
            ),
            openai_compatible_config=(
                openai_config_factory() if callable(openai_config_factory) else None
            ),
            backup_translation_config=(
                backup_config_factory() if callable(backup_config_factory) else None
            ),
        )


@dataclass(frozen=True, slots=True)
class SettingsApiSurfaceRegions:
    rows: tuple[ft.Control, ...]
    provider_row: ft.Container
    provider_controls: ft.Row
    translation_connection_row: ft.Container
    translation_connection_controls: ft.Row
    gpu_device_row: ft.Container
    gpu_device_controls: ft.Row


@dataclass(frozen=True, slots=True)
class SettingsGeneralSurfaceSlots:
    ui: ft.Control
    chatbox_source: ft.Control
    audio_host_api: ft.Control
    microphone: ft.Control
    loopback: ft.Control
    audio_test: ft.Control
    self_vad: ft.Control
    peer_vad: ft.Control
    clipboard_auto_translate: ft.Control
    vrchat_mic_intercept: ft.Control
    activation_notice: ft.Control
    activation_notice_middle: ft.Control
    activation_notice_trailing: ft.Control


@dataclass(frozen=True, slots=True)
class SettingsPromptSurfaceSlots:
    custom_vocabulary: ft.Control
    peer_expected_language: ft.Control
    persona: ft.Control


@dataclass(frozen=True, slots=True)
class SettingsOverlaySurfaceSlots:
    overlay_target: ft.Control
    overlay_translation: ft.Control
    overlay_peer_original: ft.Control
    anchor: ft.Control
    distance: ft.Control
    offset_x: ft.Control
    offset_y: ft.Control
    text_scale: ft.Control
    vr_reset: ft.Control
    desktop_size: ft.Control
    desktop_lock: ft.Control
    desktop_background_alpha: ft.Control
    desktop_swap_caption_languages: ft.Control
    desktop_reset: ft.Control
    desktop_reset_spacer: ft.Control
    desktop_status: ft.Control
    desktop_status_trailing: ft.Control


@dataclass(frozen=True, slots=True)
class SettingsGeneralSurfaceRegions:
    rows: tuple[ft.Control, ...]
    primary_row: ft.Container
    audio_row: ft.Container
    vad_row: ft.Container
    clipboard_row: ft.Container
    activation_notice_row: ft.Container
    primary_row_placeholder: ft.Control


@dataclass(frozen=True, slots=True)
class SettingsPromptSurfaceRegions:
    rows: tuple[ft.Control, ...]


@dataclass(frozen=True, slots=True)
class SettingsOverlaySurfaceRegions:
    rows: tuple[ft.Control, ...]
    target_row: ft.Container
    vr_rows: tuple[ft.Container, ...]
    desktop_rows: tuple[ft.Container, ...]
    desktop_controls_row: ft.Container
    recovery_row: ft.Container
    recovery_row_placeholder: ft.Control


__all__ = [
    "SettingsApiSlotProvider",
    "SettingsApiSurfaceRegions",
    "SettingsApiSurfaceSlots",
    "SettingsGeneralIntents",
    "SettingsGeneralSurfaceRegions",
    "SettingsGeneralSurfaceSlots",
    "SettingsIntentConsumer",
    "SettingsOverlayIntents",
    "SettingsOverlaySurfaceRegions",
    "SettingsOverlaySurfaceSlots",
    "SettingsPromptIntents",
    "SettingsPromptSurfaceRegions",
    "SettingsPromptSurfaceSlots",
    "SettingsChatGptIntents",
    "SettingsProviderIntents",
    "SettingsProviderStateSink",
    "SettingsSurfaceIntents",
]
