from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

from puripuly_heart.core.clock import Clock, SystemClock
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.translation_service import TranslationService
from puripuly_heart.core.output_dispatcher import OutputDispatcher
from puripuly_heart.core.pipeline.channel_runtime import (
    ChannelRuntime,
    ContextEntry,
    _MergeBuffer,
)
from puripuly_heart.core.pipeline.context import ContextMode, ContextResolver
from puripuly_heart.ports.osc import OscSink
from puripuly_heart.ports.overlay import OverlayEventFactory, OverlaySink
from puripuly_heart.core.runtime_logging import (
    SessionRuntimeLoggingService,
)
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart, VadEvent
from puripuly_heart.domain.events import (
    UIEvent,
)
from puripuly_heart.domain.models import (
    ChannelId,
    Transcript,
    Translation,
    UtteranceBundle,
)
from puripuly_heart.ports.hub import STTProvider
from puripuly_heart.core.pipeline.latency_tracker import LatencyTracker
from puripuly_heart.core.pipeline.pipeline_context import PipelineContext
from puripuly_heart.core.pipeline.peer_turn_tracker import PeerTurnTracker
from puripuly_heart.core.pipeline.overlay_emitter import OverlayEmitter
from puripuly_heart.core.pipeline.buffer_manager_standalone import BufferManager
from puripuly_heart.core.pipeline.output_mediator import OutputMediator, _SELF_SPEECH_TYPING_REASON
from puripuly_heart.core.pipeline.translation_logger import TranslationLogger
from puripuly_heart.core.pipeline.translation_executor import TranslationExecutor
from puripuly_heart.core.pipeline.transcript_mediator import TranscriptMediator
from puripuly_heart.core.pipeline.audio_mediator import AudioMediator
from puripuly_heart.core.pipeline.vad_handler import VADHandler
from puripuly_heart.core.pipeline.lifecycle_manager import LifecycleManager

__all__ = ["STTProvider", "Pipeline"]


