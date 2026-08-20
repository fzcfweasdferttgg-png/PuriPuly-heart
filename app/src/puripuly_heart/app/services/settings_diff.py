"""Pure diff computation for settings changes.

Computes what changed between two AppSettings snapshots.
Zero side effects — just data in, data out. Testable with simple data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


def _effective_peer_language(language: str, peer_language: str) -> str:
    return peer_language or language


@dataclass(frozen=True)
class SettingsDiff:
    """Immutable summary of what changed between two settings snapshots."""

    # Language changes
    source_language_changed: bool = False
    target_language_changed: bool = False
    second_target_language_changed: bool = False
    effective_peer_source_changed: bool = False
    effective_peer_target_changed: bool = False

    # Provider changes
    llm_provider_changed: bool = False
    low_latency_changed: bool = False

    # Overlay changes
    overlay_target_changed: bool = False
    overlay_enabled_changed: bool = False

    # VRC mic sync
    vrc_mic_sync_changed: bool = False

    # Locale
    locale_changed: bool = False

    # STT runtime
    should_restart_stt: bool = False
    should_refresh_peer: bool = False

    # Microphone test
    mic_test_audio_changed: bool = False

    # Peer translation state
    peer_translation_enabled_changed: bool = False
    peer_activation_changed: bool = False


def compute_settings_diff(
    prev: AppSettings | None,
    next_settings: AppSettings,
    *,
    hub_source_lang: str | None,
    hub_target_lang: str | None,
    hub_peer_source_lang: str | None,
    hub_peer_target_lang: str | None,
    hub_low_latency: bool | None,
    hub_second_target_lang: str,
    prev_self_signature: tuple[object, ...] | None,
    prev_peer_signature: tuple[object, ...] | None,
    prev_peer_enabled: bool,
    prev_peer_activation: bool,
    next_self_signature: tuple[object, ...],
    next_peer_signature: tuple[object, ...],
    next_peer_activation: bool,
    prev_overlay_target: str | None,
    next_overlay_target: str,
    prev_overlay_enabled: bool,
    prev_vrc_mic_sync: bool | None,
    prev_locale: str,
) -> SettingsDiff:
    """Compute what changed between two settings snapshots.

    Pure function — no I/O, no side effects. All context passed as arguments.
    """
    # Language changes
    source_language_changed = (
        hub_source_lang is not None
        and hub_source_lang != next_settings.languages.source_language
    )
    target_language_changed = (
        hub_target_lang is not None
        and hub_target_lang != next_settings.languages.target_language
    )
    second_target_language_changed = (
        hub_second_target_lang != next_settings.languages.second_target_language
    )

    # Effective peer language changes
    prev_effective_peer_source = (
        _effective_peer_language(hub_source_lang, hub_peer_source_lang)
        if hub_source_lang is not None and hub_peer_source_lang is not None
        else None
    )
    prev_effective_peer_target = (
        _effective_peer_language(hub_target_lang, hub_peer_target_lang)
        if hub_target_lang is not None and hub_peer_target_lang is not None
        else None
    )
    effective_peer_source_changed = (
        prev_effective_peer_source is not None
        and prev_effective_peer_source
        != _effective_peer_language(
            next_settings.languages.source_language,
            next_settings.languages.peer_source_language,
        )
    )
    effective_peer_target_changed = (
        prev_effective_peer_target is not None
        and prev_effective_peer_target
        != _effective_peer_language(
            next_settings.languages.target_language,
            next_settings.languages.peer_target_language,
        )
    )

    # LLM provider changes
    prev_llm_provider = prev.provider.llm if prev else None
    prev_llm_model = prev.provider.openai_compatible.model if prev else None
    prev_llm_base_url = prev.provider.openai_compatible.base_url if prev else None
    llm_provider_changed = (
        prev_llm_provider is not None
        and (
            prev_llm_provider != next_settings.provider.llm
            or prev_llm_model != next_settings.provider.openai_compatible.model
            or prev_llm_base_url != next_settings.provider.openai_compatible.base_url
        )
    )

    # Low latency
    low_latency_changed = (
        hub_low_latency is not None
        and hub_low_latency != next_settings.stt.low_latency_mode
    )

    # Overlay
    overlay_target_changed = (
        prev_overlay_target is not None and prev_overlay_target != next_overlay_target
    )
    overlay_enabled_changed = (
        prev_overlay_enabled != next_settings.ui.overlay_enabled
    )

    # VRC mic sync
    vrc_mic_sync_changed = (
        prev_vrc_mic_sync is not None
        and prev_vrc_mic_sync != next_settings.osc.vrc_mic_intercept
    )

    # Locale
    locale_changed = prev_locale != next_settings.ui.locale

    # STT runtime signatures
    should_restart_stt = (
        prev_self_signature is not None
        and next_self_signature != prev_self_signature
    )
    should_refresh_peer = (
        prev_peer_signature is None
        or next_peer_signature != prev_peer_signature
        or prev_peer_enabled != next_settings.ui.peer_translation_enabled
        or prev_peer_activation != next_peer_activation
    )

    # Peer translation state changes
    peer_translation_enabled_changed = (
        prev_peer_enabled != next_settings.ui.peer_translation_enabled
    )
    peer_activation_changed = prev_peer_activation != next_peer_activation

    return SettingsDiff(
        source_language_changed=source_language_changed,
        target_language_changed=target_language_changed,
        second_target_language_changed=second_target_language_changed,
        effective_peer_source_changed=effective_peer_source_changed,
        effective_peer_target_changed=effective_peer_target_changed,
        llm_provider_changed=llm_provider_changed,
        low_latency_changed=low_latency_changed,
        overlay_target_changed=overlay_target_changed,
        overlay_enabled_changed=overlay_enabled_changed,
        vrc_mic_sync_changed=vrc_mic_sync_changed,
        locale_changed=locale_changed,
        should_restart_stt=should_restart_stt,
        should_refresh_peer=should_refresh_peer,
        mic_test_audio_changed=False,  # computed separately in apply_settings
        peer_translation_enabled_changed=peer_translation_enabled_changed,
        peer_activation_changed=peer_activation_changed,
    )
