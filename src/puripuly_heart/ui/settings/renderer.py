from __future__ import annotations

from collections.abc import Callable

import flet as ft

from puripuly_heart.ui.foundation.tokens import FOUNDATION_DESIGN_TOKENS
from puripuly_heart.ui.settings.contract import (
    SettingsApiSurfaceRegions,
    SettingsApiSurfaceSlots,
    SettingsGeneralSurfaceRegions,
    SettingsGeneralSurfaceSlots,
    SettingsOverlaySurfaceRegions,
    SettingsOverlaySurfaceSlots,
    SettingsPromptSurfaceRegions,
    SettingsPromptSurfaceSlots,
)

SETTINGS_ROW_SPACING = FOUNDATION_DESIGN_TOKENS.spacing.page


def compose_settings_api_surface(
    slots: SettingsApiSurfaceSlots,
) -> SettingsApiSurfaceRegions:
    provider_items: list[ft.Control] = [slots.self_stt]
    if slots.shared_engine is not None:
        provider_items.append(slots.shared_engine)
    provider_items.append(slots.peer_stt)
    provider_items.append(slots.translation_provider)
    if slots.backup_translation is not None:
        provider_items.append(slots.backup_translation)
    provider_controls = ft.Row(
        provider_items,
        spacing=SETTINGS_ROW_SPACING,
        expand=True,
    )
    provider_row = ft.Container(content=provider_controls)

    translation_connection_controls = ft.Row(
        [
            slots.translation_connection,
        ],
        spacing=SETTINGS_ROW_SPACING,
        expand=True,
    )
    translation_connection_row = ft.Container(
        content=translation_connection_controls,
        visible=True,
    )
    gpu_items: list[ft.Control] = [slots.gpu_device]
    if slots.peer_gpu_device is not None:
        gpu_items.append(slots.peer_gpu_device)
    gpu_items.extend([slots.stt_model, slots.gpu_llm, slots.model_assets])
    gpu_device_controls = ft.Row(
        gpu_items,
        spacing=SETTINGS_ROW_SPACING,
        expand=True,
    )
    gpu_device_row = ft.Container(content=gpu_device_controls)
    gpu_device_row.visible = True

    translation_config_items: list[ft.Control] = []
    if slots.openai_compatible_config is not None:
        translation_config_items.append(slots.openai_compatible_config)
    if slots.backup_translation_config is not None:
        translation_config_items.append(slots.backup_translation_config)
    translation_config_row = ft.Container(
        content=ft.Row(
            translation_config_items,
            spacing=SETTINGS_ROW_SPACING,
            expand=True,
        ),
        visible=bool(translation_config_items),
    )
    rows: list[ft.Control] = [
        provider_row,
        translation_connection_row,
    ]
    if translation_config_items:
        rows.append(translation_config_row)
    rows.extend(
        [
            gpu_device_row,
            slots.local_llm_connection,
        ]
    )
    if slots.http_extension is not None:
        rows.insert(2, slots.http_extension)

    return SettingsApiSurfaceRegions(
        rows=tuple(rows),
        provider_row=provider_row,
        provider_controls=provider_controls,
        translation_connection_row=translation_connection_row,
        translation_connection_controls=translation_connection_controls,
        gpu_device_row=gpu_device_row,
        gpu_device_controls=gpu_device_controls,
    )


def compose_settings_triple_row(
    first: ft.Control,
    second: ft.Control,
    third: ft.Control,
    *,
    visible: bool = True,
) -> ft.Container:
    row = ft.Container(
        content=ft.Row(
            [first, second, third],
            spacing=SETTINGS_ROW_SPACING,
            expand=True,
        ),
    )
    row.visible = visible
    return row


def compose_settings_general_surface(
    slots: SettingsGeneralSurfaceSlots,
    *,
    placeholder_factory: Callable[[], ft.Control],
) -> SettingsGeneralSurfaceRegions:
    primary_row_placeholder = placeholder_factory()
    primary_row = compose_settings_triple_row(
        slots.ui,
        slots.chatbox_source,
        primary_row_placeholder,
    )
    audio_row = compose_settings_triple_row(
        slots.audio_host_api,
        slots.microphone,
        slots.loopback,
    )
    vad_row = compose_settings_triple_row(
        slots.audio_test,
        slots.self_vad,
        slots.peer_vad,
    )
    clipboard_row = compose_settings_triple_row(
        slots.clipboard_auto_translate,
        slots.vrchat_mic_intercept,
        placeholder_factory(),
    )
    activation_notice_row = compose_settings_triple_row(
        slots.activation_notice,
        slots.activation_notice_middle,
        slots.activation_notice_trailing,
    )
    return SettingsGeneralSurfaceRegions(
        rows=(primary_row, audio_row, vad_row, clipboard_row, activation_notice_row),
        primary_row=primary_row,
        audio_row=audio_row,
        vad_row=vad_row,
        clipboard_row=clipboard_row,
        activation_notice_row=activation_notice_row,
        primary_row_placeholder=primary_row_placeholder,
    )


def compose_settings_prompt_surface(
    slots: SettingsPromptSurfaceSlots,
) -> SettingsPromptSurfaceRegions:
    return SettingsPromptSurfaceRegions(
        rows=(slots.custom_vocabulary, slots.peer_expected_language, slots.persona)
    )


def compose_settings_overlay_surface(
    slots: SettingsOverlaySurfaceSlots,
    *,
    placeholder_factory: Callable[[], ft.Control],
) -> SettingsOverlaySurfaceRegions:
    target_row = compose_settings_triple_row(
        slots.overlay_target,
        slots.overlay_translation,
        slots.overlay_peer_original,
    )
    vr_anchor_row = compose_settings_triple_row(slots.anchor, slots.distance, slots.offset_x)
    vr_offset_row = compose_settings_triple_row(slots.offset_y, slots.text_scale, slots.vr_reset)
    desktop_controls_row = compose_settings_triple_row(
        slots.desktop_size,
        slots.desktop_lock,
        slots.desktop_background_alpha,
    )
    desktop_reset_row = compose_settings_triple_row(
        slots.desktop_swap_caption_languages,
        slots.desktop_reset,
        slots.desktop_reset_spacer,
    )
    recovery_row_placeholder = placeholder_factory()
    recovery_row = compose_settings_triple_row(
        slots.desktop_status,
        recovery_row_placeholder,
        slots.desktop_status_trailing,
        visible=False,
    )
    return SettingsOverlaySurfaceRegions(
        rows=(
            target_row,
            vr_anchor_row,
            vr_offset_row,
            desktop_controls_row,
            desktop_reset_row,
            recovery_row,
        ),
        target_row=target_row,
        vr_rows=(vr_anchor_row, vr_offset_row),
        desktop_rows=(desktop_controls_row, desktop_reset_row),
        desktop_controls_row=desktop_controls_row,
        recovery_row=recovery_row,
        recovery_row_placeholder=recovery_row_placeholder,
    )


__all__ = [
    "SETTINGS_ROW_SPACING",
    "compose_settings_api_surface",
    "compose_settings_general_surface",
    "compose_settings_overlay_surface",
    "compose_settings_prompt_surface",
    "compose_settings_triple_row",
]
