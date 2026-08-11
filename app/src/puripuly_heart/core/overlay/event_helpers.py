"""Common helpers for apply_* reductions extracted from OverlayPresentationState.

Handles terminal update decisions, active entry resolution, live pointer
management, translation visibility, and entry retirement.
Dependencies: EntryStore, retired_preview_self_seqs (shared), callbacks for
              entry_is_publishable and _ensure_entry_visibility_metadata.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Callable
from uuid import UUID

from puripuly_heart.domain.overlay_types import (
    OverlayEntryKey,
    OverlayEntryRemovalRecord,
    OverlayLogicalTurnEntry,
    OverlayTurnDecisionRecord,
)

from puripuly_heart.core.overlay.types_common import (
    NextAppearanceSeq,
    OverlayTerminalUpdateReason,
    format_entry_key,
)

if TYPE_CHECKING:
    from puripuly_heart.core.overlay.entry_store import EntryStore

_RETIRED_PREVIEW_SELF_SEQ_LIMIT = 64

__all__ = ["EventHelpers"]


class EventHelpers:
    """Common helpers for apply_* reductions extracted from OverlayPresentationState.

    Dependencies injected via constructor:
    - store: EntryStore (entry access)
    - retired_preview_self_seqs: OrderedDict (shared reference from state)
    - on_entry_is_publishable: callback for entry_is_publishable
    - on_ensure_entry_visibility_metadata: callback for _ensure_entry_visibility_metadata
    """

    def __init__(
        self,
        store: EntryStore,
        retired_preview_self_seqs: OrderedDict,
        *,
        on_entry_is_publishable: Callable[..., bool],
        on_ensure_entry_visibility_metadata: Callable[..., None],
        on_finalized_occupant_key: Callable[[str, UUID], str],
    ) -> None:
        self._store = store
        self.retired_preview_self_seqs = retired_preview_self_seqs
        self._on_entry_is_publishable = on_entry_is_publishable
        self._on_ensure_entry_visibility_metadata = on_ensure_entry_visibility_metadata
        self._on_finalized_occupant_key = on_finalized_occupant_key

    # ------------------------------------------------------------------
    # Terminal update decisions
    # ------------------------------------------------------------------

    # terminal_reason is set by expiration_engine when entry is evicted — not cleared on new event arrival.
    # Late events check this via predicate callback.
    def append_terminal_update_decision(
        self,
        terminal_reason: str | None,
        *,
        key: OverlayEntryKey,
        decisions: list[OverlayTurnDecisionRecord],
    ) -> bool:
        if terminal_reason is None:
            return False
        if terminal_reason == "evicted_by_newer_turn":
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_late_update_ignored_after_eviction",
                    disposition="evicted",
                    key=key,
                    extras={"terminal_reason": terminal_reason},
                )
            )
        elif terminal_reason == "expired":
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_late_update_ignored_after_idle_hide",
                    disposition="hidden_idle_ttl",
                    key=key,
                    extras={"terminal_reason": terminal_reason},
                )
            )
        return True

    # ------------------------------------------------------------------
    # Active entry resolution
    # ------------------------------------------------------------------

    def active_update_entry_or_none(
        self,
        *,
        channel: str,
        utterance_id: UUID | None,
        event_seq: int,
        now: float,
        show_translation: bool,
        terminal_update_reason: OverlayTerminalUpdateReason,
        decisions: list[OverlayTurnDecisionRecord],
    ) -> tuple[OverlayEntryKey, object] | None:
        key = self._store.entry_key(channel, utterance_id)
        # Returns None on terminal_reason match OR when event_seq < live_entry.last_updated_seq.
        # Two independent early-exit paths.
        if self.append_terminal_update_decision(
            terminal_update_reason(channel, utterance_id),
            key=key,
            decisions=decisions,
        ):
            return None
        live_entry = self._store.live_entry_for_channel(channel)
        if live_entry is not None:
            live_key, current_live_entry = live_entry
            if live_key != key and event_seq < current_live_entry.last_updated_seq:
                decisions.append(
                    OverlayTurnDecisionRecord(
                        decision="overlay_turn_superseded",
                        disposition="superseded",
                        key=key,
                        entry=current_live_entry,
                        extras={
                            "event_seq": event_seq,
                            "superseded_by_entry": format_entry_key(live_key),
                            "superseded_by_seq": current_live_entry.last_updated_seq,
                        },
                    )
                )
                return None

        entry = self._store.entry_for(channel, utterance_id, OverlayLogicalTurnEntry)
        if event_seq < entry.last_updated_seq:
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_superseded",
                    disposition="superseded",
                    key=key,
                    entry=entry,
                    extras={"event_seq": event_seq, "last_updated_seq": entry.last_updated_seq},
                )
            )
            return None
        if live_entry is not None and live_entry[0] != key:
            if channel == "self":
                self.clear_live_self_pointer(
                    reason="live_self_replaced",
                    now=now,
                    show_translation=show_translation,
                )
            else:
                self.clear_live_peer_pointer()
        return key, entry

    def active_update_matches_live_payload(
        self,
        *,
        channel: str,
        key: OverlayEntryKey,
        entry: object,
        text: str,
        secondary_text: str,
        occupant_key: str,
        update_id: str | None,
        origin_wall_clock_ms: int | None,
        session_scope: str | None,
        source_text_hash: str | None,
        source_text_len: int | None,
        logical_turn_key: str | None,
    ) -> bool:
        if self._store.live_turn_key_for_channel(channel) != key:
            return False
        if entry.live_text != text or entry.occupant_key != occupant_key:
            return False
        if channel == "peer":
            return True
        if channel != "self":
            raise ValueError(f"invalid overlay channel: {channel!r}")
        return (
            entry.live_secondary_text == secondary_text
            and entry.live_update_id == update_id
            and entry.live_origin_wall_clock_ms == origin_wall_clock_ms
            and entry.live_session_scope == session_scope
            and entry.live_source_text_hash == source_text_hash
            and entry.live_source_text_len == source_text_len
            and entry.live_logical_turn_key == logical_turn_key
        )

    # ------------------------------------------------------------------
    # Live pointer management
    # ------------------------------------------------------------------

    def clear_live_self_pointer(
        self,
        *,
        reason: str,
        now: float,
        show_translation: bool,
    ) -> None:
        # Clears live self + updates translation visibility + potentially retires preview-only entry.
        # Three side effects must happen in this order.
        live_self = self._store.live_entry_for_channel("self")
        if live_self is None:
            return
        key, entry = live_self
        previous_rendered_translation_text = self.rendered_self_translation_text(entry)
        self.clear_self_live_payload(entry)
        self._store.set_live_turn_key_for_channel("self", None)
        self.update_self_translation_visibility(
            entry,
            previous_rendered_text=previous_rendered_translation_text,
            next_rendered_text=self.rendered_self_translation_text(entry),
            now=now,
            show_translation=show_translation,
        )
        if self._should_retire_preview_only_self_entry(entry):
            self._retire_preview_only_self_entry(key, entry, reason=reason, now=now)

    def clear_live_peer_pointer(self) -> None:
        live_peer = self._store.live_entry_for_channel("peer")
        if live_peer is None:
            return
        _, entry = live_peer
        entry.live_text = ""
        entry.live_secondary_text = ""
        entry.live_seq = None
        self._store.set_live_turn_key_for_channel("peer", None)

    def clear_self_live_payload(self, entry: object) -> None:
        entry.live_text = ""
        entry.live_secondary_text = ""
        entry.live_primary_language = None
        entry.live_secondary_language = None
        entry.live_update_id = None
        entry.live_origin_wall_clock_ms = None
        entry.live_session_scope = None
        entry.live_source_text_hash = None
        entry.live_source_text_len = None
        entry.live_logical_turn_key = None
        entry.live_seq = None

    # ------------------------------------------------------------------
    # Translation visibility
    # ------------------------------------------------------------------

    def rendered_self_translation_text(self, entry: object) -> str:
        live_secondary_text = entry.live_secondary_text.strip()
        if live_secondary_text:
            return live_secondary_text
        return entry.translation_text.strip()

    def update_self_translation_visibility(
        self,
        entry: object,
        *,
        previous_rendered_text: str,
        next_rendered_text: str,
        now: float,
        show_translation: bool,
    ) -> None:
        if not show_translation:
            return
        entry.translation_visible_since = self._next_translation_visible_since(
            previous_text=previous_rendered_text,
            next_text=next_rendered_text,
            previous_visible_since=entry.translation_visible_since,
            now=now,
        )
        if next_rendered_text and entry.translation_observed_visible_since is None:
            entry.translation_observed_visible_since = now

    def _next_translation_visible_since(
        self,
        *,
        previous_text: str,
        next_text: str,
        previous_visible_since: float | None,
        now: float,
    ) -> float | None:
        next_clean = next_text.strip()
        if not next_clean:
            return None
        if previous_text.strip() != next_clean:
            return now
        return previous_visible_since

    # ------------------------------------------------------------------
    # Entry input seq
    # ------------------------------------------------------------------

    def remember_entry_input_seq(
        self,
        entry: object,
        *,
        event_seq: int,
    ) -> None:
        if entry.first_input_seq is None:
            entry.first_input_seq = event_seq

    # ------------------------------------------------------------------
    # Entry visibility and expiration
    # ------------------------------------------------------------------

    def refresh_entry_visibility_and_expiration(
        self,
        key: OverlayEntryKey,
        entry: object,
        *,
        now: float,
        publishable_seq: int | None,
        next_appearance_seq: NextAppearanceSeq,
        show_peer_original: bool = True,
        decisions: list[OverlayTurnDecisionRecord],
    ) -> None:
        if self._on_entry_is_publishable(entry, show_peer_original=show_peer_original):
            self._on_ensure_entry_visibility_metadata(
                entry,
                occupant_key=self._on_finalized_occupant_key(entry.channel, entry.utterance_id),
                publishable_seq=publishable_seq,
                next_appearance_seq=next_appearance_seq,
            )
            entry.ever_publishable = True
            if entry.visible_since is None:
                entry.visible_since = now
        else:
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_not_yet_publishable",
                    key=key,
                    entry=entry,
                )
            )

    # ------------------------------------------------------------------
    # Entry retirement
    # ------------------------------------------------------------------

    def _should_retire_preview_only_self_entry(
        self,
        entry: object,
    ) -> bool:
        return (
            entry.channel == "self"
            and not entry.original_text.strip()
            and not entry.translation_text.strip()
        )

    def _retire_preview_only_self_entry(
        self,
        key: OverlayEntryKey,
        entry: object,
        *,
        reason: str,
        now: float,
    ) -> None:
        self._remember_retired_preview_self_seq(key, entry.last_updated_seq)
        self._store.remove_entry(key, reason=reason, now=now)

    def _remember_retired_preview_self_seq(
        self,
        key: OverlayEntryKey,
        retired_seq: int,
    ) -> None:
        self.retired_preview_self_seqs.pop(key, None)
        self.retired_preview_self_seqs[key] = retired_seq
        while len(self.retired_preview_self_seqs) > _RETIRED_PREVIEW_SELF_SEQ_LIMIT:
            self.retired_preview_self_seqs.popitem(last=False)
