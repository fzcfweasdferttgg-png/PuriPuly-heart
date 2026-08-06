"""OSC output dispatcher — merges transcript + translation for VRChat chatbox.

Called by pipeline._enqueue_osc() (which is invoked from
pipeline._translate_and_enqueue()).
Formats the chatbox message based on chatbox_include_source setting,
then enqueues to OscSink for batched UDP send.

Cleanup side effect: dispatch_osc() pops the utterance from
utterance_start_times and speech_ended_ids before enqueue.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from puripuly_heart.domain.models import OSCMessage

logger = logging.getLogger(__name__)


class _OscSinkLike(Protocol):
    def enqueue(self, message: object) -> None: ...
    def send_typing(self, is_typing: bool) -> None: ...
    def process_due(self) -> None: ...
    def send_immediate(self, text: str) -> bool: ...


class _ClockLike(Protocol):
    def now(self) -> float: ...


class _ChannelRuntimeLike(Protocol):
    channel: str
    utterance_start_times: dict[UUID, float]
    speech_ended_ids: set[UUID]


@dataclass(slots=True)
class OutputDispatcher:
    osc: _OscSinkLike
    clock: _ClockLike
    chatbox_include_source: bool = True

    async def dispatch_osc(
        self,
        utterance_id: UUID,
        *,
        transcript_text: str,
        translation_text: str | None,
        runtime: _ChannelRuntimeLike,
    ) -> None:
        # Chatbox formatting: 3 modes based on translation availability
        # and chatbox_include_source toggle (set directly from settings by controller).
        if translation_text is None:
            merged = transcript_text
        elif self.chatbox_include_source:
            merged = f"{transcript_text} ({translation_text})"
        else:
            merged = translation_text

        msg = OSCMessage(utterance_id=utterance_id, text=merged, created_at=self.clock.now())
        logger.info(
            "[Hub] OSC enqueue preview: channel=%s text=%r",
            runtime.channel,
            merged,
        )

        runtime.utterance_start_times.pop(utterance_id, None)
        runtime.speech_ended_ids.discard(utterance_id)

        self.osc.enqueue(msg)

    async def run_osc_flush(self) -> None:
        try:
            while True:
                self.osc.process_due()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
