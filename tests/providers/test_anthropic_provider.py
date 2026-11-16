from __future__ import annotations

import asyncio
from uuid import uuid4

import anthropic
import httpx2
import pytest
from anthropic.types import Message, TextBlock, Usage

from puripuly_heart.providers.llm.anthropic import (
    ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS,
    AnthropicLLMProvider,
)
from puripuly_heart.providers.llm.error_details import LLMResponseError


class Messages:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []
        self.waiting = asyncio.Event()

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, BaseException):
            raise self.response
        if isinstance(self.response, str) and self.response == "wait":
            await self.waiting.wait()
        return self.response


class Client:
    def __init__(self, response) -> None:
        self.messages = Messages(response)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def message(*, text: str = "translated", stop_reason: str = "end_turn") -> Message:
    return Message(
        id="msg_test",
        content=[
            TextBlock(type="text", text=f" {text} "),
            TextBlock(type="text", text="second block"),
        ],
        model="claude-test",
        role="assistant",
        stop_reason=stop_reason,
        type="message",
        usage=Usage(input_tokens=10, output_tokens=4),
    )


def translate(provider: AnthropicLLMProvider):
    return provider.translate(
        utterance_id=uuid4(),
        text="hello",
        system_prompt="Translate from {source_language} to {target_language}",
        source_language="ja",
        target_language="en",
        context="VRChat",
        scene_participant_count=2,
    )


@pytest.mark.asyncio
async def test_anthropic_provider_maps_messages_request_and_text_blocks() -> None:
    client = Client(message())
    provider = AnthropicLLMProvider(model="claude-test", api_key=" secret ", client=client)

    result = await translate(provider)

    assert result.text == "translated\nsecond block"
    call = client.messages.calls[0]
    assert call["model"] == "claude-test"
    assert call["max_tokens"] == ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS
    assert call["system"] == "Translate from ja to en"
    assert call["stream"] is False
    assert call["messages"][0]["role"] == "user"
    assert "<context>\nVRChat\n</context>" in call["messages"][0]["content"]
    assert "<scene>\nPeople: 2\n</scene>" in call["messages"][0]["content"]
    assert "<input>\nhello\n</input>" in call["messages"][0]["content"]

    await provider.close()
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_anthropic_provider_uses_caller_token_limit() -> None:
    client = Client(message())
    provider = AnthropicLLMProvider(model="claude-test", client=client)

    await provider.translate(
        utterance_id=uuid4(),
        text="hello",
        system_prompt="",
        source_language="ja",
        target_language="en",
        max_output_tokens=321,
    )

    assert client.messages.calls[0]["max_tokens"] == 321
    await provider.close()


@pytest.mark.asyncio
async def test_anthropic_provider_rejects_truncated_response() -> None:
    client = Client(message(stop_reason="max_tokens"))
    provider = AnthropicLLMProvider(model="claude-test", client=client)

    with pytest.raises(LLMResponseError, match="truncated"):
        await translate(provider)
    await provider.close()


@pytest.mark.asyncio
async def test_anthropic_provider_wraps_sdk_failures() -> None:
    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "http://localhost/v1/messages")
    )
    client = Client(error)
    provider = AnthropicLLMProvider(model="claude-test", client=client)

    with pytest.raises(LLMResponseError, match="Anthropic request failed"):
        await translate(provider)
    await provider.close()


@pytest.mark.asyncio
async def test_anthropic_provider_close_cancels_active_translation() -> None:
    client = Client("wait")
    provider = AnthropicLLMProvider(model="claude-test", client=client)
    task = asyncio.create_task(translate(provider))
    await asyncio.sleep(0)

    await provider.close()

    assert task.cancelled()
    assert client.close_calls == 1


def test_anthropic_owned_client_disables_internal_retries() -> None:
    provider = AnthropicLLMProvider(model="claude-test", api_key="test")

    client = provider._client_for_call()

    assert client.max_retries == 0
    assert str(client.base_url) == "https://api.anthropic.com"
    asyncio.run(provider.close())
