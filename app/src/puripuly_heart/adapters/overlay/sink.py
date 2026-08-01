from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from puripuly_heart.ports.clock import Clock
from puripuly_heart.domain.models import ChannelId, Transcript
from puripuly_heart.ports.overlay import (
    AppliedContextMode,
    OverlayEvent,
    OverlayEventUnion,
    OverlaySink,
    PeerActiveUpdate,
    PeerTranscriptFinal,
    SelfActiveClear,
    SelfActiveUpdate,
    SelfTranscriptFinal,
    TranslationFinal,
    TranslationStreamUpdate,
    UtteranceClosed,
)

__all__ = [
    "AppliedContextMode",
    "OverlayEvent",
    "OverlayEventAdapter",
    "OverlayEventUnion",
    "OverlaySink",
    "PeerActiveUpdate",
    "PeerTranscriptFinal",
    "SelfActiveClear",
    "SelfActiveUpdate",
    "SelfTranscriptFinal",
    "TranslationFinal",
    "TranslationStreamUpdate",
    "UtteranceClosed",
]


@dataclass(slots=True)
class OverlayEventAdapter:
    clock: Clock
    _seq: int = 0

    def transcript_final(
        self,
        transcript: Transcript,
        *,
        source_language: str,
        target_language: str,
    ) -> SelfTranscriptFinal | PeerTranscriptFinal:
        common = self._common_event_fields(
            utterance_id=transcript.utterance_id,
            channel=transcript.channel,
            created_at=transcript.created_at,
        )
        event_cls = SelfTranscriptFinal if transcript.channel == "self" else PeerTranscriptFinal
        return event_cls(
            **common,
            text=transcript.text,
            source_language=source_language,
            target_language=target_language,
            is_final=True,
        )

    def translation_stream_update(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        text: str,
        source_text: str = "",
        source_language: str,
        target_language: str,
        applied_context_mode: AppliedContextMode | None,
        created_at: float | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> TranslationStreamUpdate:
        return TranslationStreamUpdate(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel=channel,
                created_at=created_at,
                update_id=update_id or uuid4().hex,
                origin_wall_clock_ms=origin_wall_clock_ms,
                session_scope=session_scope,
                source_text_hash=source_text_hash,
                source_text_len=source_text_len,
                logical_turn_key=logical_turn_key,
            ),
            text=text,
            source_text=source_text,
            source_language=source_language,
            target_language=target_language,
            is_final=False,
            applied_context_mode=applied_context_mode,
        )

    def self_active_update(
        self,
        *,
        text: str,
        utterance_id: UUID,
        secondary_text: str = "",
        occupant_key: str,
        source_language: str = "",
        target_language: str = "",
        created_at: float | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> SelfActiveUpdate:
        return SelfActiveUpdate(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel="self",
                created_at=created_at,
                update_id=update_id,
                origin_wall_clock_ms=origin_wall_clock_ms,
                session_scope=session_scope,
                source_text_hash=source_text_hash,
                source_text_len=source_text_len,
                logical_turn_key=logical_turn_key,
            ),
            text=text,
            secondary_text=secondary_text,
            occupant_key=occupant_key,
            source_language=source_language,
            target_language=target_language,
        )

    def peer_active_update(
        self,
        *,
        text: str,
        utterance_id: UUID,
        occupant_key: str,
        source_language: str = "",
        target_language: str = "",
        created_at: float | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> PeerActiveUpdate:
        return PeerActiveUpdate(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel="peer",
                created_at=created_at,
                update_id=update_id,
                origin_wall_clock_ms=origin_wall_clock_ms,
                session_scope=session_scope,
                source_text_hash=source_text_hash,
                source_text_len=source_text_len,
                logical_turn_key=logical_turn_key,
            ),
            text=text,
            occupant_key=occupant_key,
            source_language=source_language,
            target_language=target_language,
        )

    def self_active_clear(self, *, created_at: float | None = None) -> SelfActiveClear:
        return SelfActiveClear(
            **self._common_event_fields(
                utterance_id=None,
                channel="self",
                created_at=created_at,
            )
        )

    def translation_final(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        text: str,
        source_text: str = "",
        source_language: str,
        target_language: str,
        applied_context_mode: AppliedContextMode | None,
        created_at: float | None = None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> TranslationFinal:
        return TranslationFinal(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel=channel,
                created_at=created_at,
                update_id=update_id or uuid4().hex,
                origin_wall_clock_ms=origin_wall_clock_ms,
                session_scope=session_scope,
                source_text_hash=source_text_hash,
                source_text_len=source_text_len,
                logical_turn_key=logical_turn_key,
            ),
            text=text,
            source_text=source_text,
            source_language=source_language,
            target_language=target_language,
            is_final=True,
            applied_context_mode=applied_context_mode,
        )

    def utterance_closed(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        is_final: bool = True,
        created_at: float | None = None,
    ) -> UtteranceClosed:
        return UtteranceClosed(
            **self._common_event_fields(
                utterance_id=utterance_id,
                channel=channel,
                created_at=created_at,
            ),
            is_final=is_final,
        )

    def _common_event_fields(
        self,
        *,
        utterance_id: UUID | None,
        channel: ChannelId | None,
        created_at: float | None,
        update_id: str | None = None,
        origin_wall_clock_ms: int | None = None,
        session_scope: str | None = None,
        source_text_hash: str | None = None,
        source_text_len: int | None = None,
        logical_turn_key: str | None = None,
    ) -> dict[str, object]:
        self._seq += 1
        return {
            "event_id": f"evt-{self._seq}",
            "seq": self._seq,
            "utterance_id": utterance_id,
            "channel": channel,
            "created_at": created_at if created_at is not None else self.clock.now(),
            "update_id": update_id,
            "origin_wall_clock_ms": origin_wall_clock_ms,
            "session_scope": session_scope,
            "source_text_hash": source_text_hash,
            "source_text_len": source_text_len,
            "logical_turn_key": logical_turn_key,
        }
