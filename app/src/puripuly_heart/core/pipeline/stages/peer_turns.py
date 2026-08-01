from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from puripuly_heart.domain.models import Transcript


class PeerTurnsMixin:
    """Peer logical-turn bookkeeping extracted from Pipeline."""

    # ------------------------------------------------------------------
    # Peer logical turn state management
    # ------------------------------------------------------------------

    def _clear_peer_logical_turn_state(self) -> None:
        self._peer_turn_parent_ids.clear()
        self._peer_parent_turn_ids.clear()
        self._peer_completed_turn_ids.clear()
        self._peer_parent_speech_end_times.clear()

    def _peer_parent_speech_end_time(self, parent_utterance_id: UUID) -> float | None:
        parent_end_time = self.peer_runtime.utterance_start_times.get(parent_utterance_id)
        if parent_end_time is not None:
            return parent_end_time
        return self._peer_parent_speech_end_times.get(parent_utterance_id)

    def _peer_parent_speech_ended(self, parent_utterance_id: UUID) -> bool:
        return (
            parent_utterance_id in self.peer_runtime.speech_ended_ids
            or parent_utterance_id in self._peer_parent_speech_end_times
        )

    def _register_peer_logical_turn(
        self,
        *,
        parent_utterance_id: UUID,
        peer_turn_id: UUID,
    ) -> None:
        self._peer_turn_parent_ids[peer_turn_id] = parent_utterance_id
        self._peer_parent_turn_ids.setdefault(parent_utterance_id, set()).add(peer_turn_id)
        self._inherit_peer_parent_vad_bookkeeping(
            parent_utterance_id=parent_utterance_id,
            peer_turn_id=peer_turn_id,
        )

    def _inherit_peer_parent_vad_bookkeeping(
        self,
        *,
        parent_utterance_id: UUID,
        peer_turn_id: UUID,
    ) -> None:
        runtime = self.peer_runtime
        parent_end_time = self._peer_parent_speech_end_time(parent_utterance_id)
        if parent_end_time is not None:
            runtime.utterance_start_times[peer_turn_id] = parent_end_time
            self._latency._record_latency_stage(
                channel="peer",
                utterance_id=peer_turn_id,
                stage="speech_end",
                timestamp=parent_end_time,
                overwrite=False,
            )
        if self._peer_parent_speech_ended(parent_utterance_id):
            runtime.speech_ended_ids.add(peer_turn_id)
        self._latency._inherit_latency_for_output(
            channel="peer",
            output_utterance_id=peer_turn_id,
            source_utterance_ids=[parent_utterance_id],
        )

    def _clear_peer_parent_vad_bookkeeping(
        self,
        parent_utterance_id: UUID,
        *,
        preserve_parent_speech_end_time: bool = False,
    ) -> None:
        peer_turn_ids = self._peer_parent_turn_ids.pop(parent_utterance_id, set())
        for peer_turn_id in peer_turn_ids:
            self._peer_turn_parent_ids.pop(peer_turn_id, None)
            self._peer_completed_turn_ids.discard(peer_turn_id)
        self.peer_runtime.utterance_start_times.pop(parent_utterance_id, None)
        self.peer_runtime.speech_ended_ids.discard(parent_utterance_id)
        if not preserve_parent_speech_end_time:
            self._peer_parent_speech_end_times.pop(parent_utterance_id, None)
        self._latency._clear_latency_timeline(channel="peer", utterance_id=parent_utterance_id)

    def _maybe_clear_completed_peer_parent(
        self,
        parent_utterance_id: UUID,
        *,
        preserve_parent_speech_end_time: bool = False,
    ) -> None:
        peer_turn_ids = self._peer_parent_turn_ids.get(parent_utterance_id)
        if not peer_turn_ids:
            self._clear_peer_parent_vad_bookkeeping(
                parent_utterance_id,
                preserve_parent_speech_end_time=preserve_parent_speech_end_time,
            )
            return
        if not self._peer_parent_speech_ended(parent_utterance_id):
            return
        if peer_turn_ids.issubset(self._peer_completed_turn_ids):
            self._clear_peer_parent_vad_bookkeeping(
                parent_utterance_id,
                preserve_parent_speech_end_time=preserve_parent_speech_end_time,
            )

    def _complete_peer_logical_turn(
        self,
        peer_turn_id: UUID,
        *,
        preserve_parent_speech_end_time: bool = False,
    ) -> None:
        parent_utterance_id = self._peer_turn_parent_ids.get(peer_turn_id)
        if parent_utterance_id is None:
            return
        self._peer_completed_turn_ids.add(peer_turn_id)
        self._maybe_clear_completed_peer_parent(
            parent_utterance_id,
            preserve_parent_speech_end_time=preserve_parent_speech_end_time,
        )

    def _peer_logical_turn_transcript(self, transcript: Transcript) -> tuple[UUID, Transcript]:
        parent_utterance_id = transcript.utterance_id
        peer_turn_id = uuid4()
        self._register_peer_logical_turn(
            parent_utterance_id=parent_utterance_id,
            peer_turn_id=peer_turn_id,
        )
        return parent_utterance_id, Transcript(
            utterance_id=peer_turn_id,
            text=transcript.text,
            is_final=True,
            created_at=transcript.created_at,
            channel="peer",
        )
