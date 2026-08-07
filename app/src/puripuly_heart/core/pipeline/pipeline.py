from __future__ import annotations

import asyncio
import logging
import sys
import time
import traceback
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
from puripuly_heart.domain.models import Transcript
from puripuly_heart.ports.osc import OscSink
from puripuly_heart.ports.overlay import OverlayEventFactory, OverlaySink
from puripuly_heart.core.runtime_logging import (
    SessionLoggingMode,
    SessionRuntimeLoggingService,
    format_translation_ready_for_output,
)
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart, VadEvent
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
from puripuly_heart.domain.models import (
    ChannelId,
    OSCMessage,
    Transcript,
    Translation,
    UtteranceBundle,
)
from puripuly_heart.ports.hub import STTProvider
from puripuly_heart.core.pipeline.latency_tracker import LatencyTracker, _LatencyTimeline
from puripuly_heart.core.pipeline import text_merge
from puripuly_heart.core.pipeline.stages.buffer_manager import BufferManagerMixin
from puripuly_heart.core.pipeline.stages.overlay_helpers import OverlayHelpersHost, OverlayHelpersMixin
from puripuly_heart.core.pipeline.stages.peer_turns import PeerTurnsMixin

__all__ = ["STTProvider", "Pipeline"]


_PROMO_INTERVAL_SEC: float = 300.0  # 5 minutes
_SELF_SPEECH_TYPING_REASON = "self_speech_pending"


