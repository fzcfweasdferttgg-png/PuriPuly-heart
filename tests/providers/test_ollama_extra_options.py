from __future__ import annotations

import json
from uuid import uuid4

import httpx2
import pytest
from llm_lib import LLM

from puripuly_heart.providers.llm.local_llm_lib import LLMLibLocalLLMProvider


@pytest.mark.asyncio
async def test_ollama_seed_and_stop_extra_body_reach_native_options() -> None:
    captured: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        payload = {
            "model": "qwen-test",
            "message": {"role": "assistant", "content": "translated"},
            "done": True,
            "done_reason": "stop",
        }
        return httpx2.Response(200, text=json.dumps(payload) + "\n")

    provider = LLMLibLocalLLMProvider(
        backend="ollama",
        base_url="http://127.0.0.1:11434",
        model="qwen-test",
        extra_body={"seed": 42, "stop": ["END"]},
    )
    provider.client = LLM(
        provider._settings(),
        async_transport=httpx2.MockTransport(handler),
    )

    await provider.translate(
        utterance_id=uuid4(),
        text="source",
        system_prompt="translate",
        source_language="en",
        target_language="ru",
    )
    await provider.close()

    body = json.loads(captured[0].content)
    assert (body["options"].get("seed"), body["options"].get("stop")) == (42, ["END"])
