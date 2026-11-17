"""Claude's Messages API through the official `anthropic` SDK (pip install llm-lib[anthropic]).

Anthropic's API is a different shape end to end -- the system prompt is a top-level `system`
parameter, tool calls are `tool_use` content blocks, tool results are `tool_result` blocks inside
the next user message, and streaming is typed events -- so this module translates OpenAI-shaped
messages and tools going out, and the SDK's events into llm_lib Events coming back.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from llm_lib.errors import LLMError, LLMRefusal, LLMTimeout, LLMUnavailable, status_error
from llm_lib.settings import Settings
from llm_lib.types import Event, Message, Request, Tool, ToolCall, Usage, text_of

DEFAULT_MAX_TOKENS = 64000  # streamed, so a large cap costs nothing until it's used

# Server-side refusal fallbacks (`fallbacks: "default"`, routed by refusal category).
FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Model families that accept output_config.effort.
EFFORT_PREFIXES = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-5",
    "claude-opus-4-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
)
# Model families that reject temperature / top_p / top_k with a 400.
NO_SAMPLING_PREFIXES = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-opus-4-7",
    "claude-opus-4-8",
)


def split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    """The leading system message(s) as Anthropic's top-level `system`, and the rest."""
    system, rest = [], []
    for m in messages:
        if m.get("role") == "system" and not rest:
            system.append(text_of(m.get("content")))
        else:
            rest.append(m)
    return "\n\n".join(s for s in system if s), rest


def translate_tools(tools: list[Tool] | None) -> list[dict[str, Any]] | None:
    """OpenAI's {"type": "function", "function": {name, description, parameters}} -> Anthropic's
    {name, description, input_schema}: the same JSON Schema in a different wrapper."""
    if not tools:
        return None
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or fn.get("input_schema") or {"type": "object", "properties": {}},
            }
        )
    return out


def _image_block(url: str) -> dict[str, Any]:
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        media_type = header[len("data:") :].split(";")[0] or "image/png"
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _user_content(content: Any) -> Any:
    if not isinstance(content, list):
        return text_of(content)
    blocks = []
    for part in content:
        if part.get("type") == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if url:
                blocks.append(_image_block(url))
        else:
            blocks.append(part)  # already an Anthropic block (document, image, ...)
    return blocks


def translate_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """OpenAI-shaped history (system already split off) -> Anthropic messages.

    An assistant's `tool_calls` become `tool_use` blocks in that same message, arguments parsed into
    an object. Each `role: "tool"` message becomes a `tool_result` block, and consecutive ones are
    merged into a single user message: Anthropic wants every result of one turn together.
    """
    out: list[dict[str, Any]] = []
    tool_batch_open = False
    for m in messages:
        role = m.get("role")
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            if text := text_of(m.get("content")):
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                args = fn.get("arguments") or "{}"
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                blocks.append({"type": "tool_use", "id": tc["id"], "name": fn.get("name", ""), "input": args})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            tool_batch_open = False
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id"), "content": text_of(m.get("content"))}
            if tool_batch_open:
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
                tool_batch_open = True
        elif role == "user":
            out.append({"role": "user", "content": _user_content(m.get("content"))})
            tool_batch_open = False
        elif role == "system":
            # A mid-conversation system message (operator instruction), which current models accept
            out.append({"role": "system", "content": text_of(m.get("content"))})
            tool_batch_open = False
    return out


def build_kwargs(req: Request, settings: Settings) -> tuple[dict[str, Any], bool]:
    """(keyword arguments for messages.stream, whether to use the beta endpoint for fallbacks)."""
    if req.is_text:
        raise LLMError("the Anthropic provider has no text-completion mode; send messages")
    model = settings.model
    system, rest = split_system(req.messages or [])
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": req.max_tokens or settings.max_tokens or DEFAULT_MAX_TOKENS,
        "messages": translate_messages(rest),
    }
    if system:
        kwargs["system"] = system
    if tools := translate_tools(req.tools):
        kwargs["tools"] = tools
    if not model.startswith(NO_SAMPLING_PREFIXES):
        for name in ("temperature", "top_p", "top_k"):
            value = getattr(req, name)
            value = getattr(settings, name) if value is None else value
            if value is not None:
                kwargs[name] = value
    if req.stop:
        kwargs["stop_sequences"] = req.stop
    output_config: dict[str, Any] = {}
    if settings.effort and model.startswith(EFFORT_PREFIXES):
        output_config["effort"] = settings.effort
    if req.json_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": req.json_schema}
    if output_config:
        kwargs["output_config"] = output_config
    kwargs.update(settings.extra)
    kwargs.update(req.extra)
    beta = model in FALLBACK_MODELS and "fallbacks" not in kwargs
    if beta:
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"
    return kwargs, beta


