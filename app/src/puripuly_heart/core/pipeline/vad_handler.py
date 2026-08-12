"""VAD event handling extracted from Pipeline.

Handles self and peer VAD events (SpeechStart, SpeechChunk, SpeechEnd).
Dependencies: PipelineContext, BufferManager, PeerTurnTracker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID
    from puripuly_heart.core.pipeline.buffer_manager_standalone import BufferManager
    from puripuly_heart.core.pipeline.channel_runtime import _MergeBuffer
    from puripuly_heart.core.pipeline.peer_turn_tracker import PeerTurnTracker
    from puripuly_heart.core.pipeline.pipeline_context import PipelineContext
    from puripuly_heart.core.vad.gating import VadEvent

__all__ = ["VADHandler"]


class VADHandler:
    """VAD event handling extracted from Pipeline.

    Dependencies injected via constructor:
    - ctx: PipelineContext (config, runtime, logging, latency)
    - buffer: BufferManager (merge buffer management)
    - peer_turns: PeerTurnTracker (peer speech end tracking)
    """

    def __init__(
        self,
        ctx: PipelineContext,
        buffer: BufferManager,
        peer_turns: PeerTurnTracker,
    ) -> None:
        self._ctx = ctx
        self._buffer = buffer
        self._peer_turns = peer_turns

    # All methods in VADHandler delegate to buffer_manager or peer_turn_tracker.
    # The handler is purely a facade — don't add logic here. All VAD state
    # lives in buffer_manager.py (low_latency_mode) and peer_turn_tracker.py
    # (peer channel). This keeps VAD handling testable independently.

    def mark_resume_pending(self, event: VadEvent) -> None:
        self._buffer._mark_resume_pending(event)

    def maybe_confirm_resume(self, event: VadEvent) -> _MergeBuffer | None:
        return self._buffer._maybe_confirm_resume(event)

    def maybe_update_buffer_end_time(self, utterance_id: UUID) -> None:
        self._buffer._maybe_update_buffer_end_time(utterance_id)

    def maybe_start_finalize_wait(self, utterance_id: UUID) -> None:
        self._buffer._maybe_start_finalize_wait(utterance_id)

    async def maybe_clear_resume_on_end(self, event: VadEvent) -> None:
        await self._buffer._maybe_clear_resume_on_end(event)

    def record_peer_speech_end(self, utterance_id: UUID, speech_end_at: float) -> None:
        self._peer_turns.on_peer_speech_end(utterance_id, speech_end_at)
