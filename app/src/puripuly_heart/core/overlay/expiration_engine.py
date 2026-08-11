from __future__ import annotations

"""Expiration engine for overlay presentation entries.

Encapsulates the three expiration-related computations extracted from
OverlayPresentationState: component calculation, deadline calculation,
and bulk expiration sweep.
"""

from typing import TYPE_CHECKING

from puripuly_heart.core.overlay.entry_store import EntryStore

if TYPE_CHECKING:
    from puripuly_heart.core.overlay.types_common import OverlayPresentationEntry


class ExpirationEngine:
    """Pure expiration logic operating on an EntryStore."""

    def __init__(self, store: EntryStore) -> None:
        self._store = store

    def expire_entries(
        self,
        *,
        now: float,
        show_translation: bool,
        late_arrival_window_seconds: float,
        visible_ttl_seconds: float,
        self_translation_min_visible_seconds: float,
    ) -> bool:
        # Returns True if any entry expired — caller (state.py) uses this to trigger overlay publish.
        # Returning True forces a fresh render.
        expired_keys = [
            key
            for key, entry in self._store.entries.items()
            if (
                deadline := self.entry_expiration_deadline(
                    entry,
                    show_translation=show_translation,
                    late_arrival_window_seconds=late_arrival_window_seconds,
                    visible_ttl_seconds=visible_ttl_seconds,
                    self_translation_min_visible_seconds=self_translation_min_visible_seconds,
                )
            )
            is not None
            and now >= deadline
        ]
        for key in expired_keys:
            entry = self._store.entries.get(key)
            if entry is None:
                continue
            self._store.remove_entry(
                key,
                reason="expired",
                now=now,
                tombstone_seq=entry.last_updated_seq if entry.closed_seq is None else None,
            )
        return bool(expired_keys)

    def entry_expiration_deadline(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_translation: bool,
        late_arrival_window_seconds: float,
        visible_ttl_seconds: float,
        self_translation_min_visible_seconds: float,
    ) -> float | None:
        # Calls entry_expiration_components then returns only [0] (effective).
        # Components [1], [2] are debug-only, used by observability logging.
        return self.entry_expiration_components(
            entry,
            show_translation=show_translation,
            late_arrival_window_seconds=late_arrival_window_seconds,
            visible_ttl_seconds=visible_ttl_seconds,
            self_translation_min_visible_seconds=self_translation_min_visible_seconds,
        )[0]

    # EXPIRATION CHAIN: three independent deadlines, combined with min/max logic.
    #   hidden_deadline = window_evicted_at + late_arrival (min with effective)
    #   visible_deadline = last_meaningful_visible_at + visible_ttl (or closed_at + late_arrival)
    #   translation_deadline = translation_visible_since + min_visible_seconds (self only)
    # effective = max(visible, translation), then min with hidden if present.
    # A None deadline means "no constraint" — entry lives until another deadline fires.
    def entry_expiration_components(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_translation: bool,
        late_arrival_window_seconds: float,
        visible_ttl_seconds: float,
        self_translation_min_visible_seconds: float,
    ) -> tuple[float | None, float | None, float | None]:
        hidden_deadline = (
            entry.window_evicted_at + late_arrival_window_seconds
            if entry.retained_hidden and entry.window_evicted_at is not None
            else None
        )
        visible_anchor = entry.last_meaningful_visible_at
        if visible_anchor is None and entry.visible_since is not None:
            visible_anchor = entry.visible_since

        visible_deadline: float | None = None
        if visible_anchor is not None:
            visible_deadline = visible_anchor + visible_ttl_seconds
        elif entry.closed_at is not None:
            visible_deadline = entry.closed_at + late_arrival_window_seconds

        translation_deadline: float | None = None
        if (
            entry.channel == "self"
            and show_translation
            and entry.translation_visible_since is not None
        ):
            translation_deadline = (
                entry.translation_visible_since + self_translation_min_visible_seconds
            )
        effective_deadline = visible_deadline
        if translation_deadline is not None:
            if effective_deadline is None:
                effective_deadline = translation_deadline
            else:
                effective_deadline = max(effective_deadline, translation_deadline)
        if hidden_deadline is not None:
            if effective_deadline is None:
                effective_deadline = hidden_deadline
            else:
                effective_deadline = min(effective_deadline, hidden_deadline)
        return effective_deadline, visible_deadline, translation_deadline
