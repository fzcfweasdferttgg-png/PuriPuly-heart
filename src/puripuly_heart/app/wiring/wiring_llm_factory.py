"""LLM provider factory.

Only two translation backends remain in this build: the local OpenAI-
compatible endpoint and the local managed Gemma runtime. The cloud
providers (DeepSeek, Qwen, OpenAI, OpenRouter, ChatGPT plan, index-
translate) and the managed OpenRouter release path were removed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Awaitable

from puripuly_heart.config.alibaba_connection import resolve_alibaba_connection
from puripuly_heart.config.resolved import ResolvedLLMConfig, ResolvedLLMTarget
from puripuly_heart.config.runtime_resolution import (
    MANAGED_GEMMA_26B_MODEL,
    MANAGED_GEMMA_MODEL,
    PROVIDER_LOCAL_LLM,
    PROVIDER_MANAGED_GEMMA,
    DirectProviderRuntimeIntent,
    RuntimeResolutionInput,
    normalize_openrouter_runtime_intent,
    normalize_translation_runtime_intent,
    resolve_llm_config,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.chatgpt.session import ChatGptAccessTokenPort
from puripuly_heart.core.llm import FallbackRacingLLMProvider
from puripuly_heart.core.llm.fallback_racing import LLMProviderAttempt
from puripuly_heart.core.llm.provider import LLMProvider, SemaphoreLLMProvider
from puripuly_heart.core.local_translation.assets import (
    GEMMA_26B_MODEL_ID,
    GEMMA_MODEL_ID,
    resolve_gemma_spec,
)
from puripuly_heart.core.local_translation.devices import resolve_llama_vulkan_device
from puripuly_heart.core.local_translation.runtime import ManagedGemmaRuntimeOwner
from puripuly_heart.core.observability import ProviderObservationPort
from puripuly_heart.core.storage.secrets import SecretStore
from puripuly_heart.core.translation_policy import FIXED_TRANSLATION_POLICY
from puripuly_heart.domain.models import Translation
from puripuly_heart.providers.llm.local_openai import LocalOpenAICompatibleLLMProvider
from puripuly_heart.providers.llm.managed_gemma import ManagedGemmaLLMProvider

MANAGED_OPENROUTER_RELEASE_SERVICE_REQUIRED_ERROR = (
    "OpenRouter managed mode requires a managed release service; "
    "non-GUI paths are not wired for managed OpenRouter mode yet"
)

REMOVED_PROVIDER_ERROR = (
    "LLM provider `{provider}` was removed from this build; use the local "
    "OpenAI-compatible endpoint or the local Gemma runtime"
)


@dataclass(frozen=True, slots=True)
class LlmFactoryManagedIdentity:
    installation_id: str | None = None
    active_managed_credential_ref: str | None = None
    active_managed_expires_at: str | None = None
    local_managed_claim_sources: tuple[str, ...] = ()
    pending_managed_operation_id: str | None = None
    pending_managed_operation_source: str | None = None
    pending_delivery_ack_source: str | None = None


@dataclass(frozen=True, slots=True)
class LlmFactoryResolvedExtras:
    managed_identity: LlmFactoryManagedIdentity | None = None
    gpu_device_id: str = "auto"
    openrouter_credentials: object | None = field(default=None, repr=False)


def llm_factory_extras_from_vnext(settings: AppSettingsVNext) -> LlmFactoryResolvedExtras:
    identity = settings.state.managed_connection
    installation_id = identity.installation_id or None
    return LlmFactoryResolvedExtras(
        managed_identity=LlmFactoryManagedIdentity(
            installation_id=installation_id,
            active_managed_credential_ref=identity.active_managed_credential_ref,
            active_managed_expires_at=identity.active_managed_expires_at,
            local_managed_claim_sources=tuple(identity.local_managed_claim_sources),
            pending_managed_operation_id=identity.pending_managed_operation_id,
            pending_managed_operation_source=identity.pending_managed_operation_source,
            pending_delivery_ack_source=identity.pending_delivery_ack_source,
        ),
        gpu_device_id=settings.intent.translation.gpu_device_id,
    )


def prepare_llm_factory_extras(
    runtime_input: RuntimeResolutionInput,
    *,
    secrets: SecretStore,
    extras: LlmFactoryResolvedExtras | None,
) -> LlmFactoryResolvedExtras:
    _ = (runtime_input, secrets)
    return extras or LlmFactoryResolvedExtras()


def user_translation_intent_from_vnext(settings: AppSettingsVNext):
    from puripuly_heart.config.runtime_resolution import (
        UserBackupTranslationRuntimeIntent,
        UserLocalLLMRuntimeIntent,
        UserOpenAICompatibleRuntimeIntent,
        UserTranslationRuntimeIntent,
    )

    backup = settings.intent.backup_translation
    openai_compatible = settings.intent.openai_compatible
    local_llm = settings.intent.local_llm
    return UserTranslationRuntimeIntent(
        openai_compatible=UserOpenAICompatibleRuntimeIntent(
            provider_key=openai_compatible.provider_key,
            base_url=openai_compatible.base_url,
            model=openai_compatible.model,
        ),
        local_llm=UserLocalLLMRuntimeIntent(
            base_url=local_llm.base_url,
            model=local_llm.model,
            extra_body=local_llm.extra_body,
        ),
        backup=UserBackupTranslationRuntimeIntent(
            enabled=backup.enabled,
            model=backup.model,
            openai_compatible=UserOpenAICompatibleRuntimeIntent(
                provider_key=backup.openai_compatible.provider_key,
                base_url=backup.openai_compatible.base_url,
                model=backup.openai_compatible.model,
            ),
            local_llm=UserLocalLLMRuntimeIntent(
                base_url=backup.local_llm.base_url,
                model=backup.local_llm.model,
                extra_body=backup.local_llm.extra_body,
            ),
        ),
    )


def runtime_resolution_input_from_vnext(settings: AppSettingsVNext) -> RuntimeResolutionInput:
    translation = settings.intent.translation
    local_llm = settings.intent.local_llm
    openrouter_intent = normalize_openrouter_runtime_intent(
        model=translation.openrouter_model,
        selected_source=translation.openrouter_selected_source,
        selection_alias=translation.openrouter_selection_alias,
        routing_mode=translation.openrouter_routing_mode,
        provider_routing=translation.openrouter_provider_routing,
        managed_credential_kind=("qq" if translation.connection == "managed_china" else "standard"),
        broker_base_url=translation.openrouter_broker_base_url,
    )
    return RuntimeResolutionInput(
        translation=normalize_translation_runtime_intent(
            model=translation.model,
            connection=translation.connection,
            concurrency_limit=translation.concurrency_limit,
        ),
        openrouter=openrouter_intent,
        direct=DirectProviderRuntimeIntent(
            gemini_flash_model=translation.gemini.llm_model,
            deepseek_v4_flash_model=translation.deepseek.llm_model,
            qwen_38_flash_model=translation.qwen.llm_model,
            qwen_region=translation.qwen.region,
            qwen_connection=(
                resolve_alibaba_connection(
                    translation.qwen.region, getattr(translation.qwen, translation.qwen.region)
                )
                if translation.model == "qwen38_flash"
                else None
            ),
            local_llm_backend=local_llm.backend,
            local_llm_base_url=local_llm.base_url,
            local_llm_model=local_llm.model,
            local_llm_extra_body=local_llm.extra_body,
        ),
        user=user_translation_intent_from_vnext(settings),
    )


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
        scene_participant_count: int | None = None,
        max_output_tokens: int | None = None,
    ) -> Translation:
        delegate = await self._ensure_delegate()
        kwargs = {
            "utterance_id": utterance_id,
            "text": text,
            "system_prompt": system_prompt,
            "source_language": source_language,
            "target_language": target_language,
            "context": context,
            "scene_participant_count": scene_participant_count,
        }
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        return await delegate.translate(**kwargs)  # type: ignore[arg-type]

    async def close(self) -> None:
        if self._delegate is not None:
            await self._delegate.close()


def _plain_resolved_option_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_resolved_option_value(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_resolved_option_value(child) for child in value]
    return value


def _resolved_option_mapping(
    options: Mapping[str, object], key: str
) -> dict[str, object] | None:
    value = options.get(key)
    if isinstance(value, Mapping):
        return {str(k): _plain_resolved_option_value(v) for k, v in value.items()}
    return None


def _provider_from_resolved_target(
    target: ResolvedLLMTarget,
    *,
    secrets: SecretStore,
    managed_release_service: object | None = None,
    managed_delegate_ready: Callable[[], object] | None = None,
    runtime_logging: ProviderObservationPort | None = None,
    extras: LlmFactoryResolvedExtras | None = None,
    managed_gemma_runtime: ManagedGemmaRuntimeOwner | None = None,
    managed_gemma_release: Callable[[], Awaitable[None]] | None = None,
    qwen_low_latency_mode: bool = True,
    force_managed_wrapper: bool = False,
    include_selection_alias: bool = True,
    chatgpt_session: ChatGptAccessTokenPort | None = None,
) -> LLMProvider:
    _ = (
        managed_release_service,
        managed_delegate_ready,
        qwen_low_latency_mode,
        force_managed_wrapper,
        include_selection_alias,
        chatgpt_session,
    )
    if target.provider == PROVIDER_MANAGED_GEMMA:
        if managed_gemma_runtime is None:
            raise RuntimeError("managed Gemma runtime is unavailable")
        backend = target.provider_options.get("backend")
        if backend not in {"cpu", "gpu"}:
            raise ValueError("managed Gemma backend must be cpu or gpu")
        vulkan_device = "Vulkan0"
        if extras is not None:
            vulkan_device = resolve_llama_vulkan_device(extras.gpu_device_id)
        return ManagedGemmaLLMProvider(
            runtime=managed_gemma_runtime,
            backend=backend,
            spec=resolve_gemma_spec(
                {
                    MANAGED_GEMMA_MODEL: GEMMA_MODEL_ID,
                    MANAGED_GEMMA_26B_MODEL: GEMMA_26B_MODEL_ID,
                }[target.model]
            ),
            vulkan_device=vulkan_device,
            release_runtime=managed_gemma_release,
            runtime_logging=runtime_logging,
        )

    if target.provider == PROVIDER_LOCAL_LLM:
        api_key = (secrets.get("local_llm_api_key") or "").strip()
        return LocalOpenAICompatibleLLMProvider(
            base_url=target.base_url or "http://127.0.0.1:11434/v1",
            model=target.model,
            extra_body=_resolved_option_mapping(target.provider_options, "extra_body"),
            api_key=api_key,
        )

    raise ValueError(REMOVED_PROVIDER_ERROR.format(provider=target.provider))


def _base_llm_provider_from_resolved_config(
    config: ResolvedLLMConfig,
    *,
    secrets: SecretStore,
    managed_release_service: object | None = None,
    managed_delegate_ready: Callable[[], object] | None = None,
    runtime_logging: ProviderObservationPort | None = None,
    extras: LlmFactoryResolvedExtras | None = None,
    managed_gemma_runtime: ManagedGemmaRuntimeOwner | None = None,
    managed_gemma_release: Callable[[], Awaitable[None]] | None = None,
    qwen_low_latency_mode: bool = True,
    chatgpt_session: ChatGptAccessTokenPort | None = None,
) -> LLMProvider:
    return _provider_from_resolved_target(
        config.primary,
        secrets=secrets,
        managed_release_service=managed_release_service,
        managed_delegate_ready=managed_delegate_ready,
        runtime_logging=runtime_logging,
        extras=extras,
        managed_gemma_runtime=managed_gemma_runtime,
        managed_gemma_release=managed_gemma_release,
        qwen_low_latency_mode=qwen_low_latency_mode,
        chatgpt_session=chatgpt_session,
    )


def _fallback_attempt_log_summary(
    target: ResolvedLLMTarget,
    *,
    start_after_ms: int,
) -> str:
    fields = [f"provider={target.provider}"]
    if len(target.models) == 1:
        fields.append(f"model={target.models[0]}")
    else:
        fields.append(f"models=[{','.join(target.models)}]")
    if target.routing_mode:
        fields.append(f"mode={target.routing_mode}")
    if target.provider_routing:
        fields.append(f"route={target.provider_routing}")
    fields.append(f"delay={start_after_ms}ms")
    return ", ".join(fields)


def create_llm_provider_from_resolved_config(
    config: ResolvedLLMConfig,
    *,
    secrets: SecretStore,
    managed_release_service: object | None = None,
    managed_delegate_ready: Callable[[], object] | None = None,
    runtime_logging: ProviderObservationPort | None = None,
    extras: LlmFactoryResolvedExtras | None = None,
    managed_gemma_runtime: ManagedGemmaRuntimeOwner | None = None,
    managed_gemma_release: Callable[[], Awaitable[None]] | None = None,
    qwen_low_latency_mode: bool = True,
    chatgpt_session: ChatGptAccessTokenPort | None = None,
) -> LLMProvider:
    _ = (managed_release_service, managed_delegate_ready, chatgpt_session)
    if extras is not None and extras.openrouter_credentials is not None:
        extras = replace(extras, openrouter_credentials=None)
    base = _base_llm_provider_from_resolved_config(
        config,
        secrets=secrets,
        runtime_logging=runtime_logging,
        extras=extras,
        managed_gemma_runtime=managed_gemma_runtime,
        managed_gemma_release=managed_gemma_release,
        qwen_low_latency_mode=qwen_low_latency_mode,
    )
    if len(config.attempts) > 1:
        attempt_providers: list[LLMProviderAttempt] = [
            LLMProviderAttempt(
                provider=base,
                start_after_ms=config.attempts[0].start_after_ms,
                provider_name=config.primary.provider,
                model=config.primary.model,
            )
        ]
        for attempt_plan in config.attempts[1:]:
            attempt_providers.append(
                LLMProviderAttempt(
                    provider=_LazyFactoryLLMProvider(
                        factory=lambda attempt_plan=attempt_plan: _provider_from_resolved_target(
                            attempt_plan.target,
                            secrets=secrets,
                            runtime_logging=runtime_logging,
                            extras=extras,
                            managed_gemma_runtime=managed_gemma_runtime,
                            managed_gemma_release=managed_gemma_release,
                            qwen_low_latency_mode=qwen_low_latency_mode,
                            include_selection_alias=False,
                        )
                    ),
                    provider_name=attempt_plan.target.provider,
                    model=attempt_plan.target.model,
                    start_after_ms=attempt_plan.start_after_ms,
                    start_on_primary_error=attempt_plan.start_on_primary_error,
                    log_summary=_fallback_attempt_log_summary(
                        attempt_plan.target,
                        start_after_ms=attempt_plan.start_after_ms,
                    ),
                )
            )
        base = FallbackRacingLLMProvider(
            primary=base,
            fallback=attempt_providers[1].provider,
            attempts=tuple(attempt_providers),
            fallback_timeout_ms=config.attempts[1].start_after_ms,
            loser_grace_ms=config.loser_grace_ms,
            runtime_logging=runtime_logging,
            request_admission=None,
        )
    if config.primary.provider == PROVIDER_MANAGED_GEMMA:
        return base
    return SemaphoreLLMProvider(
        inner=base,
        semaphore=asyncio.Semaphore(config.concurrency_limit),
        provider_name=config.primary.provider,
        model=config.primary.model,
    )


def create_llm_provider(
    runtime_input: RuntimeResolutionInput,
    *,
    secrets: SecretStore,
    extras: LlmFactoryResolvedExtras | None = None,
    managed_release_service: object | None = None,
    managed_delegate_ready: Callable[[], object] | None = None,
    runtime_logging: ProviderObservationPort | None = None,
    managed_gemma_runtime: ManagedGemmaRuntimeOwner | None = None,
    managed_gemma_release: Callable[[], Awaitable[None]] | None = None,
    chatgpt_session: ChatGptAccessTokenPort | None = None,
) -> LLMProvider:
    return create_llm_provider_from_resolved_config(
        resolve_llm_config(runtime_input),
        secrets=secrets,
        extras=extras,
        managed_release_service=managed_release_service,
        managed_delegate_ready=managed_delegate_ready,
        runtime_logging=runtime_logging,
        managed_gemma_runtime=managed_gemma_runtime,
        managed_gemma_release=managed_gemma_release,
        qwen_low_latency_mode=FIXED_TRANSLATION_POLICY.fast_translation_enabled,
        chatgpt_session=chatgpt_session,
    )
