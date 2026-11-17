"""A second LLM to use while the first can't be reached (its machine is off, say)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from llm_lib.client import LLM, Cancel
from llm_lib.errors import LLMCancelled, LLMUnavailable
from llm_lib.types import Completion, Event, Message

FAILOVER_RETRY_SECONDS = 60.0


class FailoverLLM:
    """Sends to `primary`; when it can't be reached before any reply text has come, sends to
    `fallback` instead -- and keeps doing so for `retry_after` seconds, so each call doesn't wait out
    the connect timeout first. Once text has streamed, a failure is raised: asking the fallback then
    would write the reply twice.

    `primary_off()` (optional) says the primary isn't to be asked right now -- unloaded on purpose,
    paused for a GPU-heavy job -- and sends everything to the fallback until it returns False.
    """

    def __init__(
        self,
        primary: LLM,
        fallback: LLM,
        *,
        retry_after: float = FAILOVER_RETRY_SECONDS,
        primary_off: Callable[[], bool] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.retry_after = retry_after
        self.primary_off = primary_off or (lambda: False)
        self.log = log or primary.log or (lambda _: None)
        self.primary_down_until = 0.0

    @property
    def on_fallback(self) -> bool:
        return self.primary_off() or time.monotonic() < self.primary_down_until

    @property
    def active(self) -> LLM:
        """The LLM calls go to right now."""
        return self.fallback if self.on_fallback else self.primary

    def _mark_down(self, exc: Exception) -> None:
        self.primary_down_until = time.monotonic() + self.retry_after
        self.log(
            f"{self.primary.label} didn't answer ({exc}); using {self.fallback.label} "
            f"for the next {self.retry_after:g}s"
        )

    def stream(
        self, messages: list[Message] | None = None, *, cancel: Cancel | None = None, **params: Any
    ) -> Iterator[Event]:
        if self.on_fallback:
            yield from self.fallback.stream(messages, cancel=cancel, **params)
            return
        started = False
        try:
            for event in self.primary.stream(messages, cancel=cancel, **params):
                started = started or event.type in ("content", "reasoning", "tool_calls")
                yield event
            return
        except LLMCancelled:
            raise
        except LLMUnavailable as exc:
            if started:
                raise
            self._mark_down(exc)
        yield from self.fallback.stream(messages, cancel=cancel, **params)

    async def astream(
        self, messages: list[Message] | None = None, *, cancel: Cancel | None = None, **params: Any
    ) -> AsyncIterator[Event]:
        if self.on_fallback:
            async for event in self.fallback.astream(messages, cancel=cancel, **params):
                yield event
            return
        started = False
        try:
            async for event in self.primary.astream(messages, cancel=cancel, **params):
                started = started or event.type in ("content", "reasoning", "tool_calls")
                yield event
            return
        except LLMCancelled:
            raise
        except LLMUnavailable as exc:
            if started:
                raise
            self._mark_down(exc)
        async for event in self.fallback.astream(messages, cancel=cancel, **params):
            yield event

    def chat(self, messages: list[Message] | None = None, **params: Any) -> Completion:
        result = Completion()
        for event in self.stream(messages, **params):
            result.add(event)
        return result

    async def achat(self, messages: list[Message] | None = None, **params: Any) -> Completion:
        result = Completion()
        async for event in self.astream(messages, **params):
            result.add(event)
        return result

    def complete(self, system: str, prompt: str, **params: Any) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        return self.chat(messages, **params).text.strip()
