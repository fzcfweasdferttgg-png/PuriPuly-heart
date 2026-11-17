from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from puripuly_heart.core.llm.provider import SemaphoreLLMProvider
from puripuly_heart.providers.llm.anthropic import AnthropicLLMProvider
from puripuly_heart.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


def _translate(provider):
    return provider.translate(
        utterance_id=uuid4(),
        text="source",
        system_prompt="translate",
        source_language="en",
        target_language="ru",
    )


class BlockingCompletions:
    def __init__(self) -> None:
        self.calls = 0
        self.first_started = asyncio.Event()
        self.second_started = asyncio.Event()

    async def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
        else:
            self.second_started.set()
        await asyncio.Event().wait()


class BlockingMessages(BlockingCompletions):
    pass


class OpenAIClient:
    def __init__(self) -> None:
        self.completions = BlockingCompletions()
        self.chat = type("Chat", (), {"completions": self.completions})()
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class AnthropicClient:
    def __init__(self) -> None:
        self.messages = BlockingMessages()
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


async def _check_queued_close(provider, client, operation: BlockingCompletions) -> None:
    limited = SemaphoreLLMProvider(inner=provider, semaphore=asyncio.Semaphore(1))
    first = asyncio.create_task(_translate(limited))
    await asyncio.wait_for(operation.first_started.wait(), timeout=1)
    queued = asyncio.create_task(_translate(limited))
    await asyncio.sleep(0)

    await asyncio.wait_for(limited.close(), timeout=1)
    await asyncio.sleep(0)

    try:
        assert operation.calls == 1
        assert not operation.second_started.is_set()
        assert queued.done() and queued.cancelled()
    finally:
        queued.cancel()
        await asyncio.gather(first, queued, return_exceptions=True)


@pytest.mark.asyncio
async def test_openai_close_does_not_admit_queued_translation() -> None:
    client = OpenAIClient()
    provider = OpenAICompatibleLLMProvider(
        base_url="http://127.0.0.1:9000/v1",
        model="test-model",
        client=client,
    )
    await _check_queued_close(provider, client, client.completions)


@pytest.mark.asyncio
async def test_anthropic_close_does_not_admit_queued_translation() -> None:
    client = AnthropicClient()
    provider = AnthropicLLMProvider(model="test-model", client=client)
    await _check_queued_close(provider, client, client.messages)
