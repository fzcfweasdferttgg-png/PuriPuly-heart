from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from puripuly_heart.config.llm_profiles import (
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_OPENROUTER,
    openrouter_alias_for_fields,
    profile_for_alias,
    resolve_openrouter_fallback_model,
)
from puripuly_heart.config.settings import (
    STT_INTERNAL_SAMPLE_RATE_HZ,
    AppSettings,
    CerebrasLLMModel,
    DeepSeekLLMModel,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterFallbackSelectionAlias,
    OpenRouterLLMModel,
    OpenRouterProviderRouting,
    OpenRouterSelectionAlias,
    SecretsBackend,
    SecretsSettings,
    STTProviderName,
    TranslationFallbackSelectionAlias,
)
from puripuly_heart.core.llm import FallbackRacingLLMProvider
from puripuly_heart.core.llm.provider import LLMProvider, SemaphoreLLMProvider
from puripuly_heart.core.openrouter_credentials import (
    require_openrouter_execution_api_key,
)
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.core.storage.secrets import (
    EncryptedFileSecretStore,
    KeyringSecretStore,
    SecretStore,
)
from puripuly_heart.core.stt.backend import STTBackend
from puripuly_heart.core.stt.custom_vocab import get_effective_custom_terms
from puripuly_heart.domain.models import Translation
from puripuly_heart.providers.llm.cerebras import CerebrasLLMProvider
from puripuly_heart.providers.llm.deepseek import DeepSeekLLMProvider
from puripuly_heart.providers.llm.gemini import GeminiLLMProvider
from puripuly_heart.providers.llm.local_openai import LocalOpenAICompatibleLLMProvider
from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider
from puripuly_heart.providers.llm.qwen import QwenLLMProvider
from puripuly_heart.providers.llm.qwen_async import AsyncQwenLLMProvider

SECRETS_PASSPHRASE_ENV = "PURIPULY_HEART_SECRETS_PASSPHRASE"


