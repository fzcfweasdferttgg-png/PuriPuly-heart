from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from .constants import LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS, LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS
from .enums import (
    LLMProviderName,
    LocalLLMBackend,
    QwenRegion,
    STTProviderName,
    SecretsBackend,
    _parse_local_llm_backend,
    _parse_llm_provider,
    _parse_qwen_region,
    _parse_stt_provider,
    _parse_peer_stt_provider,
)


def _default_secrets_backend() -> SecretsBackend:
    from puripuly_heart.config.paths import is_portable

    if is_portable():
        return SecretsBackend.ENCRYPTED_FILE
    return SecretsBackend.KEYRING


def _default_local_llm_extra_body() -> dict[str, object]:
    return {"reasoning_effort": "none"}


def _normalize_local_llm_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid local llm base url")
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("invalid local llm base url") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("invalid local llm base url")
    if not parsed.hostname:
        raise ValueError("invalid local llm base url")
    if (
        "@" in parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid local llm base url")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _parse_local_llm_base_url(value: object) -> str:
    if isinstance(value, str):
        try:
            return _normalize_local_llm_base_url(value)
        except ValueError:
            pass
    return "http://127.0.0.1:11434/v1"


def _normalize_local_llm_model(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid local llm model")
    normalized = value.strip()
    if not normalized:
        raise ValueError("invalid local llm model")
    return normalized


def _parse_local_llm_model(value: object) -> str:
    if isinstance(value, str):
        try:
            return _normalize_local_llm_model(value)
        except ValueError:
            pass
    return "llama3.1:8b"


def _parse_local_llm_extra_body(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return _default_local_llm_extra_body()
    normalized = {key: val for key, val in value.items() if isinstance(key, str)}
    lowered = {key.lower() for key in normalized}
    if LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS.intersection(lowered):
        return _default_local_llm_extra_body()
    if LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS.intersection(lowered):
        return _default_local_llm_extra_body()
    try:
        json.dumps(normalized, allow_nan=False)
    except (TypeError, ValueError):
        return _default_local_llm_extra_body()
    return copy.deepcopy(normalized)


@dataclass(slots=True)
class OpenAICompatibleSettings:
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"

    def validate(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("openai_compatible base_url must be a non-empty string")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("openai_compatible model must be a non-empty string")


@dataclass(slots=True)
class ProviderSettings:
    stt: STTProviderName = STTProviderName.LOCAL_QWEN
    peer_stt: STTProviderName = STTProviderName.LOCAL_QWEN
    stt_compute: str = "gpu"
    peer_stt_compute: str = "gpu"
    stt_backend: str = "onnx"
    peer_stt_backend: str = "onnx"
    stt_quant: str = "auto"
    peer_stt_quant: str = "auto"
    llm: LLMProviderName = LLMProviderName.OPENAI_COMPATIBLE
    openai_compatible: OpenAICompatibleSettings = field(default_factory=OpenAICompatibleSettings)

    def validate(self) -> None:
        if not isinstance(self.stt, STTProviderName):
            raise ValueError("invalid stt provider")
        if not isinstance(self.peer_stt, STTProviderName):
            raise ValueError("invalid peer stt provider")
        if self.stt_compute not in ("gpu", "cpu"):
            raise ValueError("stt_compute must be 'gpu' or 'cpu'")
        if self.peer_stt_compute not in ("gpu", "cpu"):
            raise ValueError("peer_stt_compute must be 'gpu' or 'cpu'")
        if self.stt_backend not in ("onnx", "gguf"):
            raise ValueError("stt_backend must be 'onnx' or 'gguf'")
        if self.peer_stt_backend not in ("onnx", "gguf"):
            raise ValueError("peer_stt_backend must be 'onnx' or 'gguf'")
        if not isinstance(self.stt_quant, str) or not self.stt_quant:
            raise ValueError("stt_quant must be a non-empty string")
        if not isinstance(self.peer_stt_quant, str) or not self.peer_stt_quant:
            raise ValueError("peer_stt_quant must be a non-empty string")
        if not isinstance(self.llm, LLMProviderName):
            raise ValueError("invalid llm provider")
        if not isinstance(self.openai_compatible, OpenAICompatibleSettings):
            raise ValueError("invalid openai_compatible settings")
        if self.llm == LLMProviderName.OPENAI_COMPATIBLE:
            self.openai_compatible.validate()


@dataclass(slots=True)
class SecretsSettings:
    backend: SecretsBackend = SecretsBackend.KEYRING
    encrypted_file_path: str = "secrets.json"

    def validate(self) -> None:
        if not isinstance(self.backend, SecretsBackend):
            raise ValueError("invalid secrets backend")
        if self.backend == SecretsBackend.ENCRYPTED_FILE and not self.encrypted_file_path:
            raise ValueError("encrypted_file_path must be set for encrypted_file backend")


@dataclass(slots=True)
class QwenSettings:
    region: QwenRegion = QwenRegion.BEIJING

    def validate(self) -> None:
        if not isinstance(self.region, QwenRegion):
            raise ValueError("invalid qwen region")


@dataclass(slots=True)
class LocalLLMSettings:
    backend: LocalLLMBackend = LocalLLMBackend.OLLAMA
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "llama3.1:8b"
    extra_body: dict[str, object] = field(default_factory=_default_local_llm_extra_body)

    def validate(self) -> None:
        if not isinstance(self.backend, LocalLLMBackend):
            raise ValueError("invalid local llm backend")
        self.base_url = _normalize_local_llm_base_url(self.base_url)
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("invalid local llm base url")
        self.model = _normalize_local_llm_model(self.model)
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("invalid local llm model")
        if not isinstance(self.extra_body, dict):
            raise ValueError("invalid local llm extra body")
        normalized = {key: value for key, value in self.extra_body.items() if isinstance(key, str)}
        if len(normalized) != len(self.extra_body):
            raise ValueError("local llm extra body keys must be strings")
        lowered = {key.lower() for key in normalized}
        reserved = LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS.intersection(lowered)
        if reserved:
            key = sorted(reserved)[0]
            raise ValueError(f"reserved local llm extra_body key: {key}")
        sensitive = LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS.intersection(lowered)
        if sensitive:
            key = sorted(sensitive)[0]
            raise ValueError(f"sensitive local llm extra_body key: {key}")
        try:
            json.dumps(normalized, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("local llm extra body must be JSON serializable") from exc
        self.extra_body = copy.deepcopy(normalized)


def _parse_openai_compatible_settings(data: dict) -> OpenAICompatibleSettings:
    base_url = str(data.get("base_url", "https://api.openai.com/v1")).strip()
    model = str(data.get("model", "gpt-4o-mini")).strip()
    return OpenAICompatibleSettings(base_url=base_url, model=model)


def _normalize_local_llm_data(data: dict) -> bool:
    raw_local_llm = data.get("local_llm")
    local_llm_data = raw_local_llm if isinstance(raw_local_llm, dict) else {}
    normalized = {
        "backend": _parse_local_llm_backend(local_llm_data.get("backend")).value,
        "base_url": _parse_local_llm_base_url(local_llm_data.get("base_url")),
        "model": _parse_local_llm_model(local_llm_data.get("model")),
        "extra_body": _parse_local_llm_extra_body(local_llm_data.get("extra_body")),
    }
    if raw_local_llm != normalized:
        data["local_llm"] = normalized
        return True
    return False
