from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from puripuly_heart.core.llm.fallback_chain import SequentialFallbackLLMProvider
from puripuly_heart.domain.models import Translation
from puripuly_heart.providers.llm.local_openai import (
    HttpxLocalOpenAIClient,
    LocalOpenAICompatibleLLMProvider,
)


class BlockingAsyncClient:
    def __init__(self, expected_requests: int = 1) -> None:
        self.expected_requests = expected_requests
        self.started_requests = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def post(self, _url: str, **_kwargs: object):
        self.started_requests += 1
        if self.started_requests == self.expected_requests:
            self.started.set()
        await self.release.wait()
        if self.closed:
            raise RuntimeError("transport closed during request")
        return Response()

    async def aclose(self) -> None:
        self.closed = True


class Response:
    status_code = 200

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": "translated"}}]}


class ImmediateAsyncClient:
    def __init__(self) -> None:
        self.closed = False

    async def post(self, _url: str, **_kwargs: object) -> Response:
        if self.closed:
            raise RuntimeError("transport closed during request")
        return Response()

    async def aclose(self) -> None:
        self.closed = True


def translate(client: HttpxLocalOpenAIClient, text: str = "source"):
    return client.translate(
        text=text,
        system_prompt="prompt",
        source_language="en",
        target_language="ko",
    )


@pytest.mark.asyncio
async def test_close_cancels_parallel_requests_before_closing_transport(monkeypatch) -> None:
    http = BlockingAsyncClient(expected_requests=2)
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: http)
    client = HttpxLocalOpenAIClient()
    requests = [
        asyncio.create_task(translate(client, "first")),
        asyncio.create_task(translate(client, "second")),
    ]
    await asyncio.wait_for(http.started.wait(), timeout=1)

    await asyncio.wait_for(client.close(), timeout=1)
    http.release.set()
    outcomes = await asyncio.gather(*requests, return_exceptions=True)

    assert all(isinstance(outcome, asyncio.CancelledError) for outcome in outcomes)
    assert http.closed is True


@pytest.mark.asyncio
async def test_backup_chain_close_cancels_primary_without_starting_backup(monkeypatch) -> None:
    http = BlockingAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: http)
    primary = LocalOpenAICompatibleLLMProvider()

    class Backup:
        calls = 0
        closed = False

        async def translate(self, *, utterance_id, **_kwargs) -> Translation:
            self.calls += 1
            return Translation(
                utterance_id=utterance_id,
                text="backup",
                source_text="source",
                source_language="en",
                target_language="ko",
            )

        async def close(self) -> None:
            self.closed = True

    backup = Backup()
    chain = SequentialFallbackLLMProvider(primary=primary, fallback=backup)
    request = asyncio.create_task(
        chain.translate(
            utterance_id=uuid4(),
            text="source",
            system_prompt="prompt",
            source_language="en",
            target_language="ko",
        )
    )
    await asyncio.wait_for(http.started.wait(), timeout=1)

    await asyncio.wait_for(chain.close(), timeout=1)
    http.release.set()
    outcomes = await asyncio.gather(request, return_exceptions=True)

    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert backup.calls == 0
    assert backup.closed is True
    assert http.closed is True


@pytest.mark.asyncio
async def test_close_can_be_followed_by_a_fresh_client(monkeypatch) -> None:
    transports: list[ImmediateAsyncClient] = []

    def factory(**_kwargs: object) -> ImmediateAsyncClient:
        transport = ImmediateAsyncClient()
        transports.append(transport)
        return transport

    monkeypatch.setattr("httpx.AsyncClient", factory)
    client = HttpxLocalOpenAIClient()

    assert await translate(client, "before close") == "translated"
    await client.close()
    assert await translate(client, "after close") == "translated"
    await client.close()

    assert len(transports) == 2
    assert all(transport.closed for transport in transports)
