"""Inline reasoning that local models (Qwen, DeepSeek-R1, GLM, ...) write into the reply itself.

`ThinkFilter` splits a streamed reply into visible text and reasoning as it arrives, even when a
<think> tag is cut across chunks; `strip_reasoning` does the same for a whole reply.
"""

from __future__ import annotations

import re

NO_THINK = "/no_think"  # Qwen3's soft switch: skip the (slow) reasoning phase; other models ignore it

_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
_OPEN_RE = re.compile(r"<think(?:ing)?>", re.IGNORECASE)
_OPEN_TAGS = ("<think>", "<thinking>")
_CLOSE_TAGS = ("</think>", "</thinking>")


def strip_reasoning(text: str) -> str:
    """Drop <think>...</think> blocks. An unclosed tag means the reply was cut off mid-thought, so
    nothing after it is usable."""
    text = _THINK_RE.sub("", text)
    cut = _OPEN_RE.search(text)
    return (text[: cut.start()] if cut else text).strip()


def _partial_tail(buf: str, tags: tuple[str, ...]) -> int:
    """How many characters at the end of `buf` could be the start of one of `tags`."""
    low = buf.lower()
    best = 0
    for tag in tags:
        for n in range(1, len(tag)):
            if low.endswith(tag[:n]):
                best = max(best, n)
    return best


def _find(buf: str, tags: tuple[str, ...]) -> tuple[int, int]:
    """(index, tag length) of the first of `tags` in `buf`, or (-1, 0)."""
    low = buf.lower()
    hits = [(i, len(t)) for t in tags if (i := low.find(t)) >= 0]
    return min(hits) if hits else (-1, 0)


class ThinkFilter:
    """feed(chunk) -> [(kind, text)] where kind is "content" or "reasoning".

    Leading whitespace before the first visible text is dropped, so a reply that opens with a
    think block doesn't start with blank lines. Call flush() at the end of the stream.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._thinking = False
        self._started = False

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self._buf += chunk
        out: list[tuple[str, str]] = []
        while self._buf:
            if self._thinking:
                end, size = _find(self._buf, _CLOSE_TAGS)
                if end < 0:
                    keep = _partial_tail(self._buf, _CLOSE_TAGS)
                    self._emit(out, "reasoning", self._buf[: len(self._buf) - keep])
                    self._buf = self._buf[len(self._buf) - keep :]
                    break
                self._emit(out, "reasoning", self._buf[:end])
                self._buf, self._thinking = self._buf[end + size :], False
                continue
            start, size = _find(self._buf, _OPEN_TAGS)
            if start >= 0:
                self._emit(out, "content", self._buf[:start])
                self._buf, self._thinking = self._buf[start + size :], True
                continue
            keep = _partial_tail(self._buf, _OPEN_TAGS)
            self._emit(out, "content", self._buf[: len(self._buf) - keep])
            self._buf = self._buf[len(self._buf) - keep :]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self._emit(out, "reasoning" if self._thinking else "content", self._buf)
        self._buf = ""
        return out

    def _emit(self, out: list[tuple[str, str]], kind: str, text: str) -> None:
        if not text:
            return
        if kind == "content" and not self._started:
            text = text.lstrip()
            if not text:
                return
            self._started = True
        out.append((kind, text))
