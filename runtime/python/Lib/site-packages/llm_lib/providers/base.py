"""The sans-IO provider contract: a provider turns a Request into an HTTP call and turns the reply's
lines into Events, but never does I/O itself -- so one implementation serves the sync and the async
client alike."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from llm_lib.settings import Settings
from llm_lib.types import Event, Request, ToolCall


@dataclass(slots=True)
class HttpCall:
    url: str
    json: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    method: str = "POST"
    dropped: list[str] = field(default_factory=list)  # fields left out because this server refuses them


class StreamParser(Protocol):
    def feed(self, line: str) -> list[Event]:
        """Events for one line of the response body (an SSE `data:` line, an NDJSON object, ...)."""
        ...

    def finish(self) -> list[Event]:
        """Events once the body has ended: buffered tool calls, the `done` event."""
        ...


class HttpProvider(Protocol):
    def build(self, req: Request, settings: Settings, *, include_usage: bool = True) -> HttpCall: ...

    def parser(self, req: Request) -> StreamParser: ...


class ToolCallBuffer:
    """Tool calls arrive in fragments keyed by index (id and name first, then pieces of the
    arguments JSON); this puts them back together."""

    def __init__(self) -> None:
        self._parts: dict[int, ToolCall] = {}

    def add(self, index: int, *, id: str | None = None, name: str | None = None, arguments: str | None = None) -> None:
        call = self._parts.setdefault(index, ToolCall(id="", name=""))
        if id:
            call.id = id
        if name:
            call.name += name
        if arguments:
            call.arguments += arguments

    def add_whole(self, name: str, arguments: str, id: str | None = None) -> None:
        index = len(self._parts)
        self._parts[index] = ToolCall(id=id or "", name=name, arguments=arguments)

    def take(self) -> list[ToolCall]:
        calls = [self._parts[i] for i in sorted(self._parts)]
        for n, call in enumerate(calls):
            call.id = call.id or f"call_{n}"
        self._parts.clear()
        return calls
