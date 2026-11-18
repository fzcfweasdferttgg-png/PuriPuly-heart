from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_openai_probe_preserves_success_when_close_fails(monkeypatch, caplog) -> None:
    class Client:
        async def get(self, *_args, **_kwargs):
            return httpx.Response(200, text="ok")

        async def aclose(self):
            raise OSError("PRIVATE_CLOSE")

    monkeypatch.setattr(model_discovery.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.WARNING)

    result = await model_discovery.test_connection("http://localhost/v1")

    assert result == (200, "ok")
    records = _records(caplog)
    assert [(record[1], record[2], record[3]) for record in records] == [
        ("warning", "degraded", "cleanup")
    ]
    assert records[0][0].cause_id is None
    assert records[0][0].fields["cleanup_confirmed"] is False
    assert "PRIVATE_" not in repr(records[0][0])


@pytest.mark.asyncio
async def test_anthropic_probe_preserves_success_when_close_fails(monkeypatch, caplog) -> None:
    class Models:
        async def list(self, *, limit: int):
            yield object()

    class Client:
        models = Models()

        async def close(self):
            raise OSError("PRIVATE_CLOSE")

    monkeypatch.setattr(model_discovery, "_anthropic_client", lambda *_args: Client())
    caplog.set_level(logging.WARNING)

    result = await model_discovery.test_anthropic_connection("", "")

    assert result == (200, "ok")
    records = _records(caplog)
    assert [(record[1], record[2], record[3]) for record in records] == [
        ("warning", "degraded", "cleanup")
    ]
    assert records[0][0].cause_id is None
    assert records[0][0].fields["cleanup_confirmed"] is False
    assert "PRIVATE_" not in repr(records[0][0])


@pytest.mark.asyncio
async def test_llm_lib_probe_preserves_success_when_close_fails(monkeypatch, caplog) -> None:
    class Client:
        async def aclose(self):
            raise OSError("PRIVATE_CLOSE")

    async def list_models(_client):
        return ["model"]

    monkeypatch.setattr(model_discovery, "_llm_lib_discovery_client", lambda *_args: Client())
    monkeypatch.setattr(model_discovery, "_list_llm_lib_models", list_models)
    caplog.set_level(logging.WARNING)

    result = await model_discovery.test_llm_lib_connection("ollama", "http://localhost")

    assert result == (200, "ok")
    records = _records(caplog)
    assert [(record[1], record[2], record[3]) for record in records] == [
        ("warning", "degraded", "cleanup")
    ]
    assert records[0][0].cause_id is None
    assert records[0][0].fields["cleanup_confirmed"] is False
    assert "PRIVATE_" not in repr(records[0][0])


@pytest.mark.asyncio
async def test_openai_probe_cancellation_survives_close_failure(monkeypatch, caplog) -> None:
    class Client:
        async def get(self, *_args, **_kwargs):
            raise asyncio.CancelledError

        async def aclose(self):
            raise OSError("PRIVATE_CLOSE")

    monkeypatch.setattr(model_discovery.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.WARNING)

    with pytest.raises(asyncio.CancelledError):
        await model_discovery.test_connection("http://localhost/v1")

    records = _records(caplog)
    assert [(record[1], record[2], record[3]) for record in records] == [
        ("warning", "degraded", "cleanup")
    ]
    assert records[0][0].cause_id is None


