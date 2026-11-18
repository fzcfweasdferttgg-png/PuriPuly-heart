from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message import ChatCompletionMessage as ChoiceMessage

from puripuly_heart.core.error_messages import provider_failure_report
from puripuly_heart.providers.llm.error_details import LLMResponseError
from puripuly_heart.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


class Completions:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, BaseException):
            raise self.response
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


@pytest.mark.asyncio
async def test_openai_sdk_adapter_preserves_transport_failure_diagnostics() -> None:
    error = openai.APIConnectionError(
        request=httpx2.Request("POST", "http://localhost/v1/chat/completions")
    )
    client = Client(error)
    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.example.com/v1", model="gpt-test", client=client
    )

    with pytest.raises(LLMResponseError, match="OpenAI-compatible request failed") as caught:
        await provider.translate(
            utterance_id=uuid4(),
            text="hello",
            system_prompt="",
            source_language="ja",
            target_language="en",
        )
    await provider.close()

    report = provider_failure_report(caught.value, provider="llm", operation="translate")
    assert report.diagnostics.fields["provider"] == "openai_compatible"
    assert report.diagnostics.category == "network"
    assert report.message.key == "error.network_error"


@pytest.mark.asyncio
async def test_openai_sdk_adapter_presents_http_authentication_failure() -> None:
    request = httpx2.Request("POST", "https://api.example.com/v1/chat/completions")
    error = openai.AuthenticationError(
        "neutral",
        response=httpx2.Response(401, request=request),
        body=None,
    )
    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.example.com/v1",
        model="gpt-test",
        client=Client(error),
    )

    with pytest.raises(LLMResponseError) as caught:
        await provider.translate(
            utterance_id=uuid4(),
            text="hello",
            system_prompt="",
            source_language="ja",
            target_language="en",
        )
    await provider.close()

    report = provider_failure_report(caught.value, provider="llm", operation="translate")
    assert report.message.key == "error.openai.authentication_rejected"
    assert report.diagnostics.category == "auth"
    assert report.diagnostics.status_code == 401


def test_openai_owned_client_uses_base_url_and_disables_retries() -> None:
    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.example.com/v1", model="gpt-test", api_key="test"
    )

    client = provider._client_for_call()

    assert client.max_retries == 0
    assert str(client.base_url) == "https://api.example.com/v1/"
    asyncio.run(provider.close())


@pytest.mark.parametrize("provider", ["openai", "openai_compatible"])
@pytest.mark.parametrize(
    ("status_code", "diagnostic_category", "category", "message_key"),
    [
        (400, "unknown", "invalid_response", "error.openai.request_rejected"),
        (401, "unknown", "auth", "error.openai.authentication_rejected"),
        (403, "unknown", "auth", "error.openai.permission_denied"),
        (404, "unknown", "invalid_response", "error.openai.model_unavailable"),
        (402, "unknown", "quota", "error.openai.quota"),
        (429, "unknown", "rate_limit", "error.openai.rate_limit"),
        (429, "quota", "quota", "error.openai.quota"),
        (503, "unknown", "service_unavailable", "error.openai.service_unavailable"),
        (None, "timeout", "timeout", "error.network_timeout"),
        (None, "network", "network", "error.network_error"),
    ],
)
def test_openai_api_failures_share_status_category_message_mapping(
    provider: str,
    status_code: int | None,
    diagnostic_category: str,
    category: str,
    message_key: str,
) -> None:
    error = LLMResponseError(
        "neutral",
        provider=provider,
        status_code=status_code,
        diagnostic_category=diagnostic_category,
    )

    report = provider_failure_report(error, provider="llm", operation="translate")

    assert report.message.key == message_key
    assert report.diagnostics.category == category
    assert report.diagnostics.status_code == status_code
    assert report.diagnostics.fields["provider"] == provider
