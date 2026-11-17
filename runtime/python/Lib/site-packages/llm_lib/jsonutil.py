"""Getting JSON out of a reply that may wrap it in a code fence or a sentence."""

from __future__ import annotations

import json
from typing import Any

_MISSING = object()


def extract_json(text: str, default: Any = None) -> Any:
    """The JSON object or array in `text` -- alone, fenced, or with words around it -- else `default`.
    Tries the whole text first, then the widest {...} and [...] spans."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    candidates = []
    for start, end in (("{", "}"), ("[", "]")):
        first, last = text.find(start), text.rfind(end)
        if 0 <= first < last:
            candidates.append((first, text[first : last + 1]))
    for _, span in sorted(candidates):  # whichever opens first is the outer value
        try:
            return json.loads(span)
        except ValueError:
            continue
    return default


def has_json(text: str) -> bool:
    """Whether a reply holds a JSON object or array."""
    return isinstance(extract_json(text, _MISSING), (dict, list))
