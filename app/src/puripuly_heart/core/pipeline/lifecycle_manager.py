"""Lifecycle management extracted from Pipeline.

Handles start/stop/replace STT providers.
Dependencies: PipelineContext, AudioMediator, OutputMediator, OverlayEmitter.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puripuly_heart.core.pipeline.audio_mediator import AudioMediator
    from puripuly_heart.core.pipeline.output_mediator import OutputMediator
    from puripuly_heart.core.pipeline.overlay_emitter import OverlayEmitter
    from puripuly_heart.core.pipeline.pipeline import Pipeline
    from puripuly_heart.core.pipeline.pipeline_context import PipelineContext
    from puripuly_heart.ports.hub import STTProvider

__all__ = ["LifecycleManager"]


class LifecycleManager:
    """Lifecycle management extracted from Pipeline.

    Dependencies injected via constructor:
    - ctx: PipelineContext
    - audio: AudioMediator (STT event loops)
    - output: OutputMediator (OSC flush)
    - overlay: OverlayEmitter (reset overlay preview)
    """

    def __init__(
        self,
        ctx: PipelineContext,
        audio: AudioMediator,
        output: OutputMediator,
        overlay: OverlayEmitter,
    ) -> None:
        self._ctx = ctx
        self._audio = audio
        self._output = output
        self._overlay = overlay

    async def start(
        self,
        pipeline: Pipeline,
        *,
        auto_flush_osc: bool = False,
    ) -> None:
        if pipeline._running:
            return
        pipeline._running = True
        if pipeline.stt is not None:
            pipeline._stt_task = asyncio.create_task(
                self._audio.run_stt_event_loop(pipeline.stt)
            )
        if pipeline.peer_stt is not None:
            pipeline._peer_stt_task = asyncio.create_task(
                self._audio.run_stt_event_loop(pipeline.peer_stt)
            )
        if auto_flush_osc:
            pipeline._osc_flush_task = asyncio.create_task(
                self._output.run_flush_loop()
            )

    async def stop(self, pipeline: Pipeline) -> None:
        if not pipeline._running:
            self._output.clear_typing_reasons()
            return
        # Stop order: cancel OSC flush → stop STT tasks → close STT providers → close LLM.
        # Closing STT before stopping tasks may leave dangling tasks.
        # Closing LLM last allows pending translation tasks to drain.
        pipeline._running = False
        self._output.clear_typing_reasons()

        if pipeline._osc_flush_task:
            pipeline._osc_flush_task.cancel()
            await asyncio.gather(pipeline._osc_flush_task, return_exceptions=True)
            pipeline._osc_flush_task = None

        await self._audio.stop_stt_task("_stt_task", pipeline)
        await self._audio.stop_stt_task("_peer_stt_task", pipeline)
        await self._overlay.reset_overlay_preview()
        await self._audio.reset_stt_runtime_state()

        if pipeline.stt is not None:
            await pipeline.stt.close()
        if pipeline.peer_stt is not None:
            await pipeline.peer_stt.close()
        if pipeline.llm is not None:
            await pipeline.llm.close()

    async def replace_stt_provider(
        self, pipeline: Pipeline, stt: STTProvider | None
    ) -> None:
        # STT replacement: stop old → reset runtime state → close old → set new → start new.
        # reset_runtime_state() must happen BEFORE closing old STT to cancel pending tasks.
        old_stt = pipeline.stt
        await self._audio.stop_stt_task("_stt_task", pipeline)
        await self._overlay.reset_overlay_preview()
        await self._ctx.self_runtime.reset_runtime_state()
        self._ctx._latency._clear_latency_state(channel="self")

        if old_stt is not None:
            await old_stt.close()

        pipeline.stt = stt
        self._ctx.self_runtime.stt = stt
        if pipeline._running and stt is not None:
            pipeline._stt_task = asyncio.create_task(
                self._audio.run_stt_event_loop(stt)
            )

    async def replace_peer_stt_provider(
        self, pipeline: Pipeline, stt: STTProvider | None
    ) -> None:
        old_stt = pipeline.peer_stt
        await self._audio.stop_stt_task("_peer_stt_task", pipeline)
        await self._ctx.peer_runtime.reset_runtime_state()
        self._ctx._latency._clear_latency_state(channel="peer")

        if old_stt is not None:
            await old_stt.close()

        pipeline.peer_stt = stt
        self._ctx.peer_runtime.stt = stt
        if pipeline._running and stt is not None:
            pipeline._peer_stt_task = asyncio.create_task(
                self._audio.run_stt_event_loop(stt)
            )
