from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx2
import pytest
from llm_lib import LLM

from puripuly_heart.core.llm import model_discovery
from puripuly_heart.providers.llm.local_llm_lib import LLMLibLocalLLMProvider


def _translation(provider: LLMLibLocalLLMProvider):
    return provider.translate(
        utterance_id=uuid4(),
        text="source",
        system_prompt="translate",
        source_language="en",
        target_language="ru",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("backend", "environment_key"),
    [("openai-compatible", "OPENAI_API_KEY"), ("colibri", "COLI_API_KEY")],
)
async def test_blank_generic_key_does_not_leak_environment_or_preset_key(
    monkeypatch,
    backend: str,
    environment_key: str,
) -> None:
    monkeypatch.setenv(environment_key, "test-only-environment-secret")
    captured: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        chunk = {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}
        return httpx2.Response(
            200,
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    provider = LLMLibLocalLLMProvider(
        backend=backend,
        base_url="http://other-server.invalid/v1",
        model="test-model",
        api_key="",
    )
    provider.client = LLM(
        provider._settings(),
        async_transport=httpx2.MockTransport(handler),
    )

    await _translation(provider)
    await provider.close()

    assert captured[0].headers.get("authorization") is None


@pytest.mark.asyncio
async def test_close_cancels_in_flight_llm_lib_translation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        started.set()
        await release.wait()
        chunk = {"choices": [{"delta": {"content": "late"}, "finish_reason": "stop"}]}
        return httpx2.Response(
            200,
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    client = LLM(
        LLMLibLocalLLMProvider(
            backend="llamacpp",
            base_url="http://127.0.0.1:8080/v1",
            model="test-model",
        )._settings(),
        async_transport=httpx2.MockTransport(handler),
    )
    provider = LLMLibLocalLLMProvider(
        backend="llamacpp",
        base_url="http://127.0.0.1:8080/v1",
        model="test-model",
        client=client,
    )
    task = asyncio.create_task(_translation(provider))
    await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(provider.close(), timeout=1)
    await asyncio.sleep(0)

    try:
        assert task.done() and task.cancelled()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [False, True])
async def test_close_failure_can_be_retried(owned: bool) -> None:
    class Client:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("temporary close failure")

    client = Client()
    provider = LLMLibLocalLLMProvider(
        backend="llamacpp",
        base_url="http://127.0.0.1:8080/v1",
        model="test-model",
    )
    if owned:
        provider._internal_client = client
    else:
        provider.client = client

    with pytest.raises(RuntimeError, match="temporary close failure"):
        await provider.close()
    await provider.close()

    assert client.close_calls == 2


@pytest.mark.asyncio
async def test_cancelled_discovery_closes_after_async_request_finishes(monkeypatch) -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    class Client:
        def __init__(self) -> None:
            self.settings = model_discovery.ExplicitLLMLibSettings(provider="ollama")
            self.async_http = self
            self.closed_while_running = False

        async def get(self, _url: str, *, headers: dict) -> httpx2.Response:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
            return httpx2.Response(200, json={"models": []})

        async def aclose(self) -> None:
            self.closed_while_running = not finished.is_set()

    client = Client()
    monkeypatch.setattr(model_discovery, "_llm_lib_discovery_client", lambda *_args: client)
    task = asyncio.create_task(
        model_discovery.fetch_llm_lib_models("ollama", "http://127.0.0.1:11434")
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set() and not client.closed_while_running


@pytest.mark.asyncio
async def test_secret_change_rebuilds_non_local_primary() -> None:
    from puripuly_heart.ui.app import TranslatorApp

    class Application:
        calls: list[bool] = []

        def local_llm_selected(self) -> bool:
            return False

        async def apply_providers(self, *, force_rebuild_llm: bool) -> None:
            self.calls.append(force_rebuild_llm)

    app = TranslatorApp.__new__(TranslatorApp)
    app._ui_application = Application()
    queued = []
    app._queue_settings_mutation_task = queued.append

    app._on_local_llm_secret_changed()
    assert len(queued) == 1
    await queued[0]()

    assert app.application.calls == [True]
