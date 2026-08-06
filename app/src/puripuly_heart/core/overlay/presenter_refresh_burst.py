"""Overlay presentation refresh bursts — periodic re-publishing after updates.

When a new translation arrives, the overlay entry is re-published
periodically for PEER_PRESENTATION_REFRESH_BURST_SECONDS to ensure the
SteamVR overlay renders the updated content.  Two independent burst types:

- **Peer burst**: triggered by peer translation events, refreshes the
  peer overlay block until deadline or content changes.  Uses
  sequence-based dedup (entry.last_updated_seq == event.seq).
- **Self burst**: triggered by self transcript/translation events, refreshes
  the self overlay block.  More complex — tracks cancel reasons and
  cleanup publish counts for diagnostic logging.  Uses content signature
  comparison to prevent unnecessary bursts.

The self burst uses a done callback for edge-case cleanup; the peer burst
relies on its finally block.

Called by overlay/presenter.py (event handling).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

from .presenter_constants import (
    PEER_PRESENTATION_REFRESH_BURST_INTERVAL_SECONDS,
    PEER_PRESENTATION_REFRESH_BURST_SECONDS,
)
from puripuly_heart.domain.overlay_events import (
    PeerActiveUpdate,
    PeerTranscriptFinal,
    SelfTranscriptFinal,
    TranslationFinal,
    TranslationStreamUpdate,
)

if TYPE_CHECKING:
    from puripuly_heart.domain.overlay_types import (
        OverlayPresentationBlock,
        OverlayPresentationSnapshot,
    )
    from puripuly_heart.domain.overlay_events import OverlayEventUnion


class PresenterRefreshBurstMixin:
    """Peer and self presentation refresh burst lifecycle management."""

    def _self_presentation_refresh_key_for_event(
        self,
        event: OverlayEventUnion,
    ) -> tuple[str, UUID] | None:
        if not self.self_presentation_refresh_burst:
            return None
        if event.channel != "self" or event.utterance_id is None:
            return None
        if isinstance(event, (SelfTranscriptFinal, TranslationFinal)):
            return ("self", event.utterance_id)
        return None

    def _snapshot_has_refreshable_self_key(self, key: tuple[str, UUID]) -> bool:
        return self._refreshable_self_block_in_snapshot(self.snapshot(), key) is not None

    def _self_presentation_refresh_request_key_for_event(
        self,
        event: OverlayEventUnion,
        *,
        previous_snapshot: OverlayPresentationSnapshot,
    ) -> tuple[str, UUID] | None:
        key = self._self_presentation_refresh_key_for_event(event)
        if key is None:
            return None
        current_block = self._refreshable_self_block_in_snapshot(self.snapshot(), key)
        if current_block is None:
            return None
        previous_block = self._refreshable_self_block_in_snapshot(previous_snapshot, key)
        previous_signature = (
            self._presentation_state.visible_block_content_signature(previous_block)
            if previous_block is not None
            else None
        )
        current_signature = self._presentation_state.visible_block_content_signature(current_block)
        if previous_signature == current_signature:
            return None
        return key

    def _refreshable_self_block_in_snapshot(
        self,
        snapshot: OverlayPresentationSnapshot,
        key: tuple[str, UUID],
    ) -> OverlayPresentationBlock | None:
        if key[0] != "self":
            return None
        block_id = f"self:{key[1]}"
        for block in snapshot.blocks:
            if block.channel != "self" or block.id != block_id:
                continue
            if block.block_variant == "finalized" and block.primary_text.strip():
                return block
        return None

    def _peer_presentation_refresh_key_for_event(
        self,
        event: OverlayEventUnion,
    ) -> tuple[str, UUID] | None:
        if event.channel != "peer" or event.utterance_id is None:
            return None
        if isinstance(
            event,
            (
                PeerActiveUpdate,
                PeerTranscriptFinal,
                TranslationStreamUpdate,
                TranslationFinal,
            ),
        ):
            return ("peer", event.utterance_id)
        return None

    def _snapshot_has_refreshable_peer_key(self, key: tuple[str, UUID]) -> bool:
        block_id = f"{key[0]}:{key[1]}"
        for block in self.snapshot().blocks:
            if block.channel != "peer" or block.id != block_id:
                continue
            # Only normal product rows made primary-visible by peer translation
            # arrival are refreshable. Source-only finalized rows and reserved
            # active_peer compatibility rows must not start the burst.
            if block.block_variant == "finalized" and block.primary_text.strip():
                return True
        return False

    def _peer_presentation_refresh_event_is_current(self, event: OverlayEventUnion) -> bool:
        key = self._peer_presentation_refresh_key_for_event(event)
        if key is None:
            return False
        entry = self._entries.get(key)
        return entry is not None and entry.last_updated_seq == event.seq

    async def _start_peer_presentation_refresh_burst_for_event(
        self,
        event: OverlayEventUnion,
    ) -> None:
        if not self.peer_presentation_refresh_burst:
            return
        key = self._peer_presentation_refresh_key_for_event(event)
        if key is None or not self._snapshot_has_refreshable_peer_key(key):
            return
        needs_clean_publish = self._presentation_state.begin_peer_presentation_refresh(key)
        self._cancel_peer_presentation_refresh_burst_task()
        if needs_clean_publish:
            await self._publish_if_changed()
        self._peer_presentation_refresh_burst_task = asyncio.create_task(
            self._run_peer_presentation_refresh_burst(key)
        )

    async def _start_self_presentation_refresh_burst_for_event(
        self,
        event: OverlayEventUnion,
        *,
        previous_snapshot: OverlayPresentationSnapshot,
    ) -> None:
        key = self._self_presentation_refresh_request_key_for_event(
            event,
            previous_snapshot=previous_snapshot,
        )
        if key is None:
            return
        needs_clean_publish = self._presentation_state.begin_self_presentation_refresh(key)
        self._cancel_self_presentation_refresh_burst_task(
            reason="target_replaced",
            cleanup_publish_count=1 if needs_clean_publish else 0,
        )
        if needs_clean_publish:
            await self._publish_if_changed()
        self._record_self_presentation_refresh_burst_start(
            key,
            reason="eligible_finalized_self_update",
        )
        self._self_presentation_refresh_burst_task = (
            self._create_self_presentation_refresh_burst_task(key)
        )

    async def _run_peer_presentation_refresh_burst(self, key: tuple[str, UUID]) -> None:
        # Each tick MUST call _publish_if_changed — the nonce increment in
        # state.tick_peer_presentation_refresh makes this tick revision-worthy,
        # forcing the SteamVR overlay renderer to do fresh GPU work.
        deadline = self.clock.now() + PEER_PRESENTATION_REFRESH_BURST_SECONDS
        try:
            while self.peer_presentation_refresh_burst and self.clock.now() < deadline:
                await self.sleep(PEER_PRESENTATION_REFRESH_BURST_INTERVAL_SECONDS)
                if not self.peer_presentation_refresh_burst:
                    return
                if self._presentation_state.peer_presentation_refresh_target_key != key:
                    return
                if not self._snapshot_has_refreshable_peer_key(key):
                    return
                if not self._presentation_state.tick_peer_presentation_refresh(key):
                    return
                await self._publish_if_changed()
        except asyncio.CancelledError:
            raise
        finally:
            current_task = self._current_task()
            if (
                current_task is not None
                and self._peer_presentation_refresh_burst_task is current_task
            ):
                self._peer_presentation_refresh_burst_task = None
                # end returns True when a refresh marker was in the snapshot —
                # must publish to render the snapshot WITHOUT the marker.
                if self._presentation_state.end_peer_presentation_refresh(key):
                    await self._publish_if_changed()

    async def _run_self_presentation_refresh_burst(self, key: tuple[str, UUID]) -> None:
        deadline = self.clock.now() + PEER_PRESENTATION_REFRESH_BURST_SECONDS
        tick_count = 0
        cleanup_publish_count = 0
        end_reason = "deadline_expired"
        current_task = self._current_task()
        try:
            while self.self_presentation_refresh_burst and self.clock.now() < deadline:
                await self.sleep(PEER_PRESENTATION_REFRESH_BURST_INTERVAL_SECONDS)
                if not self.self_presentation_refresh_burst:
                    end_reason = "disabled"
                    return
                if self._presentation_state.self_presentation_refresh_target_key != key:
                    end_reason = "target_replaced"
                    return
                if not self._snapshot_has_refreshable_self_key(key):
                    end_reason = "target_invalid"
                    return
                if not self._presentation_state.tick_self_presentation_refresh(key):
                    end_reason = "target_replaced"
                    return
                tick_count += 1
                await self._publish_if_changed()
            end_reason = "deadline_expired"
        except asyncio.CancelledError:
            if current_task is not None:
                end_reason = self._self_presentation_refresh_burst_cancel_reasons.get(
                    current_task,
                    "cancelled",
                )
            else:
                end_reason = "cancelled"
            raise
        finally:
            active_task = current_task is not None and (
                self._self_presentation_refresh_burst_task is current_task
            )
            if active_task:
                self._self_presentation_refresh_burst_task = None
                # end returns True when a refresh marker was in the snapshot —
                # must publish to render the snapshot WITHOUT the marker.
                if self._presentation_state.end_self_presentation_refresh(key):
                    await self._publish_if_changed()
                    cleanup_publish_count += 1
            if current_task is not None:
                # Accumulate cleanup publishes from both the task's own end-cleanup
                # (above) and the cancel caller's count (below) for diagnostic logging.
                cleanup_publish_count += (
                    self._self_presentation_refresh_burst_cancel_cleanup_counts.pop(
                        current_task,
                        0,
                    )
                )
                self._self_presentation_refresh_burst_cancel_reasons.pop(current_task, None)
            self._record_self_presentation_refresh_burst_end(
                key,
                reason=end_reason,
                tick_count=tick_count,
                cleanup_publish_count=cleanup_publish_count,
            )

    def _create_self_presentation_refresh_burst_task(
        self,
        key: tuple[str, UUID],
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(self._run_self_presentation_refresh_burst(key))

        def record_unstarted_cancel_end(completed_task: asyncio.Task[None]) -> None:
            self._record_unstarted_self_presentation_refresh_cancel_end(
                completed_task,
                key,
            )

        # Safety-net done callback: clean up cancel metadata if the task's
        # finally block never consumed it (e.g. cancelled before first await).
        # Without this, Task objects would leak in the cancel-metadata dicts.
        task.add_done_callback(record_unstarted_cancel_end)
        return task

    def _record_unstarted_self_presentation_refresh_cancel_end(
        self,
        task: asyncio.Task[None],
        key: tuple[str, UUID],
    ) -> None:
        has_cancel_metadata = (
            task in self._self_presentation_refresh_burst_cancel_reasons
            or task in self._self_presentation_refresh_burst_cancel_cleanup_counts
        )
        if not has_cancel_metadata:
            return
        reason = self._self_presentation_refresh_burst_cancel_reasons.pop(
            task,
            "cancelled",
        )
        cleanup_publish_count = self._self_presentation_refresh_burst_cancel_cleanup_counts.pop(
            task, 0
        )
        if self._self_presentation_refresh_burst_task is task:
            self._self_presentation_refresh_burst_task = None
        self._record_self_presentation_refresh_burst_end(
            key,
            reason=reason,
            tick_count=0,
            cleanup_publish_count=cleanup_publish_count,
        )

    def _cancel_peer_presentation_refresh_burst_task(self) -> None:
        task = self._peer_presentation_refresh_burst_task
        self._peer_presentation_refresh_burst_task = None
        if task is not None and not task.done():
            task.cancel()

    def _cancel_self_presentation_refresh_burst_task(
        self,
        *,
        reason: str = "cancelled",
        cleanup_publish_count: int = 0,
    ) -> None:
        task = self._self_presentation_refresh_burst_task
        # Clear reference BEFORE cancel so the task's finally block sees
        # active_task=False and skips its own end_self_presentation_refresh.
        # The caller owns the cleanup: either start_self_* calls begin first,
        # or reset_scene/detach explicitly calls end afterward.
        self._self_presentation_refresh_burst_task = None
        if task is None:
            return
        # Store metadata BEFORE cancel — the task may run its finally block
        # before we return, and it reads these dicts in except/finally.
        self._self_presentation_refresh_burst_cancel_reasons[task] = reason
        self._self_presentation_refresh_burst_cancel_cleanup_counts[task] = cleanup_publish_count
        if not task.done():
            task.cancel()
        else:
            # Task already finished — its finally block already ran, so pop the
            # metadata we just wrote to prevent dict leak.
            self._self_presentation_refresh_burst_cancel_reasons.pop(task, None)
            self._self_presentation_refresh_burst_cancel_cleanup_counts.pop(task, None)

    async def _cancel_peer_presentation_refresh_burst_task_and_wait(self) -> None:
        task = self._peer_presentation_refresh_burst_task
        self._peer_presentation_refresh_burst_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _cancel_self_presentation_refresh_burst_task_and_wait(
        self,
        *,
        reason: str = "cancelled",
        allow_task_cleanup: bool = False,
        cleanup_publish_count: int = 0,
    ) -> None:
        # allow_task_cleanup=False (default): clear reference before await so the
        # task skips its own end_self_presentation_refresh; caller handles cleanup.
        # allow_task_cleanup=True: keep reference during await so the task's finally
        # block does end + publish naturally. Used only from the "disable burst"
        # path (update_self_presentation_refresh_burst) where the task must publish
        # the marker-removal snapshot itself.
        task = self._self_presentation_refresh_burst_task
        if task is None:
            return
        self._self_presentation_refresh_burst_cancel_reasons[task] = reason
        self._self_presentation_refresh_burst_cancel_cleanup_counts[task] = cleanup_publish_count
        if not allow_task_cleanup:
            self._self_presentation_refresh_burst_task = None
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            self._self_presentation_refresh_burst_cancel_reasons.pop(task, None)
            self._self_presentation_refresh_burst_cancel_cleanup_counts.pop(task, None)
        if allow_task_cleanup and self._self_presentation_refresh_burst_task is task:
            self._self_presentation_refresh_burst_task = None
