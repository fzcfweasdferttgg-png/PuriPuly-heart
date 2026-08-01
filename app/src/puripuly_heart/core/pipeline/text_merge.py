from __future__ import annotations

"""Text merge utility functions extracted from Pipeline.

These are pure (or near-pure) functions for merging overlapping text
segments produced by streaming translation.  They were originally
private methods on Pipeline; the logic is preserved verbatim.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ── constants ────────────────────────────────────────────────────────

_RELAXED_OVERLAP_MIN_CHARS: int = 3
_BOUNDARY_PUNCT = {".", ",", ";", ":", "!", "?"}
_SOFT_REUSE_PUNCT = {".", ",", "…", "。", "，", "、"}


# ── helpers used by _relaxed_overlap_merge ───────────────────────────

def _is_boundary_char(ch: str) -> bool:
    return ch.isspace() or ch in _BOUNDARY_PUNCT


def _strip_trailing_boundary(text: str) -> tuple[str, int]:
    idx = len(text)
    while idx > 0 and _is_boundary_char(text[idx - 1]):
        idx -= 1
    return text[:idx], len(text) - idx


def _strip_leading_boundary(text: str) -> tuple[str, int]:
    idx = 0
    while idx < len(text) and _is_boundary_char(text[idx]):
        idx += 1
    return text[idx:], idx


# ── core merge functions ─────────────────────────────────────────────

def _relaxed_overlap_merge(existing: str, addition: str) -> str | None:
    if not existing or not addition:
        return None

    left_trimmed, left_trimmed_len = _strip_trailing_boundary(existing)
    right_trimmed, right_trimmed_len = _strip_leading_boundary(addition)
    if left_trimmed_len == 0 and right_trimmed_len == 0:
        return None
    if not left_trimmed or not right_trimmed:
        return None

    max_overlap = min(len(left_trimmed), len(right_trimmed))
    overlap_len = 0
    for i in range(1, max_overlap + 1):
        if left_trimmed[-i:] == right_trimmed[:i]:
            overlap_len = i

    if overlap_len < _RELAXED_OVERLAP_MIN_CHARS:
        return None

    cut = right_trimmed_len + overlap_len
    if cut <= 0 or cut > len(addition):
        return None

    base = existing[:-left_trimmed_len] if left_trimmed_len else existing
    if cut >= len(addition):
        return base
    return f"{base}{addition[cut:]}"


def _merge_with_overlap(existing: str, addition: str) -> str:
    if not existing:
        return addition
    if not addition:
        return existing
    if existing.endswith(addition):
        return existing

    max_overlap = min(len(existing), len(addition))
    overlap_len = 0
    for i in range(1, max_overlap + 1):
        if existing[-i:] == addition[:i]:
            overlap_len = i
    if overlap_len:
        return existing + addition[overlap_len:]

    relaxed_merge = _relaxed_overlap_merge(existing, addition)
    if relaxed_merge is not None:
        return relaxed_merge

    if _needs_space(existing, addition):
        return f"{existing} {addition}"
    return f"{existing}{addition}"


def _merge_text(
    parts: list[str],
    *,
    merge_buffer=None,
    low_latency_mode: bool = False,
) -> str:
    merged = ""
    for part in parts:
        part_clean = part.strip()
        if not part_clean:
            continue
        if not merged:
            merged = part_clean
            continue
        merged = _merge_with_overlap(merged, part_clean)
    return merged.strip()


# ── soft-reuse helpers ───────────────────────────────────────────────

def _is_soft_reuse_boundary_char(ch: str) -> bool:
    return ch.isspace() or ch in _SOFT_REUSE_PUNCT


def _normalize_soft_reuse_text(text: str) -> str:
    start = 0
    end = len(text)
    while start < end and _is_soft_reuse_boundary_char(text[start]):
        start += 1
    while end > start and _is_soft_reuse_boundary_char(text[end - 1]):
        end -= 1
    return text[start:end]


def _soft_reuse_mode(
    spec_text: str | None,
    final_text: str,
    *,
    low_latency_mode: bool = False,
    low_latency_merge_gap_ms: int = 600,
) -> str | None:
    if spec_text is None:
        return None
    if spec_text == final_text:
        return "exact"

    normalized_spec = _normalize_soft_reuse_text(spec_text)
    normalized_final = _normalize_soft_reuse_text(final_text)
    if not normalized_spec or not normalized_final:
        return None
    if normalized_spec == normalized_final:
        return "soft_boundary"
    return None


# ── token-level helpers ──────────────────────────────────────────────

def _needs_space(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_ch = left[-1]
    right_ch = right[0]
    if _is_ascii_alnum(left_ch) and _is_ascii_alnum(right_ch):
        return True
    if (" " in left or " " in right) and left_ch.isalnum() and right_ch.isalnum():
        return True
    return False


def _is_ascii_alnum(ch: str) -> bool:
    return ord(ch) < 128 and ch.isalnum()
