"""`LLM`: one client for every provider, sync and async.

    llm = LLM(Settings(provider="lmstudio", model="qwen3.8-27b"))
    text = llm.complete("You are terse.", "Name three colours.")         # one (system, user) turn
    reply = llm.chat(messages, tools=tools)                             # -> Completion
    for event in llm.stream(messages, cancel=stop_event): ...            # -> Event
    reply = await llm.achat(messages); async for event in llm.astream(messages): ...

Requests stream internally, always: a slow local model then times out per chunk rather than per
reply, and a cancel takes effect at the next chunk.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import replace
from typing import Any

import httpx2

from llm_lib.errors import (
    LLMCancelled,
    LLMError,
    LLMTimeout,
    LLMUnavailable,
    is_context_exceeded,
    status_error,
)
from llm_lib.jsonutil import has_json
from llm_lib.providers.base import HttpCall, HttpProvider
from llm_lib.providers.mock import mock_events
from llm_lib.providers.ollama import OllamaProvider
from llm_lib.providers.openai import OpenAIProvider, rejects_stream_options
from llm_lib.reasoning import NO_THINK, ThinkFilter
from llm_lib.settings import Settings
from llm_lib.types import Completion, Event, Message, Request

logger = logging.getLogger("llm_lib")
Log = Callable[[str], None]
Cancel = threading.Event | asyncio.Event

_REQUEST_FIELDS = frozenset(Request.__dataclass_fields__) - {"messages", "prompt"}


def _http_timeout(settings: Settings) -> httpx2.Timeout:
    return httpx2.Timeout(settings.resolved_timeout, connect=15.0, write=30.0, pool=15.0)


def _transport_error(exc: Exception, label: str, url: str) -> LLMUnavailable:
    if isinstance(exc, httpx2.ConnectTimeout):
        return LLMUnavailable(f"cannot reach {label} at {url} (connect timed out)")
    if isinstance(exc, httpx2.TimeoutException):
        return LLMTimeout(f"{label} didn't reply in time; a slow model needs a longer timeout (LLM_TIMEOUT, seconds)")
    return LLMUnavailable(f"cannot reach {label} at {url}: {exc or type(exc).__name__}")


def _apply_no_think(messages: list[Message]) -> list[Message]:
    """Qwen3's /no_think soft switch on the last user message."""
    out = [dict(m) for m in messages]
    for m in reversed(out):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            m["content"] = f"{content}\n\n{NO_THINK}"
        elif isinstance(content, list):
            m["content"] = [*content, {"type": "text", "text": f"\n\n{NO_THINK}"}]
        break
    return out


def empty_reason(text: str, reasoning: str, finish_reason: str | None, max_tokens: int | None) -> str:
    """A note for the log line when a reply was cut off or has no usable text (else "")."""
    if finish_reason in ("length", "max_tokens"):
        limit = f"{max_tokens:,}-token" if max_tokens else "length"
        what = "so it may be incomplete" if text else "before any usable text"
        return f" -- cut off at the {limit} limit, {what}"
    if text:
        return ""
    if reasoning.strip():
        return f" -- {len(reasoning):,} chars, all reasoning"
    return " -- empty"


class _RetryBudget:
    """How many more resends one request may make, per kind of failure."""

    def __init__(self, settings: Settings) -> None:
        self.waits = list(settings.resolved_retry_waits)
        self.rate_limits = settings.preset.rate_limit_retries


def _retry_after_seconds(value: str | None) -> float | None:
    """Retry-After in seconds (the HTTP-date form is rare from LLM servers and read as unknown)."""
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None


class _Post:
    """What every stream goes through on the way out: inline <think> blocks split off as reasoning,
    and the log line when the reply ends."""

    def __init__(self, llm: LLM, req: Request) -> None:
        self.llm = llm
        self.req = req
        self.filter = ThinkFilter() if llm.settings.strip_think and llm.settings.kind != "anthropic" else None
        self.started = time.perf_counter()
        self.chars = 0
        self.reasoning = ""
        self.text_seen = False

    def feed(self, event: Event) -> list[Event]:
        if event.type == "content" and self.filter is not None:
            return [self._count(Event(kind, text)) for kind, text in self.filter.feed(event.text)]  # type: ignore[arg-type]
        if event.type == "done":
            out = [self._count(Event(k, t)) for k, t in self.filter.flush()] if self.filter else []  # type: ignore[arg-type]
            self._log_done(event.finish_reason)
            return [*out, event]
        return [self._count(event)]

    def _count(self, event: Event) -> Event:
        if event.type == "content":
            self.chars += len(event.text)
            self.text_seen = self.text_seen or bool(event.text.strip())
        elif event.type == "reasoning":
            self.reasoning += event.text
        return event

    def _log_done(self, finish_reason: str | None) -> None:
        max_tokens = self.req.max_tokens or self.llm.settings.max_tokens
        note = empty_reason("x" if self.text_seen else "", self.reasoning, finish_reason, max_tokens)
        self.llm._log(
            f"<- {self.llm.label} ({self.llm.model}): replied in {time.perf_counter() - self.started:.1f}s "
            f"(~{self.chars:,} chars){note}"
        )


