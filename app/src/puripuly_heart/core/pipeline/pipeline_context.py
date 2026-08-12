"""Shared state and utilities extracted from Pipeline.

PipelineContext owns logging, runtime lookups, language config access,
utterance source tracking, text merge delegation, and context clearing.
Config fields are accessed via live references to Pipeline — no copies, no sync.
"""

from __future__ import annotations

import logging
import sys
import traceback
from typing import TYPE_CHECKING, Any, Callable, Awaitable
from uuid import UUID

from puripuly_heart.core.language import source_language_for, target_language_for

from puripuly_heart.core.pipeline.channel_runtime import ChannelRuntime
from puripuly_heart.core.pipeline.latency_tracker import LatencyTracker
from puripuly_heart.core.runtime_logging import (
    SessionRuntimeLoggingService,
    format_translation_ready_for_output,
)
from puripuly_heart.domain.models import ChannelId, Translation, UtteranceBundle
from puripuly_heart.core.pipeline import text_merge

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

__all__ = ["PipelineContext"]


class PipelineContext:
    """Shared state and utilities extracted from Pipeline.

    All config fields accessed via live references to Pipeline — no copies,
    no sync mechanism needed. Changes to Pipeline fields propagate instantly.
    """

    def __init__(
        self,
        pipeline: Any,
    ) -> None:
        self._pipeline = pipeline
        self._on_peer_turn_state_clear: Callable[[], None] | None = None
        self._on_overlay_preview_reset: Callable[[], Awaitable[None]] | None = None

    # ── Config properties (live references to Pipeline) ────────────────

    @property
    def llm(self) -> Any:
        return self._pipeline.llm

    @property
    def translation_enabled(self) -> bool:
        return self._pipeline.translation_enabled

    @property
    def peer_translation_enabled(self) -> bool:
        return self._pipeline.peer_translation_enabled

    @property
    def fallback_transcript_only(self) -> bool:
        return self._pipeline.fallback_transcript_only

    @property
    def source_language(self) -> str:
        return self._pipeline.source_language

    @property
    def target_language(self) -> str:
        return self._pipeline.target_language

    @property
    def peer_source_language(self) -> str:
        return self._pipeline.peer_source_language

    @property
    def peer_target_language(self) -> str:
        return self._pipeline.peer_target_language

    @property
    def active_chatbox_channel(self) -> ChannelId:
        return self._pipeline.active_chatbox_channel

    @property
    def self_runtime(self) -> ChannelRuntime:
        return self._pipeline.self_runtime

    @property
    def peer_runtime(self) -> ChannelRuntime:
        return self._pipeline.peer_runtime

    @property
    def runtime_logging(self) -> SessionRuntimeLoggingService | None:
        return self._pipeline.runtime_logging

    @property
    def _latency(self) -> LatencyTracker:
        return self._pipeline._latency

    # ── OSC sink (for AudioMediator) ───────────────────────────────────

    @property
    def osc(self) -> Any:
        return self._pipeline.osc

    # ── Overlay properties (for OverlayEmitter) ────────────────────────

    @property
    def overlay_sink(self) -> Any:
        return self._pipeline.overlay_sink

    @property
    def overlay_event_adapter(self) -> Any:
        return self._pipeline.overlay_event_adapter

    @property
    def last_error_source(self) -> str | None:
        return self._pipeline.last_error_source

    @last_error_source.setter
    def last_error_source(self, value: str | None) -> None:
        self._pipeline.last_error_source = value

    @property
    def _last_overlay_secondary_runtime_signature(self) -> tuple[object, ...] | None:
        return self._pipeline._last_overlay_secondary_runtime_signature

    @_last_overlay_secondary_runtime_signature.setter
    def _last_overlay_secondary_runtime_signature(
        self, value: tuple[object, ...] | None
    ) -> None:
        self._pipeline._last_overlay_secondary_runtime_signature = value

    # ── BufferManager properties ───────────────────────────────────────

    @property
    def clock(self) -> Any:
        return self._pipeline.clock

    @property
    def ui_events(self) -> Any:
        return self._pipeline.ui_events

    @property
    def translation_service(self) -> Any:
        return self._pipeline.translation_service

    @property
    def output_dispatcher(self) -> Any:
        return self._pipeline.output_dispatcher

    @property
    def low_latency_mode(self) -> bool:
        return self._pipeline.low_latency_mode

    @property
    def low_latency_awaiting_vad_timeout_s(self) -> float:
        return self._pipeline.low_latency_awaiting_vad_timeout_s

    @property
    def low_latency_finalize_wait_ms(self) -> int:
        return self._pipeline.low_latency_finalize_wait_ms

    @property
    def _merge_buffer(self) -> Any:
        """Read-only accessor. Write via self_runtime.merge_buffer to keep ChannelRuntime in sync."""
        return self._pipeline._merge_buffer

    @property
    def _utterance_start_times(self) -> dict:
        return self._pipeline._utterance_start_times

    @property
    def _speech_ended_ids(self) -> set:
        return self._pipeline._speech_ended_ids

    @property
    def _translation_tasks(self) -> dict:
        return self._pipeline._translation_tasks

    # ── Logging (1.2) ──────────────────────────────────────────────────

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

    # ── Runtime (1.3) ──────────────────────────────────────────────────

    def _runtime_for_channel(self, channel: ChannelId) -> ChannelRuntime:
        return self.self_runtime if channel == "self" else self.peer_runtime

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

    # ── Language (1.4) ─────────────────────────────────────────────────

    def _source_language_for(self, runtime: ChannelRuntime) -> str:
        return source_language_for(self, runtime)

    def _target_language_for(self, runtime: ChannelRuntime) -> str:
        return target_language_for(self, runtime)

    def _other_runtime(self, runtime: ChannelRuntime) -> ChannelRuntime:
        return self.peer_runtime if runtime is self.self_runtime else self.self_runtime

    # ── Config (1.5) ───────────────────────────────────────────────────

    def _should_publish_to_chatbox(self, runtime: ChannelRuntime) -> bool:
        return runtime.channel == self.active_chatbox_channel

    def _translation_enabled_for_runtime(self, runtime: ChannelRuntime) -> bool:
        if runtime.channel == "peer":
            return self.translation_enabled and self.peer_translation_enabled
        return self.translation_enabled

    # ── Source (1.6) ───────────────────────────────────────────────────

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

    # ── Context clearing (1.9) ─────────────────────────────────────────

    def clear_context(self) -> None:
        self.self_runtime.clear_context()
        self.peer_runtime.clear_context()
        self._emit_basic("[Hub] Context history cleared")

    async def clear_language_runtime_state(self, *, channel: ChannelId) -> None:
        # clear_language_runtime_state is called when languages change or context resets.
        # The order matters: runtime.clear_live_translation_state → peer_turn_clear →
        # latency_clear → overlay_preview_reset. Changing this order causes orphaned state.
        runtime = self._runtime_for_channel(channel)
        await runtime.clear_live_translation_state()
        if channel == "peer" and self._on_peer_turn_state_clear is not None:
            self._on_peer_turn_state_clear()
        self._latency._clear_latency_state(channel=channel)
        if channel == "self" and self._on_overlay_preview_reset is not None:
            await self._on_overlay_preview_reset()

    # ── Text merge delegation (1.8) ────────────────────────────────────

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

    # ── Translation logging (used by BufferManager) ────────────────────

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
