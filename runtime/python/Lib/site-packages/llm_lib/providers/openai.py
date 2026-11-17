"""OpenAI Chat Completions (and legacy /completions) over server-sent events: OpenAI itself and every
server that copies it -- LM Studio, llama.cpp, vLLM, OpenRouter, colibri, KoboldCpp, ..."""

from __future__ import annotations

import json
from typing import Any

from llm_lib.errors import LLMError
from llm_lib.providers.base import HttpCall, ToolCallBuffer
from llm_lib.settings import Settings
from llm_lib.types import Event, Request, Usage

MAX_CHAT_STOPS = 4  # OpenAI rejects more


def sampling(req: Request, settings: Settings) -> dict[str, Any]:
    """The sampling fields that are set, the request's over the settings'. Non-standard ones (top_k,
    min_p, repetition penalty) only go out when set, since OpenAI itself rejects them."""

    def pick(name: str) -> Any:
        value = getattr(req, name)
        return getattr(settings, name) if value is None else value

    out: dict[str, Any] = {}
    for name in ("temperature", "top_p", "top_k", "min_p", "max_tokens", "frequency_penalty", "seed"):
        value = pick(name) if name != "seed" else req.seed
        if value is not None:
            out[name] = value
    rep = pick("repetition_penalty")
    if rep is not None and rep != 1:
        out["repeat_penalty"] = rep  # llama.cpp / LM Studio
        out["repetition_penalty"] = rep  # vLLM / text-generation-webui
    return out


class OpenAIProvider:
    def build(self, req: Request, settings: Settings, *, include_usage: bool = True) -> HttpCall:
        base = settings.resolved_base_url()
        if not base:
            raise LLMError(f"no base URL configured for the {settings.provider} provider")
        body: dict[str, Any] = {**sampling(req, settings), "stream": True}
        if settings.model:
            body["model"] = settings.model
        if req.is_text:
            url = f"{base}/completions"
            body["prompt"] = req.prompt
            if req.stop:
                body["stop"] = req.stop
        else:
            url = f"{base}/chat/completions"
            body["messages"] = req.messages or []
            if req.stop:
                body["stop"] = req.stop[:MAX_CHAT_STOPS]
            if req.tools:
                body["tools"] = req.tools
            if req.json_schema is not None:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": req.json_schema},
                }
        if include_usage:
            # most servers only put token counts in a stream when asked
            body["stream_options"] = {"include_usage": True}
        body.update(reasoning_fields(settings))
        body.update(settings.extra)
        body.update(req.extra)
        dropped = sorted(k for k in settings.preset.drop_params if body.pop(k, None) is not None)
        headers = {"Content-Type": "application/json", **settings.headers}
        if key := settings.resolved_api_key():
            headers["Authorization"] = f"Bearer {key}"
        return HttpCall(url=url, json=body, headers=headers, dropped=dropped)

    def parser(self, req: Request) -> SSEParser:
        return SSEParser(text_mode=req.is_text)


def reasoning_fields(settings: Settings) -> dict[str, Any]:
    """Settings.thinking / no_think / effort in the form this server takes."""
    out: dict[str, Any] = {}
    thinking = False if settings.thinking_off else settings.thinking
    if settings.effort:
        out["reasoning_effort"] = settings.effort
    if thinking is not None:
        param = settings.preset.thinking_param
        if param == "enable_thinking":
            out["enable_thinking"] = thinking
            if not thinking and "reasoning_effort" in out:
                out["reasoning_effort"] = "none"  # colibri: any other effort turns reasoning back on
        elif param == "chat_template_kwargs":
            out["chat_template_kwargs"] = {"enable_thinking": thinking}
    return out


def rejects_stream_options(status_code: int, body: str) -> bool:
    """A 400 (422 on some) naming stream_options: a server that validates unknown fields strictly."""
    return status_code in (400, 422) and "stream_options" in body


class SSEParser:
    def __init__(self, *, text_mode: bool = False) -> None:
        self.text_mode = text_mode
        self.calls = ToolCallBuffer()
        self.finish_reason: str | None = None
        self.done = False

    def feed(self, line: str) -> list[Event]:
        line = line.strip()
        if not line.startswith("data:") or self.done:
            return []
        data = line[5:].strip()
        if data == "[DONE]":
            self.done = True
            return []
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return []
        if chunk.get("error"):
            error = chunk["error"]
            raise LLMError(str(error.get("message", error) if isinstance(error, dict) else error))
        events: list[Event] = []
        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            if self.text_mode:
                if text := choice.get("text"):
                    events.append(Event("content", text))
                continue
            delta = choice.get("delta") or {}
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                events.append(Event("reasoning", reasoning))
            if content := delta.get("content"):
                events.append(Event("content", content))
            for tc in delta.get("tool_calls") or []:
                fn = tc.get("function") or {}
                self.calls.add(tc.get("index", 0), id=tc.get("id"), name=fn.get("name"), arguments=fn.get("arguments"))
        if usage := chunk.get("usage"):
            counts = Usage(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
            events.append(Event("usage", usage=counts))
        return events

    def finish(self) -> list[Event]:
        events = []
        if calls := self.calls.take():
            events.append(Event("tool_calls", tool_calls=calls))
        events.append(Event("done", finish_reason=self.finish_reason))
        return events