class LLM:
    def __init__(
        self,
        settings: Settings,
        *,
        log: Log | None = None,
        http: httpx2.Client | None = None,
        async_http: httpx2.AsyncClient | None = None,
        transport: httpx2.BaseTransport | None = None,
        async_transport: httpx2.AsyncBaseTransport | None = None,
        anthropic_client: Any = None,
        async_anthropic_client: Any = None,
    ) -> None:
        self.settings = settings
        self.log = log
        self._http = http
        self._async_http = async_http
        self._transport = transport
        self._async_transport = async_transport
        self._include_usage = True  # off for good the first time a server rejects stream_options
        self._load_lock = threading.Lock()
        self._load_checked = False
        self._model_checked = False
        self._dropped_logged: set[str] = set()
        providers: dict[str, HttpProvider] = {"openai": OpenAIProvider(), "ollama": OllamaProvider()}
        self._provider = providers.get(settings.kind)
        self._anthropic = None
        if settings.kind == "anthropic":
            from llm_lib.providers.anthropic import AnthropicBackend

            self._anthropic = AnthropicBackend(settings, client=anthropic_client, async_client=async_anthropic_client)
        if settings.preset.key_required and not settings.resolved_api_key():
            raise LLMError(f"{settings.label} needs an API key: set {' or '.join(settings.preset.key_env)}")

    # ------------------------------------------------------------------ plumbing

    @property
    def label(self) -> str:
        return self.settings.label

    @property
    def model(self) -> str:
        return self.settings.model

    @property
    def http(self) -> httpx2.Client:
        if self._http is None:
            self._http = httpx2.Client(timeout=_http_timeout(self.settings), transport=self._transport)
        return self._http

    @property
    def async_http(self) -> httpx2.AsyncClient:
        if self._async_http is None:
            self._async_http = httpx2.AsyncClient(timeout=_http_timeout(self.settings), transport=self._async_transport)
        return self._async_http

    def _log(self, line: str) -> None:
        logger.info(line)
        if self.log is not None:
            self.log(line)

    def request(self, messages: list[Message] | None = None, *, prompt: str | None = None, **params: Any) -> Request:
        """A Request from call arguments: Request fields by name, anything else into the body (`extra`)."""
        if messages is None and prompt is None:
            raise ValueError("pass messages (chat) or prompt (text completion)")
        fields = {k: v for k, v in params.items() if k in _REQUEST_FIELDS}
        extra = {**fields.pop("extra", {}), **{k: v for k, v in params.items() if k not in _REQUEST_FIELDS}}
        msgs = list(messages) if messages is not None else None
        if msgs is not None and self.settings.no_think:
            msgs = _apply_no_think(msgs)
        return Request(messages=msgs, prompt=prompt, extra=extra, **fields)

    def _prepare(self, req: Request) -> Request:
        s = self.settings
        if req.json_schema is not None and s.kind == "openai" and not s.resolved_structured_output:
            return replace(req, json_schema=None)  # this server only gets the schema through the prompt
        return req

    def _announce(self, req: Request) -> None:
        if req.is_text:
            size = len(req.prompt or "")
        else:
            size = sum(len(str(m.get("content") or "")) for m in req.messages or [])
        self._log(f"-> {self.label} ({self.model}): sending request (~{size:,} chars)")

    def _needs_setup(self) -> bool:
        s = self.settings
        return (not s.model and s.kind == "openai" and not self._model_checked) or (
            s.provider == "lmstudio" and bool(s.load_context) and not self._load_checked
        )

    def _setup(self) -> None:
        """Once, before the first request (requests sent in parallel wait here until it's done):
        - no model set on an OpenAI-protocol server that serves exactly one (colibri, llama.cpp,
          vLLM, mlx-lm, ...): use that one. colibri refuses a request whose `model` isn't its own.
        - LM Studio with load_context: load the model with it unless it's loaded already."""
        if not self._needs_setup():
            return
        with self._load_lock:
            if not self.settings.model and self.settings.kind == "openai" and not self._model_checked:
                # Asked again next time when the server didn't answer: it may be starting up
                self._model_checked = self._resolve_model()
            s = self.settings
            if s.provider == "lmstudio" and s.load_context and not self._load_checked:
                try:
                    from llm_lib.servers.lmstudio import LMStudio

                    LMStudio(
                        s.resolved_base_url() or "", api_key=s.resolved_api_key(), http=self.http, log=self._log
                    ).ensure_loaded(s.model, s.load_context, parallel=s.parallel, cli_args=s.load_args)
                finally:
                    self._load_checked = True

    def _resolve_model(self) -> bool:
        """True once the server has answered (whether or not it serves exactly one model)."""
        try:
            models = self.list_models()
        except LLMUnavailable:
            return False  # the request itself will say the server is down
        except (LLMError, ValueError):
            return True  # it answered, just not with a model list
        if len(models) == 1:
            self.settings = self.settings.with_(model=models[0])
            self._log(f"{self.label} serves one model, {models[0]}; using it")
        return True

    def _log_dropped(self, call: HttpCall) -> None:
        new = [p for p in call.dropped if p not in self._dropped_logged]
        if new:
            self._dropped_logged.update(new)
            self._log(f"{self.label} refuses {', '.join(new)}; left out of its requests")

    def _status_action(
        self, status: int, body: str, retry_after: str | None, budget: _RetryBudget, include_usage: bool
    ) -> str | float:
        """What to do about an HTTP error: "no_usage" (resend without stream_options), a number
        (wait that long and resend), or raise."""
        if include_usage and rejects_stream_options(status, body):
            return "no_usage"
        if is_context_exceeded(body):
            raise status_error(self.label, status, body)
        wait: float | None = None
        if status == 429 and budget.rate_limits > 0:
            # Rate limited, or a local server's queue is full: nothing ran yet, so resending is safe.
            n = self.settings.preset.rate_limit_retries - budget.rate_limits
            budget.rate_limits -= 1
            wait = _retry_after_seconds(retry_after)
            wait = min(60.0, (wait if wait is not None else 1.0) * 2**n)
        elif status >= 500 and budget.waits:
            # A 5xx before any text is often passing: LM Studio answers a bare "Internal Server Error"
            # to a call that comes in while it JIT-loads the model for another one.
            wait = float(budget.waits.pop(0))
        if wait is None:
            raise status_error(self.label, status, body)
        self._log(f"<- {self.label} ({self.model}): HTTP {status}; sending it again in {wait:g}s")
        return wait

    @staticmethod
    def _cancelled(cancel: Cancel | None) -> bool:
        return cancel is not None and cancel.is_set()

    # ------------------------------------------------------------------ sync

    def _http_events(self, req: Request) -> Iterator[Event]:
        assert self._provider is not None
        budget = _RetryBudget(self.settings)
        while True:
            include_usage = self._include_usage and self.settings.kind == "openai"
            call: HttpCall = self._provider.build(req, self.settings, include_usage=include_usage)
            parser = self._provider.parser(req)
            self._log_dropped(call)
            action: str | float | None = None
            try:
                with self.http.stream(call.method, call.url, json=call.json, headers=call.headers) as resp:
                    if resp.status_code >= 400:
                        body = resp.read().decode("utf-8", "replace")
                        if resp.status_code == 404 and self.settings.kind == "ollama" and "not found" in body:
                            raise LLMError(f"Ollama has no model {self.model!r}; run `ollama pull {self.model}`")
                        action = self._status_action(
                            resp.status_code, body, resp.headers.get("retry-after"), budget, include_usage
                        )
                    else:
                        for line in resp.iter_lines():
                            yield from parser.feed(line)
                        yield from parser.finish()
                        return
            except httpx2.TransportError as exc:
                raise _transport_error(exc, self.label, call.url) from exc
            if action == "no_usage":
                self._include_usage = False
            elif isinstance(action, float):
                time.sleep(action)

    def _raw_events(self, req: Request) -> Iterator[Event]:
        if self.settings.kind == "mock":
            delay = float(self.settings.extra.get("delay", 0.0))
            for event in mock_events(req):
                if delay:
                    time.sleep(delay)
                yield event
        elif self._anthropic is not None:
            yield from self._anthropic.stream(req)
        else:
            yield from self._http_events(req)

    def stream(
        self,
        messages: list[Message] | None = None,
        *,
        prompt: str | None = None,
        cancel: Cancel | None = None,
        **params: Any,
    ) -> Iterator[Event]:
        """Stream one reply as Events. `cancel` (a threading.Event) set mid-reply raises LLMCancelled
        and closes the connection, which stops generation on Ollama, LM Studio and llama.cpp too."""
        yield from self._stream(self.request(messages, prompt=prompt, **params), cancel)

    def _stream(self, req: Request, cancel: Cancel | None) -> Iterator[Event]:
        req = self._prepare(req)
        self._setup()
        self._announce(req)
        post = _Post(self, req)
        events = self._raw_events(req)
        try:
            for event in events:
                if self._cancelled(cancel):
                    raise LLMCancelled("interrupted")
                yield from post.feed(event)
        finally:
            events.close()

    def chat(
        self,
        messages: list[Message] | None = None,
        *,
        prompt: str | None = None,
        cancel: Cancel | None = None,
        on_event: Callable[[Event], None] | None = None,
        **params: Any,
    ) -> Completion:
        """One whole reply. With `json_schema` on a server that enforces it, a reply that comes back
        empty or not JSON is asked for once more with the schema in the prompt only -- seen with LM
        Studio serving GGUF Qwen3, which accepts response_format but answers empty."""
        req = self.request(messages, prompt=prompt, **params)
        result = self._collect(self._stream(req, cancel), on_event)
        if self._schema_failed(req, result):
            self._log(
                f"<- {self.label} ({self.model}): {'empty' if not result.text.strip() else 'not JSON'} "
                "with response_format=json_schema; retrying without it"
            )
            result = self._collect(self._stream(replace(req, json_schema=None), cancel), on_event)
        return result

    def complete(self, system: str, prompt: str, *, json_schema: dict[str, Any] | None = None, **params: Any) -> str:
        """The reply text to a single (system, user) turn, reasoning removed."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        return self.chat(messages, json_schema=json_schema, **params).text.strip()

    def complete_text(self, prompt: str, **params: Any) -> str:
        """A raw text completion of a fully formatted prompt (/completions)."""
        return self.chat(prompt=prompt, **params).text

    @staticmethod
    def _collect(events: Iterator[Event], on_event: Callable[[Event], None] | None) -> Completion:
        result = Completion()
        for event in events:
            if on_event is not None:
                on_event(event)
            result.add(event)
        return result

    def _schema_failed(self, req: Request, result: Completion) -> bool:
        if req.json_schema is None or self.settings.kind != "openai" or not self.settings.resolved_structured_output:
            return False
        if not result.text.strip():
            return True
        return not result.truncated and not has_json(result.text)

    # ------------------------------------------------------------------ async

    async def _ahttp_events(self, req: Request) -> AsyncIterator[Event]:
        assert self._provider is not None
        budget = _RetryBudget(self.settings)
        while True:
            include_usage = self._include_usage and self.settings.kind == "openai"
            call = self._provider.build(req, self.settings, include_usage=include_usage)
            parser = self._provider.parser(req)
            self._log_dropped(call)
            action: str | float | None = None
            try:
                async with self.async_http.stream(call.method, call.url, json=call.json, headers=call.headers) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", "replace")
                        if resp.status_code == 404 and self.settings.kind == "ollama" and "not found" in body:
                            raise LLMError(f"Ollama has no model {self.model!r}; run `ollama pull {self.model}`")
                        action = self._status_action(
                            resp.status_code, body, resp.headers.get("retry-after"), budget, include_usage
                        )
                    else:
                        async for line in resp.aiter_lines():
                            for event in parser.feed(line):
                                yield event
                        for event in parser.finish():
                            yield event
                        return
            except httpx2.TransportError as exc:
                raise _transport_error(exc, self.label, call.url) from exc
            if action == "no_usage":
                self._include_usage = False
            elif isinstance(action, float):
                await asyncio.sleep(action)

    async def _araw_events(self, req: Request) -> AsyncIterator[Event]:
        if self.settings.kind == "mock":
            delay = float(self.settings.extra.get("delay", 0.0))
            for event in mock_events(req):
                await asyncio.sleep(delay)
                yield event
        elif self._anthropic is not None:
            async for event in self._anthropic.astream(req):
                yield event
        else:
            async for event in self._ahttp_events(req):
                yield event

    async def astream(
        self,
        messages: list[Message] | None = None,
        *,
        prompt: str | None = None,
        cancel: Cancel | None = None,
        **params: Any,
    ) -> AsyncIterator[Event]:
        """Async stream(). Cancelling the task stops it too."""
        async for event in self._astream(self.request(messages, prompt=prompt, **params), cancel):
            yield event

    async def _astream(self, req: Request, cancel: Cancel | None) -> AsyncIterator[Event]:
        req = self._prepare(req)
        if self._needs_setup():
            await asyncio.to_thread(self._setup)
        self._announce(req)
        post = _Post(self, req)
        events = self._araw_events(req)
        try:
            async for event in events:
                if self._cancelled(cancel):
                    raise LLMCancelled("interrupted")
                for out in post.feed(event):
                    yield out
        finally:
            await events.aclose()

    async def achat(
        self,
        messages: list[Message] | None = None,
        *,
        prompt: str | None = None,
        cancel: Cancel | None = None,
        on_event: Callable[[Event], None] | None = None,
        **params: Any,
    ) -> Completion:
        req = self.request(messages, prompt=prompt, **params)
        result = await self._acollect(self._astream(req, cancel), on_event)
        if self._schema_failed(req, result):
            self._log(
                f"<- {self.label} ({self.model}): {'empty' if not result.text.strip() else 'not JSON'} "
                "with response_format=json_schema; retrying without it"
            )
            result = await self._acollect(self._astream(replace(req, json_schema=None), cancel), on_event)
        return result

    async def acomplete(
        self, system: str, prompt: str, *, json_schema: dict[str, Any] | None = None, **params: Any
    ) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        return (await self.achat(messages, json_schema=json_schema, **params)).text.strip()

    @staticmethod
    async def _acollect(events: AsyncIterator[Event], on_event: Callable[[Event], None] | None) -> Completion:
        result = Completion()
        async for event in events:
            if on_event is not None:
                on_event(event)
            result.add(event)
        return result

    # ------------------------------------------------------------------ the server

    def list_models(self) -> list[str]:
        """Model ids the server offers."""
        s = self.settings
        if s.kind == "mock":
            return ["mock"]
        if self._anthropic is not None:
            return self._anthropic.list_models()
        base = s.resolved_base_url()
        headers = dict(s.headers)
        if key := s.resolved_api_key():
            headers["Authorization"] = f"Bearer {key}"
        try:
            if s.kind == "ollama":
                resp = self.http.get(f"{base}/api/tags", headers=headers, timeout=15.0)
                if resp.status_code >= 400:
                    raise status_error(self.label, resp.status_code, resp.text)
                return sorted(m.get("name", "") for m in resp.json().get("models", []))
            resp = self.http.get(f"{base}/models", headers=headers, timeout=15.0)
        except httpx2.TransportError as exc:
            raise _transport_error(exc, self.label, str(base)) from exc
        if resp.status_code >= 400:
            raise status_error(self.label, resp.status_code, resp.text)
        data = resp.json()
        items = data.get("data", data) if isinstance(data, dict) else data
        return sorted(m["id"] for m in items if isinstance(m, dict) and "id" in m)

    def available(self) -> bool:
        """The server is up and offers the configured model (cheap: one GET, for a health check)."""
        if self.settings.kind in ("mock", "anthropic"):
            return True
        try:
            models = self.list_models()
        except (LLMError, ValueError):
            return False
        if not self.model:
            return True
        return self.model in models or f"{self.model}:latest" in models

    def unload(self) -> None:
        """Free the model's memory on the server, where the backend can: Ollama evicts it right away
        (keep_alive=0), LM Studio unloads its LLMs. Others: nothing to do."""
        s = self.settings
        if s.kind == "ollama":
            try:
                self.http.post(f"{s.resolved_base_url()}/api/generate", json={"model": s.model, "keep_alive": 0})
            except httpx2.TransportError:
                pass
        elif s.provider == "lmstudio":
            from llm_lib.servers.lmstudio import LMStudio

            try:
                LMStudio(s.resolved_base_url() or "", api_key=s.resolved_api_key(), http=self.http).unload_all()
            except LLMError as exc:
                self._log(f"couldn't unload LM Studio's models: {exc}")
            self._load_checked = False  # the next request loads it again, with load_context

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
        if self._anthropic is not None:
            self._anthropic.close()

    async def aclose(self) -> None:
        if self._async_http is not None:
            await self._async_http.aclose()
        if self._anthropic is not None:
            await self._anthropic.aclose()
        self.close()

    def __enter__(self) -> LLM:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    async def __aenter__(self) -> LLM:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def create_client(settings: Settings | None = None, *, log: Log | None = None, **kwargs: Any) -> LLM:
    """An LLM for `settings` (default: Settings.from_env())."""
    return LLM(settings or Settings.from_env(), log=log, **kwargs)
