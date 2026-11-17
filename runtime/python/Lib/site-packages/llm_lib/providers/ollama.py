"""Ollama's native /api/chat, streamed as NDJSON.

Used instead of Ollama's OpenAI-compatible endpoint because only the native API accepts `num_ctx`;
without it Ollama silently truncates prompts to its small default window. It also takes `think`
(Qwen3 ignores the /no_think soft switch on newer Ollama) and a JSON schema as `format`.
"""

from __future__ import annotations

import json
from typing import Any

from llm_lib.errors import LLMError
from llm_lib.providers.base import HttpCall, ToolCallBuffer
from llm_lib.settings import Settings
from llm_lib.types import Event, Message, Request, Usage, text_of


def to_ollama_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """OpenAI-shaped history -> Ollama's: tool-call arguments as objects rather than JSON strings, tool
    results named after their call, images as bare base64 in `images`."""
    names: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        msg: dict[str, Any] = {"role": role, "content": text_of(m.get("content"))}
        content = m.get("content")
        if isinstance(content, list):
            images = []
            for part in content:
                url = (part.get("image_url") or {}).get("url", "") if part.get("type") == "image_url" else ""
                if url.startswith("data:"):
                    images.append(url.partition(",")[2])
            if images:
                msg["images"] = images
        if m.get("images"):
            msg["images"] = list(m["images"])
        if m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except ValueError:
                        args = {}
                if tc.get("id"):
                    names[tc["id"]] = fn.get("name", "")
                calls.append({"function": {"name": fn.get("name", ""), "arguments": args}})
            msg["tool_calls"] = calls
        if role == "tool":
            name = m.get("tool_name") or m.get("name") or names.get(m.get("tool_call_id", ""), "")
            if name:
                msg["tool_name"] = name
        if m.get("thinking"):
            msg["thinking"] = m["thinking"]
        out.append(msg)
    return out


class OllamaProvider:
    def build(self, req: Request, settings: Settings, *, include_usage: bool = True) -> HttpCall:
        base = settings.resolved_base_url()

        def pick(name: str) -> Any:
            value = getattr(req, name)
            return getattr(settings, name) if value is None else value

        options: dict[str, Any] = {}
        if settings.context:
            options["num_ctx"] = settings.context
        for ours, theirs in (
            ("temperature", "temperature"),
            ("top_p", "top_p"),
            ("top_k", "top_k"),
            ("min_p", "min_p"),
            ("max_tokens", "num_predict"),
            ("frequency_penalty", "frequency_penalty"),
            ("repetition_penalty", "repeat_penalty"),
        ):
            if (value := pick(ours)) is not None:
                options[theirs] = value
        if req.stop:
            options["stop"] = req.stop
        if req.seed is not None:
            options["seed"] = req.seed

        body: dict[str, Any] = {"model": settings.model, "stream": True, "options": options}
        if req.is_text:
            url = f"{base}/api/generate"
            body["prompt"] = req.prompt
            body["raw"] = True  # the prompt is already formatted
        else:
            url = f"{base}/api/chat"
            body["messages"] = to_ollama_messages(req.messages or [])
            if req.tools:
                body["tools"] = req.tools
        if req.json_schema is not None:
            body["format"] = req.json_schema
        if settings.thinking_off:
            body["think"] = False
        elif settings.effort in ("low", "medium", "high"):
            body["think"] = settings.effort  # gpt-oss takes a level
        elif settings.thinking is not None:
            body["think"] = settings.thinking
        body.update(settings.extra)
        body.update(req.extra)
        return HttpCall(url=url, json=body, headers={"Content-Type": "application/json", **settings.headers})

    def parser(self, req: Request) -> NDJSONParser:
        return NDJSONParser(text_mode=req.is_text)


class NDJSONParser:
    def __init__(self, *, text_mode: bool = False) -> None:
        self.text_mode = text_mode
        self.calls = ToolCallBuffer()
        self.finish_reason: str | None = None

    def feed(self, line: str) -> list[Event]:
        line = line.strip()
        if not line:
            return []
        try:
            part = json.loads(line)
        except json.JSONDecodeError:
            return []
        if part.get("error"):
            raise LLMError(f"Ollama: {part['error']}")
        events: list[Event] = []
        if self.text_mode:
            if thinking := part.get("thinking"):
                events.append(Event("reasoning", thinking))
            if text := part.get("response"):
                events.append(Event("content", text))
        else:
            message = part.get("message") or {}
            if thinking := message.get("thinking"):
                events.append(Event("reasoning", thinking))
            if content := message.get("content"):
                events.append(Event("content", content))
            for tc in message.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                self.calls.add_whole(
                    fn.get("name", ""), args if isinstance(args, str) else json.dumps(args or {}), tc.get("id")
                )
        if part.get("done"):
            self.finish_reason = part.get("done_reason") or "stop"
            events.append(Event("usage", usage=Usage(part.get("prompt_eval_count") or 0, part.get("eval_count") or 0)))
        return events

    def finish(self) -> list[Event]:
        events = []
        if calls := self.calls.take():
            events.append(Event("tool_calls", tool_calls=calls))
            if self.finish_reason == "stop":
                self.finish_reason = "tool_calls"
        events.append(Event("done", finish_reason=self.finish_reason))
        return events
