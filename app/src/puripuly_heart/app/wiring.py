from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Callable

from puripuly_heart.config.settings import (
    STT_INTERNAL_SAMPLE_RATE_HZ,
    AppSettings,
    LLMProviderName,
    SecretsBackend,
    SecretsSettings,
    STTProviderName,
)
from puripuly_heart.core.llm.provider import SemaphoreLLMProvider
from puripuly_heart.domain.peer_types import ResolvedPeerSTTConfig
from puripuly_heart.ports.llm import LLMProvider
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.core.storage.secrets import (
    EncryptedFileSecretStore,
    KeyringSecretStore,
)
from puripuly_heart.ports.secrets import SecretStore
from puripuly_heart.ports.stt import STTBackend
from puripuly_heart.core.stt.custom_vocab import get_effective_custom_terms
from puripuly_heart.domain.models import Translation
from puripuly_heart.adapters.llm.local_openai import LocalOpenAICompatibleLLMProvider
from puripuly_heart.adapters.llm.openai_compatible import OpenAICompatibleLLMProvider

logger = logging.getLogger(__name__)

SECRETS_PASSPHRASE_ENV = "PURIPULY_HEART_SECRETS_PASSPHRASE"


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


def create_llm_provider(
    settings: AppSettings,
    *,
    secrets: SecretStore,
    runtime_logging: SessionRuntimeLoggingService | None = None,
) -> LLMProvider:
    if settings.provider.llm == LLMProviderName.LOCAL_LLM:
        api_key = (secrets.get("local_llm_api_key") or "").strip()
        logger.info(
            "[LLM] Creating LOCAL_LLM provider: base_url=%s model=%s has_key=%s",
            settings.local_llm.base_url,
            settings.local_llm.model,
            bool(api_key),
        )
        base: LLMProvider = LocalOpenAICompatibleLLMProvider(
            base_url=settings.local_llm.base_url,
            model=settings.local_llm.model,
            extra_body=settings.local_llm.extra_body,
            api_key=api_key,
            runtime_logging=runtime_logging,
        )
    elif settings.provider.llm == LLMProviderName.OPENAI_COMPATIBLE:
        api_key = (secrets.get("openai_compatible_api_key") or "").strip()
        oc = settings.provider.openai_compatible
        if not oc.base_url.strip() or not oc.model.strip():
            raise ValueError("OpenAI Compatible: base_url and model are required")
        logger.info(
            "[LLM] Creating OPENAI_COMPATIBLE provider: base_url=%s model=%s has_key=%s",
            oc.base_url,
            oc.model,
            bool(api_key),
        )
        base = OpenAICompatibleLLMProvider(
            api_key=api_key,
            base_url=oc.base_url,
            model=oc.model,
            runtime_logging=runtime_logging,
        )
    else:
        # Legacy provider from old settings — migrate to OPENAI_COMPATIBLE
        logger.warning(
            "[LLM] Legacy provider '%s' detected, migrating to OPENAI_COMPATIBLE. "
            "Please reconfigure your API key and model in Settings.",
            settings.provider.llm.value,
        )
        api_key = (secrets.get("openai_compatible_api_key") or "").strip()
        base = OpenAICompatibleLLMProvider(
            api_key=api_key,
            base_url=settings.provider.openai_compatible.base_url,
            model=settings.provider.openai_compatible.model,
            runtime_logging=runtime_logging,
        )

    return SemaphoreLLMProvider(
        inner=base,
        semaphore=asyncio.Semaphore(settings.llm.concurrency_limit),
    )


def create_fallback_llm_provider(
    settings: AppSettings,
    *,
    secrets: SecretStore,
    runtime_logging: SessionRuntimeLoggingService | None = None,
) -> LLMProvider | None:
    bt = settings.backup_translation
    if not bt.enabled:
        return None
    if bt.mode == LLMProviderName.LOCAL_LLM:
        local = bt.local_llm
        if not local.base_url.strip():
            return None
        api_key = (secrets.get("local_llm_api_key") or "").strip()
        logger.info(
            "[LLM] Creating backup LOCAL_LLM provider: base_url=%s model=%s",
            local.base_url,
            local.model,
        )
        return LocalOpenAICompatibleLLMProvider(
            base_url=local.base_url,
            model=local.model,
            extra_body=local.extra_body,
            api_key=api_key,
            runtime_logging=runtime_logging,
        )
    oc = bt.openai_compatible
    if not oc.base_url.strip() or not oc.model.strip():
        return None
    api_key = (secrets.get("backup_api_key") or secrets.get("openai_compatible_api_key") or "").strip()
    logger.info(
        "[LLM] Creating backup OPENAI_COMPATIBLE provider: base_url=%s model=%s",
        oc.base_url,
        oc.model,
    )
    return OpenAICompatibleLLMProvider(
        api_key=api_key,
        base_url=oc.base_url,
        model=oc.model,
        runtime_logging=runtime_logging,
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