@dataclass(slots=True)
class _LazyFactoryLLMProvider(LLMProvider):
    factory: Callable[[], LLMProvider]
    _delegate: LLMProvider | None = field(init=False, default=None, repr=False)
    _delegate_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)

    async def _ensure_delegate(self) -> LLMProvider:
        if self._delegate is not None:
            return self._delegate

        async with self._delegate_lock:
            if self._delegate is None:
                self._delegate = self.factory()
            return self._delegate

    async def translate(
        self,
        *,
        utterance_id,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        delegate = await self._ensure_delegate()
        return await delegate.translate(
            utterance_id=utterance_id,
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
        )

    async def close(self) -> None:
        if self._delegate is not None:
            await self._delegate.close()


def _resolve_primary_openrouter_alias(settings: AppSettings) -> str:
    if settings.openrouter.selection_alias is not None:
        return settings.openrouter.selection_alias.value
    if settings.openrouter.selected_source == OpenRouterCredentialSource.NONE:
        raise ValueError("OpenRouter selected source must not be `none` for execution")
    return openrouter_alias_for_fields(
        model=settings.openrouter.llm_model.value,
        source=settings.openrouter.selected_source.value,
    )


def _settings_for_openrouter_alias(settings: AppSettings, *, alias: str) -> AppSettings:
    profile = profile_for_alias(alias)
    if profile.openrouter_model is None:
        raise ValueError(f"LLM selection alias `{alias}` is not an OpenRouter profile")
    canonical_alias = OpenRouterSelectionAlias(
        openrouter_alias_for_fields(
            model=profile.openrouter_model,
            source=profile.openrouter_source,
        )
    )
    return replace(
        settings,
        openrouter=replace(
            settings.openrouter,
            llm_model=OpenRouterLLMModel(profile.openrouter_model),
            selected_source=OpenRouterCredentialSource(profile.openrouter_source),
            selection_alias=canonical_alias,
        ),
    )


def _settings_for_openrouter_fallback_model(
    settings: AppSettings,
    *,
    fallback_model: str,
    provider_routing: OpenRouterProviderRouting | None = None,
) -> AppSettings:
    resolved_settings = replace(settings)
    resolved_settings.openrouter = replace(settings.openrouter)
    resolved_settings.openrouter.llm_model = OpenRouterLLMModel(fallback_model)
    resolved_settings.openrouter.selection_alias = None
    if provider_routing is not None:
        resolved_settings.openrouter.provider_routing = provider_routing
    return resolved_settings


def _provider_routing_for_openrouter_fallback(
    fallback_selection_alias: OpenRouterFallbackSelectionAlias,
) -> OpenRouterProviderRouting:
    if fallback_selection_alias == OpenRouterFallbackSelectionAlias.DEEPSEEK_V4_FLASH_CHINA:
        return OpenRouterProviderRouting.DEEPSEEK_ONLY
    return OpenRouterProviderRouting.DEFAULT


def _create_llm_provider_from_alias_profile(
    settings: AppSettings,
    *,
    alias: str,
    secrets: SecretStore,
    runtime_logging: SessionRuntimeLoggingService | None,
) -> LLMProvider:
    profile = profile_for_alias(alias)
    if profile.provider == LLM_PROVIDER_GEMINI:
        api_key = require_secret(secrets, key="google_api_key", env_var="GOOGLE_API_KEY")
        return GeminiLLMProvider(
            api_key=api_key,
            model=profile.gemini_model or settings.gemini.llm_model.value,
            runtime_logging=runtime_logging,
        )
    if profile.provider != LLM_PROVIDER_OPENROUTER:
        raise ValueError(f"Unsupported LLM selection alias: {alias}")

    alias_settings = _settings_for_openrouter_alias(settings, alias=alias)

    api_key = require_openrouter_execution_api_key(alias_settings, secrets=secrets)
    return OpenRouterLLMProvider(
        api_key=api_key,
        model=alias_settings.openrouter.llm_model.value,
        routing_mode=settings.openrouter.routing_mode,
        provider_routing=alias_settings.openrouter.provider_routing,
        runtime_logging=runtime_logging,
    )


def _create_openrouter_fallback_provider(
    *,
    settings: AppSettings,
    secrets: SecretStore,
    runtime_logging: SessionRuntimeLoggingService | None,
) -> LLMProvider:
    fallback_model = resolve_openrouter_fallback_model(
        settings.openrouter.fallback_selection_alias.value
    )
    if fallback_model is None:
        raise ValueError("OpenRouter fallback selection must resolve to a model")

    resolved_settings = _settings_for_openrouter_fallback_model(
        settings,
        fallback_model=fallback_model,
        provider_routing=_provider_routing_for_openrouter_fallback(
            settings.openrouter.fallback_selection_alias
        ),
    )

    api_key = require_openrouter_execution_api_key(resolved_settings, secrets=secrets)
    return OpenRouterLLMProvider(
        api_key=api_key,
        model=resolved_settings.openrouter.llm_model.value,
        routing_mode=resolved_settings.openrouter.routing_mode,
        provider_routing=resolved_settings.openrouter.provider_routing,
        runtime_logging=runtime_logging,
    )


def _translation_fallback_openrouter_source(settings: AppSettings) -> OpenRouterCredentialSource:
    return OpenRouterCredentialSource.BYOK


def _openrouter_settings_for_translation_fallback(
    settings: AppSettings,
    *,
    model: OpenRouterLLMModel,
) -> AppSettings:
    resolved_settings = _settings_for_openrouter_fallback_model(
        settings,
        fallback_model=model.value,
        provider_routing=OpenRouterProviderRouting.DEFAULT,
    )
    resolved_settings.openrouter.selected_source = _translation_fallback_openrouter_source(settings)
    resolved_settings.openrouter.selection_alias = None
    return resolved_settings


def _create_openrouter_translation_fallback_provider(
    *,
    settings: AppSettings,
    secrets: SecretStore,
    model: OpenRouterLLMModel,
    runtime_logging: SessionRuntimeLoggingService | None,
) -> LLMProvider:
    resolved_settings = _openrouter_settings_for_translation_fallback(settings, model=model)

    api_key = require_openrouter_execution_api_key(resolved_settings, secrets=secrets)
    return OpenRouterLLMProvider(
        api_key=api_key,
        model=resolved_settings.openrouter.llm_model.value,
        routing_mode=resolved_settings.openrouter.routing_mode,
        provider_routing=resolved_settings.openrouter.provider_routing,
        runtime_logging=runtime_logging,
    )


def _create_translation_fallback_provider(
    *,
    settings: AppSettings,
    secrets: SecretStore,
    alias: TranslationFallbackSelectionAlias,
    runtime_logging: SessionRuntimeLoggingService | None,
) -> LLMProvider:
    if alias == TranslationFallbackSelectionAlias.DEEPSEEK_V4_FLASH_OFFICIAL:
        return DeepSeekLLMProvider(
            api_key=require_secret(
                secrets,
                key="deepseek_api_key",
                env_var="DEEPSEEK_API_KEY",
            ),
            model=DeepSeekLLMModel.DEEPSEEK_V4_FLASH.value,
            runtime_logging=runtime_logging,
        )
    if alias == TranslationFallbackSelectionAlias.CEREBRAS_GEMMA4_31B:
        return CerebrasLLMProvider(
            api_key=require_secret(
                secrets,
                key="cerebras_api_key",
                env_var="CEREBRAS_API_KEY",
            ),
            model=CerebrasLLMModel.GEMMA_4_31B.value,
            runtime_logging=runtime_logging,
        )
    if alias == TranslationFallbackSelectionAlias.OPENROUTER_DEEPSEEK_V4_FLASH:
        return _create_openrouter_translation_fallback_provider(
            settings=settings,
            secrets=secrets,
            model=OpenRouterLLMModel.DEEPSEEK_V4_FLASH,
            runtime_logging=runtime_logging,
        )
    if alias == TranslationFallbackSelectionAlias.OPENROUTER_GEMMA4_26B_A4B:
        return _create_openrouter_translation_fallback_provider(
            settings=settings,
            secrets=secrets,
            model=OpenRouterLLMModel.GEMMA_4_26B_A4B_IT,
            runtime_logging=runtime_logging,
        )
    raise ValueError(f"Unsupported translation fallback selection: {alias}")


def _translation_fallback_matches_primary(
    settings: AppSettings,
    alias: TranslationFallbackSelectionAlias,
) -> bool:
    if alias == TranslationFallbackSelectionAlias.DEEPSEEK_V4_FLASH_OFFICIAL:
        return (
            settings.provider.llm == LLMProviderName.DEEPSEEK
            and settings.deepseek.llm_model == DeepSeekLLMModel.DEEPSEEK_V4_FLASH
        )
    if alias == TranslationFallbackSelectionAlias.CEREBRAS_GEMMA4_31B:
        return (
            settings.provider.llm == LLMProviderName.CEREBRAS
            and settings.cerebras.llm_model == CerebrasLLMModel.GEMMA_4_31B
        )
    if alias == TranslationFallbackSelectionAlias.OPENROUTER_DEEPSEEK_V4_FLASH:
        return (
            settings.provider.llm == LLMProviderName.OPENROUTER
            and settings.openrouter.llm_model == OpenRouterLLMModel.DEEPSEEK_V4_FLASH
        )
    if alias == TranslationFallbackSelectionAlias.OPENROUTER_GEMMA4_26B_A4B:
        return (
            settings.provider.llm == LLMProviderName.OPENROUTER
            and settings.openrouter.llm_model == OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
        )
    return False


def _emit_translation_fallback_noop(
    *,
    runtime_logging: SessionRuntimeLoggingService | None,
    alias: TranslationFallbackSelectionAlias,
) -> None:
    if runtime_logging is None:
        return
    emit = getattr(runtime_logging, "emit_basic", None)
    if callable(emit):
        with contextlib.suppress(Exception):
            emit(f"[Fallback] Skipped matching fallback target: {alias.value}")


@dataclass(frozen=True, slots=True)
class ResolvedPeerSTTConfig:
    provider: STTProviderName
    source_language: str
    sample_rate_hz: int
    keyterms: tuple[str, ...]


def _portable_passphrase() -> str:
    """Generate or load a passphrase for the portable encrypted-file secret store."""
    from puripuly_heart.config.paths import portable_data_dir

    key_file = portable_data_dir() / ".secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()

    import secrets as _secrets

    passphrase = _secrets.token_urlsafe(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(passphrase, encoding="utf-8")
    return passphrase


def create_secret_store(
    settings: SecretsSettings,
    *,
    config_path: Path,
    passphrase: str | None = None,
) -> SecretStore:
    from puripuly_heart.config.paths import is_portable, portable_data_dir

    passphrase = passphrase or os.getenv(SECRETS_PASSPHRASE_ENV)

    if is_portable() and settings.backend == SecretsBackend.KEYRING:
        passphrase = passphrase or _portable_passphrase()
        path = portable_data_dir() / "secrets.json"
        return EncryptedFileSecretStore(path=path, passphrase=passphrase)

    if settings.backend == SecretsBackend.KEYRING:
        return KeyringSecretStore()

    if settings.backend == SecretsBackend.ENCRYPTED_FILE:
        if not passphrase:
            raise ValueError(
                "encrypted_file secrets backend requires a passphrase; "
                f"set {SECRETS_PASSPHRASE_ENV} or pass passphrase explicitly"
            )
        path = Path(settings.encrypted_file_path)
        if not path.is_absolute():
            path = config_path.parent / path
        return EncryptedFileSecretStore(path=path, passphrase=passphrase)

    raise ValueError(f"Unsupported secrets backend: {settings.backend}")


def _get_secret(
    secrets: SecretStore,
    *,
    key: str,
    env_var: str,
) -> str | None:
    value = secrets.get(key)
    if value:
        return value
    env = os.getenv(env_var)
    if env:
        return env
    return None


def _get_secret_any(
    secrets: SecretStore,
    *,
    key: str,
    env_vars: tuple[str, ...],
    legacy_keys: tuple[str, ...] = (),
) -> str | None:
    value = secrets.get(key)
    if value:
        return value
    for legacy_key in legacy_keys:
        legacy_value = secrets.get(legacy_key)
        if legacy_value:
            # Backfill to the new key so subsequent runs do not rely on fallback.
            with contextlib.suppress(Exception):
                secrets.set(key, legacy_value)
            return legacy_value
    for env_var in env_vars:
        env = os.getenv(env_var)
        if env:
            return env
    return None


def require_secret_any(
    secrets: SecretStore,
    *,
    key: str,
    env_vars: tuple[str, ...],
    legacy_keys: tuple[str, ...] = (),
) -> str:
    value = _get_secret_any(secrets, key=key, env_vars=env_vars, legacy_keys=legacy_keys)
    if value:
        return value
    env_list = ", ".join(env_vars)
    raise ValueError(f"Missing secret `{key}` (or env vars {env_list})")


def require_secret(
    secrets: SecretStore,
    *,
    key: str,
    env_var: str,
) -> str:
    value = _get_secret(secrets, key=key, env_var=env_var)
    if value:
        return value
    raise ValueError(f"Missing secret `{key}` (or env var {env_var})")


def create_llm_provider(
    settings: AppSettings,
    *,
    secrets: SecretStore,
    runtime_logging: SessionRuntimeLoggingService | None = None,
) -> LLMProvider:
    if settings.provider.llm == LLMProviderName.GEMINI:
        api_key = require_secret(secrets, key="google_api_key", env_var="GOOGLE_API_KEY")
        base: LLMProvider = GeminiLLMProvider(
            api_key=api_key,
            model=settings.gemini.llm_model.value,
            runtime_logging=runtime_logging,
        )
    elif settings.provider.llm == LLMProviderName.OPENROUTER:
        primary_alias = _resolve_primary_openrouter_alias(settings)
        base = _create_llm_provider_from_alias_profile(
            settings,
            alias=primary_alias,
            secrets=secrets,
            runtime_logging=runtime_logging,
        )
    elif settings.provider.llm == LLMProviderName.QWEN:
        from puripuly_heart.config.settings import QwenRegion

        if settings.qwen.region == QwenRegion.BEIJING:
            api_key = require_secret_any(
                secrets,
                key="alibaba_api_key_beijing",
                env_vars=("ALIBABA_API_KEY_BEIJING", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
                legacy_keys=("alibaba_api_key",),
            )
        else:
            api_key = require_secret_any(
                secrets,
                key="alibaba_api_key_singapore",
                env_vars=("ALIBABA_API_KEY_SINGAPORE", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
                legacy_keys=("alibaba_api_key",),
            )
        if settings.stt.low_latency_mode:
            # Low-latency mode: use httpx async client for immediate cancellation
            base_url = settings.qwen.get_llm_base_url()
            # Convert SDK URL to OpenAI-compatible URL
            async_base_url = base_url.replace("/api/v1", "/compatible-mode/v1")
            base = AsyncQwenLLMProvider(
                api_key=api_key,
                base_url=async_base_url,
                model=settings.qwen.llm_model.value,
                runtime_logging=runtime_logging,
            )
        else:
            # Standard mode: use DashScope SDK
            base = QwenLLMProvider(
                api_key=api_key,
                base_url=settings.qwen.get_llm_base_url(),
                model=settings.qwen.llm_model.value,
                runtime_logging=runtime_logging,
            )
    elif settings.provider.llm == LLMProviderName.DEEPSEEK:
        api_key = require_secret(
            secrets,
            key="deepseek_api_key",
            env_var="DEEPSEEK_API_KEY",
        )
        base = DeepSeekLLMProvider(
            api_key=api_key,
            model=settings.deepseek.llm_model.value,
            runtime_logging=runtime_logging,
        )
    elif settings.provider.llm == LLMProviderName.CEREBRAS:
        api_key = require_secret(
            secrets,
            key="cerebras_api_key",
            env_var="CEREBRAS_API_KEY",
        )
        base = CerebrasLLMProvider(
            api_key=api_key,
            model=settings.cerebras.llm_model.value,
            runtime_logging=runtime_logging,
        )
    elif settings.provider.llm == LLMProviderName.LOCAL_LLM:
        api_key = (secrets.get("local_llm_api_key") or "").strip()
        base = LocalOpenAICompatibleLLMProvider(
            base_url=settings.local_llm.base_url,
            model=settings.local_llm.model,
            extra_body=settings.local_llm.extra_body,
            api_key=api_key,
            runtime_logging=runtime_logging,
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {settings.provider.llm}")

    translation_fallback_alias = settings.translation.fallback_selection_alias
    if translation_fallback_alias != TranslationFallbackSelectionAlias.NONE:
        if _translation_fallback_matches_primary(settings, translation_fallback_alias):
            _emit_translation_fallback_noop(
                runtime_logging=runtime_logging,
                alias=translation_fallback_alias,
            )
        else:
            base = FallbackRacingLLMProvider(
                primary=base,
                fallback=_LazyFactoryLLMProvider(
                    factory=lambda: _create_translation_fallback_provider(
                        settings=settings,
                        secrets=secrets,
                        alias=translation_fallback_alias,
                        runtime_logging=runtime_logging,
                    )
                ),
                runtime_logging=runtime_logging,
            )
    elif (
        settings.provider.llm == LLMProviderName.OPENROUTER
        and settings.openrouter.fallback_selection_alias != OpenRouterFallbackSelectionAlias.NONE
        and settings.openrouter.provider_routing != OpenRouterProviderRouting.DEEPSEEK_ONLY
    ):
        base = FallbackRacingLLMProvider(
            primary=base,
            fallback=_LazyFactoryLLMProvider(
                factory=lambda: _create_openrouter_fallback_provider(
                    settings=settings,
                    secrets=secrets,
                    runtime_logging=runtime_logging,
                )
            ),
            runtime_logging=runtime_logging,
        )

    return SemaphoreLLMProvider(
        inner=base,
        semaphore=asyncio.Semaphore(settings.llm.concurrency_limit),
    )


def _resolve_compute(compute: str) -> tuple[str, int]:
    """Map settings compute ("gpu"/"cpu") to (provider_type, device)."""
    import os
    if compute == "cpu":
        return "cpu", 0
    device_str = os.environ.get("SHERPA_GPU_DEVICE", "")
    device = int(device_str) if device_str.isdigit() and int(device_str) > 0 else 0
    return "directml", device


def _resolve_compute_transcribecpp(compute: str) -> tuple[str, int]:
    """Map settings compute ("gpu"/"cpu") to transcribe.cpp backend."""
    import os
    if compute == "cpu":
        return "cpu", 0
    env_device = os.environ.get("TRANSCRIBE_VULKAN_DEVICE", "")
    device = int(env_device) if env_device.isdigit() and int(env_device) > 0 else 0
    return "vulkan", device


def create_stt_backend(
    settings: AppSettings,
    *,
    secrets: SecretStore,
    diagnostics_enabled: Callable[[], bool] | None = None,
) -> STTBackend:
    effective_terms = get_effective_custom_terms(settings, settings.languages.source_language)

    if settings.provider.stt in (STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B):
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.language import get_local_qwen_language_hint
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_name = {
            STTProviderName.LOCAL_QWEN: "local_qwen",
            STTProviderName.LOCAL_QWEN_17B: "local_qwen_17b",
        }[settings.provider.stt]
        _provider_type, _device = _resolve_compute(settings.provider.stt_compute)
        return SubprocessSTTBackend(
            provider=_provider_name,
            model_dir=default_local_stt_model_dir(resolve_model_id(settings.provider.stt.value, settings.provider.stt_quant)),
            language_hint=get_local_qwen_language_hint(settings.languages.source_language),
            provider_type=_provider_type,
            device=_device,
        )

    if settings.provider.stt == STTProviderName.LOCAL_GIGAAM_RNNT:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute(settings.provider.stt_compute)
        return SubprocessSTTBackend(
            provider="local_gigaam_rnnt",
            model_dir=default_local_stt_model_dir(resolve_model_id(settings.provider.stt.value, settings.provider.stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    if settings.provider.stt == STTProviderName.LOCAL_PARAKEET_TDT:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute(settings.provider.stt_compute)
        return SubprocessSTTBackend(
            provider="local_parakeet_tdt",
            model_dir=default_local_stt_model_dir(resolve_model_id(settings.provider.stt.value, settings.provider.stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    if settings.provider.stt == STTProviderName.LOCAL_GIGAAM_RNNT_GGUF:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute_transcribecpp(settings.provider.stt_compute)
        return SubprocessSTTBackend(
            provider="local_transcribecpp",
            model_dir=default_local_stt_model_dir(resolve_model_id(settings.provider.stt.value, settings.provider.stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    if settings.provider.stt == STTProviderName.LOCAL_PARAKEET_TDT_GGUF:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute_transcribecpp(settings.provider.stt_compute)
        return SubprocessSTTBackend(
            provider="local_transcribecpp",
            model_dir=default_local_stt_model_dir(resolve_model_id(settings.provider.stt.value, settings.provider.stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    if settings.provider.stt == STTProviderName.LOCAL_QWEN3_ASR_GGUF:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute_transcribecpp(settings.provider.stt_compute)
        return SubprocessSTTBackend(
            provider="local_transcribecpp",
            model_dir=default_local_stt_model_dir(resolve_model_id(settings.provider.stt.value, settings.provider.stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    if settings.provider.stt == STTProviderName.LOCAL_QWEN_17B_GGUF:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute_transcribecpp(settings.provider.stt_compute)
        return SubprocessSTTBackend(
            provider="local_transcribecpp",
            model_dir=default_local_stt_model_dir(resolve_model_id(settings.provider.stt.value, settings.provider.stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    raise ValueError(f"Unsupported STT provider: {settings.provider.stt}")


def resolve_peer_stt_config(settings: AppSettings) -> ResolvedPeerSTTConfig:
    peer_source_language = settings.languages.effective_peer_source
    provider = settings.provider.peer_stt

    return ResolvedPeerSTTConfig(
        provider=provider,
        source_language=peer_source_language,
        sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
        keyterms=(),
    )


def build_peer_stt_provider_signature(settings: AppSettings) -> tuple[object, ...]:
    resolved = resolve_peer_stt_config(settings)
    return (
        resolved.provider,
        resolved.source_language,
        resolved.sample_rate_hz,
        resolved.keyterms,
        settings.provider.peer_stt_compute,
        settings.provider.peer_stt_quant,
    )


def create_peer_stt_backend(
    settings: AppSettings,
    *,
    secrets: SecretStore,
    diagnostics_enabled: Callable[[], bool] | None = None,
) -> STTBackend:
    resolved = resolve_peer_stt_config(settings)

    if resolved.provider in (STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B):
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.language import get_local_qwen_language_hint
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_name = {
            STTProviderName.LOCAL_QWEN: "local_qwen",
            STTProviderName.LOCAL_QWEN_17B: "local_qwen_17b",
        }[resolved.provider]
        _provider_type, _device = _resolve_compute(settings.provider.peer_stt_compute)
        return SubprocessSTTBackend(
            provider=_provider_name,
            model_dir=default_local_stt_model_dir(resolve_model_id(resolved.provider.value, settings.provider.peer_stt_quant)),
            language_hint=get_local_qwen_language_hint(resolved.source_language),
            provider_type=_provider_type,
            device=_device,
        )

    if resolved.provider == STTProviderName.LOCAL_GIGAAM_RNNT:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute(settings.provider.peer_stt_compute)
        return SubprocessSTTBackend(
            provider="local_gigaam_rnnt",
            model_dir=default_local_stt_model_dir(resolve_model_id(resolved.provider.value, settings.provider.peer_stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    if resolved.provider == STTProviderName.LOCAL_PARAKEET_TDT:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute(settings.provider.peer_stt_compute)
        return SubprocessSTTBackend(
            provider="local_parakeet_tdt",
            model_dir=default_local_stt_model_dir(resolve_model_id(resolved.provider.value, settings.provider.peer_stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    if resolved.provider == STTProviderName.LOCAL_GIGAAM_RNNT_GGUF:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute_transcribecpp(settings.provider.peer_stt_compute)
        return SubprocessSTTBackend(
            provider="local_transcribecpp",
            model_dir=default_local_stt_model_dir(resolve_model_id(resolved.provider.value, settings.provider.peer_stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    if resolved.provider == STTProviderName.LOCAL_PARAKEET_TDT_GGUF:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute_transcribecpp(settings.provider.peer_stt_compute)
        return SubprocessSTTBackend(
            provider="local_transcribecpp",
            model_dir=default_local_stt_model_dir(resolve_model_id(resolved.provider.value, settings.provider.peer_stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    if resolved.provider == STTProviderName.LOCAL_QWEN3_ASR_GGUF:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute_transcribecpp(settings.provider.peer_stt_compute)
        return SubprocessSTTBackend(
            provider="local_transcribecpp",
            model_dir=default_local_stt_model_dir(resolve_model_id(resolved.provider.value, settings.provider.peer_stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    if resolved.provider == STTProviderName.LOCAL_QWEN_17B_GGUF:
        from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

        _provider_type, _device = _resolve_compute_transcribecpp(settings.provider.peer_stt_compute)
        return SubprocessSTTBackend(
            provider="local_transcribecpp",
            model_dir=default_local_stt_model_dir(resolve_model_id(resolved.provider.value, settings.provider.peer_stt_quant)),
            provider_type=_provider_type,
            device=_device,
        )

    raise ValueError(f"Unsupported peer STT provider: {resolved.provider}")
