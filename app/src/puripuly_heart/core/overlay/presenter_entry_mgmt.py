from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

from .presenter_constants import (
    _CLOSED_TOMBSTONE_LIMIT,
    LATE_ARRIVAL_WINDOW_SECONDS,
    SELF_TRANSLATION_MIN_VISIBLE_SECONDS,
    VISIBLE_TTL_SECONDS,
)

if TYPE_CHECKING:
    from .protocol import OverlayPresentationBlock
    from .state import OverlayLogicalTurnEntry


class PresenterEntryMgmtMixin:
    """Entry lifecycle management: creation, expiration, tombstoning, visibility."""

    def _entry_key(self, channel: str | None, utterance_id: UUID | None) -> tuple[str, UUID]:
        if channel not in ("self", "peer"):
            raise ValueError(f"invalid overlay channel: {channel!r}")
        if utterance_id is None:
            raise ValueError("overlay presenter requires utterance_id for finalized entries")
        return (channel, utterance_id)

    def _is_tombstoned(self, channel: str | None, utterance_id: UUID | None) -> bool:
        key = self._entry_key(channel, utterance_id)
        return key in self._scene_terminal_keys or key in self._terminal_registry

    def _live_turn_key_for_channel(self, channel: str) -> tuple[str, UUID] | None:
        if channel == "self":
            return self._live_self_turn_key
        if channel == "peer":
            return self._live_peer_turn_key
        raise ValueError(f"invalid overlay channel: {channel!r}")

    def _set_live_turn_key_for_channel(
        self,
        channel: str,
        key: tuple[str, UUID] | None,
    ) -> None:
        if channel == "self":
            self._live_self_turn_key = key
            return
        if channel == "peer":
            self._live_peer_turn_key = key
            return
        raise ValueError(f"invalid overlay channel: {channel!r}")

    def _live_entry_for_channel(
        self,
        channel: str,
    ) -> tuple[tuple[str, UUID], OverlayLogicalTurnEntry] | None:
        live_key = self._live_turn_key_for_channel(channel)
        if live_key is None:
            return None
        entry = self._entries.get(live_key)
        if entry is None:
            self._set_live_turn_key_for_channel(channel, None)
            return None
        return live_key, entry

    def _live_self_entry(self) -> tuple[tuple[str, UUID], OverlayLogicalTurnEntry] | None:
        return self._live_entry_for_channel("self")

    def _live_peer_entry(self) -> tuple[tuple[str, UUID], OverlayLogicalTurnEntry] | None:
        return self._live_entry_for_channel("peer")

    def _refresh_visible_expiration_deadlines(
        self,
        rendered_entries: list[tuple[tuple[str, UUID], OverlayPresentationBlock]],
        *,
        previous_blocks: list[OverlayPresentationBlock],
        now: float,
    ) -> None:
        previous_signatures = {
            block.id: self._presentation_state.visible_block_content_signature(block)
            for block in previous_blocks
        }
        for key, block in rendered_entries:
            if previous_signatures.get(
                block.id
            ) == self._presentation_state.visible_block_content_signature(block):
                continue
            entry = self._entries.get(key)
            if entry is None:
                continue
            entry.ever_visible = True
            if entry.visible_since is None:
                entry.visible_since = now
            entry.last_meaningful_visible_at = now
            self._schedule_expiration(key, entry)

    def _prune_displaced_finalized_entries(
        self,
        visible_entry_keys: set[tuple[str, UUID]],
        *,
        candidate_keys: list[tuple[str, UUID]],
    ) -> None:
        displaced_keys = [
            key
            for key in candidate_keys
            if (entry := self._entries.get(key)) is not None
            and self._presentation_state.entry_is_selectable(
                entry,
                show_peer_original=self.show_peer_original,
            )
            and key not in visible_entry_keys
        ]
        for key in displaced_keys:
            entry = self._entries.get(key)
            if entry is None:
                continue
            self._remove_entry(
                key,
                reason="evicted_by_newer_turn",
                now=self.clock.now(),
                tombstone_seq=entry.last_updated_seq,
            )

    def _mark_entries_visible(self, visible_entry_keys: list[tuple[str, UUID]]) -> None:
        for key in visible_entry_keys:
            entry = self._entries.get(key)
            if entry is not None:
                if entry.retained_hidden:
                    entry.retained_hidden = False
                    entry.window_evicted_at = None
                    self._schedule_expiration(key, entry)
                entry.ever_visible = True

    def _next_appearance_seq(self) -> int:
        self._appearance_seq += 1
        return self._appearance_seq

    def _remember_tombstone(self, key: tuple[str, UUID], closed_seq: int) -> None:
        self._terminal_registry.pop(key, None)
        self._terminal_registry[key] = closed_seq
        while len(self._terminal_registry) > _CLOSED_TOMBSTONE_LIMIT:
            self._terminal_registry.popitem(last=False)

    def _schedule_expiration(
        self,
        key: tuple[str, UUID],
        entry: OverlayLogicalTurnEntry,
    ) -> None:
        self._cancel_expiration_task(key)
        if self._entry_expiration_deadline(entry) is None:
            return
        entry.expiration_revision += 1
        self._record_deadline(entry)
        self._expiration_tasks[key] = asyncio.create_task(
            self._expire_entry_after_ttl(key, entry.expiration_revision)
        )

    async def _expire_entry_after_ttl(
        self, key: tuple[str, UUID], expiration_revision: int
    ) -> None:
        try:
            while True:
                entry = self._entries.get(key)
                if entry is None or entry.expiration_revision != expiration_revision:
                    return

                deadline = self._entry_expiration_deadline(entry)
                if deadline is None:
                    return
                remaining = deadline - self.clock.now()
                if remaining > 0:
                    await self.sleep(remaining)
                    continue

                self._remove_entry(
                    key,
                    reason="expired",
                    now=self.clock.now(),
                    current_task=self._current_task(),
                    tombstone_seq=entry.last_updated_seq if entry.closed_seq is None else None,
                )
                await self._publish_if_changed()
                return
        except asyncio.CancelledError:
            raise
        finally:
            current_task = self._current_task()
            if current_task is not None and self._expiration_tasks.get(key) is current_task:
                self._expiration_tasks.pop(key, None)

    def _expire_closed_entries(self, *, now: float) -> None:
        current_task = self._current_task()
        self._presentation_state.expire_entries(
            now=now,
            show_translation=self.show_translation,
            late_arrival_window_seconds=LATE_ARRIVAL_WINDOW_SECONDS,
            visible_ttl_seconds=VISIBLE_TTL_SECONDS,
            self_translation_min_visible_seconds=SELF_TRANSLATION_MIN_VISIBLE_SECONDS,
        )
        self._drain_presentation_state_removals(current_task=current_task)

    def _entry_expiration_deadline(self, entry: OverlayLogicalTurnEntry) -> float | None:
        return self._entry_expiration_components(entry)[0]

    def _entry_expiration_components(
        self,
        entry: OverlayLogicalTurnEntry,
    ) -> tuple[float | None, float | None, float | None]:
        return self._presentation_state.entry_expiration_components(
            entry,
            show_translation=self.show_translation,
            late_arrival_window_seconds=LATE_ARRIVAL_WINDOW_SECONDS,
            visible_ttl_seconds=VISIBLE_TTL_SECONDS,
            self_translation_min_visible_seconds=SELF_TRANSLATION_MIN_VISIBLE_SECONDS,
        )

    def _remove_entry(
        self,
        key: tuple[str, UUID],
        *,
        reason: str,
        now: float | None = None,
        current_task: asyncio.Task[None] | None = None,
        tombstone_seq: int | None = None,
    ) -> None:
        if self._expiration_tasks.get(key) is not current_task:
            self._cancel_expiration_task(key)
        self._presentation_state.remove_entry(
            key,
            reason=reason,
            now=now,
            tombstone_seq=tombstone_seq,
        )
        self._drain_presentation_state_removals(current_task=current_task)

    def _drain_presentation_state_removals(
        self,
        *,
        current_task: asyncio.Task[None] | None = None,
    ) -> None:
        for record in self._presentation_state.drain_pending_removals():
            if self._expiration_tasks.get(record.key) is not current_task:
                self._cancel_expiration_task(record.key)
            self._record_removed_entry(record)

    def _cancel_expiration_task(self, key: tuple[str, UUID]) -> None:
        task = self._expiration_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    def _cancel_all_expiration_tasks(self) -> None:
        for task in self._expiration_tasks.values():
            if not task.done():
                task.cancel()
        self._expiration_tasks.clear()

    def _clear_entries_for_reason(self, reason: str) -> None:
        for key in list(self._entries):
            self._remove_entry(key, reason=reason, now=self.clock.now())

    def _current_task(self) -> asyncio.Task[None] | None:
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None