class _EventMapper:
    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.tools: dict[int, ToolCall] = {}

    def map(self, event: Any) -> list[Event]:
        kind = getattr(event, "type", None)
        if kind == "message_start":
            self.prompt_tokens = getattr(event.message.usage, "input_tokens", 0) or 0
        elif kind == "content_block_start":
            block = event.content_block
            if block.type == "tool_use":
                self.tools[event.index] = ToolCall(id=block.id, name=block.name)
        elif kind == "content_block_delta":
            d = event.delta
            if d.type == "text_delta":
                return [Event("content", d.text)]
            if d.type == "thinking_delta" and d.thinking:
                return [Event("reasoning", d.thinking)]
            if d.type == "input_json_delta" and event.index in self.tools:
                self.tools[event.index].arguments += d.partial_json
        elif kind == "message_delta":
            usage = getattr(event, "usage", None)
            out_tokens = getattr(usage, "output_tokens", 0) or 0
            return [Event("usage", usage=Usage(self.prompt_tokens, out_tokens))]
        return []

    def finish(self, final: Any, model: str) -> list[Event]:
        stop_reason = getattr(final, "stop_reason", None)
        if stop_reason == "refusal":
            category = getattr(getattr(final, "stop_details", None), "category", None)
            raise LLMRefusal(f"{model} declined the request (category: {category})", category=category)
        events = []
        if self.tools:
            calls = [self.tools[i] for i in sorted(self.tools)]
            for call in calls:
                call.arguments = call.arguments or "{}"
            events.append(Event("tool_calls", tool_calls=calls))
        finish = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls", "stop_sequence": "stop"}.get(
            stop_reason or "", stop_reason
        )
        events.append(Event("done", finish_reason=finish))
        return events


def _sdk() -> Any:
    try:
        import anthropic
    except ImportError as exc:
        raise ImportError("provider='anthropic' needs the anthropic SDK: pip install 'llm-lib[anthropic]'") from exc
    return anthropic


def _client_kwargs(settings: Settings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"timeout": settings.resolved_timeout}
    if key := settings.resolved_api_key():
        kwargs["api_key"] = key  # else the SDK finds credentials itself (env, `ant auth login` profile)
    if url := settings.resolved_base_url():
        kwargs["base_url"] = url
    if settings.headers:
        kwargs["default_headers"] = dict(settings.headers)
    return kwargs


def _translate_error(anthropic: Any, exc: Exception, label: str) -> Exception:
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMTimeout(f"{label} didn't reply within the timeout")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMUnavailable(f"cannot reach the Anthropic API: {exc}")
    if isinstance(exc, anthropic.APIStatusError):
        return status_error(label, exc.status_code, getattr(exc, "message", "") or str(exc))
    return LLMError(f"Anthropic client error: {exc}")


class AnthropicBackend:
    """Sync and async streaming over the SDK. Clients are created on first use."""

    def __init__(self, settings: Settings, *, client: Any = None, async_client: Any = None) -> None:
        self.settings = settings
        self._client = client
        self._async_client = async_client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = _sdk().Anthropic(**_client_kwargs(self.settings))
        return self._client

    @property
    def async_client(self) -> Any:
        if self._async_client is None:
            self._async_client = _sdk().AsyncAnthropic(**_client_kwargs(self.settings))
        return self._async_client

    def stream(self, req: Request) -> Iterator[Event]:
        anthropic = _sdk()
        kwargs, beta = build_kwargs(req, self.settings)
        api = self.client.beta.messages if beta else self.client.messages
        mapper = _EventMapper()
        try:
            with api.stream(**kwargs) as stream:
                for event in stream:
                    yield from mapper.map(event)
                final = stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise _translate_error(anthropic, exc, self.settings.label) from exc
        yield from mapper.finish(final, self.settings.model)

    async def astream(self, req: Request) -> AsyncIterator[Event]:
        anthropic = _sdk()
        kwargs, beta = build_kwargs(req, self.settings)
        api = self.async_client.beta.messages if beta else self.async_client.messages
        mapper = _EventMapper()
        try:
            async with api.stream(**kwargs) as stream:
                async for event in stream:
                    for out in mapper.map(event):
                        yield out
                final = await stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise _translate_error(anthropic, exc, self.settings.label) from exc
        for out in mapper.finish(final, self.settings.model):
            yield out

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self.client.models.list())

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.close()
