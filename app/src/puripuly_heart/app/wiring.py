from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import logging
import os
import sys
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
from puripuly_heart.core.clock import Clock
from puripuly_heart.domain.peer_types import ResolvedPeerSTTConfig
from puripuly_heart.ports.llm import LLMProvider
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.adapters.storage.secrets import (
    EncryptedFileSecretStore,
    KeyringSecretStore,
)
from puripuly_heart.adapters.osc.chatbox_paginator import ChatboxPaginator
from puripuly_heart.adapters.osc.udp_sender import VrchatOscUdpSender
from puripuly_heart.ports.logging import SessionLogger
from puripuly_heart.ports.osc import OscSink
from puripuly_heart.ports.secrets import SecretStore
from puripuly_heart.ports.stt import STTBackend
from puripuly_heart.core.stt.custom_vocab import get_effective_custom_terms
from puripuly_heart.domain.models import Translation
from puripuly_heart.adapters.llm.local_openai import LocalOpenAICompatibleLLMProvider
from puripuly_heart.adapters.llm.openai_compatible import OpenAICompatibleLLMProvider

logger = logging.getLogger(__name__)

SECRETS_PASSPHRASE_ENV = "PURIPULY_HEART_SECRETS_PASSPHRASE"

# --- Job Object: kills all child processes when parent exits ---
_JOB_HANDLE: int | None = None


def get_or_create_job_handle() -> int | None:
    """Return (or lazily create) a Windows Job Object with kill-on-close."""
    global _JOB_HANDLE
    if _JOB_HANDLE is not None:
        return _JOB_HANDLE
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.windll.kernel32

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            logger.warning("[JobObject] CreateJobObject failed")
            return None

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("_pad1", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("_pad2", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE

        JobObjectExtendedLimitInformation = 9
        result = kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not result:
            logger.warning("[JobObject] SetInformationJobObject failed")
            kernel32.CloseHandle(job)
            return None

        _JOB_HANDLE = job
        logger.info("[JobObject] Created with KILL_ON_JOB_CLOSE, handle=%d", job)
        return job
    except Exception as exc:
        logger.warning("[JobObject] Failed to create: %s", exc)
        return None


def assign_to_job(pid: int, job_handle: int | None) -> None:
    """Assign a process to a Job Object by PID."""
    if job_handle is None:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        proc_handle = kernel32.OpenProcess(0x1F0FFF, False, pid)  # PROCESS_ALL_ACCESS
        if not proc_handle:
            logger.warning("[JobObject] OpenProcess failed for pid=%d", pid)
            return
        result = kernel32.AssignProcessToJobObject(job_handle, proc_handle)
        kernel32.CloseHandle(proc_handle)
        if result:
            logger.info("[JobObject] Assigned pid=%d to job", pid)
        else:
            logger.warning("[JobObject] AssignProcessToJobObject failed for pid=%d", pid)
    except Exception as exc:
        logger.warning("[JobObject] Failed to assign pid=%d: %s", pid, exc)


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


def create_osc_sink(
    settings: AppSettings,
    *,
    clock: Clock,
    runtime_logging: SessionLogger | None = None,
) -> tuple[OscSink, VrchatOscUdpSender]:
    sender = VrchatOscUdpSender(
        host=settings.osc.host,
        port=settings.osc.port,
        chatbox_address=settings.osc.chatbox_address,
        chatbox_send=settings.osc.chatbox_send,
        chatbox_clear=settings.osc.chatbox_clear,
    )
    sink = ChatboxPaginator(
        sender=sender,
        clock=clock,
        max_chars=settings.osc.chatbox_max_chars,
        runtime_logging=runtime_logging,
    )
    return sink, sender


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
        api_key = (secrets.get("fallback_local_llm_api_key") or "").strip()
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


_QWEN_PROVIDER_NAMES: dict[STTProviderName, str] = {
    STTProviderName.LOCAL_QWEN: "local_qwen",
    STTProviderName.LOCAL_QWEN_17B: "local_qwen_17b",
}

_PROVIDER_STRING_NAMES: dict[STTProviderName, str] = {
    STTProviderName.LOCAL_GIGAAM_RNNT: "local_gigaam_rnnt",
    STTProviderName.LOCAL_PARAKEET_TDT: "local_parakeet_tdt",
}


def _create_subprocess_stt_backend(
    provider: STTProviderName,
    compute: str,
    quant: str,
    language: str,
    *,
    data_dir: Path,
) -> STTBackend:
    from puripuly_heart.core.inference.subprocess_backend import SubprocessSTTBackend
    from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir, resolve_model_id

    language_hint: str | None = None
    if provider in _QWEN_PROVIDER_NAMES:
        from puripuly_heart.domain.language import get_local_qwen_language_hint
        provider_str = _QWEN_PROVIDER_NAMES[provider]
        provider_type, device = _resolve_compute(compute)
        language_hint = get_local_qwen_language_hint(language)
    elif provider in _PROVIDER_STRING_NAMES:
        provider_str = _PROVIDER_STRING_NAMES[provider]
        provider_type, device = _resolve_compute(compute)
    else:
        provider_str = "local_transcribecpp"
        provider_type, device = _resolve_compute_transcribecpp(compute)

    kwargs: dict[str, object] = {
        "provider": provider_str,
        "model_dir": default_local_stt_model_dir(resolve_model_id(provider.value, quant), data_dir=data_dir),
        "provider_type": provider_type,
        "device": device,
        "job_handle": get_or_create_job_handle(),
    }
    if language_hint is not None:
        kwargs["language_hint"] = language_hint
    return SubprocessSTTBackend(**kwargs)


def create_stt_backend(
    settings: AppSettings,
    *,
    secrets: SecretStore,
    diagnostics_enabled: Callable[[], bool] | None = None,
) -> STTBackend:
    effective_terms = get_effective_custom_terms(settings.stt.custom_terms, settings.stt.custom_vocabulary_enabled, settings.languages.source_language)
    from puripuly_heart.config.paths import default_models_dir
    data_dir = default_models_dir()

    return _create_subprocess_stt_backend(
        settings.provider.stt,
        settings.provider.stt_compute,
        settings.provider.stt_quant,
        settings.languages.source_language,
        data_dir=data_dir,
    )


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
    from puripuly_heart.config.paths import default_models_dir
    data_dir = default_models_dir()

    return _create_subprocess_stt_backend(
        resolved.provider,
        settings.provider.peer_stt_compute,
        settings.provider.peer_stt_quant,
        resolved.source_language,
        data_dir=data_dir,
    )
