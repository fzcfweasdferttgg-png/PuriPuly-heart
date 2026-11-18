from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from llm_lib import Settings


class ExplicitLLMLibSettings(Settings):
    def resolved_api_key(self, env: Mapping[str, str] | None = None) -> str | None:
        return self.api_key


@dataclass(frozen=True, slots=True)
class LLMLibBackendCapabilities:
    model_required: bool = False
    auto_select_single_model: bool = True
    discovery_api: str = "openai"
    unsupported_extra_body_keys: frozenset[str] = frozenset()


_GENERIC = LLMLibBackendCapabilities()
_CAPABILITIES = {
    "colibri": LLMLibBackendCapabilities(
        unsupported_extra_body_keys=frozenset(
            {"frequency_penalty", "presence_penalty", "repeat_penalty", "repetition_penalty"}
        )
    ),
    "lmstudio": _GENERIC,
    "llamacpp": _GENERIC,
    "vllm": _GENERIC,
    "sglang": _GENERIC,
    "mlx": _GENERIC,
    "koboldcpp": _GENERIC,
    "textgen": _GENERIC,
    "tabbyapi": _GENERIC,
    "localai": _GENERIC,
    "jan": _GENERIC,
    "openai-compatible": _GENERIC,
    "ollama": LLMLibBackendCapabilities(
        model_required=True,
        auto_select_single_model=False,
        discovery_api="ollama",
    ),
}


def llm_lib_backend_capabilities(backend: str) -> LLMLibBackendCapabilities:
    return _CAPABILITIES.get((backend or "").strip(), _GENERIC)


def normalized_llm_lib_base_url(backend: str, base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if backend == "ollama" and normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


__all__ = [
    "ExplicitLLMLibSettings",
    "LLMLibBackendCapabilities",
    "llm_lib_backend_capabilities",
    "normalized_llm_lib_base_url",
]
