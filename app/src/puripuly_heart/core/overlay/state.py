from __future__ import annotations

"""Overlay presentation state — pure composition root.

OverlayPresentationState owns the extracted modules (EntryStore,
EventHelpers, SelfEventDispatcher, PeerEventDispatcher, RefreshManager,
ExpirationEngine, SelectionEngine, BlockRenderer) and delegates every
public API method to the appropriate module.

Data types (OverlayLogicalTurnEntry, OverlayEntryRemovalRecord,
OverlayTurnDecisionRecord, OverlayReductionResult, OverlayVisibleBlockSelection)
live in domain/overlay_types.py.
"""

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from puripuly_heart.domain.overlay_types import (
    ActiveSelfOverlayMetadata,
    OverlayEntryKey,
    OverlayEntryRemovalRecord,
    OverlayLogicalTurnEntry,
    OverlayPresentationBlock,
    OverlayPresentationCalibration,
    OverlayPresentationSnapshot,
    OverlayReductionResult,
    OverlayTurnDecisionRecord,
    OverlayVisibleBlockSelection,
)
from puripuly_heart.core.overlay.block_renderer import BlockRenderer
from puripuly_heart.core.overlay.entry_store import EntryStore
from puripuly_heart.core.overlay.event_helpers import EventHelpers
from puripuly_heart.core.overlay.expiration_engine import ExpirationEngine
from puripuly_heart.core.overlay.peer_event_dispatcher import PeerEventDispatcher
from puripuly_heart.core.overlay.refresh_manager import RefreshManager
from puripuly_heart.core.overlay.selection_engine import SelectionEngine
from puripuly_heart.core.overlay.self_event_dispatcher import SelfEventDispatcher
from puripuly_heart.domain.overlay_events import (
    PeerActiveUpdate,
    PeerTranscriptFinal,
    SelfActiveClear,
    SelfActiveUpdate,
    SelfTranscriptFinal,
    TranslationFinal,
    TranslationStreamUpdate,
    UtteranceClosed,
)

from puripuly_heart.core.overlay.types_common import (
    NextAppearanceSeq,
    OverlayPresentationEntry,
    OverlayTerminalUpdatePredicate,
    OverlayTerminalUpdateReason,
)
from puripuly_heart.ports.overlay_modules import (
    EntryStoreProtocol,
    SelfEventDispatcherProtocol,
    PeerEventDispatcherProtocol,
    EventHelpersProtocol,
    RefreshManagerProtocol,
    ExpirationEngineProtocol,
    SelectionEngineProtocol,
    BlockRendererProtocol,
)


# ── Composition root ──────────────────────────────────────────────────


