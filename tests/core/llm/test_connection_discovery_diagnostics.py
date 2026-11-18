from __future__ import annotations

import logging

import anthropic
import httpx
import httpx2
import pytest
from llm_lib import LLM, Settings

from puripuly_heart.core.diagnostics import DIAGNOSTIC_RECORD_ATTRIBUTE
from puripuly_heart.core.llm import model_discovery


def _failures(caplog):
    return [
        getattr(record, DIAGNOSTIC_RECORD_ATTRIBUTE)[0]
        for record in caplog.records
        if hasattr(record, DIAGNOSTIC_RECORD_ATTRIBUTE)
    ]


@pytest.mark.asyncio
async def test_openai_connection_http_failure_emits_safe_probe(monkeypatch, caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="PRIVATE_RESPONSE_BODY")

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        model_discovery.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    caplog.set_level(logging.ERROR)

    status, _body = await model_discovery.test_connection("http://localhost/v1")

    assert status == 401
    failure = _failures(caplog)[0]
    assert failure.operation == "probe"
    assert failure.fields["provider"] == "openai_compatible"
    assert failure.category == "auth"
    assert failure.status_code == 401
    assert "PRIVATE_" not in repr(failure)


@pytest.mark.asyncio
async def test_anthropic_connection_failure_emits_safe_probe(monkeypatch, caplog) -> None:
    class Models:
        def list(self, *, limit: int):
            return self

        async def __aiter__(self):
            raise anthropic.APIConnectionError(
                message="PRIVATE_EXCEPTION_TEXT",
                request=httpx2.Request("GET", "http://localhost/v1/models"),
            )
            yield None

    class Client:
        def __init__(self) -> None:
            self.models = Models()

        async def close(self) -> None:
            pass

    monkeypatch.setattr(model_discovery, "_anthropic_client", lambda *_args: Client())
    caplog.set_level(logging.ERROR)

    status, _body = await model_discovery.test_anthropic_connection("", "")

    assert status == 0
    failure = _failures(caplog)[0]
    assert failure.operation == "probe"
    assert failure.fields["provider"] == "anthropic"
    assert failure.category == "network"
    assert "PRIVATE_" not in repr(failure)


@pytest.mark.asyncio
async def test_llm_lib_connection_failure_emits_safe_probe(monkeypatch, caplog) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("PRIVATE_EXCEPTION_TEXT", request=request)

    client = LLM(
        Settings(provider="ollama"),
        async_transport=httpx2.MockTransport(handler),
    )
    monkeypatch.setattr(model_discovery, "_llm_lib_discovery_client", lambda *_args: client)
    caplog.set_level(logging.ERROR)

    status, _body = await model_discovery.test_llm_lib_connection(
        "ollama", "http://127.0.0.1:11434"
    )

    assert status == 0
    failure = _failures(caplog)[0]
    assert failure.operation == "probe"
    assert failure.fields["provider"] == "local_llm"
    assert failure.category == "network"
    assert "PRIVATE_" not in repr(failure)
