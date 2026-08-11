"""Selection engine extracted from OverlayPresentationState.

Picks which finalized entries appear in the overlay window and builds
the visible block selection result.
Dependencies: EntryStore, callback for _build_presentation_block.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING
from uuid import UUID

from puripuly_heart.domain.overlay_types import (
    OverlayEntryKey,
    OverlayPresentationBlock,
    OverlayVisibleBlockSelection,
)

from puripuly_heart.core.overlay.types_common import NextAppearanceSeq, OverlayPresentationEntry, format_entry_key

if TYPE_CHECKING:
    from puripuly_heart.core.overlay.entry_store import EntryStore

__all__ = ["OverlayVisibleBlockSelection", "SelectionEngine"]


class SelectionEngine:
    """Picks which finalized entries appear in the overlay window.

    Extracted from OverlayPresentationState. Delegates store access to
    EntryStore and block building to a caller-provided callback.

    Constructor params:
    - store: EntryStore (entry access)
    - on_build_presentation_block: callback that builds an OverlayPresentationBlock
      from an entry (delegates to OverlayPresentationState._build_presentation_block)
    """

    def __init__(
        self,
        store: EntryStore,
        *,
        on_build_presentation_block: Callable,
    ) -> None:
        self._store = store
        self._on_build_presentation_block = on_build_presentation_block

    # SELECTION ALGORITHM: picks which finalized entries appear in the overlay window.
    # Active (live) entries are protected and always shown — they're excluded from
    # this selection. The remaining finalized entries are sorted by (appearance_seq,
    # occupant_key, formatted_key) and the most recent `finalized_limit` are selected.
    # Display order uses appearance_seq; selection order uses publishable_seq (most
    # recently finalized wins the limited slots).
    def visible_block_selection(
        self,
        *,
        entries: Mapping[OverlayEntryKey, OverlayPresentationEntry],
        live_self_entry: tuple[OverlayEntryKey, OverlayPresentationEntry] | None,
        live_peer_entry: tuple[OverlayEntryKey, OverlayPresentationEntry] | None,
        visible_window_target_blocks: int,
        show_translation: bool,
        show_peer_original: bool,
        peer_presentation_refresh_burst: bool,
        next_appearance_seq: NextAppearanceSeq,
        self_presentation_refresh_burst: bool = True,
    ) -> OverlayVisibleBlockSelection:
        active_self_key = (
            live_self_entry[0]
            if live_self_entry is not None and live_self_entry[1].live_text
            else None
        )
        active_self_present = active_self_key is not None
        active_peer_key = (
            live_peer_entry[0]
            if live_peer_entry is not None
            and self._live_peer_entry_is_drawable(
                live_peer_entry[1],
                show_peer_original=show_peer_original,
            )
            else None
        )
        protected_keys = [key for key in (active_self_key, active_peer_key) if key is not None]
        protected_key_set = set(protected_keys)
        finalized_limit = max(
            visible_window_target_blocks - len(protected_keys),
            0,
        )
        visible_entry_keys, candidate_keys = self._logical_visible_entry_keys(
            entries=entries,
            finalized_limit=finalized_limit,
            excluded_keys=protected_key_set,
            show_peer_original=show_peer_original,
            next_appearance_seq=next_appearance_seq,
        )
        rendered_entries = [
            (key, block)
            for key in visible_entry_keys
            if (entry := entries.get(key)) is not None
            and (
                block := self._on_build_presentation_block(
                    entry,
                    show_translation=show_translation,
                    show_peer_original=show_peer_original,
                    peer_presentation_refresh_burst=peer_presentation_refresh_burst,
                    self_presentation_refresh_burst=self_presentation_refresh_burst,
                )
            )
            is not None
        ]
        for protected_key in protected_keys:
            active_entry = entries.get(protected_key)
            if active_entry is None:
                continue
            block = self._on_build_presentation_block(
                active_entry,
                prefer_live_self=protected_key == active_self_key,
                show_translation=show_translation,
                show_peer_original=show_peer_original,
                peer_presentation_refresh_burst=peer_presentation_refresh_burst,
                self_presentation_refresh_burst=self_presentation_refresh_burst,
            )
            if block is None:
                continue
            rendered_entries.append((protected_key, block))
        rendered_entries.sort(key=lambda item: (item[1].appearance_seq, item[1].occupant_key))
        retained_hidden = [key for key, entry in entries.items() if entry.retained_hidden]
        return OverlayVisibleBlockSelection(
            rendered_entries=rendered_entries,
            active_self_present=active_self_present,
            finalized_limit=finalized_limit,
            candidate_keys=candidate_keys,
            selected_keys=visible_entry_keys,
            protected_keys=protected_keys,
            retained_hidden=retained_hidden,
        )

    def _logical_visible_entry_keys(
        self,
        *,
        entries: Mapping[OverlayEntryKey, OverlayPresentationEntry],
        finalized_limit: int,
        excluded_keys: set[OverlayEntryKey],
        show_peer_original: bool,
        next_appearance_seq: NextAppearanceSeq,
    ) -> tuple[list[OverlayEntryKey], list[OverlayEntryKey]]:
        # _logical_visible_entry_keys: returns (selected, all_candidates) — caller uses selected for rendering, all_candidates for displacement tracking
        if finalized_limit == 0:
            return [], []

        publishable: list[tuple[int, int, str, str, OverlayEntryKey]] = []
        for key, entry in entries.items():
            if key in excluded_keys:
                continue
            if not self.entry_is_selectable(entry, show_peer_original=show_peer_original):
                continue
            self._ensure_entry_visibility_metadata(
                entry,
                occupant_key=self._finalized_occupant_key(entry.channel, entry.utterance_id),
                next_appearance_seq=next_appearance_seq,
            )
            if entry.publishable_seq is None or entry.appearance_seq is None:
                continue
            publishable.append(
                (
                    entry.publishable_seq,
                    entry.appearance_seq,
                    entry.occupant_key,
                    format_entry_key(key),
                    key,
                )
            )

        display_order = sorted(publishable, key=lambda item: (item[1], item[2], item[3]))
        # Sorting order: (publishable_seq, appearance_seq, occupant_key, formatted_key) — most recently finalized wins limited slots
        selected_candidates = sorted(
            publishable,
            key=lambda item: (item[0], item[1], item[2], item[3]),
        )[-finalized_limit:]
        selected_set = {key for *_, key in selected_candidates}
        selected = [key for *_, key in display_order if key in selected_set]
        return selected, [key for *_, key in display_order]

    def entry_is_publishable(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_peer_original: bool,
    ) -> bool:
        if entry.channel == "peer":
            return bool(
                entry.translation_text.strip()
                or (show_peer_original and (entry.live_text.strip() or entry.original_text.strip()))
            )
        return bool(entry.original_text.strip())

    # entry_is_publishable vs entry_is_selectable: selectable = publishable AND NOT retained_hidden
    def entry_is_selectable(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_peer_original: bool,
    ) -> bool:
        return (
            self.entry_is_publishable(
                entry,
                show_peer_original=show_peer_original,
            )
            and not entry.retained_hidden
        )

    def _live_peer_entry_is_drawable(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_peer_original: bool,
    ) -> bool:
        if entry.translation_text.strip():
            return True
        if not show_peer_original:
            return False
        return bool(entry.live_text.strip() or entry.original_text.strip())

    def _ensure_entry_visibility_metadata(
        self,
        entry: OverlayPresentationEntry,
        *,
        occupant_key: str,
        next_appearance_seq: NextAppearanceSeq,
        publishable_seq: int | None = None,
    ) -> None:
        # _ensure_entry_visibility_metadata: lazy initialization — appearance_seq and publishable_seq set on first selection pass
        if not entry.occupant_key:
            entry.occupant_key = occupant_key
        if entry.appearance_seq is None:
            if entry.first_input_seq is not None:
                entry.appearance_seq = entry.first_input_seq
            elif publishable_seq is not None:
                entry.appearance_seq = publishable_seq
            else:
                entry.appearance_seq = next_appearance_seq()
        if entry.publishable_seq is None:
            if publishable_seq is not None:
                entry.publishable_seq = publishable_seq
            elif entry.last_updated_seq > 0:
                entry.publishable_seq = entry.last_updated_seq

    def _finalized_occupant_key(self, channel: str, utterance_id: UUID) -> str:
        return f"{channel}:{utterance_id}"
