"""provider="mock": a canned reply streamed word by word, for tests and UI work with no LLM server."""

from __future__ import annotations

from llm_lib.types import Event, Request, text_of

WORD_DELAY_S = 0.02


def mock_reply(req: Request) -> str:
    if req.is_text:
        said = f"a {len(req.prompt or '')}-character text-completion prompt"
    else:
        said = next((text_of(m.get("content")) for m in reversed(req.messages or []) if m.get("role") == "user"), "")
    return f'You said: "{said}". This is a mock reply streamed word by word.'


def mock_events(req: Request) -> list[Event]:
    words = mock_reply(req).split(" ")
    events = [Event("content", w + (" " if i < len(words) - 1 else "")) for i, w in enumerate(words)]
    return [*events, Event("done", finish_reason="stop")]
