"""Transcript handling extracted from Pipeline.

Manages transcript lifecycle: emit to overlay, decide translate-or-osc,
handle peer final transcripts, and submit text.
Dependencies: PipelineContext, OverlayEmitter, TranslationExecutor, OutputMediator, PeerTurnTracker.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from puripuly_heart.domain.events import UIEvent, UIEventType
from puripuly_heart.domain.models import Transcript

if TYPE_CHECKING:
    from puripuly_heart.core.pipeline.output_mediator import OutputMediator
    from puripuly_heart.core.pipeline.overlay_emitter import OverlayEmitter
    from puripuly_heart.core.pipeline.peer_turn_tracker import PeerTurnTracker
    from puripuly_heart.core.pipeline.pipeline_context import PipelineContext
    from puripuly_heart.core.pipeline.translation_executor import TranslationExecutor

logger = logging.getLogger(__name__)

__all__ = ["TranscriptMediator"]


class TranscriptMediator:
    """Transcript handling extracted from Pipeline.

    Dependencies injected via constructor:
    - ctx: PipelineContext (config, runtime, logging)
    - overlay: OverlayEmitter (overlay events)
    - translation: TranslationExecutor (ensure_translation)
    - output: OutputMediator (OSC enqueue)
    - peer_turns: PeerTurnTracker (peer logical turns)
    """

    def __init__(
        self,
        ctx: PipelineContext,
        overlay: OverlayEmitter,
        translation: TranslationExecutor,
        output: OutputMediator,
        peer_turns: PeerTurnTracker,
    ) -> None:
        self._ctx = ctx
        self._overlay = overlay
        self._translation = translation
        self._output = output
        self._peer_turns = peer_turns

    async def submit_text(self, text: str, *, source: str = "You") -> UUID:
        text = text.strip()
        if not text:
            raise ValueError("text must be non-empty")

        utterance_id = uuid4()
        self._ctx._remember_source(utterance_id, source)

        transcript = Transcript(
            utterance_id=utterance_id,
            text=text,
            is_final=True,
            created_at=self._ctx.clock.now(),
        )
        await self.handle(transcript, is_final=True, source=source)

        if self._ctx.llm is None or not self._ctx.translation_enabled:
            await self._output.enqueue_osc(
                utterance_id, transcript_text=text, translation_text=None,
            )
        else:
            await self._translation.ensure_translation(transcript)

        return utterance_id

    async def handle(
        self, transcript: Transcript, *, is_final: bool, source: str | None
    ) -> None:
        runtime = self._ctx._runtime_for_channel(transcript.channel)
        bundle = self._ctx.get_or_create_bundle(
            transcript.utterance_id, channel=transcript.channel,
        )
        bundle.with_transcript(transcript)
        self._ctx._remember_source(
            transcript.utterance_id, source, channel=transcript.channel,
        )
        await self._ctx.ui_events.put(
            UIEvent(
                type=UIEventType.TRANSCRIPT_FINAL if is_final else UIEventType.TRANSCRIPT_PARTIAL,
                utterance_id=transcript.utterance_id,
                payload=transcript,
                source=source,
            )
        )
        if is_final:
            await self._overlay.emit_final_transcript_to_overlay(transcript)
            if not self._overlay.overlay_translation_will_follow(runtime):
                await self._overlay.emit_overlay_utterance_closed(
                    utterance_id=transcript.utterance_id,
                    channel=transcript.channel,
                    is_final=True,
                )
                if self._ctx._should_publish_to_chatbox(runtime):
                    await self._output.enqueue_osc(
                        transcript.utterance_id,
                        transcript_text=transcript.text,
                        translation_text=None,
                    )

    async def handle_peer_final(
        self,
        transcript: Transcript,
        parent_utterance_id: UUID,
        *,
        source: str,
    ) -> None:
        _ = parent_utterance_id
        runtime = self._ctx.peer_runtime
        bundle = runtime.get_or_create_bundle(transcript.utterance_id)
        bundle.with_transcript(transcript)
        self._ctx._remember_source(transcript.utterance_id, source, channel="peer")
        await self._ctx.ui_events.put(
            UIEvent(
                type=UIEventType.TRANSCRIPT_FINAL,
                utterance_id=transcript.utterance_id,
                payload=transcript,
                source=source,
            )
        )
        self._ctx._latency._record_latency_stage(
            channel="peer",
            utterance_id=transcript.utterance_id,
            stage="stt_final",
        )
        if self._ctx.llm is None or not self._ctx._translation_enabled_for_runtime(
            runtime
        ):
            self._ctx._log_translation_skipped(
                stage="final",
                runtime=runtime,
                publish_chatbox=self._ctx._should_publish_to_chatbox(runtime),
            )
            await self._overlay.finalize_peer_source_only(
                transcript,
                close_is_final=True,
                finalize_latency=not self._ctx._should_publish_to_chatbox(runtime),
                preserve_parent_speech_end_time=True,
            )
            if self._ctx._should_publish_to_chatbox(runtime):
                await self._output.enqueue_osc(
                    transcript.utterance_id,
                    transcript_text=transcript.text,
                    translation_text=None,
                )
            return
        await self._translation.ensure_translation(transcript)
