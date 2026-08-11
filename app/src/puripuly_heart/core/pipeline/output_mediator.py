"""OSC output management extracted from Pipeline.

Handles OSC message dispatch, typing reasons, and flush loop.
Dependencies: PipelineContext (config, runtime, logging), OscSink, OutputDispatcher.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from puripuly_heart.domain.events import UIEvent, UIEventType
from puripuly_heart.domain.models import OSCMessage

if TYPE_CHECKING:
    from puripuly_heart.core.pipeline.pipeline_context import PipelineContext
    from puripuly_heart.ports.osc import OscSink

logger = logging.getLogger(__name__)

_SELF_SPEECH_TYPING_REASON = "self_speech_pending"

__all__ = ["OutputMediator"]


class OutputMediator:
    """OSC output management extracted from Pipeline.

    Dependencies injected via constructor:
    - ctx: PipelineContext (config, runtime, logging, latency)
    - osc: OscSink (OSC output port)
    """

    def __init__(
        self,
        ctx: PipelineContext,
        osc: OscSink,
    ) -> None:
        self._ctx = ctx
        self._osc = osc

    async def enqueue_osc(
        self,
        utterance_id: UUID,
        *,
        transcript_text: str,
        translation_text: str | None,
    ) -> None:
        runtime = self._ctx._runtime_for_utterance(utterance_id)

        self._ctx._emit_detailed(
            "[Hub] OSC enqueue preview: channel=%s text=%r",
            runtime.channel,
            translation_text or transcript_text,
            fallback_level=logging.INFO,
        )
        if runtime.channel == "self":
            self._ctx._latency._record_latency_stage(
                channel=runtime.channel,
                utterance_id=utterance_id,
                stage="self_chatbox_enqueue",
            )

        if self._ctx.output_dispatcher is not None:
            await self._ctx.output_dispatcher.dispatch_osc(
                utterance_id,
                transcript_text=transcript_text,
                translation_text=translation_text,
                runtime=runtime,
            )
        else:
            # enqueue_osc pops utterance_id from runtime.utterance_start_times and
            # speech_ended_ids. These are needed by latency_tracker for E2E calculation.
            # If you remove these pop/discard calls, latency summary will include stale data.
            runtime.utterance_start_times.pop(utterance_id, None)
            runtime.speech_ended_ids.discard(utterance_id)

        if runtime.channel == "self":
            self.set_typing_reason(_SELF_SPEECH_TYPING_REASON, False)

        await self._ctx.ui_events.put(
            UIEvent(
                type=UIEventType.OSC_SENT,
                utterance_id=utterance_id,
                payload=None,
                source=self._ctx._get_source(utterance_id),
                channel=runtime.channel,
            )
        )
        self._ctx._latency._clear_latency_timeline(
            channel=runtime.channel, utterance_id=utterance_id
        )

    def enqueue_peer_translation_disclosure(self, text: str) -> None:
        msg = OSCMessage(
            utterance_id=uuid4(), text=text, created_at=self._ctx.clock.now()
        )
        self._ctx._emit_detailed(
            "[Hub] OSC disclosure enqueue: channel=peer text_len=%s",
            len(text),
            fallback_level=logging.INFO,
        )
        self._osc.enqueue(msg)

    def set_typing_reason(self, reason: str, active: bool) -> None:
        set_reason = getattr(self._osc, "set_typing_reason", None)
        if callable(set_reason):
            set_reason(reason, active)
            return
        self._osc.send_typing(active)

    def clear_typing_reasons(self) -> None:
        clear_reasons = getattr(self._osc, "clear_typing_reasons", None)
        if callable(clear_reasons):
            clear_reasons()
            return
        self._osc.send_typing(False)

    async def run_flush_loop(self) -> None:
        # run_flush_loop delegates to output_dispatcher.run_osc_flush if configured.
        # If output_dispatcher is None, this is a no-op. This allows OSC to be optional
        # in development/test environments. Don't make this a hard dependency.
        if self._ctx.output_dispatcher is not None:
            await self._ctx.output_dispatcher.run_osc_flush()
