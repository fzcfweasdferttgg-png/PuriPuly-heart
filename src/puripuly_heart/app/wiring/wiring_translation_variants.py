"""User-facing translation variants and the backup translator.

The two variant forms follow the old application: a network OpenAI-compatible
endpoint (providers.json preset or own address) and a local OpenAI-compatible
server, each with its key from the plaintext secret store and a model picked
via API polling or typed by hand. The backup translator engages when the
primary fails or stalls (SequentialFallbackLLMProvider).
"""

from __future__ import annotations

import logging

from puripuly_heart.config.runtime_resolution import (
    UserBackupTranslationRuntimeIntent,
    UserLocalLLMRuntimeIntent,
    UserOpenAICompatibleRuntimeIntent,
)
from puripuly_heart.core.llm.fallback_chain import (
    DEFAULT_PRIMARY_WAIT_S,
    SequentialFallbackLLMProvider,
)
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.storage.secrets import SecretStore
from puripuly_heart.providers.llm.local_openai import LocalOpenAICompatibleLLMProvider

logger = logging.getLogger(__name__)

USER_VARIANT_LOCAL_LLM = "local_llm"
USER_VARIANT_OPENAI_COMPATIBLE = "openai_compatible"

SECRET_LOCAL_LLM_API_KEY = "local_llm_api_key"
SECRET_OPENAI_COMPATIBLE_API_KEY = "openai_compatible_api_key"
SECRET_BACKUP_LOCAL_LLM_API_KEY = "fallback_local_llm_api_key"
SECRET_BACKUP_OPENAI_COMPATIBLE_API_KEY = "backup_api_key"


def is_user_variant(model: str) -> bool:
    return model in (USER_VARIANT_LOCAL_LLM, USER_VARIANT_OPENAI_COMPATIBLE)


def _api_key(secrets: SecretStore, slot: str, fallback_slot: str | None = None) -> str:
    value = (secrets.get(slot) or "").strip()
    if not value and fallback_slot is not None:
        value = (secrets.get(fallback_slot) or "").strip()
    return value


def _preset_base_url(provider_key: str) -> str:
    key = provider_key.strip()
    if not key:
        return ""
    from puripuly_heart.config.providers_registry import load_provider_presets

    preset = load_provider_presets().get(key)
    return preset.base_url if preset is not None else ""


def build_variant_provider(
    variant: str,
    *,
    local_llm: UserLocalLLMRuntimeIntent,
    openai_compatible: UserOpenAICompatibleRuntimeIntent,
    secrets: SecretStore,
    local_key_slot: str = SECRET_LOCAL_LLM_API_KEY,
    openai_key_slot: str = SECRET_OPENAI_COMPATIBLE_API_KEY,
    openai_key_fallback_slot: str | None = None,
) -> LLMProvider | None:
    if variant == USER_VARIANT_LOCAL_LLM:
        if not local_llm.base_url.strip() or not local_llm.model.strip():
            return None
        logger.info(
            "[LLM] Building local translation provider: base_url=%s model=%s",
            local_llm.base_url,
            local_llm.model,
        )
        return LocalOpenAICompatibleLLMProvider(
            base_url=local_llm.base_url,
            model=local_llm.model,
            api_key=_api_key(secrets, local_key_slot),
            extra_body=local_llm.extra_body,
        )
    if variant == USER_VARIANT_OPENAI_COMPATIBLE:
        base_url = openai_compatible.base_url.strip() or _preset_base_url(
            openai_compatible.provider_key
        )
        if not base_url or not openai_compatible.model.strip():
            return None
        logger.info(
            "[LLM] Building network translation provider: base_url=%s model=%s",
            base_url,
            openai_compatible.model,
        )
        return LocalOpenAICompatibleLLMProvider(
            base_url=base_url,
            model=openai_compatible.model,
            api_key=_api_key(secrets, openai_key_slot, openai_key_fallback_slot),
            extra_body={},
        )
    return None


def build_backup_provider(
    backup: UserBackupTranslationRuntimeIntent,
    *,
    secrets: SecretStore,
) -> LLMProvider | None:
    if not backup.enabled or not backup.model:
        return None
    provider = build_variant_provider(
        backup.model,
        local_llm=UserLocalLLMRuntimeIntent(
            base_url=backup.local_llm.base_url,
            model=backup.local_llm.model,
            extra_body=backup.local_llm.extra_body,
        ),
        openai_compatible=UserOpenAICompatibleRuntimeIntent(
            provider_key=backup.openai_compatible.provider_key,
            base_url=backup.openai_compatible.base_url,
            model=backup.openai_compatible.model,
        ),
        secrets=secrets,
        local_key_slot=SECRET_BACKUP_LOCAL_LLM_API_KEY,
        openai_key_slot=SECRET_BACKUP_OPENAI_COMPATIBLE_API_KEY,
        openai_key_fallback_slot=SECRET_OPENAI_COMPATIBLE_API_KEY,
    )
    if provider is None:
        logger.warning("[LLM] Backup translation enabled but not configured")
    return provider


def wrap_backup_chain(
    primary: LLMProvider,
    backup: LLMProvider | None,
    *,
    primary_wait_s: float = DEFAULT_PRIMARY_WAIT_S,
    on_fallback_used: object = None,
) -> LLMProvider:
    if backup is None:
        return primary
    logger.info("[LLM] Backup translation armed (primary -> backup chain)")
    return SequentialFallbackLLMProvider(
        primary=primary,
        fallback=backup,
        primary_wait_s=primary_wait_s,
        on_fallback_used=on_fallback_used,
    )
