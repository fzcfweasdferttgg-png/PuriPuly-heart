from __future__ import annotations

from collections.abc import Mapping

from llm_lib import Settings


class ExplicitLLMLibSettings(Settings):
    def resolved_api_key(self, env: Mapping[str, str] | None = None) -> str | None:
        return self.api_key


def normalized_llm_lib_base_url(backend: str, base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if backend == "ollama" and normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


__all__ = ["ExplicitLLMLibSettings", "normalized_llm_lib_base_url"]
