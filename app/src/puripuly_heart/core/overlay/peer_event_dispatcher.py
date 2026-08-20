from __future__ import annotations

"""Peer-event dispatcher for overlay presentation state.

Handles the 4 peer-event apply methods: active update, finalized update,
translation update, and utterance closed.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from puripuly_heart.core.overlay.entry_store import EntryStore
from puripuly_heart.core.overlay.event_helpers import EventHelpers
from puripuly_heart.domain.overlay_events import (
    PeerActiveUpdate,
    PeerTranscriptFinal,
    TranslationFinal,
    TranslationStreamUpdate,
    UtteranceClosed,
)
from puripuly_heart.domain.overlay_types import OverlayLogicalTurnEntry
from puripuly_heart.domain.overlay_types import (
    OverlayReductionResult,
    OverlayTurnDecisionRecord,
)

if TYPE_CHECKING:
    from puripuly_heart.core.overlay.types_common import (
        NextAppearanceSeq,
        OverlayTerminalUpdatePredicate,
        OverlayTerminalUpdateReason,
    )

from puripuly_heart.core.overlay.types_common import content_language_or_none, line_language


class PeerEventDispatcher:
    """Dispatches peer-event reductions against overlay presentation state.

    This class owns the 4 peer-event apply methods extracted from
    OverlayPresentationState. It receives store and helpers via constructor
    to avoid circular dependencies.
    """

    __slots__ = ("_store", "_helpers")

    def __init__(
        self,
        store: EntryStore,
        helpers: EventHelpers,
    ) -> None:
        self._store = store
        self._helpers = helpers

    # SELF vs PEER BEHAVIOR:
    # - Self: live_text shows during speech, then finalized_text takes over after STT final.
    #   Translation appears as secondary text.
    # - Peer: NO live text shown to user (product decision). Peer active updates only
    #   populate internal state for translation context. Visible text arrives only with
    #   translation (apply_peer_translation_update) or source-only finalization.
    # Product decision: peer overlay text is emitted with translation arrival. Peer source-only/active updates must not become visible normal-flow rows.
    def apply_peer_active_update(
        self,
        event: PeerActiveUpdate,
        *,
        now: float,
        show_peer_original: bool,
        next_appearance_seq: NextAppearanceSeq,
        terminal_update_reason: OverlayTerminalUpdateReason,
    ) -> OverlayReductionResult:
        """Apply reserved peer active fallback state without normalizing it as product flow."""

        decisions: list[OverlayTurnDecisionRecord] = []
        active_entry = self._helpers.active_update_entry_or_none(
            channel=event.channel,
            utterance_id=event.utterance_id,
            event_seq=event.seq,
            now=now,
            show_translation=True,
            terminal_update_reason=terminal_update_reason,
            decisions=decisions,
        )
        if active_entry is None:
            return OverlayReductionResult(False, tuple(decisions))
        key, entry = active_entry
        if self._helpers.active_update_matches_live_payload(
            channel=event.channel,
            key=key,
            entry=entry,
            text=event.text,
            secondary_text="",
            occupant_key=event.occupant_key,
            update_id=event.update_id,
            origin_wall_clock_ms=event.origin_wall_clock_ms,
            session_scope=event.session_scope,
            source_text_hash=event.source_text_hash,
            source_text_len=event.source_text_len,
            logical_turn_key=event.logical_turn_key,
        ):
            next_original_language = line_language(event.source_language, event.text)
            language_changed = entry.original_language != next_original_language
            entry.original_language = next_original_language
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_coalesced",
                    disposition="coalesced",
                    key=key,
                    entry=entry,
                    extras={"event_seq": event.seq},
                )
            )
            entry.last_updated_seq = event.seq
            return OverlayReductionResult(language_changed, tuple(decisions))

        self._helpers.remember_entry_input_seq(entry, event_seq=event.seq)
        if not entry.occupant_key:
            entry.occupant_key = event.occupant_key
        # Peer active updates set entry.live_text but it's never rendered (product decision).
        # Visible peer text comes from translation/finalized events only.
        entry.live_text = event.text
        entry.original_text = event.text
        entry.original_language = line_language(event.source_language, event.text)
        entry.live_seq = event.seq
        entry.original_seq = event.seq
        entry.last_updated_seq = event.seq
        self._helpers.refresh_entry_visibility_and_expiration(
            key,
            entry,
            now=now,
            publishable_seq=event.seq,
            next_appearance_seq=next_appearance_seq,
            show_peer_original=show_peer_original,
            decisions=decisions,
        )
        self._store.set_live_turn_key_for_channel(event.channel, key)
        return OverlayReductionResult(True, tuple(decisions))

    def apply_peer_finalized_update(
        self,
        event: PeerTranscriptFinal,
        *,
        now: float,
        show_peer_original: bool,
        next_appearance_seq: NextAppearanceSeq,
        terminal_update_reason: OverlayTerminalUpdateReason,
    ) -> OverlayReductionResult:
        decisions: list[OverlayTurnDecisionRecord] = []
        key = self._store.entry_key(event.channel, event.utterance_id)
        if self._helpers.append_terminal_update_decision(
            terminal_update_reason(event.channel, event.utterance_id),
            key=key,
            decisions=decisions,
        ):
            return OverlayReductionResult(False, tuple(decisions))
        entry = self._store.entry_for(event.channel, event.utterance_id, OverlayLogicalTurnEntry)
        if entry.retained_hidden:
            return OverlayReductionResult(False)
        if event.seq < entry.last_updated_seq:
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_superseded",
                    disposition="superseded",
                    key=key,
                    entry=entry,
                    extras={"event_seq": event.seq, "last_updated_seq": entry.last_updated_seq},
                )
            )
            return OverlayReductionResult(False, tuple(decisions))
        event_source_language = content_language_or_none(event.source_language)
        if (
            entry.original_text == event.text
            and entry.original_language == event_source_language
            and entry.last_updated_seq == event.seq
        ):
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_coalesced",
                    disposition="coalesced",
                    key=key,
                    entry=entry,
                    extras={"event_seq": event.seq},
                )
            )
            return OverlayReductionResult(False, tuple(decisions))

        self._helpers.remember_entry_input_seq(entry, event_seq=event.seq)
        entry.original_text = event.text
        entry.original_language = event_source_language
        entry.original_seq = event.seq
        # Sets entry.live_text = '' after writing original_text.
        # Live pointer cleared because peer finalized text goes through original_text, not live_text path.
        entry.live_text = ""
        entry.live_seq = None
        entry.last_updated_seq = event.seq
        self._helpers.refresh_entry_visibility_and_expiration(
            key,
            entry,
            now=now,
            publishable_seq=event.seq,
            next_appearance_seq=next_appearance_seq,
            show_peer_original=show_peer_original,
            decisions=decisions,
        )
        return OverlayReductionResult(True, tuple(decisions))

    def apply_peer_translation_update(
        self,
        event: TranslationStreamUpdate | TranslationFinal,
        *,
        now: float,
        show_peer_original: bool,
        next_appearance_seq: NextAppearanceSeq,
        terminal_update_reason: OverlayTerminalUpdateReason,
    ) -> OverlayReductionResult:
        decisions: list[OverlayTurnDecisionRecord] = []
        key = self._store.entry_key(event.channel, event.utterance_id)
        if self._helpers.append_terminal_update_decision(
            terminal_update_reason(event.channel, event.utterance_id),
            key=key,
            decisions=decisions,
        ):
            return OverlayReductionResult(False, tuple(decisions))
        entry = self._store.entry_for(event.channel, event.utterance_id, OverlayLogicalTurnEntry)
        if event.seq < entry.last_updated_seq:
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_superseded",
                    disposition="superseded",
                    key=key,
                    entry=entry,
                    extras={"event_seq": event.seq, "last_updated_seq": entry.last_updated_seq},
                )
            )
            return OverlayReductionResult(False, tuple(decisions))
        event_source_language = content_language_or_none(event.source_language)
        event_target_language = content_language_or_none(event.target_language)
        if (
            entry.translation_text == event.text
            and (event_source_language is None or entry.original_language == event_source_language)
            and entry.translation_language == event_target_language
            and entry.last_updated_seq == event.seq
        ):
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_coalesced",
                    disposition="coalesced",
                    key=key,
                    entry=entry,
                    extras={"event_seq": event.seq},
                )
            )
            return OverlayReductionResult(False, tuple(decisions))

        self._helpers.remember_entry_input_seq(entry, event_seq=event.seq)
        if event.source_text.strip():
            entry.original_text = event.source_text
            entry.original_language = event_source_language
            entry.original_seq = event.seq
        elif event_source_language is not None:
            entry.original_language = event_source_language
        if not entry.retained_hidden:
            # Directly calls self._helpers._next_translation_visible_since() — private method access
            # because no public wrapper exists. Do not extract without adding public method in EventHelpers.
            entry.translation_visible_since = self._helpers._next_translation_visible_since(
                previous_text=entry.translation_text,
                next_text=event.text,
                previous_visible_since=entry.translation_visible_since,
                now=now,
            )
        entry.translation_text = event.text
        if event.text.strip():
            entry.translation_language = event_target_language
            entry.translation_update_id = event.update_id
            entry.translation_origin_wall_clock_ms = event.origin_wall_clock_ms
            entry.translation_session_scope = event.session_scope
            entry.translation_source_text_hash = event.source_text_hash
            entry.translation_source_text_len = event.source_text_len
            entry.translation_logical_turn_key = event.logical_turn_key
            entry.translation_seq = event.seq
            entry.live_text = ""
            entry.live_seq = None
        else:
            entry.translation_language = None
            entry.translation_update_id = None
            entry.translation_origin_wall_clock_ms = None
            entry.translation_session_scope = None
            entry.translation_source_text_hash = None
            entry.translation_source_text_len = None
            entry.translation_logical_turn_key = None
            if not entry.live_secondary_text.strip():
                entry.translation_seq = None
        if event.text.strip() and entry.translation_observed_visible_since is None:
            entry.translation_observed_visible_since = now
        entry.last_updated_seq = event.seq
        self._helpers.refresh_entry_visibility_and_expiration(
            key,
            entry,
            now=now,
            publishable_seq=event.seq,
            next_appearance_seq=next_appearance_seq,
            show_peer_original=show_peer_original,
            decisions=decisions,
        )
        return OverlayReductionResult(True, tuple(decisions))

    def apply_peer_utterance_closed(
        self,
        event: UtteranceClosed,
        *,
        now: float,
        is_tombstoned: OverlayTerminalUpdatePredicate,
    ) -> OverlayReductionResult:
        decisions: list[OverlayTurnDecisionRecord] = []
        key = self._store.entry_key(event.channel, event.utterance_id)
        if is_tombstoned(event.channel, event.utterance_id):
            return OverlayReductionResult(False)
        entry = self._store.entries.get(key)
        if entry is None:
            return OverlayReductionResult(False)
        if event.seq < entry.last_updated_seq:
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_superseded",
                    disposition="superseded",
                    key=key,
                    entry=entry,
                    extras={"event_seq": event.seq, "last_updated_seq": entry.last_updated_seq},
                )
            )
            return OverlayReductionResult(False, tuple(decisions))
        if entry.closed_seq == event.seq:
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_coalesced",
                    disposition="coalesced",
                    key=key,
                    entry=entry,
                    extras={"event_seq": event.seq},
                )
            )
            return OverlayReductionResult(False, tuple(decisions))
        entry.closed_seq = event.seq
        entry.closed_at = now
        entry.last_updated_seq = event.seq
        return OverlayReductionResult(True, tuple(decisions))
