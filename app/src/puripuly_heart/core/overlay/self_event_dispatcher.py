from __future__ import annotations

"""Self-event dispatcher for overlay presentation state.

Extracted from OverlayPresentationState to reduce the god-object surface.
Handles the 5 self-event apply methods: active update, active clear,
finalized update, translation update, and utterance closed.
"""

from collections import OrderedDict
from typing import TYPE_CHECKING
from uuid import UUID

from puripuly_heart.core.overlay.entry_store import EntryStore
from puripuly_heart.core.overlay.event_helpers import EventHelpers
from puripuly_heart.domain.overlay_events import (
    SelfActiveClear,
    SelfActiveUpdate,
    SelfTranscriptFinal,
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
    from puripuly_heart.domain.overlay_types import (
        OverlayEntryKey,
    )
    from puripuly_heart.core.overlay.types_common import (
        NextAppearanceSeq,
        OverlayTerminalUpdatePredicate,
        OverlayTerminalUpdateReason,
    )

from puripuly_heart.core.overlay.types_common import content_language_or_none, line_language


class SelfEventDispatcher:
    """Dispatches self-event reductions against overlay presentation state.

    This class owns the 5 self-event apply methods extracted from
    OverlayPresentationState. It receives store and helpers via constructor
    to avoid circular dependencies.
    """

    __slots__ = ("_store", "_helpers", "retired_preview_self_seqs")

    def __init__(
        self,
        store: EntryStore,
        helpers: EventHelpers,
        retired_preview_self_seqs: OrderedDict[OverlayEntryKey, int],
    ) -> None:
        self._store = store
        self._helpers = helpers
        self.retired_preview_self_seqs = retired_preview_self_seqs

    def apply_self_active_update(
        self,
        event: SelfActiveUpdate,
        *,
        now: float,
        show_translation: bool,
        terminal_update_reason: OverlayTerminalUpdateReason,
    ) -> OverlayReductionResult:
        decisions: list[OverlayTurnDecisionRecord] = []
        key = self._store.entry_key(event.channel, event.utterance_id)
        retired_preview_seq = self.retired_preview_self_seqs.get(key)
        if retired_preview_seq is not None and event.seq <= retired_preview_seq:
            decisions.append(
                OverlayTurnDecisionRecord(
                    decision="overlay_turn_superseded",
                    disposition="superseded",
                    key=key,
                    extras={"event_seq": event.seq, "retired_preview_seq": retired_preview_seq},
                )
            )
            return OverlayReductionResult(False, tuple(decisions))
        active_entry = self._helpers.active_update_entry_or_none(
            channel=event.channel,
            utterance_id=event.utterance_id,
            event_seq=event.seq,
            now=now,
            show_translation=show_translation,
            terminal_update_reason=terminal_update_reason,
            decisions=decisions,
        )
        if active_entry is None:
            return OverlayReductionResult(False, tuple(decisions))
        key, entry = active_entry

        previous_rendered_translation_text = self._helpers.rendered_self_translation_text(entry)

        if self._helpers.active_update_matches_live_payload(
            channel=event.channel,
            key=key,
            entry=entry,
            text=event.text,
            secondary_text=event.secondary_text,
            occupant_key=event.occupant_key,
            update_id=event.update_id,
            origin_wall_clock_ms=event.origin_wall_clock_ms,
            session_scope=event.session_scope,
            source_text_hash=event.source_text_hash,
            source_text_len=event.source_text_len,
            logical_turn_key=event.logical_turn_key,
        ):
            next_primary_language = line_language(event.source_language, event.text)
            next_secondary_language = line_language(event.target_language, event.secondary_text)
            language_changed = (
                entry.live_primary_language != next_primary_language
                or entry.live_secondary_language != next_secondary_language
            )
            entry.live_primary_language = next_primary_language
            entry.live_secondary_language = next_secondary_language
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
        if entry.visible_since is None:
            entry.visible_since = now

        if retired_preview_seq is not None and event.seq > retired_preview_seq:
            self.retired_preview_self_seqs.pop(key, None)
        entry.live_text = event.text
        entry.live_primary_language = line_language(event.source_language, event.text)
        entry.live_seq = event.seq
        entry.original_seq = event.seq
        entry.live_secondary_text = event.secondary_text
        if event.secondary_text.strip():
            entry.live_secondary_language = line_language(
                event.target_language,
                event.secondary_text,
            )
            entry.live_update_id = event.update_id
            entry.live_origin_wall_clock_ms = event.origin_wall_clock_ms
            entry.live_session_scope = event.session_scope
            entry.live_source_text_hash = event.source_text_hash
            entry.live_source_text_len = event.source_text_len
            entry.live_logical_turn_key = event.logical_turn_key
            entry.translation_seq = event.seq
        else:
            entry.live_secondary_language = None
            entry.live_update_id = None
            entry.live_origin_wall_clock_ms = None
            entry.live_session_scope = None
            entry.live_source_text_hash = None
            entry.live_source_text_len = None
            entry.live_logical_turn_key = None
            if not entry.translation_text.strip():
                entry.translation_seq = None
        self._helpers.update_self_translation_visibility(
            entry,
            previous_rendered_text=previous_rendered_translation_text,
            next_rendered_text=self._helpers.rendered_self_translation_text(entry),
            now=now,
            show_translation=show_translation,
        )
        entry.last_updated_seq = event.seq
        self._store.set_live_turn_key_for_channel(event.channel, key)
        return OverlayReductionResult(True, tuple(decisions))

    def apply_self_active_clear(
        self,
        event: SelfActiveClear,
        *,
        now: float,
        show_translation: bool,
    ) -> OverlayReductionResult:
        decisions: list[OverlayTurnDecisionRecord] = []
        live_self = self._store.live_entry_for_channel("self")
        if live_self is None:
            return OverlayReductionResult(False)
        key, entry = live_self
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
        if not entry.live_text:
            self._store.set_live_turn_key_for_channel("self", None)
            entry.last_updated_seq = event.seq
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

        previous_rendered_translation_text = self._helpers.rendered_self_translation_text(entry)
        self._helpers.clear_self_live_payload(entry)
        self._helpers.update_self_translation_visibility(
            entry,
            previous_rendered_text=previous_rendered_translation_text,
            next_rendered_text=self._helpers.rendered_self_translation_text(entry),
            now=now,
            show_translation=show_translation,
        )
        entry.last_updated_seq = event.seq
        if self._store.live_turn_key_for_channel("self") == key:
            self._store.set_live_turn_key_for_channel("self", None)
        if self._helpers._should_retire_preview_only_self_entry(entry):
            self._helpers._retire_preview_only_self_entry(
                key,
                entry,
                reason="live_self_cleared",
                now=now,
            )
        return OverlayReductionResult(True, tuple(decisions))

    def apply_self_finalized_update(
        self,
        event: SelfTranscriptFinal,
        *,
        now: float,
        show_translation: bool,
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

        previous_rendered_translation_text = self._helpers.rendered_self_translation_text(entry)
        self._helpers.remember_entry_input_seq(entry, event_seq=event.seq)
        entry.original_text = event.text
        entry.original_language = event_source_language
        entry.original_seq = event.seq
        entry.last_updated_seq = event.seq
        if self._store.live_turn_key_for_channel("self") == key:
            promoted_secondary_text = entry.live_secondary_text.strip()
            if promoted_secondary_text:
                entry.translation_text = promoted_secondary_text
                entry.translation_language = entry.live_secondary_language
                entry.translation_update_id = entry.live_update_id
                entry.translation_origin_wall_clock_ms = entry.live_origin_wall_clock_ms
                entry.translation_session_scope = entry.live_session_scope
                entry.translation_source_text_hash = entry.live_source_text_hash
                entry.translation_source_text_len = entry.live_source_text_len
                entry.translation_logical_turn_key = entry.live_logical_turn_key
                if entry.live_seq is not None:
                    entry.translation_seq = entry.live_seq
                if show_translation:
                    if entry.translation_visible_since is None:
                        entry.translation_visible_since = now
                    if entry.translation_observed_visible_since is None:
                        entry.translation_observed_visible_since = now
            self._helpers.clear_self_live_payload(entry)
            self._store.set_live_turn_key_for_channel("self", None)
        self._helpers.update_self_translation_visibility(
            entry,
            previous_rendered_text=previous_rendered_translation_text,
            next_rendered_text=self._helpers.rendered_self_translation_text(entry),
            now=now,
            show_translation=show_translation,
        )
        self.retired_preview_self_seqs.pop(key, None)
        self._helpers.refresh_entry_visibility_and_expiration(
            key,
            entry,
            now=now,
            publishable_seq=event.seq,
            next_appearance_seq=next_appearance_seq,
            decisions=decisions,
        )
        return OverlayReductionResult(True, tuple(decisions))

    def apply_self_translation_update(
        self,
        event: TranslationStreamUpdate | TranslationFinal,
        *,
        now: float,
        show_translation: bool,
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
        if event_source_language is not None:
            entry.original_language = event_source_language
        previous_rendered_translation_text = self._helpers.rendered_self_translation_text(entry)
        self._helpers.remember_entry_input_seq(entry, event_seq=event.seq)
        # RETAINED HIDDEN: entries hidden from overlay (window_evicted_at set) but kept
        # alive for late-arriving translations. When translation arrives, unhide and resume.
        if entry.retained_hidden and event.text.strip():
            entry.retained_hidden = False
            entry.window_evicted_at = None
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
        if not entry.retained_hidden:
            self._helpers.update_self_translation_visibility(
                entry,
                previous_rendered_text=previous_rendered_translation_text,
                next_rendered_text=self._helpers.rendered_self_translation_text(entry),
                now=now,
                show_translation=show_translation,
            )
        entry.last_updated_seq = event.seq
        self.retired_preview_self_seqs.pop(key, None)
        self._helpers.refresh_entry_visibility_and_expiration(
            key,
            entry,
            now=now,
            publishable_seq=event.seq,
            next_appearance_seq=next_appearance_seq,
            decisions=decisions,
        )
        return OverlayReductionResult(True, tuple(decisions))

    def apply_self_utterance_closed(
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
