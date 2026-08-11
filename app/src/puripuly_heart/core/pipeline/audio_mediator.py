"""STT event handling extracted from Pipeline.

Handles STT event loop, event dispatch, promo notifications.
Dependencies: PipelineContext, TranscriptMediator, PeerTurnTracker, BufferManager,
              OutputMediator, TranslationExecutor.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from puripuly_heart.domain.events import (
    STTErrorEvent,
    STTEvent,
    STTFinalEvent,
    STTPartialEvent,
    STTSessionState,
    STTSessionStateEvent,
    UIEvent,
    UIEventType,
)

if TYPE_CHECKING:
    from puripuly_heart.core.pipeline.buffer_manager_standalone import BufferManager
    from puripuly_heart.core.pipeline.output_mediator import OutputMediator
    from puripuly_heart.core.pipeline.peer_turn_tracker import PeerTurnTracker
    from puripuly_heart.core.pipeline.pipeline import Pipeline
    from puripuly_heart.core.pipeline.pipeline_context import PipelineContext
    from puripuly_heart.core.pipeline.transcript_mediator import TranscriptMediator
    from puripuly_heart.core.pipeline.translation_executor import TranslationExecutor
    from puripuly_heart.ports.hub import STTProvider

logger = logging.getLogger(__name__)

_PROMO_INTERVAL_SEC: float = 300.0

__all__ = ["AudioMediator"]


class AudioMediator:
    """STT event handling extracted from Pipeline."""

    def __init__(
        self,
        ctx: PipelineContext,
        transcript: TranscriptMediator,
        peer_turns: PeerTurnTracker,
        buffer: BufferManager,
        output: OutputMediator,
        translation: TranslationExecutor,
    ) -> None:
        self._ctx = ctx
        self._transcript = transcript
        self._peer_turns = peer_turns
        self._buffer = buffer
        self._output = output
        self._translation = translation
        self._promo_eligible: bool = False
        self._last_promo_time: float | None = None

    # ------------------------------------------------------------------
    # STT event loop
    # ------------------------------------------------------------------

    async def run_stt_event_loop(self, provider: STTProvider) -> None:
        try:
            async for ev in provider.events():
                await self.handle_stt_event(ev)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._ctx._emit_exception_summary(
                "[Hub] STT event loop crashed: %s",
                exc,
                level=logging.ERROR,
            )
            raise

    async def stop_stt_task(self, attr_name: str, pipeline: Pipeline) -> None:
        # Dynamic attr access: attr_name is "_stt_task" or "_peer_stt_task"
        # depending on which STT loop to stop. Both attrs live on Pipeline.
        task = getattr(pipeline, attr_name)
        if task is None:
            return
        setattr(pipeline, attr_name, None)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def reset_stt_runtime_state(self) -> None:
        await self._ctx.self_runtime.reset_runtime_state()
        await self._ctx.peer_runtime.reset_runtime_state()
        self._peer_turns.clear_state()
        self._ctx._latency._clear_latency_state()

    # ------------------------------------------------------------------
    # Promo
    # ------------------------------------------------------------------

    def mark_promo_eligible(self) -> None:
        self._promo_eligible = True

    def _send_stt_connected_notification(self, osc: object) -> None:
        if not self._promo_eligible:
            return
        self._promo_eligible = False
        now = self._ctx.clock.now()
        if self._last_promo_time is not None:
            if now - self._last_promo_time < _PROMO_INTERVAL_SEC:
                return
        if getattr(osc, "send_immediate", lambda _: False)("PuriPuly ON!"):
            self._last_promo_time = now

    # ------------------------------------------------------------------
    # STT event dispatch
    # ------------------------------------------------------------------

    async def handle_stt_event(self, event: STTEvent) -> None:
        if isinstance(event, STTSessionStateEvent):
            await self._handle_session_state(event)
            return
        if isinstance(event, STTErrorEvent):
            await self._handle_error(event)
            return
        if isinstance(event, STTPartialEvent):
            await self._handle_partial(event)
            return
        if isinstance(event, STTFinalEvent):
            await self._handle_final(event)
            return

    async def _handle_session_state(self, event: STTSessionStateEvent) -> None:
        self._ctx._emit_basic(
            "[Hub] STT state: channel=%s state=%s",
            event.channel,
            event.state.name,
        )
        await self._ctx.ui_events.put(
            UIEvent(
                type=UIEventType.SESSION_STATE_CHANGED,
                payload=event.state,
                channel=event.channel,
            )
        )
        if event.state == STTSessionState.STREAMING and event.channel == "self":
            self._send_stt_connected_notification(self._ctx.osc)

    async def _handle_error(self, event: STTErrorEvent) -> None:
        await self._ctx.ui_events.put(
            UIEvent(
                type=UIEventType.ERROR,
                payload=event.message,
                source="Peer" if event.channel == "peer" else "Mic",
                channel=event.channel,
                runtime_log_handled=event.runtime_log_handled,
            )
        )

    async def _handle_partial(self, event: STTPartialEvent) -> None:
        # Peer partials are always ignored — no mechanism to forward them.
        # Self partials: processed in normal mode (forwarded to transcript_mediator),
        # ignored in low_latency_mode (buffer_manager handles full segments only).
        if event.channel == "peer":
            return
        self._send_stt_connected_notification(self._ctx.osc)
        if self._ctx.low_latency_mode:
            return
        self._ctx._emit_detailed(
            f"[Hub] STT Partial: '{event.transcript.text[:50]}...' "
            f"id={str(event.transcript.utterance_id)[:8]}",
            fallback_level=logging.DEBUG,
        )
        await self._transcript.handle(
            event.transcript, is_final=False, source="Mic",
        )

    async def _handle_final(self, event: STTFinalEvent) -> None:
        # Final event handler has different paths for peer vs self channels.
        # Self-channel goes through buffer_manager in low_latency_mode.
        # Peer-channel uses peer_turn_tracker for logical turn management.
        # These paths must not be merged — they handle different state machines.
        runtime = self._ctx._runtime_for_channel(event.channel)
        source = "Peer" if runtime.channel == "peer" else "Mic"
        logger.info(
            "[Pipeline][%s] STT final: '%s'", runtime.channel, event.transcript.text,
        )
        if runtime.channel == "peer":
            parent_utterance_id, peer_transcript = (
                self._peer_turns.peer_logical_turn_transcript(event.transcript)
            )
            await self._transcript.handle_peer_final(
                peer_transcript,
                parent_utterance_id,
                source=source,
            )
            return
        if runtime.channel == "self":
            self._send_stt_connected_notification(self._ctx.osc)
        if self._ctx.low_latency_mode and runtime.channel == "self":
            await self._buffer._handle_low_latency_final(event.transcript)
            return
        self._ctx._latency._record_latency_stage(
            channel=runtime.channel,
            utterance_id=event.transcript.utterance_id,
            stage="stt_final",
        )
        await self._transcript.handle(
            event.transcript, is_final=True, source=source,
        )
        if self._ctx.llm is None or not self._ctx._translation_enabled_for_runtime(
            runtime
        ):
            self._ctx._log_translation_skipped(
                stage="final",
                runtime=runtime,
                publish_chatbox=self._ctx._should_publish_to_chatbox(runtime),
            )
            if self._ctx._should_publish_to_chatbox(runtime):
                await self._output.enqueue_osc(
                    event.transcript.utterance_id,
                    transcript_text=event.transcript.text,
                    translation_text=None,
                )
            else:
                self._ctx._latency._finalize_latency_timeline(
                    runtime=runtime,
                    channel=runtime.channel,
                    utterance_id=event.transcript.utterance_id,
                )
        else:
            await self._translation.ensure_translation(event.transcript)
