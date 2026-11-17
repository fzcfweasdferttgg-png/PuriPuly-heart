"""The shapes passed in and out of every provider.

Messages are OpenAI Chat Completions dicts -- {"role", "content", "tool_calls", "tool_call_id"} --
because that's what every app already builds; providers with another shape (Ollama, Anthropic)
translate at the edge. Tools are OpenAI's {"type": "function", "function": {...}} too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

Message = dict[str, Any]
Tool = dict[str, Any]

EventType = Literal["content", "reasoning", "tool_calls", "usage", "done"]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str = ""  # the JSON string, as the model wrote it

    def args(self) -> dict[str, Any]:
        """The arguments parsed; {} when the model wrote something that isn't a JSON object."""
        try:
            value = json.loads(self.arguments or "{}")
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def to_message(self) -> dict[str, Any]:
        """As an entry of an assistant message's `tool_calls`."""
        return {"id": self.id, "type": "function", "function": {"name": self.name, "arguments": self.arguments}}


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(slots=True)
class Event:
    """One item of a stream.

    content     visible reply text
    reasoning   the model's thinking (a reasoning_content field, an inline <think> block, Anthropic thinking)
    tool_calls  every tool call of the reply, complete, once, after the text
    usage       token counts, when the server reports them
    done        last event; finish_reason is "stop", "length", "tool_calls", ...
    """

    type: EventType
    text: str = ""
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None
    finish_reason: str | None = None


@dataclass(slots=True)
class Completion:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage | None = None

    @property
    def truncated(self) -> bool:
        return self.finish_reason in ("length", "max_tokens")

    def add(self, event: Event) -> None:
        if event.type == "content":
            self.text += event.text
        elif event.type == "reasoning":
            self.reasoning += event.text
        elif event.type == "tool_calls":
            self.tool_calls.extend(event.tool_calls or [])
        elif event.type == "usage":
            self.usage = event.usage
        elif event.type == "done":
            self.finish_reason = event.finish_reason

    def to_message(self) -> Message:
        """The assistant message to append to the history."""
        message: Message = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            message["tool_calls"] = [c.to_message() for c in self.tool_calls]
        return message


@dataclass(slots=True)
class Request:
    """One call. `messages` for chat; `prompt` instead for a raw text completion (OpenAI-compatible
    /completions -- a fully formatted prompt, as roleplay frontends send). Sampling fields left None
    fall back to the client's Settings; `extra` is merged into the request body as is."""

    messages: list[Message] | None = None
    prompt: str | None = None
    tools: list[Tool] | None = None
    json_schema: dict[str, Any] | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    max_tokens: int | None = None
    frequency_penalty: float | None = None
    repetition_penalty: float | None = None
    stop: list[str] | None = None
    seed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_text(self) -> bool:
        return self.prompt is not None


def text_of(content: Any) -> str:
    """A message's content as plain text (OpenAI content parts joined, images left out)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
