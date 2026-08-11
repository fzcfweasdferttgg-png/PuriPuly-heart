"""Translation execution extracted from Pipeline.

Handles translation requests, speculative translation, and translation lifecycle.
Dependencies: PipelineContext, OverlayEmitter, OutputMediator, PeerTurnTracker, TranslationLogger.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import UUID

from puripuly_heart.domain.events import UIEvent, UIEventType
from puripuly_heart.domain.models import Transcript, Translation

if TYPE_CHECKING:
    from puripuly_heart.core.pipeline.channel_runtime import ChannelRuntime
    from puripuly_heart.core.pipeline.context import ContextMode
    from puripuly_heart.core.pipeline.output_mediator import OutputMediator
    from puripuly_heart.core.pipeline.overlay_emitter import OverlayEmitter
    from puripuly_heart.core.pipeline.peer_turn_tracker import PeerTurnTracker
    from puripuly_heart.core.pipeline.pipeline_context import PipelineContext
    from puripuly_heart.core.pipeline.translation_logger import TranslationLogger

logger = logging.getLogger(__name__)

__all__ = ["TranslationExecutor"]


class TranslationExecutor:
    """Translation execution extracted from Pipeline.

    Dependencies injected via constructor:
    - ctx: PipelineContext (config, runtime, logging, latency)
    - overlay: OverlayEmitter (overlay events)
    - output: OutputMediator (OSC enqueue)
    - peer_turns: PeerTurnTracker (peer logical turn completion)
    - log: TranslationLogger (translation logging)
    """

    def __init__(
        self,
        ctx: PipelineContext,
        overlay: OverlayEmitter,
        output: OutputMediator,
        peer_turns: PeerTurnTracker,
        log: TranslationLogger,
    ) -> None:
        self._ctx = ctx
        self._overlay = overlay
        self._output = output
        self._peer_turns = peer_turns
        self._log = log

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ensure_translation(self, transcript: Transcript) -> None:
        if self._ctx.llm is None:
            return
        runtime = self._ctx._runtime_for_channel(transcript.channel)
        if not self._ctx._translation_enabled_for_runtime(runtime):
            return
        utterance_id = transcript.utterance_id
        if utterance_id in self._ctx._translation_tasks:
            return
        task = asyncio.create_task(
            self.translate_and_enqueue(
                utterance_id,
                transcript.text,
                runtime=runtime,
            )
        )
        self._ctx._translation_tasks[utterance_id] = task
        task.add_done_callback(
            lambda _t: self._ctx._translation_tasks.pop(utterance_id, None)
        )

    async def translate_text(
        self,
        utterance_id: UUID,
        text: str,
        *,
        record_latency: bool = True,
    ) -> Translation:
        """Translate text and return the Translation object. Used by speculative translation."""
        if self._ctx.llm is None or self._ctx.translation_service is None:
            raise RuntimeError("LLM or translation service not available")

        runtime = self._ctx.self_runtime
        if record_latency:
            self._ctx._latency._record_latency_stage(
                channel="self",
                utterance_id=utterance_id,
                stage="llm_request_start",
            )

        formatted_prompt, context_str, now, _applied_mode = (
            self._ctx.translation_service.prepare_request(
                text, runtime=runtime,
                self_rt=self._ctx.self_runtime, peer_rt=self._ctx.peer_runtime,
            )
        )
        self._ctx.translation_service.remember_context(text, now, runtime=runtime)

        translation = await self._ctx.translation_service.translate(
            text, utterance_id=utterance_id, runtime=runtime,
            self_rt=self._ctx.self_runtime, peer_rt=self._ctx.peer_runtime,
            _prepared_prompt=formatted_prompt, _prepared_context=context_str,
        )
        if translation is None:
            raise RuntimeError("LLM translation failed")

        if record_latency:
            self._ctx._latency._record_latency_stage(
                channel="self",
                utterance_id=utterance_id,
                stage="llm_done",
            )
        return translation

    # ------------------------------------------------------------------
    # translate_and_enqueue (split into sub-methods)
    # ------------------------------------------------------------------

    async def translate_and_enqueue(
        self,
        utterance_id: UUID,
        text: str,
        *,
        runtime: ChannelRuntime | None = None,
    ) -> None:
        if self._ctx.llm is None:
            return
        runtime = runtime or self._ctx.self_runtime
        # Peer overlay active check: peer translations must finalize latency
        # differently than self translations. Peer uses finalize_peer_source_only
        # which also completes peer logical turn tracking.
        peer_overlay_active = (
            runtime.channel == "peer" and self._ctx.overlay_sink is not None
        )
        try:
            translation, applied_mode = await self._execute_translation(
                utterance_id, text, runtime=runtime,
            )
        except asyncio.CancelledError:
            await self._handle_cancelled(utterance_id, text, runtime)
            raise
        except Exception as exc:
            await self._handle_error(utterance_id, text, runtime, exc)
            return

        await self._handle_success(
            utterance_id, text, translation, runtime,
            applied_mode=applied_mode,
            peer_overlay_active=peer_overlay_active,
        )

    async def _execute_translation(
        self,
        utterance_id: UUID,
        text: str,
        *,
        runtime: ChannelRuntime,
    ) -> tuple[Translation, ContextMode | None]:
        applied_mode: ContextMode | None = None
        if self._ctx.translation_service is not None:
            formatted_prompt, context_str, now, applied_mode = (
                self._ctx.translation_service.prepare_request(
                    text, runtime=runtime,
                    self_rt=self._ctx.self_runtime, peer_rt=self._ctx.peer_runtime,
                )
            )
            self._ctx.translation_service.remember_context(text, now, runtime=runtime)
        self._ctx._latency._record_latency_stage(
            channel=runtime.channel,
            utterance_id=utterance_id,
            stage="llm_request_start",
        )
        if self._ctx.translation_service is not None:
            translation = await self._ctx.translation_service.translate(
                text, utterance_id=utterance_id, runtime=runtime,
                self_rt=self._ctx.self_runtime, peer_rt=self._ctx.peer_runtime,
                _prepared_prompt=formatted_prompt, _prepared_context=context_str,
            )
        else:
            translation = None
        if translation is None:
            raise RuntimeError("LLM translation failed")
        self._ctx._latency._record_latency_stage(
            channel=runtime.channel,
            utterance_id=utterance_id,
            stage="llm_done",
        )
        return translation, applied_mode

    async def _handle_cancelled(
        self,
        utterance_id: UUID,
        text: str,
        runtime: ChannelRuntime,
    ) -> None:
        if runtime.channel == "self":
            await self._overlay.emit_overlay_utterance_closed_with_latency(
                utterance_id=utterance_id,
                channel=runtime.channel,
                is_final=False,
                finalize_latency=not self._ctx._should_publish_to_chatbox(runtime),
            )
        elif runtime.channel == "peer":
            await self._overlay.finalize_peer_source_only(
                Transcript(
                    utterance_id=utterance_id,
                    text=text,
                    is_final=True,
                    created_at=self._ctx.clock.now(),
                    channel="peer",
                ),
                close_is_final=False,
                finalize_latency=True,
            )
        else:
            self._ctx._latency._finalize_latency_timeline(
                runtime=runtime, channel=runtime.channel, utterance_id=utterance_id,
            )

    async def _handle_error(
        self,
        utterance_id: UUID,
        text: str,
        runtime: ChannelRuntime,
        exc: Exception,
    ) -> None:
        self._log.log_failure(stage="final", runtime=runtime, exc=exc)
        fallback_to_chatbox = (
            self._ctx.fallback_transcript_only
            and self._ctx._should_publish_to_chatbox(runtime)
        )
        await self._ctx.ui_events.put(
            UIEvent(
                type=UIEventType.ERROR,
                utterance_id=utterance_id,
                payload=str(exc),
                source=self._ctx._get_source(utterance_id, channel=runtime.channel),
                channel=runtime.channel,
                runtime_log_handled=True,
            )
        )
        if runtime.channel == "self":
            await self._overlay.emit_overlay_utterance_closed_with_latency(
                utterance_id=utterance_id,
                channel=runtime.channel,
                is_final=False,
                finalize_latency=not (
                    self._ctx.fallback_transcript_only
                    and self._ctx._should_publish_to_chatbox(runtime)
                ),
            )
        elif runtime.channel == "peer":
            await self._overlay.finalize_peer_source_only(
                Transcript(
                    utterance_id=utterance_id,
                    text=text,
                    is_final=True,
                    created_at=self._ctx.clock.now(),
                    channel="peer",
                ),
                close_is_final=False,
                finalize_latency=not fallback_to_chatbox,
            )
        if fallback_to_chatbox:
            await self._output.enqueue_osc(
                utterance_id,
                transcript_text=text,
                translation_text=None,
            )

    async def _handle_success(
        self,
        utterance_id: UUID,
        text: str,
        translation: Translation,
        runtime: ChannelRuntime,
        *,
        applied_mode: ContextMode | None,
        peer_overlay_active: bool,
    ) -> None:
        # Success flow order: bundle → log → overlay → UI event → OSC enqueue.
        # Peer channel: also complete_peer_logical_turn() after all overlay work.
        publish_to_chatbox = self._ctx._should_publish_to_chatbox(runtime)
        bundle = self._ctx.get_or_create_bundle(utterance_id, channel=runtime.channel)
        bundle.with_translation(translation)
        self._log.emit_ready(
            translation=translation,
            runtime=runtime,
        )
        if peer_overlay_active:
            await self._overlay.emit_peer_translation_to_overlay(
                translation=translation,
                runtime=runtime,
                applied_context_mode=applied_mode,
            )
            await self._overlay.emit_overlay_utterance_closed_with_latency(
                utterance_id=utterance_id,
                channel=runtime.channel,
                is_final=True,
                finalize_latency=not publish_to_chatbox,
            )
        await self._ctx.ui_events.put(
            UIEvent(
                type=UIEventType.TRANSLATION_DONE,
                utterance_id=utterance_id,
                payload=translation,
                source=self._ctx._get_source(utterance_id, channel=runtime.channel),
            )
        )
        if runtime.channel == "self":
            await self._overlay.emit_translation_to_overlay(
                translation=translation,
                applied_context_mode=applied_mode,
            )
            await self._overlay.emit_overlay_utterance_closed_with_latency(
                utterance_id=utterance_id,
                channel=runtime.channel,
                is_final=True,
                finalize_latency=not self._ctx._should_publish_to_chatbox(runtime),
            )
        if publish_to_chatbox:
            await self._output.enqueue_osc(
                utterance_id,
                transcript_text=text,
                translation_text=translation.text,
            )
        if runtime.channel == "peer":
            self._peer_turns.complete_peer_logical_turn(utterance_id)
