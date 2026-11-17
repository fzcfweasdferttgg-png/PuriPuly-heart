"""Which failures are worth another try, and waiting between tries without ignoring a stop request.

The client already resends a request after a passing 5xx (Settings.retry_waits). These are for the
app's own loop around a whole step -- an agent turn, a story batch -- after that has given up.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from llm_lib.errors import LLMCancelled, LLMContextExceeded, LLMStatusError, LLMUnavailable


def is_retryable(exc: BaseException) -> bool:
    """Worth retrying: the server was unreachable or timed out (down, restarting, a dropped
    connection), or a 5xx / 429. Not a 4xx: the server already rejected that exact request (bad
    request, auth, a prompt that doesn't fit) and would again. Not a cancel."""
    if isinstance(exc, LLMCancelled):
        return False
    if isinstance(exc, LLMUnavailable):
        return True
    if isinstance(exc, LLMContextExceeded):
        return False
    if isinstance(exc, LLMStatusError):
        return exc.status_code >= 500 or exc.status_code == 429
    return False


def interruptible_sleep(seconds: float, should_stop: Callable[[], bool], poll: float = 0.2) -> bool:
    """time.sleep(seconds), checking should_stop() every `poll` seconds so a Ctrl+C or Stop during a
    retry delay takes effect right away. True if it was stopped early."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if should_stop():
            return True
        time.sleep(min(poll, max(0.0, deadline - time.monotonic())))
    return should_stop()