class _AsyncItems:
    """Minimal async iterable standing in for the anthropic model paginator."""

    def __init__(self, raise_exc: BaseException | None = None) -> None:
        self._raise_exc = raise_exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_openai_probe_cancellation_propagates_without_diagnostic(monkeypatch, caplog) -> None:
    class Client:
        async def aclose(self):
            return None

        async def get(self, *_args, **_kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(model_discovery.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.WARNING)

    with pytest.raises(asyncio.CancelledError):
        await model_discovery.test_connection("http://localhost/v1")

    assert _records(caplog) == []


@pytest.mark.asyncio
async def test_openai_probe_cancelled_close_propagates_without_diagnostic(
    monkeypatch, caplog
) -> None:
    class Client:
        async def aclose(self):
            raise asyncio.CancelledError

        async def get(self, *_args, **_kwargs):
            return httpx.Response(200, text="ok")

    monkeypatch.setattr(model_discovery.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.WARNING)

    with pytest.raises(asyncio.CancelledError):
        await model_discovery.test_connection("http://localhost/v1")

    assert _records(caplog) == []


@pytest.mark.asyncio
async def test_anthropic_probe_cancellation_propagates_without_diagnostic(
    monkeypatch, caplog
) -> None:
    class Models:
        def list(self, *, limit: int):
            return _AsyncItems(raise_exc=asyncio.CancelledError)

    class Client:
        models = Models()

        async def close(self):
            return None

    monkeypatch.setattr(model_discovery, "_anthropic_client", lambda *_args: Client())
    caplog.set_level(logging.WARNING)

    with pytest.raises(asyncio.CancelledError):
        await model_discovery.test_anthropic_connection("", "")

    assert _records(caplog) == []


@pytest.mark.asyncio
async def test_llm_lib_probe_cancelled_close_propagates_without_diagnostic(
    monkeypatch, caplog
) -> None:
    class Client:
        async def aclose(self):
            raise asyncio.CancelledError

    async def ok_listing(_client):
        return ["model-a"]

    monkeypatch.setattr(model_discovery, "_llm_lib_discovery_client", lambda *_args: Client())
    monkeypatch.setattr(model_discovery, "_list_llm_lib_models", ok_listing)
    caplog.set_level(logging.WARNING)

    with pytest.raises(asyncio.CancelledError):
        await model_discovery.test_llm_lib_connection("ollama", "http://localhost")

    assert _records(caplog) == []


@pytest.mark.asyncio
async def test_openai_fetch_preserves_models_when_close_fails(monkeypatch, caplog) -> None:
    class Client:
        async def get(self, *_args, **_kwargs):
            return httpx.Response(
                200,
                json={"data": [{"id": "model"}]},
                request=httpx.Request("GET", "http://localhost/v1/models"),
            )

        async def aclose(self):
            raise OSError("PRIVATE_CLOSE")

    monkeypatch.setattr(model_discovery.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.WARNING)

    result = await model_discovery.fetch_models("http://localhost/v1")

    assert result == ["model"]
    assert "cleanup failed" in caplog.text
    assert "PRIVATE_" not in caplog.text


@pytest.mark.asyncio
async def test_openai_fetch_client_construction_failure_returns_empty(monkeypatch, caplog) -> None:
    def fail_client(**_kwargs):
        raise RuntimeError("PRIVATE_CONSTRUCTOR")

    monkeypatch.setattr(model_discovery.httpx, "AsyncClient", fail_client)
    caplog.set_level(logging.WARNING)

    result = await model_discovery.fetch_models("http://localhost/v1")

    assert result == []
    assert "error_type=RuntimeError" in caplog.text
    assert "cleanup failed" not in caplog.text
    assert "PRIVATE_" not in caplog.text


@pytest.mark.asyncio
async def test_anthropic_fetch_preserves_models_when_close_fails(monkeypatch, caplog) -> None:
    class Models:
        async def list(self, *, limit: int):
            yield type("Model", (), {"id": "model", "lifecycle": "active"})()

    class Client:
        models = Models()

        async def close(self):
            raise OSError("PRIVATE_CLOSE")

    monkeypatch.setattr(model_discovery, "_anthropic_client", lambda *_args: Client())
    caplog.set_level(logging.WARNING)

    result = await model_discovery.fetch_anthropic_models("", "")

    assert result == ["model"]
    assert "cleanup failed" in caplog.text
    assert "PRIVATE_" not in caplog.text


@pytest.mark.asyncio
async def test_llm_lib_fetch_preserves_models_when_close_fails(monkeypatch, caplog) -> None:
    class Client:
        async def aclose(self):
            raise OSError("PRIVATE_CLOSE")

    async def list_models(_client):
        return ["model"]

    monkeypatch.setattr(model_discovery, "_llm_lib_discovery_client", lambda *_args: Client())
    monkeypatch.setattr(model_discovery, "_list_llm_lib_models", list_models)
    caplog.set_level(logging.WARNING)

    result = await model_discovery.fetch_llm_lib_models("ollama", "http://localhost")

    assert result == ["model"]
    assert "cleanup failed" in caplog.text
    assert "PRIVATE_" not in caplog.text
