"""What can go wrong talking to an LLM, as one hierarchy every provider raises.

LLMError                 anything the user may need to fix
├── LLMUnavailable       the server can't be reached (down, restarting, wrong URL)
│   ├── LLMTimeout       it was reached but didn't reply in time
│   └── LLMCancelled     the caller's cancel event was set mid-reply
├── LLMStatusError       the server answered with an HTTP error
│   └── LLMContextExceeded   ... because the prompt doesn't fit the context window
└── LLMRefusal           the model declined (Anthropic's stop_reason "refusal")
"""

from __future__ import annotations

_CONTEXT_MARKERS = (
    "context size has been exceeded",  # LM Studio / llama.cpp
    "context_length_exceeded",  # OpenAI
    "maximum context length",  # OpenAI, vLLM
    "prompt is too long",  # Anthropic
    "exceeds the context",
)


class LLMError(RuntimeError):
    """A provider call failed in a way the user needs to know about."""


class LLMUnavailable(LLMError):
    """The LLM server can't be reached (its machine is off, say), as opposed to answering badly."""


class LLMTimeout(LLMUnavailable):
    """No reply (or no next chunk) within the timeout."""


class LLMCancelled(LLMUnavailable):
    """The request was interrupted through its `cancel` event."""


class LLMStatusError(LLMError):
    def __init__(self, message: str, *, status_code: int, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class LLMContextExceeded(LLMStatusError):
    """The prompt doesn't fit the model's context. Retrying the same request won't help; shorten it."""


class LLMRefusal(LLMError):
    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        self.category = category


def is_context_exceeded(body: str) -> bool:
    low = body.lower()
    return any(marker in low for marker in _CONTEXT_MARKERS)


def status_error(label: str, status_code: int, body: str) -> LLMStatusError:
    """The right LLMStatusError for an HTTP error reply, with a message worth reading."""
    cls = LLMContextExceeded if is_context_exceeded(body) else LLMStatusError
    return cls(f"{label} returned HTTP {status_code}: {describe_body(body)}", status_code=status_code, body=body)


def describe_body(body: str, limit: int = 300) -> str:
    """An error body, shortened -- and called out when it's an HTML error page, which almost always
    means the server itself crashed or restarted rather than anything the request did."""
    text = " ".join(body.split())
    preview = text[:limit] + ("…" if len(text) > limit else "")
    if text[:15].lower().startswith(("<!doctype", "<html")):
        return (
            "the server returned an HTML error page instead of an API error -- almost always a crash "
            f"or restart on the LLM server's side; check its logs. Preview: {preview}"
        )
    return preview