@dataclass
class OverlayPresentationState:
    """Pure presentation state for overlay snapshots.

    This object intentionally does not own async tasks, sleeps, timers,
    cancellation, bridge I/O, provider I/O, OpenVR I/O, native overlay I/O,
    or dashboard side effects.

    CROSS-MODULE INVARIANT: OverlayPresenter (presenter.py) owns the lifecycle
    of this object and calls apply_* methods in response to overlay events.
    The presenter is responsible for calling generate_snapshot() after each
    reduction to update the bridge-visible snapshot.
    """

    entries: dict[OverlayEntryKey, OverlayLogicalTurnEntry] = field(default_factory=dict)
    _store: EntryStoreProtocol = field(init=False, repr=False)
    _event_helpers: EventHelpersProtocol = field(init=False, repr=False)
    _self_dispatcher: SelfEventDispatcherProtocol = field(init=False, repr=False)
    _peer_dispatcher: PeerEventDispatcherProtocol = field(init=False, repr=False)
    retired_preview_self_seqs: OrderedDict[OverlayEntryKey, int] = field(
        default_factory=OrderedDict
    )
    _refresh: RefreshManagerProtocol = field(init=False, repr=False)
    _expiration: ExpirationEngineProtocol = field(init=False, repr=False)
    _renderer: BlockRendererProtocol = field(init=False, repr=False)
    _selection: SelectionEngineProtocol = field(init=False, repr=False)
    _pending_removals: list[OverlayEntryRemovalRecord] = field(default_factory=list)
    _snapshot: OverlayPresentationSnapshot = field(default_factory=OverlayPresentationSnapshot)

    def __post_init__(self) -> None:
        self._store: EntryStoreProtocol = EntryStore()
        self._store.entries = self.entries
        self._store._pending_removals = self._pending_removals
        self._expiration: ExpirationEngineProtocol = ExpirationEngine(self._store)
        self._refresh: RefreshManagerProtocol = RefreshManager(on_snapshot=lambda: self._snapshot)
        # Composition order is deliberate: BlockRenderer before SelectionEngine (circular dependency resolved via callback)
        # BlockRenderer created before SelectionEngine so its build_presentation_block
        # can be passed as callback. selection is wired after SelectionEngine creation.
        self._renderer: BlockRendererProtocol = BlockRenderer(self._refresh)
        self._selection: SelectionEngineProtocol = SelectionEngine(
            self._store,
            on_build_presentation_block=self._renderer.build_presentation_block,
        )
        self._renderer._selection = self._selection
        self._event_helpers: EventHelpersProtocol = EventHelpers(
            self._store,
            self.retired_preview_self_seqs,
            on_entry_is_publishable=self.entry_is_publishable,
            on_ensure_entry_visibility_metadata=self._ensure_entry_visibility_metadata,
            on_finalized_occupant_key=self._finalized_occupant_key,
        )
        self._self_dispatcher: SelfEventDispatcherProtocol = SelfEventDispatcher(
            self._store, self._event_helpers, self.retired_preview_self_seqs
        )
        self._peer_dispatcher: PeerEventDispatcherProtocol = PeerEventDispatcher(self._store, self._event_helpers)

    def snapshot(self) -> OverlayPresentationSnapshot:
        return self._snapshot

    def active_self_overlay_metadata(self) -> ActiveSelfOverlayMetadata | None:
        return self._store.active_self_overlay_metadata()

    def drain_pending_removals(self) -> list[OverlayEntryRemovalRecord]:
        return self._store.drain_pending_removals()

    # ── Public accessors for live turn keys ────────────────────────────

    @property
    def live_self_turn_key(self) -> OverlayEntryKey | None:
        return self._store.live_self_turn_key

    @live_self_turn_key.setter
    def live_self_turn_key(self, value: OverlayEntryKey | None) -> None:
        self._store.live_self_turn_key = value

    @property
    def live_peer_turn_key(self) -> OverlayEntryKey | None:
        return self._store.live_peer_turn_key

    @live_peer_turn_key.setter
    def live_peer_turn_key(self, value: OverlayEntryKey | None) -> None:
        self._store.live_peer_turn_key = value

    # ── Public accessors for refresh target keys ───────────────────────

    @property
    def peer_presentation_refresh_target_key(self) -> OverlayEntryKey | None:
        return self._refresh.peer_presentation_refresh_target_key

    @property
    def self_presentation_refresh_target_key(self) -> OverlayEntryKey | None:
        return self._refresh.self_presentation_refresh_target_key

    def entry_for(
        self,
        channel: str | None,
        utterance_id: UUID | None,
    ) -> OverlayLogicalTurnEntry:
        key = self.entry_key(channel, utterance_id)
        entry = self._store.entries.get(key)
        if entry is None:
            entry = OverlayLogicalTurnEntry(channel=key[0], utterance_id=key[1])
            self._store.entries[key] = entry
        return entry

    def entry_key(self, channel: str | None, utterance_id: UUID | None) -> OverlayEntryKey:
        return EntryStore.entry_key(channel, utterance_id)

    def live_turn_key_for_channel(self, channel: str) -> OverlayEntryKey | None:
        return self._store.live_turn_key_for_channel(channel)

    def set_live_turn_key_for_channel(
        self,
        channel: str,
        key: OverlayEntryKey | None,
    ) -> None:
        self._store.set_live_turn_key_for_channel(channel, key)

    def live_entry_for_channel(
        self,
        channel: str,
    ) -> tuple[OverlayEntryKey, OverlayLogicalTurnEntry] | None:
        return self._store.live_entry_for_channel(channel)

    # PRESENTATION REFRESH BURST: forces the native overlay to re-render blocks
    # even when the text hasn't changed. Without this, revision-based dedup would
    # suppress the GPU render and the HMD would show stale frames.
    #
    # Lifecycle: begin (reset nonce) → tick (increment nonce per frame) → end (clear).
    # The nonce is injected into session_scope as "peer_presentation_refresh=<n>",
    # making each frame's metadata revision-worthy.

    def begin_peer_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        return self._refresh.begin_peer_presentation_refresh(key)

    def tick_peer_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        return self._refresh.tick_peer_presentation_refresh(key)

    def end_peer_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        return self._refresh.end_peer_presentation_refresh(key)

    def begin_self_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        return self._refresh.begin_self_presentation_refresh(key)

    def tick_self_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        return self._refresh.tick_self_presentation_refresh(key)

    def end_self_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        return self._refresh.end_self_presentation_refresh(key)

    def remove_entry(
        self,
        key: OverlayEntryKey,
        *,
        reason: str,
        now: float | None = None,
        tombstone_seq: int | None = None,
    ) -> OverlayEntryRemovalRecord | None:
        return self._store.remove_entry(
            key, reason=reason, now=now, tombstone_seq=tombstone_seq,
        )

    def apply_self_active_update(
        self,
        event: SelfActiveUpdate,
        *,
        now: float,
        show_translation: bool,
        terminal_update_reason: OverlayTerminalUpdateReason,
    ) -> OverlayReductionResult:
        return self._self_dispatcher.apply_self_active_update(
            event,
            now=now,
            show_translation=show_translation,
            terminal_update_reason=terminal_update_reason,
        )

    def apply_self_active_clear(
        self,
        event: SelfActiveClear,
        *,
        now: float,
        show_translation: bool,
    ) -> OverlayReductionResult:
        return self._self_dispatcher.apply_self_active_clear(
            event,
            now=now,
            show_translation=show_translation,
        )

    def apply_self_finalized_update(
        self,
        event: SelfTranscriptFinal,
        *,
        now: float,
        show_translation: bool,
        next_appearance_seq: NextAppearanceSeq,
        terminal_update_reason: OverlayTerminalUpdateReason,
    ) -> OverlayReductionResult:
        return self._self_dispatcher.apply_self_finalized_update(
            event,
            now=now,
            show_translation=show_translation,
            next_appearance_seq=next_appearance_seq,
            terminal_update_reason=terminal_update_reason,
        )

    def apply_self_translation_update(
        self,
        event: TranslationStreamUpdate | TranslationFinal,
        *,
        now: float,
        show_translation: bool,
        next_appearance_seq: NextAppearanceSeq,
        terminal_update_reason: OverlayTerminalUpdateReason,
    ) -> OverlayReductionResult:
        return self._self_dispatcher.apply_self_translation_update(
            event,
            now=now,
            show_translation=show_translation,
            next_appearance_seq=next_appearance_seq,
            terminal_update_reason=terminal_update_reason,
        )

    def apply_self_utterance_closed(
        self,
        event: UtteranceClosed,
        *,
        now: float,
        is_tombstoned: OverlayTerminalUpdatePredicate,
    ) -> OverlayReductionResult:
        return self._self_dispatcher.apply_self_utterance_closed(
            event,
            now=now,
            is_tombstoned=is_tombstoned,
        )

    # SELF vs PEER BEHAVIOR:
    # - Self: live_text shows during speech, then finalized_text takes over after STT final.
    #   Translation appears as secondary text.
    # - Peer: NO live text shown to user (product decision). Peer active updates only
    #   populate internal state for translation context. Visible text arrives only with
    #   translation (apply_peer_translation_update) or source-only finalization.
    # Product decision: peer overlay text is emitted with translation arrival. Peer source-only/active updates must not become visible normal-flow rows.
    # Product decision: peer live_text is hidden — only translation becomes primary-visible (see block_renderer._build_peer_block)
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
        return self._peer_dispatcher.apply_peer_active_update(
            event,
            now=now,
            show_peer_original=show_peer_original,
            next_appearance_seq=next_appearance_seq,
            terminal_update_reason=terminal_update_reason,
        )

    def apply_peer_finalized_update(
        self,
        event: PeerTranscriptFinal,
        *,
        now: float,
        show_peer_original: bool,
        next_appearance_seq: NextAppearanceSeq,
        terminal_update_reason: OverlayTerminalUpdateReason,
    ) -> OverlayReductionResult:
        return self._peer_dispatcher.apply_peer_finalized_update(
            event,
            now=now,
            show_peer_original=show_peer_original,
            next_appearance_seq=next_appearance_seq,
            terminal_update_reason=terminal_update_reason,
        )

    def apply_peer_translation_update(
        self,
        event: TranslationStreamUpdate | TranslationFinal,
        *,
        now: float,
        show_peer_original: bool,
        next_appearance_seq: NextAppearanceSeq,
        terminal_update_reason: OverlayTerminalUpdateReason,
    ) -> OverlayReductionResult:
        return self._peer_dispatcher.apply_peer_translation_update(
            event,
            now=now,
            show_peer_original=show_peer_original,
            next_appearance_seq=next_appearance_seq,
            terminal_update_reason=terminal_update_reason,
        )

    def apply_peer_utterance_closed(
        self,
        event: UtteranceClosed,
        *,
        now: float,
        is_tombstoned: OverlayTerminalUpdatePredicate,
    ) -> OverlayReductionResult:
        return self._peer_dispatcher.apply_peer_utterance_closed(
            event,
            now=now,
            is_tombstoned=is_tombstoned,
        )

    def expire_entries(
        self,
        *,
        now: float,
        show_translation: bool,
        late_arrival_window_seconds: float,
        visible_ttl_seconds: float,
        self_translation_min_visible_seconds: float,
    ) -> bool:
        return self._expiration.expire_entries(
            now=now,
            show_translation=show_translation,
            late_arrival_window_seconds=late_arrival_window_seconds,
            visible_ttl_seconds=visible_ttl_seconds,
            self_translation_min_visible_seconds=self_translation_min_visible_seconds,
        )

    # SNAPSHOT CACHING: _snapshot is updated only through generate_snapshot().
    # snapshot() returns the cached value — it does NOT recompute.
    # OverlayPresenter must call generate_snapshot() after each reduction cycle.
    # Calling snapshot() without prior generate_snapshot() returns stale data.
    def generate_snapshot(
        self,
        *,
        revision: int,
        calibration: OverlayPresentationCalibration,
        rendered_entries: list[tuple[OverlayEntryKey, OverlayPresentationBlock]],
    ) -> OverlayPresentationSnapshot:
        snapshot = OverlayPresentationSnapshot(
            revision=revision,
            calibration=calibration,
            blocks=[block for _, block in rendered_entries],
        )
        self._snapshot = snapshot
        return snapshot

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
        return self._selection.visible_block_selection(
            entries=entries,
            live_self_entry=live_self_entry,
            live_peer_entry=live_peer_entry,
            visible_window_target_blocks=visible_window_target_blocks,
            show_translation=show_translation,
            show_peer_original=show_peer_original,
            peer_presentation_refresh_burst=peer_presentation_refresh_burst,
            next_appearance_seq=next_appearance_seq,
            self_presentation_refresh_burst=self_presentation_refresh_burst,
        )

    def entry_is_publishable(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_peer_original: bool,
    ) -> bool:
        return self._selection.entry_is_publishable(
            entry,
            show_peer_original=show_peer_original,
        )

    def entry_is_selectable(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_peer_original: bool,
    ) -> bool:
        return self._selection.entry_is_selectable(
            entry,
            show_peer_original=show_peer_original,
        )

    # DEDUP SIGNATURE: two blocks with identical signatures are considered the same
    # and the native overlay won't re-render. Self-refresh metadata is included only
    # for finalized self blocks with active refresh — this forces re-render during
    # presentation refresh bursts even when text hasn't changed.
    def rendered_block_signature(
        self,
        block: OverlayPresentationBlock,
    ) -> tuple[object, ...]:
        return self._renderer.rendered_block_signature(block)

    def rendered_blocks_signature(
        self,
        blocks: list[OverlayPresentationBlock],
    ) -> tuple[object, ...]:
        return self._renderer.rendered_blocks_signature(blocks)

    def visible_block_content_signature(
        self,
        block: OverlayPresentationBlock,
    ) -> tuple[str, str, str, bool]:
        return self._renderer.visible_block_content_signature(block)

    def entry_expiration_deadline(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_translation: bool,
        late_arrival_window_seconds: float,
        visible_ttl_seconds: float,
        self_translation_min_visible_seconds: float,
    ) -> float | None:
        return self._expiration.entry_expiration_deadline(
            entry,
            show_translation=show_translation,
            late_arrival_window_seconds=late_arrival_window_seconds,
            visible_ttl_seconds=visible_ttl_seconds,
            self_translation_min_visible_seconds=self_translation_min_visible_seconds,
        )

    def entry_expiration_components(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_translation: bool,
        late_arrival_window_seconds: float,
        visible_ttl_seconds: float,
        self_translation_min_visible_seconds: float,
    ) -> tuple[float | None, float | None, float | None]:
        return self._expiration.entry_expiration_components(
            entry,
            show_translation=show_translation,
            late_arrival_window_seconds=late_arrival_window_seconds,
            visible_ttl_seconds=visible_ttl_seconds,
            self_translation_min_visible_seconds=self_translation_min_visible_seconds,
        )

    # TERMINAL UPDATE GATE: after an entry is evicted (newer turn took its slot)
    # or expired (idle TTL fired), all late-arriving updates are rejected.
    # Returns True if the update was rejected (caller should abort), False if OK.
    # This prevents stale STT/translation events from resurrecting dead entries.
    def _append_terminal_update_decision(
        self,
        terminal_reason: str | None,
        *,
        key: OverlayEntryKey,
        decisions: list[OverlayTurnDecisionRecord],
    ) -> bool:
        return self._event_helpers.append_terminal_update_decision(
            terminal_reason, key=key, decisions=decisions,
        )

    def _active_update_entry_or_none(
        self,
        *,
        channel: str,
        utterance_id: UUID | None,
        event_seq: int,
        now: float,
        show_translation: bool,
        terminal_update_reason: OverlayTerminalUpdateReason,
        decisions: list[OverlayTurnDecisionRecord],
    ) -> tuple[OverlayEntryKey, OverlayLogicalTurnEntry] | None:
        return self._event_helpers.active_update_entry_or_none(
            channel=channel,
            utterance_id=utterance_id,
            event_seq=event_seq,
            now=now,
            show_translation=show_translation,
            terminal_update_reason=terminal_update_reason,
            decisions=decisions,
        )

    def _active_update_matches_live_payload(
        self,
        *,
        channel: str,
        key: OverlayEntryKey,
        entry: OverlayLogicalTurnEntry,
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
        return self._event_helpers.active_update_matches_live_payload(
            channel=channel,
            key=key,
            entry=entry,
            text=text,
            secondary_text=secondary_text,
            occupant_key=occupant_key,
            update_id=update_id,
            origin_wall_clock_ms=origin_wall_clock_ms,
            session_scope=session_scope,
            source_text_hash=source_text_hash,
            source_text_len=source_text_len,
            logical_turn_key=logical_turn_key,
        )

    def _next_translation_visible_since(
        self,
        *,
        previous_text: str,
        next_text: str,
        previous_visible_since: float | None,
        now: float,
    ) -> float | None:
        return self._event_helpers._next_translation_visible_since(
            previous_text=previous_text,
            next_text=next_text,
            previous_visible_since=previous_visible_since,
            now=now,
        )

    def _remember_entry_input_seq(
        self,
        entry: OverlayLogicalTurnEntry,
        *,
        event_seq: int,
    ) -> None:
        self._event_helpers.remember_entry_input_seq(entry, event_seq=event_seq)

    def _refresh_entry_visibility_and_expiration(
        self,
        key: OverlayEntryKey,
        entry: OverlayLogicalTurnEntry,
        *,
        now: float,
        publishable_seq: int | None,
        next_appearance_seq: NextAppearanceSeq,
        show_peer_original: bool = True,
        decisions: list[OverlayTurnDecisionRecord],
    ) -> None:
        self._event_helpers.refresh_entry_visibility_and_expiration(
            key,
            entry,
            now=now,
            publishable_seq=publishable_seq,
            next_appearance_seq=next_appearance_seq,
            show_peer_original=show_peer_original,
            decisions=decisions,
        )

    def _should_retire_preview_only_self_entry(
        self,
        entry: OverlayLogicalTurnEntry,
    ) -> bool:
        return self._event_helpers._should_retire_preview_only_self_entry(entry)

    def _retire_preview_only_self_entry(
        self,
        key: OverlayEntryKey,
        entry: OverlayLogicalTurnEntry,
        *,
        reason: str,
        now: float,
    ) -> None:
        self._event_helpers._retire_preview_only_self_entry(
            key, entry, reason=reason, now=now,
        )

    def _remember_retired_preview_self_seq(
        self,
        key: OverlayEntryKey,
        retired_seq: int,
    ) -> None:
        self._event_helpers._remember_retired_preview_self_seq(key, retired_seq)

    # SELECTION ALGORITHM: picks which finalized entries appear in the overlay window.
    # Active (live) entries are protected and always shown — they're excluded from
    # this selection. The remaining finalized entries are sorted by (appearance_seq,
    # occupant_key, formatted_key) and the most recent `finalized_limit` are selected.
    # Display order uses appearance_seq; selection order uses publishable_seq (most
    # recently finalized wins the limited slots).
    def _logical_visible_entry_keys(
        self,
        *,
        entries: Mapping[OverlayEntryKey, OverlayPresentationEntry],
        finalized_limit: int,
        excluded_keys: set[OverlayEntryKey],
        show_peer_original: bool,
        next_appearance_seq: NextAppearanceSeq,
    ) -> tuple[list[OverlayEntryKey], list[OverlayEntryKey]]:
        return self._selection._logical_visible_entry_keys(
            entries=entries,
            finalized_limit=finalized_limit,
            excluded_keys=excluded_keys,
            show_peer_original=show_peer_original,
            next_appearance_seq=next_appearance_seq,
        )

    # RENDERING LOGIC: builds the OverlayPresentationBlock for a single entry.
    # Three branches by channel/state:
    #   prefer_live_self → active_self variant (live text as primary, translation as secondary)
    #   peer + translation → finalized variant (translation as primary, original as secondary)
    #   peer + no translation → active_peer or source-only finalized
    #   self finalized → finalized variant (original as primary, translation as secondary)
    #
    # peer_presentation_refresh_burst injects refresh nonce into session_scope
    # to force native overlay re-render (see PRESENTATION REFRESH BURST comment).
    def _build_presentation_block(
        self,
        entry: OverlayPresentationEntry,
        *,
        prefer_live_self: bool = False,
        show_translation: bool,
        show_peer_original: bool,
        peer_presentation_refresh_burst: bool,
        self_presentation_refresh_burst: bool = True,
    ) -> OverlayPresentationBlock | None:
        return self._renderer.build_presentation_block(
            entry,
            prefer_live_self=prefer_live_self,
            show_translation=show_translation,
            show_peer_original=show_peer_original,
            peer_presentation_refresh_burst=peer_presentation_refresh_burst,
            self_presentation_refresh_burst=self_presentation_refresh_burst,
        )

    def _live_peer_entry_is_drawable(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_peer_original: bool,
    ) -> bool:
        return self._selection._live_peer_entry_is_drawable(
            entry,
            show_peer_original=show_peer_original,
        )

    def _ensure_entry_visibility_metadata(
        self,
        entry: OverlayPresentationEntry,
        *,
        occupant_key: str,
        next_appearance_seq: NextAppearanceSeq,
        publishable_seq: int | None = None,
    ) -> None:
        return self._selection._ensure_entry_visibility_metadata(
            entry,
            occupant_key=occupant_key,
            next_appearance_seq=next_appearance_seq,
            publishable_seq=publishable_seq,
        )

    def _block_appearance_seq(self, entry: OverlayPresentationEntry) -> int:
        return RefreshManager.block_appearance_seq(
            entry.appearance_seq,
            entry.first_input_seq,
            entry.last_updated_seq,
        )

    def _peer_session_scope_with_presentation_refresh(
        self,
        entry: OverlayPresentationEntry,
        session_scope: str | None,
        *,
        peer_presentation_refresh_burst: bool,
    ) -> str | None:
        return self._refresh.peer_session_scope_with_presentation_refresh(
            entry.channel,
            entry.utterance_id,
            session_scope,
            peer_presentation_refresh_burst=peer_presentation_refresh_burst,
        )

    def _self_session_scope_with_presentation_refresh(
        self,
        entry: OverlayPresentationEntry,
        session_scope: str | None,
        *,
        primary_text: str,
        block_variant: str,
        self_presentation_refresh_burst: bool,
    ) -> str | None:
        return self._refresh.self_session_scope_with_presentation_refresh(
            entry.channel,
            entry.utterance_id,
            session_scope,
            primary_text=primary_text,
            block_variant=block_variant,
            self_presentation_refresh_burst=self_presentation_refresh_burst,
        )

    def _finalized_occupant_key(self, channel: str, utterance_id: UUID) -> str:
        return self._selection._finalized_occupant_key(channel, utterance_id)
