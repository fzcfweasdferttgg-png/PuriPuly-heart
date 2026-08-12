"""Pending utterance tracker — FIFO queue for final transcript correlation.

Tracks utterance IDs between speech-end and final-transcript arrival.
Provides stale cleanup, finalization lag detection, and FIFO ordering
guarantees for correct utterance↔transcript pairing.

AI-INVARIANT: append() in _on_speech_end, pop_next_final() in _consume_session_events.
  FIFO order guarantees correct utterance↔transcript pairing after session reset.
  Reordering these calls breaks transcript attribution.

AI-INVARIANT: drop_stale(has_active_utterance=False) preserves the LAST pending ID
  even if stale. This prevents losing the sole pending final when no new speech is active.
  Removing this guard causes silent transcript loss.

AI-EDGE-CASE: During session bridging (_reset_with_bridging_locked), two consumer tasks
  share this same PendingUtteranceTracker instance. The old consumer may pop IDs that the
  new consumer needs. This is a known limitation — mitigated by drain timeout.

AI-EDGE-CASE: drop_stale() returning early when ended_at is None is defensive — masks
  data integrity issues. If you see unexpected "kept" items, check _times dict consistency.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from puripuly_heart.core.clock import Clock
from puripuly_heart.domain.models import ChannelId

if TYPE_CHECKING:
    from puripuly_heart.core.stt.stt_log_sink import STTLogSink

# PENDING_FINAL_QUEUE_WARN_SIZE=8: threshold for warning — indicates finalization is falling behind.
PENDING_FINAL_QUEUE_WARN_SIZE = 8
# STT_FINALIZATION_LAG_AGE_MS=1500: age threshold for logging finalization lag warnings.
STT_FINALIZATION_LAG_AGE_MS = 1500
STT_FINALIZATION_LAG_QUEUE_SIZE = 2


@dataclass
class PendingUtteranceTracker:
    """FIFO queue of pending final utterances with auto-cleanup and lag logging.

    Invariants:
    - append() in _on_speech_end, pop_next_final() in _consume_session_events.
      FIFO order guarantees correct utterance↔transcript pairing.
    - drop_stale() is called BEFORE processing final events in _consume_session_events.
    - drop_stale(has_active_utterance=False) does not remove the last ID when there
      is no active utterance — prevents losing pending final for a sole utterance.
    """

    clock: Clock
    reconnect_window_s: float
    channel: ChannelId
    log_sink: STTLogSink

    _ids: deque[UUID] = field(default_factory=deque)
    _times: dict[UUID, float] = field(default_factory=dict)

    def append(self, utterance_id: UUID, ended_at: float) -> None:
        """Add utterance_id to the queue (called from _on_speech_end)."""
        self._ids.append(utterance_id)
        self._times[utterance_id] = ended_at
        if len(self._ids) > PENDING_FINAL_QUEUE_WARN_SIZE:
            self.log_sink.basic(
                "[STT] Pending final queue size is unexpectedly high: %s",
                len(self._ids),
                level=logging.WARNING,
            )

    def pop_next_final(self) -> UUID | None:
        """Extract the next final utterance_id (FIFO). Called when receiving a final event."""
        if not self._ids:
            return None
        uid = self._ids.popleft()
        self._times.pop(uid, None)
        return uid

    def peek_first(self) -> UUID | None:
        """Look at the first ID without extracting. For partial events."""
        return self._ids[0] if self._ids else None

    def drop_stale(self, *, has_active_utterance: bool = False) -> None:
        """Remove stale IDs (older than reconnect_window_s).

        Called before processing final events in _consume_session_events.
        Does not remove the last ID if has_active_utterance is False.
        """
        stale_after_s = max(0.0, float(self.reconnect_window_s))
        now = self.clock.now()

        while self._ids:
            # Don't remove the last ID if there's no active utterance
            if len(self._ids) <= 1 and not has_active_utterance:
                return

            utterance_id = self._ids[0]
            ended_at = self._times.get(utterance_id)
            if ended_at is None:
                return

            age_s = now - ended_at
            if age_s <= stale_after_s:
                return

            self._ids.popleft()
            self._times.pop(utterance_id, None)
            self.log_sink.detailed(
                "[STT] Dropped stale pending final id=%s age_s=%.1f",
                str(utterance_id)[:8],
                age_s,
                level=logging.WARNING,
            )

    def check_finalization_lag(
        self,
        *,
        utterance_id: UUID | None,
        pending_queue_size_before: int,
        text_len: int,
    ) -> None:
        """Check and log finalization lag for a final event.

        Called from _consume_session_events when processing a final event.
        Logs when the pending age exceeds STT_FINALIZATION_LAG_AGE_MS or
        the queue size exceeds STT_FINALIZATION_LAG_QUEUE_SIZE.
        """
        if utterance_id is None:
            return
        ended_at = self._times.get(utterance_id)
        if ended_at is None:
            if pending_queue_size_before < STT_FINALIZATION_LAG_QUEUE_SIZE:
                return
            pending_age_ms = 0
        else:
            pending_age_ms = max(0, int(round((self.clock.now() - ended_at) * 1000.0)))
            if (
                pending_age_ms < STT_FINALIZATION_LAG_AGE_MS
                and pending_queue_size_before < STT_FINALIZATION_LAG_QUEUE_SIZE
            ):
                return
        self.log_sink.detailed(
            "[STT][FinalizationLag] channel=%s utterance_id=%s "
            "pending_age_ms=%s pending_queue_size=%s text_len=%s",
            self.channel,
            str(utterance_id)[:8],
            pending_age_ms,
            pending_queue_size_before,
            text_len,
        )

    def has_pending(self) -> bool:
        """Are there pending utterances? For _should_finalize_before_stop."""
        return bool(self._ids)

    def size(self) -> int:
        """Current queue size."""
        return len(self._ids)

    def clear(self) -> None:
        """Full reset. For terminal failure and idle state."""
        self._ids.clear()
        self._times.clear()
