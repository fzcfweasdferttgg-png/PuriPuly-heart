from __future__ import annotations

import json
from uuid import uuid4

import httpx2
from llm_lib import LLM, Completion, Settings, Usage

from puripuly_heart.providers.llm.local_llm_lib import LLMLibLocalLLMProvider


def test_ollama_settings_map_extra_body_and_legacy_url() -> None:
    provider = LLMLibLocalLLMProvider(
        backend="ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="qwen-test",
        extra_body={
            "reasoning_effort": "none",
            "temperature": 0.6,
            "top_k": 40,
            "num_ctx": 8192,
            "custom_flag": True,
        },
    )

    settings = provider._settings()

    assert provider.base_url == "http://127.0.0.1:11434"
    assert settings.kind == "ollama"
    assert settings.temperature == 0.6
    assert settings.top_k == 40
    assert settings.context == 8192
    assert settings.no_think is True
    assert settings.extra == {"custom_flag": True}


async def test_provider_delegates_translation_to_llm_lib_client() -> None:
    class Client:
        model = "fake-model"

        def __init__(self) -> None:
            self.calls = []
            self.closed = False

        async def achat(self, messages, **params):
            self.calls.append((messages, params))
            params["on_event"](type("Event", (), {"type": "content", "text": "ok"})())
            return Completion(text=" translated ", usage=Usage(10, 2))

        async def aclose(self) -> None:
            self.closed = True

    client = Client()
    provider = LLMLibLocalLLMProvider(
        backend="ollama",
        base_url="http://127.0.0.1:11434",
        model="fake-model",
        client=client,
    )

    result = await provider.translate(
        utterance_id=uuid4(),
        text="source",
        system_prompt="translate",
        source_language="en",
        target_language="ru",
        max_output_tokens=32,
    )
    await provider.close()

    assert result.text == "translated"
    assert client.calls[0][0][1]["content"] == "<input>\nsource\n</input>"
    assert client.calls[0][1]["max_tokens"] == 32
    assert client.closed is True


async def test_llm_lib_ollama_native_chat_loopback() -> None:
    captured = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        payload = {
            "model": "qwen-test",
            "message": {"role": "assistant", "content": "привет"},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 12,
            "eval_count": 3,
        }
        return httpx2.Response(200, text=json.dumps(payload) + "\n")

    client = LLM(
        Settings(
            provider="ollama",
            model="qwen-test",
            temperature=0.6,
            context=8192,
            no_think=True,
        ),
        async_transport=httpx2.MockTransport(handler),
    )
    provider = LLMLibLocalLLMProvider(
        backend="ollama",
        base_url="http://127.0.0.1:11434",
        model="qwen-test",
        extra_body={"temperature": 0.6, "num_ctx": 8192, "reasoning_effort": "none"},
        client=client,
    )

    result = await provider.translate(
        utterance_id=uuid4(),
        text="hello",
        system_prompt="translate",
        source_language="en",
        target_language="ru",
    )
    await provider.close()

    assert result.text == "привет"
    assert captured[0].url.path == "/api/chat"
    body = json.loads(captured[0].content)
    assert body["options"]["temperature"] == 0.6
    assert body["options"]["num_ctx"] == 8192
    assert body["think"] is False
    assert body["stream"] is True


async def test_llm_lib_openai_server_chat_loopback() -> None:
    captured = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        chunk = {
            "choices": [{"delta": {"content": "translated"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2},
        }
        body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
        return httpx2.Response(200, text=body)

    client = LLM(
        Settings(provider="llamacpp", model="local-model"),
        async_transport=httpx2.MockTransport(handler),
    )
    provider = LLMLibLocalLLMProvider(
        backend="llamacpp",
        base_url="http://127.0.0.1:8080/v1",
        model="local-model",
        client=client,
    )

    result = await provider.translate(
        utterance_id=uuid4(),
        text="source",
        system_prompt="translate",
        source_language="en",
        target_language="ru",
    )
    await provider.close()

    assert result.text == "translated"
    assert captured[0].url.path == "/v1/chat/completions"
    assert json.loads(captured[0].content)["stream"] is True
