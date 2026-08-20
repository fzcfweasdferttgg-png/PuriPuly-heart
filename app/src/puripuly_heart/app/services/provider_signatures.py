from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from puripuly_heart.app.services.provider_signature_service import (
    build_llm_provider_signature as _build_llm_provider_signature_impl,
    build_self_stt_provider_signature as _build_self_stt_provider_signature_impl,
    build_self_stt_runtime_signature as _build_self_stt_runtime_signature_impl,
    copy_provider_prompt_apply_fields as _copy_provider_prompt_apply_fields_impl,
    llm_provider_requires_secret as _llm_provider_requires_secret_impl,
    stt_provider_applies_custom_vocabulary as _stt_provider_applies_custom_vocabulary_impl,
)
from puripuly_heart.app.wiring import build_peer_stt_provider_signature

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


class ProviderSignaturesMixin:

    def _stt_provider_applies_custom_vocabulary(self, settings: AppSettings) -> bool:
        return _stt_provider_applies_custom_vocabulary_impl(settings)

    def _llm_provider_requires_secret(self, provider: object) -> bool:
        return _llm_provider_requires_secret_impl(provider)

    def _selected_stt_provider(self):
        if self.settings is None:
            return None
        return self.settings.provider.stt

    def _stt_runtime_custom_vocabulary_signature(self, settings: AppSettings):
        from puripuly_heart.app.services.provider_signature_service import stt_runtime_custom_vocabulary_signature
        return stt_runtime_custom_vocabulary_signature(settings)

    def _build_self_stt_runtime_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return _build_self_stt_runtime_signature_impl(settings)

    def _build_self_stt_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return _build_self_stt_provider_signature_impl(settings)

    def _build_peer_stt_runtime_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return self._build_peer_runtime_config(settings).runtime_signature

    def _build_peer_stt_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return build_peer_stt_provider_signature(settings)

    def _build_llm_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return _build_llm_provider_signature_impl(settings)

    def _sync_signature_caches(self, settings: AppSettings) -> None:
        current_self_signature = self._build_self_stt_runtime_signature(settings)
        self._last_stt_runtime_signature = current_self_signature
        self._last_self_stt_runtime_signature = current_self_signature
        self._last_peer_stt_runtime_signature = self._build_peer_stt_runtime_signature(settings)
        self._last_self_stt_provider_signature = self._build_self_stt_provider_signature(settings)
        self._last_peer_stt_provider_signature = self._build_peer_stt_provider_signature(settings)
        self._last_llm_provider_signature = self._build_llm_provider_signature(settings)
        self._last_microphone_test_audio_settings_signature = (
            self._microphone_test_audio_settings_signature(settings)
        )
        self._last_peer_translation_enabled = settings.ui.peer_translation_enabled
        self._last_peer_translation_activation_requested = (
            self._peer_translation_activation_requested_for(settings)
        )

    def _copy_provider_prompt_apply_fields(self, source: AppSettings, target: AppSettings) -> None:
        _copy_provider_prompt_apply_fields_impl(source, target)

    def merge_settings_tab_apply_with_current_languages(self, pending: AppSettings) -> AppSettings:
        if self.settings is None:
            return copy.deepcopy(pending)

        merged = copy.deepcopy(self.settings)
        self._copy_provider_prompt_apply_fields(pending, merged)
        if self.hub is not None:
            merged.languages.source_language = self.hub.source_language
            merged.languages.target_language = self.hub.target_language
            merged.languages.second_target_language = getattr(
                self.hub,
                "second_target_language",
                merged.languages.second_target_language,
            )
            merged.languages.peer_source_language = getattr(
                self.hub,
                "peer_source_language",
                merged.languages.peer_source_language,
            )
            merged.languages.peer_target_language = getattr(
                self.hub,
                "peer_target_language",
                merged.languages.peer_target_language,
            )
        return merged