@dataclass(slots=True)
class Pipeline(OverlayHelpersMixin, PeerTurnsMixin, BufferManagerMixin):
    """Pipeline satisfies OverlayHelpersHost Protocol.

    All public attributes defined in OverlayHelpersHost are present as fields.
    See overlay_helpers.py for Protocol definition and requirements.
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
    low_latency_mode: bool = False
    low_latency_spec_retry_max: int = 1
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
    _speech_ended_ids: set[UUID] = field(default_factory=set)  # Track SpeechEnd arrivals
    _stt_task: asyncio.Task[None] | None = None
    _peer_stt_task: asyncio.Task[None] | None = None
    _osc_flush_task: asyncio.Task[None] | None = None
    _running: bool = False
    _last_promo_time: float | None = None
    _promo_eligible: bool = False
    _merge_buffer: _MergeBuffer | None = None
    self_runtime: ChannelRuntime = field(init=False)
    peer_runtime: ChannelRuntime = field(init=False)
    _peer_turn_parent_ids: dict[UUID, UUID] = field(default_factory=dict)
    _peer_parent_turn_ids: dict[UUID, set[UUID]] = field(default_factory=dict)
    _peer_completed_turn_ids: set[UUID] = field(default_factory=set)
    _peer_parent_speech_end_times: dict[UUID, float] = field(default_factory=dict)
    context_resolver: ContextResolver = field(init=False)
    active_chatbox_channel: ChannelId = field(init=False, default="self")
    last_error_source: str | None = None
    _last_overlay_secondary_runtime_signature: tuple[object, ...] | None = field(
        init=False,
        default=None,
    )
    _latency: LatencyTracker = field(init=False, repr=False)

    def __post_init__(self) -> None:
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
        self._latency = LatencyTracker(
            clock=self.clock,
            hangover_s=self.hangover_s,
            peer_hangover_s=self.peer_hangover_s,
            emit_basic=self._emit_basic,
            emit_detailed=self._emit_detailed,
        )

    @staticmethod
    def _format_log_message(message: str, *args: object) -> str:
        return message % args if args else message

    def _emit_basic(
        self,
        message: str,
        *args: object,
        level: int = logging.INFO,
        fallback_level: int | None = None,
    ) -> None:
        formatted = self._format_log_message(message, *args)
        if self.runtime_logging is not None:
            self.runtime_logging.emit_basic(formatted, level=level)
            return
        logger.log(level if fallback_level is None else fallback_level, formatted)

    def _emit_detailed(
        self,
        message: str,
        *args: object,
        level: int = logging.INFO,
        fallback_level: int | None = None,
    ) -> bool:
        if self.runtime_logging is not None:
            return self.runtime_logging.emit_detailed_lazy(
                lambda: self._format_log_message(message, *args),
                level=level,
            )
        _ = fallback_level
        return False

    def _emit_metric(self, message: str, *args: object) -> None:
        self._emit_detailed(message, *args, fallback_level=logging.DEBUG)

    @staticmethod
    def _latency_key(channel: ChannelId, utterance_id: UUID) -> tuple[ChannelId, UUID]:
        return LatencyTracker._latency_key(channel, utterance_id)

    def _emit_exception_summary(
        self,
        message: str,
        *args: object,
        level: int = logging.ERROR,
    ) -> None:
        formatted = self._format_log_message(message, *args)
        if self.runtime_logging is not None:
            self.runtime_logging.emit_basic(formatted, level=level)
            detail = "".join(traceback.format_exception(*sys.exc_info())).rstrip()
            if detail:
                self.runtime_logging.emit_detailed(detail, level=level)
            return
        logger.exception(formatted)

    def _translation_skip_reason(self, runtime: ChannelRuntime) -> str:
        if self.llm is None:
            return "llm unavailable"
        if not self.translation_enabled:
            return "translation disabled"
        if runtime.channel == "peer" and not self.peer_translation_enabled:
            return "peer translation disabled"
        return "translation disabled"

    def _log_translation_skipped(
        self,
        *,
        stage: str,
        runtime: ChannelRuntime,
        publish_chatbox: bool,
    ) -> None:
        self._emit_detailed(
            "[Hub] Translation skipped (stage=%s, channel=%s, publish_chatbox=%s): %s",
            stage,
            runtime.channel,
            publish_chatbox,
            self._translation_skip_reason(runtime),
            fallback_level=logging.INFO,
        )

    def _log_translation_failure(
        self,
        *,
        stage: str,
        runtime: ChannelRuntime,
        exc: Exception,
        detailed: bool = False,
    ) -> None:
        emit = self._emit_detailed if detailed else self._emit_basic
        message = str(exc)
        emit(
            "[Hub] Translation failed (stage=%s, channel=%s): %s",
            stage,
            runtime.channel,
            message,
            level=logging.ERROR,
            fallback_level=logging.ERROR,
        )

    async def start(self, *, auto_flush_osc: bool = False) -> None:
        if self._running:
            return
        self._running = True
        if self.stt is not None:
            self._stt_task = asyncio.create_task(self._run_stt_event_loop(self.stt))
        if self.peer_stt is not None:
            self._peer_stt_task = asyncio.create_task(self._run_stt_event_loop(self.peer_stt))
        if auto_flush_osc:
            self._osc_flush_task = asyncio.create_task(self._run_osc_flush_loop())

    async def stop(self) -> None:
        if not self._running:
            self._clear_osc_typing_reasons()
            return
        self._running = False
        self._clear_osc_typing_reasons()

        if self._osc_flush_task:
            self._osc_flush_task.cancel()
            await asyncio.gather(self._osc_flush_task, return_exceptions=True)
            self._osc_flush_task = None

        await self._stop_stt_event_loop()
        await self.reset_overlay_preview()
        await self._reset_stt_runtime_state()

        if self.stt is not None:
            await self.stt.close()
        if self.peer_stt is not None:
            await self.peer_stt.close()

        if self.llm is not None:
            await self.llm.close()

    async def replace_stt_provider(self, stt: STTProvider | None) -> None:
        old_stt = self.stt
        await self._stop_stt_task("_stt_task")
        await self.reset_overlay_preview()
        await self.self_runtime.reset_runtime_state()
        self._latency._clear_latency_state(channel="self")

        if old_stt is not None:
            await old_stt.close()

        self.stt = stt
        self.self_runtime.stt = stt
        if self._running and self.stt is not None:
            self._stt_task = asyncio.create_task(self._run_stt_event_loop(self.stt))

    async def replace_peer_stt_provider(self, stt: STTProvider | None) -> None:
        old_stt = self.peer_stt
        await self._stop_stt_task("_peer_stt_task")
        await self.peer_runtime.reset_runtime_state()
        self._clear_peer_logical_turn_state()
        self._latency._clear_latency_state(channel="peer")

        if old_stt is not None:
            await old_stt.close()

        self.peer_stt = stt
        self.peer_runtime.stt = stt
        if self._running and self.peer_stt is not None:
            self._peer_stt_task = asyncio.create_task(self._run_stt_event_loop(self.peer_stt))

    def mark_promo_eligible(self) -> None:
        """Mark that user clicked STT button. Next STREAMING state will send promo."""
        self._promo_eligible = True

    def clear_context(self) -> None:
        """Clear the translation context history."""
        self.self_runtime.clear_context()
        self.peer_runtime.clear_context()
        self._emit_basic("[Hub] Context history cleared")

    async def handle_vad_event(self, event: VadEvent) -> None:
        resume_overlay_resync_buffer: _MergeBuffer | None = None

        if isinstance(event, SpeechStart):
            if self.low_latency_mode:
                self._mark_resume_pending(event)

        if isinstance(event, SpeechChunk):
            if self.low_latency_mode:
                resume_overlay_resync_buffer = self._maybe_confirm_resume(event)

        # Record start time for E2E latency tracking (from speech end)
        if isinstance(event, SpeechEnd):
            speech_end_at = self.clock.now()
            self._set_osc_typing_reason(_SELF_SPEECH_TYPING_REASON, True)
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
                self._maybe_update_buffer_end_time(event.utterance_id)
                self._maybe_start_finalize_wait(event.utterance_id)
                await self._maybe_clear_resume_on_end(event)

        if self.stt is not None:
            await self.stt.handle_vad_event(event)

        if (
            resume_overlay_resync_buffer is not None
            and self._merge_buffer is resume_overlay_resync_buffer
        ):
            await self._sync_overlay_active_self(resume_overlay_resync_buffer)

    async def handle_peer_vad_event(self, event: VadEvent) -> None:
        if isinstance(event, SpeechEnd):
            speech_end_at = self.clock.now()
            self.peer_runtime.utterance_start_times[event.utterance_id] = speech_end_at
            self.peer_runtime.speech_ended_ids.add(event.utterance_id)
            self._peer_parent_speech_end_times[event.utterance_id] = speech_end_at
            self._latency._record_latency_stage(
                channel="peer",
                utterance_id=event.utterance_id,
                stage="speech_end",
                timestamp=speech_end_at,
            )
            for peer_turn_id in tuple(self._peer_parent_turn_ids.get(event.utterance_id, set())):
                if peer_turn_id in self._peer_completed_turn_ids:
                    continue
                self._inherit_peer_parent_vad_bookkeeping(
                    parent_utterance_id=event.utterance_id,
                    peer_turn_id=peer_turn_id,
                )
            if event.utterance_id in self._peer_parent_turn_ids:
                self._maybe_clear_completed_peer_parent(event.utterance_id)
        if self.peer_stt is not None:
            await self.peer_stt.handle_vad_event(event)

    async def submit_text(self, text: str, *, source: str = "You") -> UUID:
        text = text.strip()
        if not text:
            raise ValueError("text must be non-empty")

        utterance_id = uuid4()
        self._remember_source(utterance_id, source)

        transcript = Transcript(
            utterance_id=utterance_id,
            text=text,
            is_final=True,
            created_at=self.clock.now(),
        )
        await self._handle_transcript(transcript, is_final=True, source=source)

        if self.llm is None or not self.translation_enabled:
            await self._enqueue_osc(utterance_id, transcript_text=text, translation_text=None)
        else:
            await self._ensure_translation(transcript)

        return utterance_id

    def _runtime_for_channel(self, channel: ChannelId) -> ChannelRuntime:
        return self.self_runtime if channel == "self" else self.peer_runtime

    async def clear_language_runtime_state(self, *, channel: ChannelId) -> None:
        runtime = self._runtime_for_channel(channel)
        await runtime.clear_live_translation_state()
        if channel == "peer":
            self._clear_peer_logical_turn_state()
        self._latency._clear_latency_state(channel=channel)
        if channel == "self":
            await self.reset_overlay_preview()

    def _runtime_for_utterance(
        self, utterance_id: UUID, *, default_channel: ChannelId = "self"
    ) -> ChannelRuntime:
        if utterance_id in self.self_runtime.utterances:
            return self.self_runtime
        if utterance_id in self.peer_runtime.utterances:
            return self.peer_runtime
        return self._runtime_for_channel(default_channel)

    def get_or_create_bundle(
        self, utterance_id: UUID, *, channel: ChannelId = "self"
    ) -> UtteranceBundle:
        return self._runtime_for_utterance(
            utterance_id, default_channel=channel
        ).get_or_create_bundle(utterance_id)

    async def _run_stt_event_loop(self, provider: STTProvider) -> None:
        try:
            async for ev in provider.events():
                await self._handle_stt_event(ev)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._emit_exception_summary(
                "[Hub] STT event loop crashed: %s",
                exc,
                level=logging.ERROR,
            )
            raise

    async def _stop_stt_event_loop(self) -> None:
        await self._stop_stt_task("_stt_task")
        await self._stop_stt_task("_peer_stt_task")

    async def _stop_stt_task(self, attr_name: str) -> None:
        task = getattr(self, attr_name)
        if task is None:
            return
        setattr(self, attr_name, None)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _reset_stt_runtime_state(self) -> None:
        await self.self_runtime.reset_runtime_state()
        await self.peer_runtime.reset_runtime_state()
        self._clear_peer_logical_turn_state()
        self._latency._clear_latency_state()

    async def _handle_stt_event(self, event: STTEvent) -> None:
        if isinstance(event, STTSessionStateEvent):
            self._emit_basic(
                "[Hub] STT state: channel=%s state=%s",
                event.channel,
                event.state.name,
            )
            await self.ui_events.put(
                UIEvent(
                    type=UIEventType.SESSION_STATE_CHANGED,
                    payload=event.state,
                    channel=event.channel,
                )
            )
            if event.state == STTSessionState.STREAMING and event.channel == "self":
                self._send_stt_connected_notification()
            return

        if isinstance(event, STTErrorEvent):
            await self.ui_events.put(
                UIEvent(
                    type=UIEventType.ERROR,
                    payload=event.message,
                    source="Peer" if event.channel == "peer" else "Mic",
                    channel=event.channel,
                    runtime_log_handled=event.runtime_log_handled,
                )
            )
            return

        if isinstance(event, STTPartialEvent):
            if event.channel == "peer":
                return
            self._send_stt_connected_notification()
            if self.low_latency_mode:
                return
            self._emit_detailed(
                f"[Hub] STT Partial: '{event.transcript.text[:50]}...' id={str(event.transcript.utterance_id)[:8]}",
                fallback_level=logging.DEBUG,
            )
            await self._handle_transcript(event.transcript, is_final=False, source="Mic")
            return

        if isinstance(event, STTFinalEvent):
            runtime = self._runtime_for_channel(event.channel)
            source = "Peer" if runtime.channel == "peer" else "Mic"
            logger.info("[Pipeline][%s] STT final: '%s'", runtime.channel, event.transcript.text)
            if runtime.channel == "peer":
                parent_utterance_id, peer_transcript = self._peer_logical_turn_transcript(
                    event.transcript
                )
                await self._handle_peer_final_transcript(
                    peer_transcript,
                    parent_utterance_id=parent_utterance_id,
                    source=source,
                )
                return
            if runtime.channel == "self":
                self._send_stt_connected_notification()
            if self.low_latency_mode and runtime.channel == "self":
                await self._handle_low_latency_final(event.transcript)
                return
            self._latency._record_latency_stage(
                channel=runtime.channel,
                utterance_id=event.transcript.utterance_id,
                stage="stt_final",
            )
            await self._handle_transcript(event.transcript, is_final=True, source=source)
            if self.llm is None or not self._translation_enabled_for_runtime(runtime):
                self._log_translation_skipped(
                    stage="final",
                    runtime=runtime,
                    publish_chatbox=self._should_publish_to_chatbox(runtime),
                )
                if self._should_publish_to_chatbox(runtime):
                    await self._enqueue_osc(
                        event.transcript.utterance_id,
                        transcript_text=event.transcript.text,
                        translation_text=None,
                    )
                else:
                    self._latency._finalize_latency_timeline(
                        runtime=runtime, channel=runtime.channel,
                        utterance_id=event.transcript.utterance_id,
                    )
            else:
                await self._ensure_translation(event.transcript)
            return

    def _send_stt_connected_notification(self) -> None:
        """Send promo message when STT connects (only if user clicked button)."""
        if not self._promo_eligible:
            return  # Skip if not triggered by user button click
        self._promo_eligible = False

        now = self.clock.now()
        if self._last_promo_time is not None:
            if now - self._last_promo_time < _PROMO_INTERVAL_SEC:
                return
        if self.osc.send_immediate("PuriPuly ON!"):
            self._last_promo_time = now

    async def _handle_transcript(
        self, transcript: Transcript, *, is_final: bool, source: str | None
    ) -> None:
        runtime = self._runtime_for_channel(transcript.channel)
        bundle = self.get_or_create_bundle(transcript.utterance_id, channel=transcript.channel)
        bundle.with_transcript(transcript)
        self._remember_source(transcript.utterance_id, source, channel=transcript.channel)
        await self.ui_events.put(
            UIEvent(
                type=UIEventType.TRANSCRIPT_FINAL if is_final else UIEventType.TRANSCRIPT_PARTIAL,
                utterance_id=transcript.utterance_id,
                payload=transcript,
                source=source,
            )
        )
        if is_final:
            if runtime.channel == "peer":
                peer_terminal_work_will_follow = self._peer_terminal_work_will_follow(runtime)
                if self._overlay_translation_will_follow(runtime):
                    await self._ensure_translation(transcript)
                elif self.overlay_sink is not None:
                    await self._finalize_peer_source_only(
                        transcript,
                        close_is_final=True,
                        finalize_latency=not peer_terminal_work_will_follow,
                    )
                    if not self._overlay_translation_will_follow(runtime) and self._should_publish_to_chatbox(runtime):
                        await self._enqueue_osc(
                            transcript.utterance_id,
                            transcript_text=transcript.text,
                            translation_text=None,
                        )
                elif not peer_terminal_work_will_follow:
                    self._latency._finalize_latency_timeline(
                        runtime=self._runtime_for_channel(transcript.channel),
                        channel=transcript.channel,
                        utterance_id=transcript.utterance_id,
                    )
                return
            await self._emit_final_transcript_to_overlay(transcript)
            if not self._overlay_translation_will_follow(runtime):
                await self._emit_overlay_utterance_closed(
                    utterance_id=transcript.utterance_id,
                    channel=transcript.channel,
                    is_final=True,
                )
                if self._should_publish_to_chatbox(runtime):
                    await self._enqueue_osc(
                        transcript.utterance_id,
                        transcript_text=transcript.text,
                        translation_text=None,
                    )

    async def _handle_peer_final_transcript(
        self,
        transcript: Transcript,
        *,
        parent_utterance_id: UUID,
        source: str,
    ) -> None:
        _ = parent_utterance_id
        runtime = self.peer_runtime
        bundle = runtime.get_or_create_bundle(transcript.utterance_id)
        bundle.with_transcript(transcript)
        self._remember_source(transcript.utterance_id, source, channel="peer")
        await self.ui_events.put(
            UIEvent(
                type=UIEventType.TRANSCRIPT_FINAL,
                utterance_id=transcript.utterance_id,
                payload=transcript,
                source=source,
            )
        )
        self._latency._record_latency_stage(
            channel="peer",
            utterance_id=transcript.utterance_id,
            stage="stt_final",
        )
        if self.llm is None or not self._translation_enabled_for_runtime(runtime):
            self._log_translation_skipped(
                stage="final",
                runtime=runtime,
                publish_chatbox=self._should_publish_to_chatbox(runtime),
            )
            await self._finalize_peer_source_only(
                transcript,
                close_is_final=True,
                finalize_latency=not self._should_publish_to_chatbox(runtime),
                preserve_parent_speech_end_time=True,
            )
            if self._should_publish_to_chatbox(runtime):
                await self._enqueue_osc(
                    transcript.utterance_id,
                    transcript_text=transcript.text,
                    translation_text=None,
                )
            return
        await self._ensure_translation(transcript)






    def _emit_translation_ready_for_output(
        self,
        *,
        translation: Translation,
        runtime: ChannelRuntime,
    ) -> bool:
        if self.runtime_logging is None:
            return False
        return self.runtime_logging.emit_detailed_lazy(
            lambda: format_translation_ready_for_output(
                channel=runtime.channel,
                utterance_id=str(translation.utterance_id),
                update_id=translation.update_id,
                origin_wall_clock_ms=translation.origin_wall_clock_ms,
                session_scope=translation.session_scope,
                source_text_hash=translation.source_text_hash,
                source_text_len=translation.source_text_len,
                logical_turn_key=translation.logical_turn_key,
                translation_len=len(translation.text),
                elapsed_ms=self._latency._translation_ready_elapsed_ms(
                    channel=runtime.channel,
                    utterance_id=translation.utterance_id,
                ),
            )
        )









    def _merge_text(self, parts: list[str]) -> str:
        return text_merge._merge_text(parts)

    def _merge_with_overlap(self, existing: str, addition: str) -> str:
        return text_merge._merge_with_overlap(existing, addition)

    def _relaxed_overlap_merge(self, existing: str, addition: str) -> str | None:
        return text_merge._relaxed_overlap_merge(existing, addition)

    def _strip_trailing_boundary(self, text: str) -> tuple[str, int]:
        return text_merge._strip_trailing_boundary(text)

    def _strip_leading_boundary(self, text: str) -> tuple[str, int]:
        return text_merge._strip_leading_boundary(text)

    def _is_boundary_char(self, ch: str) -> bool:
        return text_merge._is_boundary_char(ch)

    def _soft_reuse_mode(self, spec_text: str | None, final_text: str) -> str | None:
        return text_merge._soft_reuse_mode(spec_text, final_text)

    def _normalize_soft_reuse_text(self, text: str) -> str:
        return text_merge._normalize_soft_reuse_text(text)





    def _is_soft_reuse_boundary_char(self, ch: str) -> bool:
        return text_merge._is_soft_reuse_boundary_char(ch)

    def _needs_space(self, left: str, right: str) -> bool:
        return text_merge._needs_space(left, right)

    def _is_ascii_alnum(self, ch: str) -> bool:
        return text_merge._is_ascii_alnum(ch)

    def _remember_source(
        self,
        utterance_id: UUID,
        source: str | None,
        *,
        channel: ChannelId = "self",
    ) -> None:
        self._runtime_for_utterance(utterance_id, default_channel=channel).remember_source(
            utterance_id, source
        )

    def _get_source(self, utterance_id: UUID, *, channel: ChannelId = "self") -> str | None:
        runtime = self._runtime_for_utterance(utterance_id, default_channel=channel)
        source = runtime.get_source(utterance_id)
        if source is not None:
            return source
        other_runtime = self.peer_runtime if runtime is self.self_runtime else self.self_runtime
        return other_runtime.get_source(utterance_id)

    def _source_language_for(self, runtime: ChannelRuntime) -> str:
        if runtime.channel == "peer" and self.peer_source_language:
            return self.peer_source_language
        return self.source_language

    def _target_language_for(self, runtime: ChannelRuntime) -> str:
        if runtime.channel == "peer" and self.peer_target_language:
            return self.peer_target_language
        return self.target_language

    def _other_runtime(self, runtime: ChannelRuntime) -> ChannelRuntime:
        return self.peer_runtime if runtime is self.self_runtime else self.self_runtime

    def _should_publish_to_chatbox(self, runtime: ChannelRuntime) -> bool:
        return runtime.channel == self.active_chatbox_channel

    def _translation_enabled_for_runtime(self, runtime: ChannelRuntime) -> bool:
        if runtime.channel == "peer":
            return self.translation_enabled and self.peer_translation_enabled
        return self.translation_enabled

    async def _ensure_translation(self, transcript: Transcript) -> None:
        if self.llm is None:
            return
        runtime = self._runtime_for_channel(transcript.channel)
        if not self._translation_enabled_for_runtime(runtime):
            return
        utterance_id = transcript.utterance_id
        if utterance_id in runtime.translation_tasks:
            return
        task = asyncio.create_task(
            self._translate_and_enqueue(
                utterance_id,
                transcript.text,
                runtime=runtime,
            )
        )
        runtime.translation_tasks[utterance_id] = task
        task.add_done_callback(lambda _t: runtime.translation_tasks.pop(utterance_id, None))

    async def _translate_text(
        self,
        utterance_id: UUID,
        text: str,
        *,
        record_latency: bool = True,
    ) -> Translation:
        """Translate text and return the Translation object. Used by speculative translation."""
        if self.llm is None or self.translation_service is None:
            raise RuntimeError("LLM or translation service not available")

        runtime = self.self_runtime
        if record_latency:
            self._latency._record_latency_stage(
                channel="self",
                utterance_id=utterance_id,
                stage="llm_request_start",
            )

        formatted_prompt, context_str, now, _applied_mode = self.translation_service.prepare_request(
            text, runtime=runtime, self_rt=self.self_runtime, peer_rt=self.peer_runtime,
        )
        self.translation_service.remember_context(text, now, runtime=runtime)

        translation = await self.translation_service.translate(
            text, utterance_id=utterance_id, runtime=runtime,
            self_rt=self.self_runtime, peer_rt=self.peer_runtime,
            _prepared_prompt=formatted_prompt, _prepared_context=context_str,
        )
        if translation is None:
            raise RuntimeError("LLM translation failed")

        if record_latency:
            self._latency._record_latency_stage(
                channel="self",
                utterance_id=utterance_id,
                stage="llm_done",
            )
        return translation

    async def _translate_and_enqueue(
        self,
        utterance_id: UUID,
        text: str,
        *,
        runtime: ChannelRuntime | None = None,
    ) -> None:
        if self.llm is None:
            return
        runtime = runtime or self.self_runtime
        applied_mode: ContextMode | None = None
        peer_overlay_active = runtime.channel == "peer" and self.overlay_sink is not None
        try:
            if self.translation_service is not None:
                formatted_prompt, context_str, now, applied_mode = self.translation_service.prepare_request(
                    text, runtime=runtime, self_rt=self.self_runtime, peer_rt=self.peer_runtime,
                )
                self.translation_service.remember_context(text, now, runtime=runtime)
            self._latency._record_latency_stage(
                channel=runtime.channel,
                utterance_id=utterance_id,
                stage="llm_request_start",
            )

            if self.translation_service is not None:
                translation = await self.translation_service.translate(
                    text, utterance_id=utterance_id, runtime=runtime,
                    self_rt=self.self_runtime, peer_rt=self.peer_runtime,
                    _prepared_prompt=formatted_prompt, _prepared_context=context_str,
                )
            else:
                translation = None
            if translation is None:
                raise RuntimeError("LLM translation failed")
            self._latency._record_latency_stage(
                channel=runtime.channel,
                utterance_id=utterance_id,
                stage="llm_done",
            )
        except asyncio.CancelledError:
            if runtime.channel == "self":
                await self._emit_overlay_utterance_closed_with_latency(
                    utterance_id=utterance_id,
                    channel=runtime.channel,
                    is_final=False,
                    finalize_latency=not self._should_publish_to_chatbox(runtime),
                )
            elif runtime.channel == "peer":
                await self._finalize_peer_source_only(
                    Transcript(
                        utterance_id=utterance_id,
                        text=text,
                        is_final=True,
                        created_at=self.clock.now(),
                        channel="peer",
                    ),
                    close_is_final=False,
                    finalize_latency=True,
                )
            else:
                self._latency._finalize_latency_timeline(runtime=runtime, channel=runtime.channel, utterance_id=utterance_id)
            raise
        except Exception as exc:
            self._log_translation_failure(stage="final", runtime=runtime, exc=exc)
            fallback_to_chatbox = self.fallback_transcript_only and self._should_publish_to_chatbox(
                runtime
            )
            payload: object = str(exc)
            await self.ui_events.put(
                UIEvent(
                    type=UIEventType.ERROR,
                    utterance_id=utterance_id,
                    payload=payload,
                    source=self._get_source(utterance_id, channel=runtime.channel),
                    channel=runtime.channel,
                    runtime_log_handled=True,
                )
            )
            if runtime.channel == "self":
                await self._emit_overlay_utterance_closed_with_latency(
                    utterance_id=utterance_id,
                    channel=runtime.channel,
                    is_final=False,
                    finalize_latency=not (
                        self.fallback_transcript_only and self._should_publish_to_chatbox(runtime)
                    ),
                )
            elif runtime.channel == "peer":
                await self._finalize_peer_source_only(
                    Transcript(
                        utterance_id=utterance_id,
                        text=text,
                        is_final=True,
                        created_at=self.clock.now(),
                        channel="peer",
                    ),
                    close_is_final=False,
                    finalize_latency=not fallback_to_chatbox,
                )
            if fallback_to_chatbox:
                await self._enqueue_osc(
                    utterance_id,
                    transcript_text=text,
                    translation_text=None,
                )
            return

        publish_to_chatbox = self._should_publish_to_chatbox(runtime)
        bundle = self.get_or_create_bundle(utterance_id, channel=runtime.channel)
        bundle.with_translation(translation)
        self._emit_translation_ready_for_output(
            translation=translation,
            runtime=runtime,
        )
        if peer_overlay_active:
            await self._emit_peer_translation_to_overlay(
                translation=translation,
                runtime=runtime,
                applied_context_mode=applied_mode,
            )
            await self._emit_overlay_utterance_closed_with_latency(
                utterance_id=utterance_id,
                channel=runtime.channel,
                is_final=True,
                finalize_latency=not publish_to_chatbox,
            )
        await self.ui_events.put(
            UIEvent(
                type=UIEventType.TRANSLATION_DONE,
                utterance_id=utterance_id,
                payload=translation,
                source=self._get_source(utterance_id, channel=runtime.channel),
            )
        )
        if runtime.channel == "self":
            await self._emit_translation_to_overlay(
                translation=translation,
                applied_context_mode=applied_mode,
            )
            await self._emit_overlay_utterance_closed_with_latency(
                utterance_id=utterance_id,
                channel=runtime.channel,
                is_final=True,
                finalize_latency=not self._should_publish_to_chatbox(runtime),
            )
        if publish_to_chatbox:
            await self._enqueue_osc(
                utterance_id,
                transcript_text=text,
                translation_text=translation.text,
            )
        if runtime.channel == "peer":
            self._complete_peer_logical_turn(utterance_id)

    async def _enqueue_osc(
        self,
        utterance_id: UUID,
        *,
        transcript_text: str,
        translation_text: str | None,
    ) -> None:
        runtime = self._runtime_for_utterance(utterance_id)

        self._emit_detailed(
            "[Hub] OSC enqueue preview: channel=%s text=%r",
            runtime.channel,
            translation_text or transcript_text,
            fallback_level=logging.INFO,
        )
        if runtime.channel == "self":
            self._latency._record_latency_stage(
                channel=runtime.channel,
                utterance_id=utterance_id,
                stage="self_chatbox_enqueue",
            )

        if self.output_dispatcher is not None:
            await self.output_dispatcher.dispatch_osc(
                utterance_id,
                transcript_text=transcript_text,
                translation_text=translation_text,
                runtime=runtime,
            )
        else:
            runtime.utterance_start_times.pop(utterance_id, None)
            runtime.speech_ended_ids.discard(utterance_id)

        if runtime.channel == "self":
            self._set_osc_typing_reason(_SELF_SPEECH_TYPING_REASON, False)

        await self.ui_events.put(
            UIEvent(
                type=UIEventType.OSC_SENT,
                utterance_id=utterance_id,
                payload=None,
                source=self._get_source(utterance_id),
                channel=runtime.channel,
            )
        )
        self._latency._clear_latency_timeline(channel=runtime.channel, utterance_id=utterance_id)

    def enqueue_peer_translation_disclosure(self, text: str) -> None:
        msg = OSCMessage(utterance_id=uuid4(), text=text, created_at=self.clock.now())
        self._emit_detailed(
            "[Hub] OSC disclosure enqueue: channel=peer text_len=%s",
            len(text),
            fallback_level=logging.INFO,
        )
        self.osc.enqueue(msg)

    def _set_osc_typing_reason(self, reason: str, active: bool) -> None:
        set_reason = getattr(self.osc, "set_typing_reason", None)
        if callable(set_reason):
            set_reason(reason, active)
            return
        self.osc.send_typing(active)

    def _clear_osc_typing_reasons(self) -> None:
        clear_reasons = getattr(self.osc, "clear_typing_reasons", None)
        if callable(clear_reasons):
            clear_reasons()
            return
        self.osc.send_typing(False)

    async def _run_osc_flush_loop(self) -> None:
        if self.output_dispatcher is not None:
            await self.output_dispatcher.run_osc_flush()
