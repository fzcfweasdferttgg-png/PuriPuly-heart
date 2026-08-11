"""Shared type aliases, protocols, and utility functions for overlay modules.

Single source of truth for types and helpers used across multiple overlay modules.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from puripuly_heart.domain.overlay_types import OverlayEntryKey

# ── Type aliases ──────────────────────────────────────────────────────

NextAppearanceSeq = Callable[[], int]
OverlayTerminalUpdatePredicate = Callable[[str | None, UUID | None], bool]
# Returns 'evicted_by_newer_turn' or 'expired' or None. Called by dispatcher before applying events.
# If non-None, event is a late arrival and should be dropped.
OverlayTerminalUpdateReason = Callable[[str | None, UUID | None], str | None]


# ── Protocol ──────────────────────────────────────────────────────────

class OverlayPresentationEntry(Protocol):
    channel: str
    utterance_id: UUID
    first_input_seq: int | None
    live_text: str
    live_secondary_text: str
    live_primary_language: str | None
    live_secondary_language: str | None
    live_update_id: str | None
    live_origin_wall_clock_ms: int | None
    live_session_scope: str | None
    live_source_text_hash: str | None
    live_source_text_len: int | None
    live_logical_turn_key: str | None
    original_text: str
    original_language: str | None
    translation_text: str
    translation_language: str | None
    translation_update_id: str | None
    translation_origin_wall_clock_ms: int | None
    translation_session_scope: str | None
    translation_source_text_hash: str | None
    translation_source_text_len: int | None
    translation_logical_turn_key: str | None
    occupant_key: str
    appearance_seq: int | None
    publishable_seq: int | None
    retained_hidden: bool
    ever_visible: bool
    last_updated_seq: int

    @property
    def block_id(self) -> str: ...


# ── Utility functions ─────────────────────────────────────────────────

def content_language_or_none(language: str | None) -> str | None:
    # Strips whitespace and returns None if empty — not just if input was None.
    # Language fields can arrive as '' from upstream, not just None.
    if language is None:
        return None
    normalized = language.strip()
    return normalized or None


def line_language(
    language: str | None,
    text: str,
    # enabled parameter defaults True — disabled when translation_visibility is off
    # and caller wants to skip language detection.
    *,
    enabled: bool = True,
) -> str | None:
    if not enabled or not text.strip():
        return None
    return content_language_or_none(language)


def format_entry_key(key: OverlayEntryKey) -> str:
    return f"{key[0]}:{key[1]}"
