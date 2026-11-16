from __future__ import annotations

import pytest

from puripuly_heart.core.llm import model_discovery


class Models:
    def __init__(self, entries) -> None:
        self.entries = entries
        self.limits: list[int] = []

    def list(self, *, limit: int):
        self.limits.append(limit)
        return self

    async def __aiter__(self):
        for entry in self.entries:
            yield entry


class Client:
    def __init__(self, entries) -> None:
        self.models = Models(entries)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_anthropic_discovery_uses_sdk_pagination_and_filters_retired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [
        type("Model", (), {"id": "z-model", "lifecycle": "active"})(),
        type("Model", (), {"id": "a-model", "lifecycle": "deprecated"})(),
        type("Model", (), {"id": "old-model", "lifecycle": "retired"})(),
    ]
    client = Client(entries)
    monkeypatch.setattr(model_discovery, "_anthropic_client", lambda *_args: client)

    result = await model_discovery.fetch_anthropic_models("https://api.anthropic.com", "key")

    assert result == ["a-model", "z-model"]
    assert client.models.limits == [1000]
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_anthropic_connection_uses_models_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client([])
    monkeypatch.setattr(model_discovery, "_anthropic_client", lambda *_args: client)

    status, body = await model_discovery.test_anthropic_connection("", "")

    assert (status, body) == (200, "ok")
    assert client.models.limits == [1]
    assert client.close_calls == 1
