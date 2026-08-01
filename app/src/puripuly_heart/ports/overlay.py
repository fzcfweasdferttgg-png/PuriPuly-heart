from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import ClassVar, Literal, Protocol
from uuid import UUID

from puripuly_heart.domain.models import ChannelId, Transcript

AppliedContextMode = Literal["local", "integrated"]


@dataclass(frozen=True, slots=True, kw_only=True)
class OverlayEvent:
    event_id: str
    seq: int
    utterance_id: UUID | None
    channel: ChannelId | None
    created_at: float
    update_id: str | None = None
    origin_wall_clock_ms: int | None = None
    session_scope: str | None = None
    source_text_hash: str | None = None
    source_text_len: int | None = None
    logical_turn_key: str | None = None

    EVENT_TYPE: ClassVar[str] = "overlay_event"

    @property
    def type(self) -> str:
        return self.EVENT_TYPE


@dataclass(frozen=True, slots=True, kw_only=True)
class _TranscriptEvent(OverlayEvent):
    text: str
    source_language: str
    target_language: str
    is_final: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class SelfTranscriptFinal(_TranscriptEvent):
    EVENT_TYPE: ClassVar[str] = "self_transcript_final"

    def __post_init__(self) -> None:
        if self.channel != "self":
            raise ValueError("SelfTranscriptFinal requires channel='self'")


@dataclass(frozen=True, slots=True, kw_only=True)
class PeerTranscriptFinal(_TranscriptEvent):
    EVENT_TYPE: ClassVar[str] = "peer_transcript_final"

    def __post_init__(self) -> None:
        if self.channel != "peer":
            raise ValueError("PeerTranscriptFinal requires channel='peer'")


@dataclass(frozen=True, slots=True, kw_only=True)
class SelfActiveUpdate(OverlayEvent):
    text: str
    occupant_key: str
    secondary_text: str = ""
    source_language: str = ""
    target_language: str = ""

    EVENT_TYPE: ClassVar[str] = "self_active_update"

    def __post_init__(self) -> None:
        if self.channel != "self":
            raise ValueError("SelfActiveUpdate requires channel='self'")
        if self.utterance_id is None:
            raise ValueError("SelfActiveUpdate requires utterance_id")
        if not self.occupant_key.strip():
            raise ValueError("SelfActiveUpdate requires non-empty occupant_key")


@dataclass(frozen=True, slots=True, kw_only=True)
class PeerActiveUpdate(OverlayEvent):
    text: str
    occupant_key: str
    source_language: str = ""
    target_language: str = ""

    EVENT_TYPE: ClassVar[str] = "peer_active_update"

    def __post_init__(self) -> None:
        if self.channel != "peer":
            raise ValueError("PeerActiveUpdate requires channel='peer'")
        if self.utterance_id is None:
            raise ValueError("PeerActiveUpdate requires utterance_id")
        if not self.occupant_key.strip():
            raise ValueError("PeerActiveUpdate requires non-empty occupant_key")


@dataclass(frozen=True, slots=True, kw_only=True)
class SelfActiveClear(OverlayEvent):
    EVENT_TYPE: ClassVar[str] = "self_active_clear"

    def __post_init__(self) -> None:
        if self.channel != "self":
            raise ValueError("SelfActiveClear requires channel='self'")


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationStreamUpdate(OverlayEvent):
    text: str
    source_language: str
    target_language: str
    is_final: bool = False
    applied_context_mode: AppliedContextMode | None = None
    source_text: str = ""

    EVENT_TYPE: ClassVar[str] = "translation_stream_update"


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationFinal(TranslationStreamUpdate):
    is_final: bool = True

    EVENT_TYPE: ClassVar[str] = "translation_final"

    def __post_init__(self) -> None:
        if not self.is_final:
            raise ValueError("TranslationFinal requires is_final=True")


@dataclass(frozen=True, slots=True, kw_only=True)
class UtteranceClosed(OverlayEvent):
    is_final: bool = True

    EVENT_TYPE: ClassVar[str] = "utterance_closed"


OverlayEventUnion = (
    SelfTranscriptFinal
    | PeerTranscriptFinal
    | SelfActiveUpdate
    | PeerActiveUpdate
    | SelfActiveClear
    | TranslationStreamUpdate
    | TranslationFinal
    | UtteranceClosed
)


class OverlaySink(Protocol):
    async def emit(self, event: OverlayEventUnion) -> None: ...
