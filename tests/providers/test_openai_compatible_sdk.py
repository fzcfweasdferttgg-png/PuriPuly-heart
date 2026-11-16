from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message import ChatCompletionMessage as ChoiceMessage

from puripuly_heart.providers.llm.error_details import LLMResponseError
from puripuly_heart.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


class Completions:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class Client:
    def __init__(self, response) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = Completions(response)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def completion(*, finish_reason: str = "stop") -> ChatCompletion:
    return ChatCompletion(
        id="chat_test",
        choices=[
            Choice(
                finish_reason=finish_reason,
                index=0,
                message=ChoiceMessage(content=" translated ", role="assistant"),
            )
        ],
        created=0,
        model="gpt-test",
        object="chat.completion",
    )


@pytest.mark.asyncio
async def test_openai_sdk_adapter_preserves_chat_completions_contract() -> None:
    client = Client(completion())
    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.example.com/v1/",
        model="gpt-test",
        api_key="secret",
        client=client,
    )

    result = await provider.translate(
        utterance_id=uuid4(),
        text="hello",
        system_prompt="Translate from {source_language} to {target_language}",
        source_language="ja",
        target_language="en",
        max_output_tokens=222,
    )

    assert result.text == "translated"
    call = client.chat.completions.calls[0]
    assert call["model"] == "gpt-test"
    assert call["max_tokens"] == 222
    assert call["stream"] is False
    assert call["messages"][0]["content"] == "Translate from ja to en"
    assert call["messages"][1]["content"] == "<input>\nhello\n</input>"
    await provider.close()
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_openai_sdk_adapter_rejects_length_finish() -> None:
    client = Client(completion(finish_reason="length"))
    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.example.com/v1", model="gpt-test", client=client
    )

    with pytest.raises(LLMResponseError, match="truncated"):
        await provider.translate(
            utterance_id=uuid4(),
            text="hello",
            system_prompt="",
            source_language="ja",
            target_language="en",
        )
    await provider.close()


def test_openai_owned_client_uses_base_url_and_disables_retries() -> None:
    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.example.com/v1", model="gpt-test", api_key="test"
    )

    client = provider._client_for_call()

    assert client.max_retries == 0
    assert str(client.base_url) == "https://api.example.com/v1/"
    asyncio.run(provider.close())
