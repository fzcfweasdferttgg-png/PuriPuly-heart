from __future__ import annotations

import logging

import httpx
import pytest

from puripuly_heart.core.diagnostics import DIAGNOSTIC_RECORD_ATTRIBUTE
from puripuly_heart.core.llm import model_discovery


def _records(caplog):
    return [
        getattr(record, DIAGNOSTIC_RECORD_ATTRIBUTE)
        for record in caplog.records
        if hasattr(record, DIAGNOSTIC_RECORD_ATTRIBUTE)
    ]


@pytest.mark.asyncio
async def test_openai_probe_preserves_http_failure_when_close_fails(monkeypatch, caplog) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.aclose()

        async def aclose(self):
            raise OSError("PRIVATE_CLOSE")

        async def get(self, *_args, **_kwargs):
            return httpx.Response(401, text="PRIVATE_RESPONSE")

    monkeypatch.setattr(model_discovery.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.WARNING)

    status, _body = await model_discovery.test_connection("http://localhost/v1")

    assert status == 401
    records = _records(caplog)
    stages = [record[3] for record in records]
    assert stages == ["probe", "cleanup"]
    assert records[1][0].cause_id == records[0][0].diagnostic_id
    assert "PRIVATE_" not in repr([record[0] for record in records])


@pytest.mark.asyncio
async def test_anthropic_probe_preserves_primary_failure_when_close_fails(
    monkeypatch, caplog
) -> None:
    class Models:
        def list(self, *, limit: int):
            raise TimeoutError("PRIVATE_PRIMARY")

    class Client:
        models = Models()

        async def close(self):
            raise OSError("PRIVATE_CLOSE")

    monkeypatch.setattr(model_discovery, "_anthropic_client", lambda *_args: Client())
    caplog.set_level(logging.WARNING)

    status, _body = await model_discovery.test_anthropic_connection("", "")

    assert status == 0
    records = _records(caplog)
    stages = [record[3] for record in records]
    assert stages == ["probe", "cleanup"]
    assert records[0][0].category == "timeout"
    assert records[1][0].cause_id == records[0][0].diagnostic_id
    assert "PRIVATE_" not in repr([record[0] for record in records])


@pytest.mark.asyncio
async def test_llm_lib_probe_preserves_primary_failure_when_close_fails(
    monkeypatch, caplog
) -> None:
    class Client:
        async def aclose(self):
            raise OSError("PRIVATE_CLOSE")

    async def fail_listing(_client):
        raise TimeoutError("PRIVATE_PRIMARY")

    monkeypatch.setattr(model_discovery, "_llm_lib_discovery_client", lambda *_args: Client())
    monkeypatch.setattr(model_discovery, "_list_llm_lib_models", fail_listing)
    caplog.set_level(logging.WARNING)

    status, _body = await model_discovery.test_llm_lib_connection("ollama", "http://localhost")

    assert status == 0
    records = _records(caplog)
    stages = [record[3] for record in records]
    assert stages == ["probe", "cleanup"]
    assert records[0][0].category == "timeout"
    assert records[1][0].cause_id == records[0][0].diagnostic_id
    assert "PRIVATE_" not in repr([record[0] for record in records])
