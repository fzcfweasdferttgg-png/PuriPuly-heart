from __future__ import annotations

import httpx2
from llm_lib import LLM, Settings

from puripuly_heart.core.llm.model_discovery import fetch_llm_lib_models


async def test_llm_lib_model_discovery_uses_ollama_tags(monkeypatch) -> None:
    captured = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return httpx2.Response(200, json={"models": [{"name": "qwen-test:latest"}]})

    client = LLM(
        Settings(provider="ollama"),
        transport=httpx2.MockTransport(handler),
    )
    monkeypatch.setattr(
        "puripuly_heart.core.llm.model_discovery._llm_lib_discovery_client",
        lambda *_args: client,
    )

    assert await fetch_llm_lib_models("ollama", "http://127.0.0.1:11434") == ["qwen-test:latest"]
    assert captured[0].url.path == "/api/tags"
