"""Entry storage and lifecycle management extracted from OverlayPresentationState.

Owns entries dict, live turn keys, and pending removals.
Dependencies: domain types only.
"""

from __future__ import annotations

from uuid import UUID

from puripuly_heart.domain.overlay_types import (
    ActiveSelfOverlayMetadata,
    OverlayEntryKey,
    OverlayEntryRemovalRecord,
)

__all__ = ["EntryStore"]


class EntryStore:
    """Entry storage and lifecycle management extracted from OverlayPresentationState.

    Owns:
    - entries dict (OverlayEntryKey → OverlayLogicalTurnEntry)
    - live_self_turn_key / live_peer_turn_key
    - _pending_removals
    """

    def __init__(self) -> None:
        self.entries: dict[OverlayEntryKey, object] = {}
        self.live_self_turn_key: OverlayEntryKey | None = None
        self.live_peer_turn_key: OverlayEntryKey | None = None
        self._pending_removals: list[OverlayEntryRemovalRecord] = []

    def entry_for(
        self,
        channel: str | None,
        utterance_id: UUID | None,
        entry_factory: type,
    ) -> object:
        key = self.entry_key(channel, utterance_id)
        entry = self.entries.get(key)
        if entry is None:
            entry = entry_factory(channel=key[0], utterance_id=key[1])
            self.entries[key] = entry
        return entry

    @staticmethod
    def entry_key(channel: str | None, utterance_id: UUID | None) -> OverlayEntryKey:
        if channel not in ("self", "peer"):
            raise ValueError(f"invalid overlay channel: {channel!r}")
        if utterance_id is None:
            raise ValueError("overlay presenter requires utterance_id for finalized entries")
        return (channel, utterance_id)

    def live_turn_key_for_channel(self, channel: str) -> OverlayEntryKey | None:
        if channel == "self":
            return self.live_self_turn_key
        if channel == "peer":
            return self.live_peer_turn_key
        raise ValueError(f"invalid overlay channel: {channel!r}")

    def set_live_turn_key_for_channel(
        self,
        channel: str,
        key: OverlayEntryKey | None,
    ) -> None:
        if channel == "self":
            self.live_self_turn_key = key
            return
        if channel == "peer":
            self.live_peer_turn_key = key
            return
        raise ValueError(f"invalid overlay channel: {channel!r}")

    def live_entry_for_channel(
        self,
        channel: str,
    ) -> tuple[OverlayEntryKey, object] | None:
        live_key = self.live_turn_key_for_channel(channel)
        if live_key is None:
            return None
        entry = self.entries.get(live_key)
        if entry is None:
            self.set_live_turn_key_for_channel(channel, None)
            return None
        return live_key, entry

    def active_self_overlay_metadata(self) -> ActiveSelfOverlayMetadata | None:
        live_key = self.live_self_turn_key
        if live_key is None:
            return None
        entry = self.entries.get(live_key)
        if entry is None or not entry.live_text:
            return None
        return ActiveSelfOverlayMetadata(
            text=entry.live_text,
            secondary_text=entry.live_secondary_text,
            utterance_id=entry.utterance_id,
            occupant_key=entry.occupant_key,
            update_id=entry.live_update_id,
            origin_wall_clock_ms=entry.live_origin_wall_clock_ms,
            session_scope=entry.live_session_scope,
            source_text_hash=entry.live_source_text_hash,
            source_text_len=entry.live_source_text_len,
            logical_turn_key=entry.live_logical_turn_key,
            primary_language=entry.live_primary_language,
            secondary_language=entry.live_secondary_language,
        )

    def remove_entry(
        self,
        key: OverlayEntryKey,
        *,
        reason: str,
        now: float | None = None,
        tombstone_seq: int | None = None,
    ) -> OverlayEntryRemovalRecord | None:
        if self.live_self_turn_key == key:
            self.live_self_turn_key = None
        if self.live_peer_turn_key == key:
            self.live_peer_turn_key = None
        entry = self.entries.pop(key, None)
        if entry is None:
            return None
        record = OverlayEntryRemovalRecord(
            key=key,
            entry=entry,
            reason=reason,
            now=now,
            tombstone_seq=tombstone_seq,
        )
        self._pending_removals.append(record)
        return record

    def drain_pending_removals(self) -> list[OverlayEntryRemovalRecord]:
        removals = self._pending_removals
        self._pending_removals.clear()
        return removals
