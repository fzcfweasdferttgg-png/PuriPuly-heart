from __future__ import annotations

import copy
import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from puripuly_heart.adapters.storage.providers_persistence import load_providers  # re-exported for ui/ callers
from puripuly_heart.adapters.storage.settings_persistence import load_settings, save_settings
from puripuly_heart.config.settings import (
    new_settings_for_first_run,
)
from puripuly_heart.config.vad_defaults import DEFAULT_STABLE_VAD_HANGOVER_MS
from puripuly_heart.domain.i18n import get_locale, set_locale

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings

logger = logging.getLogger(__name__)


class SettingsManagerMixin:

    async def on_dashboard_language_change(
        self,
        *,
        source_code: str,
        target_code: str,
        peer_source_code: str = "",
        peer_target_code: str = "",
        second_target_code: str = "",
    ) -> None:
        if self.settings is None:
            return

        updated = copy.deepcopy(self.settings)
        updated.languages.source_language = source_code
        updated.languages.target_language = target_code
        updated.languages.second_target_language = second_target_code
        updated.languages.peer_source_language = peer_source_code
        updated.languages.peer_target_language = peer_target_code
        await self.apply_settings(updated)

    async def apply_settings(self, settings: AppSettings) -> None:
        def _effective_peer_language(language: str, peer_language: str) -> str:
            return peer_language or language

        prev_microphone_test_audio_signature = (
            self._last_microphone_test_audio_settings_signature
            or self._microphone_test_audio_settings_signature(self.settings)
        )
        next_microphone_test_audio_signature = self._microphone_test_audio_settings_signature(
            settings
        )
        if (
            prev_microphone_test_audio_signature is not None
            and prev_microphone_test_audio_signature != next_microphone_test_audio_signature
        ):
            await self.stop_microphone_test_for_audio_settings_change()

        prev_locale = get_locale()
        prev_overlay_enabled = (
            self.settings.ui.overlay_enabled if self.settings is not None else False
        )
        previous_settings_for_desktop = (
            copy.deepcopy(self.settings) if self.settings is not None else None
        )
        prev_overlay_target = self._previous_overlay_target_for_apply()
        next_overlay_target = self._overlay_target_for_settings(settings)
        if (
            prev_overlay_target != next_overlay_target
            and prev_overlay_enabled
            and settings.ui.overlay_enabled
            and self._overlay_runtime_is_active()
        ):
            self.log_basic(
                "[Overlay] Target changed while running; stopping current overlay before switch"
            )
            settings = copy.deepcopy(settings)
            settings.ui.overlay_enabled = False
        desktop_runtime_controls = self._prepare_desktop_runtime_settings_update(
            previous_settings_for_desktop,
            settings,
        )
        prev_peer_translation_enabled = (
            self._last_peer_translation_enabled
            if self._last_peer_translation_enabled is not None
            else (self.settings.ui.peer_translation_enabled if self.settings is not None else False)
        )
        prev_peer_activation_requested = (
            self._last_peer_translation_activation_requested
            if self._last_peer_translation_activation_requested is not None
            else (
                self._peer_translation_activation_requested_for(self.settings)
                if self.settings is not None
                else False
            )
        )
        prev_self_signature = (
            self._last_self_stt_runtime_signature or self._last_stt_runtime_signature
        )
        prev_peer_signature = self._last_peer_stt_runtime_signature
        # hub.source_language를 기준으로 비교 (settings 객체는 이미 수정되어 전달될 수 있음)
        prev_source_lang = self.hub.source_language if self.hub else None
        prev_target_lang = self.hub.target_language if self.hub else None
        prev_peer_source_lang = (
            getattr(self.hub, "peer_source_language", None) if self.hub else None
        )
        prev_peer_target_lang = (
            getattr(self.hub, "peer_target_language", None) if self.hub else None
        )
        prev_effective_peer_source = (
            _effective_peer_language(prev_source_lang, prev_peer_source_lang)
            if prev_source_lang is not None and prev_peer_source_lang is not None
            else None
        )
        prev_effective_peer_target = (
            _effective_peer_language(prev_target_lang, prev_peer_target_lang)
            if prev_target_lang is not None and prev_peer_target_lang is not None
            else None
        )
        prev_low_latency = self.hub.low_latency_mode if self.hub else None
        source_language_changed = (
            prev_source_lang is not None and prev_source_lang != settings.languages.source_language
        )
        target_language_changed = (
            prev_target_lang is not None and prev_target_lang != settings.languages.target_language
        )
        second_target_language_changed = (
            self.hub is not None
            and getattr(self.hub, "second_target_language", "") != settings.languages.second_target_language
        )
        effective_peer_source_changed = (
            prev_effective_peer_source is not None
            and prev_effective_peer_source
            != _effective_peer_language(
                settings.languages.source_language,
                settings.languages.peer_source_language,
            )
        )
        effective_peer_target_changed = (
            prev_effective_peer_target is not None
            and prev_effective_peer_target
            != _effective_peer_language(
                settings.languages.target_language,
                settings.languages.peer_target_language,
            )
        )
        if source_language_changed or target_language_changed:
            presenter = self._overlay_presenter
            self.log_basic(
                "[Settings] Applying languages: "
                f"source={prev_source_lang}->{settings.languages.source_language} "
                f"target={prev_target_lang}->{settings.languages.target_language}"
            )
            self.log_detailed(
                "[Settings] Language apply detail: "
                f"overlay_state={self.overlay_state} "
                f"presenter_attached={presenter is not None} "
                f"bridge_attached={self._overlay_bridge is not None} "
                "overlay_sink_matches_presenter="
                f"{self.hub is not None and presenter is not None and getattr(self.hub, 'overlay_sink', None) is presenter}"
            )
        prev_llm_provider = self.settings.provider.llm if self.settings else None
        prev_llm_model = self.settings.provider.openai_compatible.model if self.settings else None
        prev_llm_base_url = self.settings.provider.openai_compatible.base_url if self.settings else None
        self.settings = settings
        self._last_microphone_test_audio_settings_signature = next_microphone_test_audio_signature
        self._sync_overlay_calibration_cache(settings)
        self._sync_desktop_overlay_interaction_mode_from_settings(settings)
        self.save_settings()
        await self._broadcast_desktop_runtime_control_payloads(desktop_runtime_controls)
        await self._sync_clipboard_watcher()
        self._refresh_local_stt_runtime_state()
        self._clear_local_stt_pending_enable_if_provider_switched_away()

        if (
            prev_low_latency is not None
            and prev_low_latency != settings.stt.low_latency_mode
        ):
            self.log_detailed(
                "[Settings] Low latency detail: "
                f"mode={prev_low_latency}->{settings.stt.low_latency_mode} rebuilding_llm_provider=True"
            )
            await self._rebuild_llm_provider()

        llm_provider_changed = (
            prev_llm_provider is not None
            and (
                prev_llm_provider != settings.provider.llm
                or prev_llm_model != settings.provider.openai_compatible.model
                or prev_llm_base_url != settings.provider.openai_compatible.base_url
            )
        )
        if llm_provider_changed:
            self.log_basic(
                f"[Settings] LLM provider changed: {prev_llm_provider}->{settings.provider.llm} "
                f"model={prev_llm_model}->{settings.provider.openai_compatible.model} rebuilding"
            )
            await self._rebuild_llm_provider()

        if self.hub is not None:
            self.hub.source_language = settings.languages.source_language
            self.hub.target_language = settings.languages.target_language
            self.hub.second_target_language = settings.languages.second_target_language
            if self.hub.translation_service is not None:
                ts = self.hub.translation_service
                ts.source_language = settings.languages.source_language
                ts.target_language = settings.languages.target_language
                ts.second_target_language = settings.languages.second_target_language
                ts.peer_source_language = settings.languages.peer_source_language
                ts.peer_target_language = settings.languages.peer_target_language
                ts.system_prompt = settings.system_prompt
            self.hub.peer_source_language = settings.languages.peer_source_language
            self.hub.peer_target_language = settings.languages.peer_target_language
            self.hub.system_prompt = settings.system_prompt
            self.hub.low_latency_mode = settings.stt.low_latency_mode
            self.hub.low_latency_spec_retry_max = settings.stt.low_latency_spec_retry_max
            self.hub.hangover_s = (
                settings.stt.low_latency_vad_hangover_ms / 1000.0
                if settings.stt.low_latency_mode
                else DEFAULT_STABLE_VAD_HANGOVER_MS / 1000.0
            )
            self.hub.peer_hangover_s = settings.desktop_audio.vad_hangover_ms / 1000.0
            self.hub.chatbox_include_source = settings.osc.chatbox_include_source
            if self.hub.output_dispatcher is not None:
                self.hub.output_dispatcher.chatbox_include_source = settings.osc.chatbox_include_source
            self._sync_effective_hub_flags(settings)

            async def _clear_language_runtime_state(channel: str) -> None:
                try:
                    await self.hub.clear_language_runtime_state(channel=channel)
                except Exception as exc:
                    self._log_error(f"Failed to clear language runtime state for {channel}: {exc}")

            if source_language_changed or target_language_changed or second_target_language_changed:
                await _clear_language_runtime_state("self")
            if effective_peer_source_changed or effective_peer_target_changed or second_target_language_changed:
                await _clear_language_runtime_state("peer")

        presenter = self._overlay_presenter
        if presenter is not None:
            await presenter.update_display_preferences(
                show_translation=settings.overlay.show_translation,
                show_peer_original=settings.overlay.show_peer_original,
            )

        if prev_overlay_enabled != settings.ui.overlay_enabled:
            await self.set_overlay_enabled(settings.ui.overlay_enabled)

        if self._last_vrc_mic_sync_enabled != settings.osc.vrc_mic_intercept:
            if self.vrc_mic_audio_gate is not None:
                self.vrc_mic_audio_gate.set_enabled(settings.osc.vrc_mic_intercept)
            self.log_detailed(f"[Settings] VRC mic sync enabled: {settings.osc.vrc_mic_intercept}")
            await self._configure_vrc_mic_receiver(enabled=settings.osc.vrc_mic_intercept)

        current_self_signature = self._build_self_stt_runtime_signature(settings)
        current_peer_signature = self._build_peer_stt_runtime_signature(settings)
        next_peer_activation_requested = self._peer_translation_activation_requested_for(settings)
        should_restart_stt = (
            prev_self_signature is not None and current_self_signature != prev_self_signature
        )
        should_refresh_peer = (
            prev_peer_signature is None
            or current_peer_signature != prev_peer_signature
            or prev_peer_translation_enabled != settings.ui.peer_translation_enabled
            or prev_peer_activation_requested != next_peer_activation_requested
        )

        self._sync_signature_caches(settings)

        if source_language_changed or target_language_changed:
            self.log_detailed(
                "[Settings] Language runtime impact: "
                f"should_restart_stt={should_restart_stt} "
                f"should_refresh_peer={should_refresh_peer} "
                f"prev_overlay_enabled={prev_overlay_enabled} "
                f"next_overlay_enabled={settings.ui.overlay_enabled}"
            )

        if should_refresh_peer and self.hub is not None:
            await self._refresh_peer_stt_runtime()
            self._sync_effective_hub_flags(settings)

        if should_restart_stt:
            await self._replace_runtime_stt_provider()

        any_language_changed = (
            source_language_changed
            or target_language_changed
            or second_target_language_changed
            or effective_peer_source_changed
            or effective_peer_target_changed
        )
        if any_language_changed:
            view_settings = getattr(self.app, "view_settings", None)
            if view_settings is not None:
                with contextlib.suppress(Exception):
                    view_settings.load_from_settings(
                        settings,
                        config_path=self.config_path,
                        preserve_custom_vocab_draft=True,
                    )

        if prev_locale != settings.ui.locale:
            set_locale(settings.ui.locale)
            apply_locale = getattr(self.app, "apply_locale", None)
            if callable(apply_locale):
                try:
                    apply_locale()
                except Exception as exc:
                    self._log_error(f"Failed to apply locale: {exc}")

        self._refresh_overlay_peer_consumers()

    def _load_or_init_settings(self, path: Path) -> AppSettings:
        if path.exists():
            return load_settings(path)
        settings = new_settings_for_first_run()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_settings(path, settings)
        return settings

    def save_settings(self) -> None:
        assert self.settings is not None
        logger = logging.getLogger(__name__)
        try:
            save_settings(self.config_path, self.settings)
            bt = self.settings.backup_translation
            logger.info(
                "[Settings] Saved: backup.enabled=%s mode=%s oc.base_url=%s oc.model=%s llm.base_url=%s llm.model=%s "
                "stt=%s stt_quant=%s peer_stt=%s peer_stt_quant=%s llm=%s",
                bt.enabled, bt.mode.value,
                bt.openai_compatible.base_url, bt.openai_compatible.model,
                bt.local_llm.base_url, bt.local_llm.model,
                self.settings.provider.stt.value,
                self.settings.provider.stt_quant,
                self.settings.provider.peer_stt.value,
                self.settings.provider.peer_stt_quant,
                self.settings.provider.llm.value,
            )
        except Exception as exc:
            self._log_error(f"Failed to save settings: {exc}")

    def _sync_ui_from_settings(self) -> None:
        settings = self.settings
        if settings is None:
            return

        # Dashboard language dropdowns are initialized by the view; set values if present.
        with contextlib.suppress(Exception):
            dash = getattr(self.app, "view_dashboard", None)
            if dash is not None:
                dash.set_languages_from_codes(
                    settings.languages.source_language,
                    settings.languages.target_language,
                    settings.languages.peer_source_language,
                    settings.languages.peer_target_language,
                    settings.languages.second_target_language,
                )
                # Load recent languages from settings
                dash.set_recent_languages(
                    settings.languages.recent_source_languages,
                    settings.languages.recent_target_languages,
                )
                # Connect callback for persistence
                dash.on_recent_languages_change = self._on_recent_languages_change

        with contextlib.suppress(Exception):
            view_settings = getattr(self.app, "view_settings", None)
            if view_settings is not None:
                view_settings.load_from_settings(settings, config_path=self.config_path)
                view_settings.set_overlay_calibration(self.overlay_calibration)

        self._refresh_overlay_peer_consumers()

    def _on_recent_languages_change(self, source: list[str], target: list[str]) -> None:
        if self.settings is None:
            return
        self.settings.languages.recent_source_languages = list(source)
        self.settings.languages.recent_target_languages = list(target)
        self.save_settings()
