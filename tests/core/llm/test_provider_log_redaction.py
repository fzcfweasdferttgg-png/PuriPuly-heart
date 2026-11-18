from __future__ import annotations

import logging

import httpx2
import pytest
from llm_lib import LLM, Settings

from puripuly_heart.app.wiring.wiring_translation_variants import build_variant_provider
from puripuly_heart.config.runtime_resolution import (
    UserLocalLLMRuntimeIntent,
    UserOpenAICompatibleRuntimeIntent,
)
from puripuly_heart.core.llm import model_discovery
from puripuly_heart.core.storage.secrets import InMemorySecretStore


@pytest.mark.asyncio
async def test_llm_lib_discovery_does_not_log_response_body(monkeypatch, caplog) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, text="PRIVATE_RESPONSE_BODY")

    client = LLM(
        Settings(provider="ollama"),
        async_transport=httpx2.MockTransport(handler),
    )
    monkeypatch.setattr(model_discovery, "_llm_lib_discovery_client", lambda *_args: client)
    caplog.set_level(logging.WARNING)

    assert await model_discovery.fetch_llm_lib_models("ollama", "http://localhost") == []

    assert "PRIVATE_RESPONSE_BODY" not in caplog.text


def test_provider_construction_does_not_log_url_credentials(caplog) -> None:
    caplog.set_level(logging.INFO)

    provider = build_variant_provider(
        "openai_compatible",
        local_llm=UserLocalLLMRuntimeIntent(),
        openai_compatible=UserOpenAICompatibleRuntimeIntent(
            base_url="https://user:PRIVATE_URL_CREDENTIAL@example.invalid/v1",
            model="test-model",
        ),
        secrets=InMemorySecretStore(),
    )

    assert provider is not None
    assert "PRIVATE_URL_CREDENTIAL" not in caplog.text