@dataclass(slots=True)
class Pipeline:
    """Composition root for all pipeline modules.

    Contains only fields, __post_init__ wiring, and thin delegation stubs.
    All business logic lives in injected modules.
    """
    stt: STTProvider | None
    llm: LLMProvider | None
    osc: OscSink
    overlay_event_adapter: OverlayEventFactory
    fallback_llm: LLMProvider | None = None
    peer_stt: STTProvider | None = None
    overlay_sink: OverlaySink | None = None
    translation_service: TranslationService | None = None
    output_dispatcher: OutputDispatcher | None = None
    clock: Clock = SystemClock()
    runtime_logging: SessionRuntimeLoggingService | None = None

    source_language: str = "ko"
    target_language: str = "en"
    second_target_language: str = ""
    peer_source_language: str = ""
    peer_target_language: str = ""
    system_prompt: str = ""
    chatbox_include_source: bool = True
    fallback_transcript_only: bool = False
    translation_enabled: bool = True
    peer_translation_enabled: bool = False
    integrated_context_enabled: bool = False
    # Self VAD hangover in seconds for user-facing E2E latency.
    hangover_s: float = 1.1
    peer_hangover_s: float = 0.6  # Peer VAD hangover in seconds for user-facing E2E latency.

    # Context memory settings
    context_time_window_s: float = 30.0  # Only include entries within this time window
    context_max_entries: int = 3  # Maximum number of context entries to include
    integrated_context_time_window_s: float = 40.0
    integrated_context_max_entries: int = 4
    # Settings below control two distinct pipeline modes. low_latency_mode enables
    # buffer-based speculative translation (buffer_manager.py). Other modes use
    # direct STT→Translation→OSC flow. Don't change these defaults without
    # understanding the different code paths through buffer_manager.py vs transcript_mediator.py.
    low_latency_mode: bool = False
    low_latency_spec_retry_max: int = 1  # wired from settings; not yet read by pipeline (planned feature)
    low_latency_finalize_wait_ms: int = 400
    low_latency_awaiting_vad_timeout_s: float = 3.0  # Timeout for awaiting_vad_end state

    ui_events: asyncio.Queue[UIEvent] = field(default_factory=asyncio.Queue)

    _utterances: dict[UUID, UtteranceBundle] = field(default_factory=dict)
    _translation_tasks: dict[UUID, asyncio.Task[None]] = field(default_factory=dict)
    _utterance_sources: dict[UUID, str] = field(default_factory=dict)
    _utterance_start_times: dict[UUID, float] = field(
        default_factory=dict
    )  # For E2E latency tracking
    _translation_history: list[ContextEntry] = field(default_factory=list)  # Context memory
    # _speech_ended_ids tracks utterance IDs that have received SpeechEnd.
    # Used by latency_tracker to finalize E2E timing. Cleared after OSC enqueue
    # in output_mediator.py. If you clear this elsewhere, latency summary won't emit.
    _speech_ended_ids: set[UUID] = field(default_factory=set)  # Track SpeechEnd arrivals
    _stt_task: asyncio.Task[None] | None = None
    _peer_stt_task: asyncio.Task[None] | None = None
    _osc_flush_task: asyncio.Task[None] | None = None
    _running: bool = False
    _merge_buffer: _MergeBuffer | None = None
    self_runtime: ChannelRuntime = field(init=False)
    peer_runtime: ChannelRuntime = field(init=False)
    context_resolver: ContextResolver = field(init=False)
    active_chatbox_channel: ChannelId = field(init=False, default="self")
    last_error_source: str | None = None
    _last_overlay_secondary_runtime_signature: tuple[object, ...] | None = field(
        init=False,
        default=None,
    )
    _latency: LatencyTracker = field(init=False, repr=False)
    ctx: PipelineContext = field(init=False, repr=False)
    peer_turn_tracker: PeerTurnTracker = field(init=False, repr=False)
    overlay_emitter: OverlayEmitter = field(init=False, repr=False)
    buffer_manager: BufferManager = field(init=False, repr=False)
    output_mediator: OutputMediator = field(init=False, repr=False)
    translation_logger: TranslationLogger = field(init=False, repr=False)
    translation_executor: TranslationExecutor = field(init=False, repr=False)
    transcript_mediator: TranscriptMediator = field(init=False, repr=False)
    audio_mediator: AudioMediator = field(init=False, repr=False)
    vad_handler: VADHandler = field(init=False, repr=False)
    lifecycle_manager: LifecycleManager = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Dependency injection order matters: ctx must be created before other mediators
        # because they access ctx properties. peer_turn_tracker needs _latency which
        # is created before it. Don't reorder these lines.
        self.self_runtime = ChannelRuntime(
            channel="self",
            stt=self.stt,
            stt_task=self._stt_task,
            utterances=self._utterances,
            translation_tasks=self._translation_tasks,
            utterance_sources=self._utterance_sources,
            utterance_start_times=self._utterance_start_times,
            translation_history=self._translation_history,
            speech_ended_ids=self._speech_ended_ids,
            merge_buffer=self._merge_buffer,
            alias_target=self,
        )
        self.peer_runtime = ChannelRuntime(channel="peer", stt=self.peer_stt)
        self.context_resolver = ContextResolver(
            clock=self.clock,
            local_time_window_s=self.context_time_window_s,
            local_max_entries=self.context_max_entries,
            integrated_time_window_s=self.integrated_context_time_window_s,
            integrated_max_entries=self.integrated_context_max_entries,
        )
        self.ctx = PipelineContext(self)
        self._latency = LatencyTracker(
            clock=self.clock,
            hangover_s=self.hangover_s,
            peer_hangover_s=self.peer_hangover_s,
            emit_basic=self.ctx._emit_basic,
            emit_detailed=self.ctx._emit_detailed,
        )
        self.peer_turn_tracker = PeerTurnTracker(self.peer_runtime, self._latency)
        self.overlay_emitter = OverlayEmitter(self.ctx, self.peer_turn_tracker)
        self.output_mediator = OutputMediator(self.ctx, self.osc)
        self.translation_logger = TranslationLogger(self.ctx)
        self.translation_executor = TranslationExecutor(
            self.ctx, self.overlay_emitter, self.output_mediator,
            self.peer_turn_tracker, self.translation_logger,
        )
        self.transcript_mediator = TranscriptMediator(
            self.ctx, self.overlay_emitter, self.translation_executor,
            self.output_mediator, self.peer_turn_tracker,
        )
        self.buffer_manager = BufferManager(
            self.ctx,
            self.overlay_emitter,
            on_handle_transcript=self.transcript_mediator.handle,
            on_translate_and_enqueue=self.translation_executor.translate_and_enqueue,
            on_translate_text=self.translation_executor.translate_text,
            on_enqueue_osc=self._enqueue_osc,
        )
        self.vad_handler = VADHandler(
            self.ctx, self.buffer_manager, self.peer_turn_tracker,
        )
        self.audio_mediator = AudioMediator(
            self.ctx, self.transcript_mediator, self.peer_turn_tracker,
            self.buffer_manager, self.output_mediator, self.translation_executor,
        )
        self.lifecycle_manager = LifecycleManager(
            self.ctx, self.audio_mediator, self.output_mediator, self.overlay_emitter,
        )
        self.ctx._on_peer_turn_state_clear = self.peer_turn_tracker.clear_state
        self.ctx._on_overlay_preview_reset = self.overlay_emitter.reset_overlay_preview

    @staticmethod
    def _format_log_message(message: str, *args: object) -> str:
        return PipelineContext._format_log_message(message, *args)

    def _emit_basic(
        self,
        message: str,
        *args: object,
        level: int = logging.INFO,
        fallback_level: int | None = None,
    ) -> None:
        self.ctx._emit_basic(message, *args, level=level, fallback_level=fallback_level)

    def _emit_detailed(
        self,
        message: str,
        *args: object,
        level: int = logging.INFO,
        fallback_level: int | None = None,
    ) -> bool:
        return self.ctx._emit_detailed(message, *args, level=level, fallback_level=fallback_level)

    def _emit_metric(self, message: str, *args: object) -> None:
        self.ctx._emit_metric(message, *args)

    @staticmethod
    def _latency_key(channel: ChannelId, utterance_id: UUID) -> tuple[ChannelId, UUID]:
        return PipelineContext._latency_key(channel, utterance_id)

    def _emit_exception_summary(
        self,
        message: str,
        *args: object,
        level: int = logging.ERROR,
    ) -> None:
        self.ctx._emit_exception_summary(message, *args, level=level)

    def _translation_skip_reason(self, runtime: ChannelRuntime) -> str:
        return self.ctx._translation_skip_reason(runtime)

    def _log_translation_skipped(
        self,
        *,
        stage: str,
        runtime: ChannelRuntime,
        publish_chatbox: bool,
    ) -> None:
        self.ctx._log_translation_skipped(
            stage=stage, runtime=runtime, publish_chatbox=publish_chatbox,
        )

    def _log_translation_failure(
        self,
        *,
        stage: str,
        runtime: ChannelRuntime,
        exc: Exception,
        detailed: bool = False,
    ) -> None:
        self.ctx._log_translation_failure(
            stage=stage, runtime=runtime, exc=exc, detailed=detailed,
        )

    async def start(self, *, auto_flush_osc: bool = False) -> None:
        await self.lifecycle_manager.start(self, auto_flush_osc=auto_flush_osc)

    async def stop(self) -> None:
        await self.lifecycle_manager.stop(self)

    async def replace_stt_provider(self, stt: STTProvider | None) -> None:
        await self.lifecycle_manager.replace_stt_provider(self, stt)

    async def replace_peer_stt_provider(self, stt: STTProvider | None) -> None:
        await self.lifecycle_manager.replace_peer_stt_provider(self, stt)

    def mark_promo_eligible(self) -> None:
        self.audio_mediator.mark_promo_eligible()

    def clear_context(self) -> None:
        """Clear the translation context history."""
        self.ctx.clear_context()

    # -- PeerTurnTracker delegation --

    def _clear_peer_logical_turn_state(self) -> None:
        self.peer_turn_tracker.clear_state()

    def _complete_peer_logical_turn(
        self,
        peer_turn_id: UUID,
        *,
        preserve_parent_speech_end_time: bool = False,
    ) -> None:
        self.peer_turn_tracker.complete_peer_logical_turn(
            peer_turn_id,
            preserve_parent_speech_end_time=preserve_parent_speech_end_time,
        )

    def _peer_logical_turn_transcript(
        self, transcript: Transcript
    ) -> tuple[UUID, Transcript]:
        return self.peer_turn_tracker.peer_logical_turn_transcript(transcript)

    # -- OverlayEmitter delegation --

    async def _emit_final_transcript_to_overlay(self, transcript: Transcript) -> None:
        await self.overlay_emitter.emit_final_transcript_to_overlay(transcript)

    async def _finalize_peer_source_only(
        self,
        transcript: Transcript,
        *,
        close_is_final: bool,
        finalize_latency: bool,
        preserve_parent_speech_end_time: bool = False,
    ) -> None:
        await self.overlay_emitter.finalize_peer_source_only(
            transcript,
            close_is_final=close_is_final,
            finalize_latency=finalize_latency,
            preserve_parent_speech_end_time=preserve_parent_speech_end_time,
        )

    async def _emit_overlay_utterance_closed(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        is_final: bool,
    ) -> None:
        await self.overlay_emitter.emit_overlay_utterance_closed(
            utterance_id=utterance_id, channel=channel, is_final=is_final,
        )

    def _finalize_latency_for_utterance(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        finalize_latency: bool | None = None,
    ) -> None:
        self.overlay_emitter.finalize_latency_for_utterance(
            utterance_id=utterance_id, channel=channel, finalize_latency=finalize_latency,
        )

    async def _emit_translation_to_overlay(
        self,
        *,
        translation: Translation,
        applied_context_mode: ContextMode | None,
    ) -> None:
        await self.overlay_emitter.emit_translation_to_overlay(
            translation=translation, applied_context_mode=applied_context_mode,
        )

    async def _emit_peer_translation_to_overlay(
        self,
        *,
        translation: Translation,
        runtime: ChannelRuntime,
        applied_context_mode: ContextMode | None,
    ) -> None:
        await self.overlay_emitter.emit_peer_translation_to_overlay(
            translation=translation, runtime=runtime,
            applied_context_mode=applied_context_mode,
        )

    async def _emit_self_active_overlay_event(self, event: object) -> None:
        await self.overlay_emitter.emit_self_active_overlay_event(event)

    def _overlay_translation_will_follow(self, runtime: ChannelRuntime) -> bool:
        return self.overlay_emitter.overlay_translation_will_follow(runtime)

    def _peer_terminal_work_will_follow(self, runtime: ChannelRuntime) -> bool:
        return self.overlay_emitter.peer_terminal_work_will_follow(runtime)

    async def _sync_overlay_active_self(
        self, buffer: _MergeBuffer | None, *, created_at: float | None = None
    ) -> None:
        await self.overlay_emitter.sync_overlay_active_self(buffer, created_at=created_at)

    async def reset_overlay_preview(self) -> None:
        await self.overlay_emitter.reset_overlay_preview()

    def _self_overlay_languages_for_utterance(
        self, utterance_id: UUID
    ) -> tuple[str, str]:
        return self.overlay_emitter._self_overlay_languages_for_utterance(utterance_id)

    def _active_self_occupant_key(self, buffer: _MergeBuffer) -> str:
        return self.overlay_emitter._active_self_occupant_key(buffer)

    def _should_blank_stale_active_secondary_before_finalizing(
        self,
        *,
        final_text: str,
        reuse_mode: str | None,
    ) -> bool:
        return self.overlay_emitter.should_blank_stale_active_secondary_before_finalizing(
            final_text=final_text, reuse_mode=reuse_mode,
        )

    # -- OutputMediator delegation --

    async def _enqueue_osc(
        self,
        utterance_id: UUID,
        *,
        transcript_text: str,
        translation_text: str | None,
    ) -> None:
        await self.output_mediator.enqueue_osc(
            utterance_id,
            transcript_text=transcript_text,
            translation_text=translation_text,
        )

    def _set_osc_typing_reason(self, reason: str, active: bool) -> None:
        self.output_mediator.set_typing_reason(reason, active)

    def _clear_osc_typing_reasons(self) -> None:
        self.output_mediator.clear_typing_reasons()

    # -- TranslationExecutor delegation --

    async def _ensure_translation(self, transcript: Transcript) -> None:
        await self.translation_executor.ensure_translation(transcript)

    async def _translate_text(
        self,
        utterance_id: UUID,
        text: str,
        *,
        record_latency: bool = True,
    ) -> Translation:
        return await self.translation_executor.translate_text(
            utterance_id, text, record_latency=record_latency,
        )

    async def _translate_and_enqueue(
        self,
        utterance_id: UUID,
        text: str,
        *,
        runtime: ChannelRuntime | None = None,
    ) -> None:
        await self.translation_executor.translate_and_enqueue(
            utterance_id, text, runtime=runtime,
        )

    # -- TranscriptMediator delegation --

    async def submit_text(self, text: str, *, source: str = "You") -> UUID:
        return await self.transcript_mediator.submit_text(text, source=source)

    async def _handle_transcript(
        self, transcript: Transcript, *, is_final: bool, source: str | None
    ) -> None:
        await self.transcript_mediator.handle(transcript, is_final=is_final, source=source)

    async def handle_vad_event(self, event: VadEvent) -> None:
        # VAD event handler branches by event type. low_latency_mode adds extra
        # bookkeeping (mark_resume_pending, maybe_confirm_resume) that interacts with
        # buffer_manager. If you add a new VAD event type, check both code paths.
        resume_overlay_resync_buffer: _MergeBuffer | None = None

        if isinstance(event, SpeechStart):
            if self.low_latency_mode:
                self.vad_handler.mark_resume_pending(event)

        if isinstance(event, SpeechChunk):
            if self.low_latency_mode:
                resume_overlay_resync_buffer = self.vad_handler.maybe_confirm_resume(event)

        if isinstance(event, SpeechEnd):
            speech_end_at = self.clock.now()
            self.output_mediator.set_typing_reason(_SELF_SPEECH_TYPING_REASON, True)
            self._utterance_start_times[event.utterance_id] = speech_end_at
            self._speech_ended_ids.add(event.utterance_id)
            self._latency._record_latency_stage(
                channel="self",
                utterance_id=event.utterance_id,
                stage="speech_end",
                timestamp=speech_end_at,
                publish_now=not self.low_latency_mode,
            )
            if self.low_latency_mode:
                self.vad_handler.maybe_update_buffer_end_time(event.utterance_id)
                self.vad_handler.maybe_start_finalize_wait(event.utterance_id)
                await self.vad_handler.maybe_clear_resume_on_end(event)

        if self.stt is not None:
            await self.stt.handle_vad_event(event)

        if (
            resume_overlay_resync_buffer is not None
            and self._merge_buffer is resume_overlay_resync_buffer
        ):
            await self.overlay_emitter.sync_overlay_active_self(
                resume_overlay_resync_buffer
            )

    async def handle_peer_vad_event(self, event: VadEvent) -> None:
        if isinstance(event, SpeechEnd):
            speech_end_at = self.clock.now()
            self.peer_runtime.utterance_start_times[event.utterance_id] = speech_end_at
            self.peer_runtime.speech_ended_ids.add(event.utterance_id)
            self._latency._record_latency_stage(
                channel="peer",
                utterance_id=event.utterance_id,
                stage="speech_end",
                timestamp=speech_end_at,
            )
            self.vad_handler.record_peer_speech_end(
                event.utterance_id, speech_end_at
            )
        if self.peer_stt is not None:
            await self.peer_stt.handle_vad_event(event)

    def _runtime_for_channel(self, channel: ChannelId) -> ChannelRuntime:
        return self.ctx._runtime_for_channel(channel)

    async def clear_language_runtime_state(self, *, channel: ChannelId) -> None:
        await self.ctx.clear_language_runtime_state(channel=channel)

    def _runtime_for_utterance(
        self, utterance_id: UUID, *, default_channel: ChannelId = "self"
    ) -> ChannelRuntime:
        return self.ctx._runtime_for_utterance(utterance_id, default_channel=default_channel)

    def get_or_create_bundle(
        self, utterance_id: UUID, *, channel: ChannelId = "self"
    ) -> UtteranceBundle:
        return self.ctx.get_or_create_bundle(utterance_id, channel=channel)

    def _emit_translation_ready_for_output(
        self,
        *,
        translation: Translation,
        runtime: ChannelRuntime,
    ) -> bool:
        return self.ctx._emit_translation_ready_for_output(
            translation=translation, runtime=runtime,
        )









    def _merge_text(self, parts: list[str]) -> str:
        return self.ctx._merge_text(parts)

    def _merge_with_overlap(self, existing: str, addition: str) -> str:
        return self.ctx._merge_with_overlap(existing, addition)

    def _relaxed_overlap_merge(self, existing: str, addition: str) -> str | None:
        return self.ctx._relaxed_overlap_merge(existing, addition)

    def _strip_trailing_boundary(self, text: str) -> tuple[str, int]:
        return self.ctx._strip_trailing_boundary(text)

    def _strip_leading_boundary(self, text: str) -> tuple[str, int]:
        return self.ctx._strip_leading_boundary(text)

    def _is_boundary_char(self, ch: str) -> bool:
        return self.ctx._is_boundary_char(ch)

    def _soft_reuse_mode(self, spec_text: str | None, final_text: str) -> str | None:
        return self.ctx._soft_reuse_mode(spec_text, final_text)

    def _normalize_soft_reuse_text(self, text: str) -> str:
        return self.ctx._normalize_soft_reuse_text(text)

    def _is_soft_reuse_boundary_char(self, ch: str) -> bool:
        return self.ctx._is_soft_reuse_boundary_char(ch)

    def _needs_space(self, left: str, right: str) -> bool:
        return self.ctx._needs_space(left, right)

    def _is_ascii_alnum(self, ch: str) -> bool:
        return self.ctx._is_ascii_alnum(ch)

    def _remember_source(
        self,
        utterance_id: UUID,
        source: str | None,
        *,
        channel: ChannelId = "self",
    ) -> None:
        self.ctx._remember_source(utterance_id, source, channel=channel)

    def _get_source(self, utterance_id: UUID, *, channel: ChannelId = "self") -> str | None:
        return self.ctx._get_source(utterance_id, channel=channel)

    def _source_language_for(self, runtime: ChannelRuntime) -> str:
        return self.ctx._source_language_for(runtime)

    def _target_language_for(self, runtime: ChannelRuntime) -> str:
        return self.ctx._target_language_for(runtime)

    def _other_runtime(self, runtime: ChannelRuntime) -> ChannelRuntime:
        return self.ctx._other_runtime(runtime)

    def _should_publish_to_chatbox(self, runtime: ChannelRuntime) -> bool:
        return self.ctx._should_publish_to_chatbox(runtime)

    def _translation_enabled_for_runtime(self, runtime: ChannelRuntime) -> bool:
        return self.ctx._translation_enabled_for_runtime(runtime)

    def enqueue_peer_translation_disclosure(self, text: str) -> None:
        self.output_mediator.enqueue_peer_translation_disclosure(text)
