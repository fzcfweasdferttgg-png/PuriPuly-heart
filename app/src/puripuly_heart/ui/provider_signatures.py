from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

from puripuly_heart.app.wiring import build_peer_stt_provider_signature
from puripuly_heart.config.settings import LLMProviderName, STTProviderName
from puripuly_heart.core.stt.custom_vocab import get_effective_custom_terms

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


def _canonical_json_signature(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ProviderSignaturesMixin:
    """Provider signature and settings merge logic extracted from GuiController."""

    def _stt_provider_applies_custom_vocabulary(self, settings: AppSettings) -> bool:
        return settings.provider.stt in (
            STTProviderName.LOCAL_QWEN,
            STTProviderName.LOCAL_QWEN_17B,
        )

    def _llm_provider_requires_secret(self, provider: LLMProviderName) -> bool:
        return provider in (
            LLMProviderName.OPENAI_COMPATIBLE,
        )

    def _selected_stt_provider(self) -> STTProviderName | None:
        if self.settings is None:
            return None
        return self.settings.provider.stt

    def _stt_runtime_custom_vocabulary_signature(
        self, settings: AppSettings
    ) -> tuple[bool, tuple[str, ...]]:
        if not self._stt_provider_applies_custom_vocabulary(settings):
            return False, ()
        if settings.provider.stt in (STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B):
            from puripuly_heart.core.stt.custom_vocab import get_effective_local_qwen_hotwords

            return (
                settings.stt.custom_vocabulary_enabled,
                tuple(
                    get_effective_local_qwen_hotwords(settings.stt.custom_terms, settings.stt.custom_vocabulary_enabled, settings.languages.source_language)
                ),
            )
        return (
            settings.stt.custom_vocabulary_enabled,
            tuple(get_effective_custom_terms(settings.stt.custom_terms, settings.stt.custom_vocabulary_enabled, settings.languages.source_language)),
        )

    def _build_self_stt_runtime_signature(self, settings: AppSettings) -> tuple[object, ...]:
        custom_vocab_enabled, custom_terms = self._stt_runtime_custom_vocabulary_signature(settings)
        return (
            settings.languages.source_language,
            settings.audio.input_host_api,
            settings.audio.input_device,
            settings.provider.stt,
            settings.stt.vad_speech_threshold,
            settings.stt.low_latency_mode,
            settings.stt.low_latency_merge_gap_ms,
            settings.stt.low_latency_spec_retry_max,
            settings.stt.low_latency_vad_hangover_ms,
            settings.stt.drain_timeout_s,
            settings.audio.ring_buffer_ms,
            settings.audio.internal_sample_rate_hz,
            settings.audio.internal_channels,
            custom_vocab_enabled,
            custom_terms,
        )

    _LOCAL_STT_PROVIDERS = frozenset({
        STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B,
        STTProviderName.LOCAL_GIGAAM_RNNT, STTProviderName.LOCAL_PARAKEET_TDT,
        STTProviderName.LOCAL_GIGAAM_RNNT_GGUF, STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
        STTProviderName.LOCAL_QWEN3_ASR_GGUF, STTProviderName.LOCAL_QWEN_17B_GGUF,
    })

    def _build_self_stt_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        local_qwen_identity = None
        if settings.provider.stt in self._LOCAL_STT_PROVIDERS:
            from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id
            from puripuly_heart.config.paths import default_models_dir
            model_id = resolve_model_id(settings.provider.stt.value, settings.provider.stt_quant)
            local_qwen_identity = str(default_local_stt_model_dir(model_id, data_dir=default_models_dir()))

        return (
            settings.provider.stt,
            local_qwen_identity,
            settings.provider.stt_compute,
            settings.provider.stt_quant,
        )

    def _build_peer_stt_runtime_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return self._build_peer_runtime_config(settings).runtime_signature

    def _build_peer_stt_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return build_peer_stt_provider_signature(settings)

    def _build_llm_provider_signature(self, settings: AppSettings) -> tuple[object, ...]:
        return (
            settings.provider.llm,
            (
                (
                    settings.provider.openai_compatible.base_url,
                    settings.provider.openai_compatible.model,
                )
                if settings.provider.llm == LLMProviderName.OPENAI_COMPATIBLE
                else None
            ),
            (
                (
                    settings.local_llm.backend,
                    settings.local_llm.base_url,
                    settings.local_llm.model,
                    _canonical_json_signature(settings.local_llm.extra_body),
                )
                if settings.provider.llm == LLMProviderName.LOCAL_LLM
                else None
            ),
            settings.backup_translation.enabled,
            settings.backup_translation.mode.value,
            settings.backup_translation.openai_compatible.base_url,
            settings.backup_translation.openai_compatible.model,
            settings.backup_translation.local_llm.base_url,
            settings.backup_translation.local_llm.model,
        )

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
        target.provider.stt = source.provider.stt
        target.provider.peer_stt = source.provider.peer_stt
        target.provider.llm = source.provider.llm
        target.provider.openai_compatible = copy.deepcopy(source.provider.openai_compatible)
        target.translation = copy.deepcopy(source.translation)
        target.local_llm = copy.deepcopy(source.local_llm)
        target.backup_translation = copy.deepcopy(source.backup_translation)
        target.system_prompt = source.system_prompt
        target.system_prompts = {}

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
