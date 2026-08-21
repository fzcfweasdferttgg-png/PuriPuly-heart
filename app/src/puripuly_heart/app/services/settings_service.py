"""SettingsService — settings persistence, diff computation, signature building.

Extracted from SettingsManagerMixin. Handles pure settings operations.
apply_settings() stays in GuiController as orchestrator of side effects.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from puripuly_heart.adapters.storage.settings_persistence import load_settings, save_settings
from puripuly_heart.app.services.settings_diff import SettingsDiff, compute_settings_diff
from puripuly_heart.app.services.provider_signature_service import (
    build_llm_provider_signature as _build_llm_provider_signature_impl,
    build_self_stt_provider_signature as _build_self_stt_provider_signature_impl,
    build_self_stt_runtime_signature as _build_self_stt_runtime_signature_impl,
    copy_provider_prompt_apply_fields as _copy_provider_prompt_apply_fields_impl,
    llm_provider_requires_secret as _llm_provider_requires_secret_impl,
    stt_provider_applies_custom_vocabulary as _stt_provider_applies_custom_vocabulary_impl,
)
from puripuly_heart.app.wiring import build_peer_stt_provider_signature
from puripuly_heart.config.settings import new_settings_for_first_run
from puripuly_heart.config.vad_defaults import DEFAULT_STABLE_VAD_HANGOVER_MS
from puripuly_heart.domain.i18n import get_locale, set_locale

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings

logger = logging.getLogger(__name__)


@dataclass
class SettingsService:
    """Settings persistence, diff computation, signature building."""

    # Callbacks
    _config_path: Path | None = None
    _hub_provider: Callable[[], object | None] | None = None
    _signature_detector_provider: Callable[[], object | None] | None = None
    _log_basic: Callable[[str], None] | None = None
    _log_detailed: Callable[[str], None] | None = None
    _log_error: Callable[[str], None] | None = None
    _mic_test_audio_signature: Callable[[object], object | None] | None = None
    _peer_activation_requested: Callable[[object], bool] | None = None
    _build_peer_runtime_config: Callable[[object], object] | None = None

    def _emit_log_basic(self, message: str) -> None:
        if self._log_basic is not None:
            self._log_basic(message)

    def _emit_log_detailed(self, message: str) -> None:
        if self._log_detailed is not None:
            self._log_detailed(message)

    def _emit_error(self, message: str) -> None:
        if self._log_error is not None:
            self._log_error(message)

    # --- Provider signature methods (from ProviderSignaturesMixin) ---

    def stt_provider_applies_custom_vocabulary(self, settings: AppSettings) -> bool:
        return _stt_provider_applies_custom_vocabulary_impl(settings)

    def llm_provider_requires_secret(self, provider: object) -> bool:
        return _llm_provider_requires_secret_impl(provider)

    def selected_stt_provider(self, settings: AppSettings | None):
        if settings is None:
            return None
        return settings.provider.stt

    def stt_runtime_custom_vocabulary_signature(self, settings: AppSettings):
        from puripuly_heart.app.services.provider_signature_service import stt_runtime_custom_vocabulary_signature
        return stt_runtime_custom_vocabulary_signature(settings)

    def build_self_stt_runtime_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return _build_self_stt_runtime_signature_impl(settings)

    def build_self_stt_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return _build_self_stt_provider_signature_impl(settings)

    def build_peer_stt_runtime_signature(self, settings: AppSettings) -> tuple[object, ...]:
        if self._build_peer_runtime_config is not None:
            return self._build_peer_runtime_config(settings).runtime_signature
        return ()

    def build_peer_stt_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return build_peer_stt_provider_signature(settings)

    def build_llm_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return _build_llm_provider_signature_impl(settings)

    def sync_signature_caches(self, settings: AppSettings) -> None:
        det = self._signature_detector_provider() if self._signature_detector_provider else None
        if det is None:
            return
        current_self_signature = self.build_self_stt_runtime_signature(settings)
        det.last_stt_runtime_signature = current_self_signature
        det.last_self_stt_runtime_signature = current_self_signature
        det.last_peer_stt_runtime_signature = self.build_peer_stt_runtime_signature(settings)
        det.last_self_stt_provider_signature = self.build_self_stt_provider_signature(settings)
        det.last_peer_stt_provider_signature = self.build_peer_stt_provider_signature(settings)
        det.last_llm_provider_signature = self.build_llm_provider_signature(settings)
        det.last_microphone_test_audio_settings_signature = (
            self._mic_test_audio_signature(settings) if self._mic_test_audio_signature else None
        )
        det.last_peer_translation_enabled = settings.ui.peer_translation_enabled
        det.last_peer_translation_activation_requested = (
            self._peer_activation_requested(settings) if self._peer_activation_requested else False
        )

    def copy_provider_prompt_apply_fields(self, source: AppSettings, target: AppSettings) -> None:
        _copy_provider_prompt_apply_fields_impl(source, target)

    def merge_settings_tab_apply_with_current_languages(self, settings: AppSettings | None, pending: AppSettings) -> AppSettings:
        if settings is None:
            return copy.deepcopy(pending)
        merged = copy.deepcopy(settings)
        self.copy_provider_prompt_apply_fields(pending, merged)
        hub = self._hub_provider() if self._hub_provider else None
        if hub is not None:
            merged.languages.source_language = hub.source_language
            merged.languages.target_language = hub.target_language
            merged.languages.second_target_language = getattr(hub, "second_target_language", merged.languages.second_target_language)
            merged.languages.peer_source_language = getattr(hub, "peer_source_language", merged.languages.peer_source_language)
            merged.languages.peer_target_language = getattr(hub, "peer_target_language", merged.languages.peer_target_language)
        return merged

    # --- Settings persistence ---

    def load_or_init_settings(self, path: Path) -> AppSettings:
        if path.exists():
            return load_settings(path)
        settings = new_settings_for_first_run()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_settings(path, settings)
        return settings

    def save_settings_to_disk(self, config_path: Path, settings: AppSettings) -> None:
        try:
            save_settings(config_path, settings)
            bt = settings.backup_translation
            logger.info(
                "[Settings] Saved: backup.enabled=%s mode=%s oc.base_url=%s oc.model=%s llm.base_url=%s llm.model=%s "
                "stt=%s stt_quant=%s peer_stt=%s peer_stt_quant=%s llm=%s",
                bt.enabled, bt.mode.value,
                bt.openai_compatible.base_url, bt.openai_compatible.model,
                bt.local_llm.base_url, bt.local_llm.model,
                settings.provider.stt.value,
                settings.provider.stt_quant,
                settings.provider.peer_stt.value,
                settings.provider.peer_stt_quant,
                settings.provider.llm.value,
            )
        except Exception as exc:
            self._emit_error(f"Failed to save settings: {exc}")

    # --- Diff computation (pure) ---

    def compute_diff(
        self,
        prev_settings: AppSettings | None,
        next_settings: AppSettings,
        *,
        hub_source_lang: str | None = None,
        hub_target_lang: str | None = None,
        hub_peer_source_lang: str | None = None,
        hub_peer_target_lang: str | None = None,
        hub_low_latency: bool | None = None,
        hub_second_target_lang: str = "",
        prev_self_signature: object | None = None,
        prev_peer_signature: object | None = None,
        prev_peer_enabled: bool = False,
        prev_peer_activation: bool = False,
        next_self_signature: object | None = None,
        next_peer_signature: object | None = None,
        next_peer_activation: bool = False,
        prev_overlay_target: str | None = None,
        next_overlay_target: str | None = None,
        prev_overlay_enabled: bool = False,
        prev_vrc_mic_sync: bool | None = None,
        prev_locale: str | None = None,
    ) -> SettingsDiff:
        return compute_settings_diff(
            prev=prev_settings,
            next_settings=next_settings,
            hub_source_lang=hub_source_lang,
            hub_target_lang=hub_target_lang,
            hub_peer_source_lang=hub_peer_source_lang,
            hub_peer_target_lang=hub_peer_target_lang,
            hub_low_latency=hub_low_latency,
            hub_second_target_lang=hub_second_target_lang,
            prev_self_signature=prev_self_signature,
            prev_peer_signature=prev_peer_signature,
            prev_peer_enabled=prev_peer_enabled,
            prev_peer_activation=prev_peer_activation,
            next_self_signature=next_self_signature,
            next_peer_signature=next_peer_signature,
            next_peer_activation=next_peer_activation,
            prev_overlay_target=prev_overlay_target,
            next_overlay_target=next_overlay_target,
            prev_overlay_enabled=prev_overlay_enabled,
            prev_vrc_mic_sync=prev_vrc_mic_sync,
            prev_locale=prev_locale,
        )
