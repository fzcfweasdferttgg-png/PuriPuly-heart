from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import UUID

from puripuly_heart.core.pipeline import text_merge
from puripuly_heart.core.pipeline.channel_runtime import _MergeBuffer
from puripuly_heart.domain.models import Transcript, Translation

if TYPE_CHECKING:
    from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart

logger = logging.getLogger(__name__)


class BufferManagerMixin:
    """Low-latency merge buffer / speculative-translation management extracted from Pipeline."""

    # ------------------------------------------------------------------
    # Merge buffer part management
    # ------------------------------------------------------------------

    def _upsert_merge_part(self, buffer: _MergeBuffer, utterance_id: UUID, text: str) -> None:
        if not text:
            return
        for idx in range(len(buffer.utterance_ids) - 1, -1, -1):
            if buffer.utterance_ids[idx] == utterance_id:
                existing = buffer.parts[idx]
                if existing == text:
                    return
                if text in existing:
                    return
                if existing in text:
                    merged = text
                else:
                    merged = self._merge_with_overlap(existing, text)
                if merged != existing:
                    buffer.parts[idx] = merged
                    self._emit_metric(
                        "[Metric] final_update id=%s index=%s text_len=%s",
                        str(buffer.merge_id)[:8],
                        idx,
                        len(merged),
                    )
                return
        buffer.parts.append(text)
        buffer.utterance_ids.append(utterance_id)

    # ------------------------------------------------------------------
    # Resume state
    # ------------------------------------------------------------------

    def _clear_resume_state(self, buffer: _MergeBuffer) -> None:
        buffer.resume_pending = False
        buffer.resume_confirmed = False
        buffer.resume_utterance_id = None
        buffer.resume_chunk_count = 0
        buffer.resume_started_at = None
        self._cancel_resume_end_timeout(buffer)

    # ------------------------------------------------------------------
    # Speculative translation latency tracking
    # ------------------------------------------------------------------

    def _clear_spec_latency_state(self, buffer: _MergeBuffer) -> None:
        buffer.spec_latency_stage_times.clear()

    def _record_spec_latency_stage(
        self,
        buffer: _MergeBuffer,
        *,
        stage: str,
        timestamp: float | None = None,
    ) -> None:
        buffer.spec_latency_stage_times[stage] = (
            self.clock.now() if timestamp is None else timestamp
        )

    def _promote_spec_latency_to_output(self, buffer: _MergeBuffer) -> None:
        if not buffer.spec_latency_stage_times:
            return
        for stage in ("llm_request_start", "llm_first_chunk", "llm_done"):
            timestamp = buffer.spec_latency_stage_times.get(stage)
            if timestamp is None:
                continue
            self._latency._record_latency_stage(
                channel="self",
                utterance_id=buffer.merge_id,
                stage=stage,
                timestamp=timestamp,
                publish_now=False,
            )
        self._clear_spec_latency_state(buffer)
        self._latency._emit_latency_contract_if_ready(channel="self", utterance_id=buffer.merge_id)

    # ------------------------------------------------------------------
    # Speculative translation state
    # ------------------------------------------------------------------

    def _clear_spec_state(self, buffer: _MergeBuffer, *, reason: str) -> bool:
        had_spec_state = any(
            value is not None
            for value in (
                buffer.spec_task,
                buffer.spec_translation,
                buffer.spec_text,
                buffer.spec_started_at,
                buffer.spec_done_at,
            )
        ) or bool(buffer.spec_latency_stage_times)
        if not had_spec_state:
            return False
        if buffer.spec_task is not None and not buffer.spec_task.done():
            buffer.spec_task.cancel()
            self._emit_metric(
                "[Metric] spec_cancel id=%s reason=%s",
                str(buffer.merge_id)[:8],
                reason,
            )
        elif buffer.spec_translation is not None:
            self._emit_metric(
                "[Metric] spec_cancel id=%s reason=%s",
                str(buffer.merge_id)[:8],
                reason,
            )
        self._clear_spec_latency_state(buffer)
        buffer.spec_task = None
        buffer.spec_translation = None
        buffer.spec_text = None
        buffer.spec_started_at = None
        buffer.spec_done_at = None
        return True

    # ------------------------------------------------------------------
    # Buffer end time tracking
    # ------------------------------------------------------------------

    def _maybe_update_buffer_end_time(self, utterance_id: UUID) -> None:
        buffer = self._merge_buffer
        if buffer is None or utterance_id not in buffer.utterance_ids:
            return
        end_time = self._utterance_start_times.get(utterance_id)
        if end_time is None:
            return
        if buffer.start_time is None or end_time < buffer.start_time:
            buffer.start_time = end_time
        if buffer.last_end_time is None or end_time > buffer.last_end_time:
            buffer.last_end_time = end_time

    # ------------------------------------------------------------------
    # Finalize wait timeout
    # ------------------------------------------------------------------

    def _cancel_finalize_wait(self, buffer: _MergeBuffer) -> None:
        task = buffer.finalize_wait_task
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
        buffer.finalize_wait_task = None
        buffer.finalize_wait_started_at = None

    def _maybe_start_finalize_wait(self, utterance_id: UUID) -> None:
        buffer = self._merge_buffer
        if buffer is None:
            return
        if not buffer.awaiting_vad_end or buffer.awaiting_vad_utterance_id != utterance_id:
            return
        buffer.awaiting_vad_end = False
        buffer.awaiting_vad_utterance_id = None
        self._cancel_awaiting_vad_timeout(buffer)
        self._restart_post_end_grace(buffer)

    # ------------------------------------------------------------------
    # Awaiting VAD timeout
    # ------------------------------------------------------------------

    def _cancel_awaiting_vad_timeout(self, buffer: _MergeBuffer) -> None:
        task = buffer.awaiting_vad_timeout_task
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
        buffer.awaiting_vad_timeout_task = None

    def _start_awaiting_vad_timeout(self, buffer: _MergeBuffer) -> None:
        if self.low_latency_awaiting_vad_timeout_s <= 0:
            return
        self._cancel_awaiting_vad_timeout(buffer)
        buffer.awaiting_vad_timeout_task = asyncio.create_task(
            self._awaiting_vad_timeout(buffer.merge_id)
        )

    async def _awaiting_vad_timeout(self, merge_id: UUID) -> None:
        try:
            await asyncio.sleep(self.low_latency_awaiting_vad_timeout_s)
        except asyncio.CancelledError:
            return
        buffer = self._merge_buffer
        if buffer is None or buffer.merge_id != merge_id:
            return
        if not buffer.awaiting_vad_end:
            return
        self._emit_metric(
            "[Metric] awaiting_vad_timeout id=%s timeout_s=%s",
            str(merge_id)[:8],
            self.low_latency_awaiting_vad_timeout_s,
        )
        buffer.awaiting_vad_end = False
        buffer.awaiting_vad_utterance_id = None
        buffer.awaiting_vad_timeout_task = None
        self._restart_post_end_grace(buffer)

    # ------------------------------------------------------------------
    # Resume end timeout
    # ------------------------------------------------------------------

    def _cancel_resume_end_timeout(self, buffer: _MergeBuffer) -> None:
        task = buffer.resume_end_timeout_task
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
        buffer.resume_end_timeout_task = None
        buffer.resume_end_utterance_id = None

    def _start_resume_end_timeout(self, buffer: _MergeBuffer, utterance_id: UUID) -> None:
        self._cancel_resume_end_timeout(buffer)
        buffer.resume_end_utterance_id = utterance_id
        buffer.resume_end_timeout_task = asyncio.create_task(
            self._resume_end_timeout(buffer.merge_id, utterance_id)
        )

    async def _resume_end_timeout(self, merge_id: UUID, utterance_id: UUID) -> None:
        try:
            await asyncio.sleep(self.low_latency_awaiting_vad_timeout_s)
        except asyncio.CancelledError:
            return
        buffer = self._merge_buffer
        if buffer is None or buffer.merge_id != merge_id:
            return
        if buffer.resume_end_utterance_id != utterance_id:
            return
        if not buffer.resume_confirmed:
            return
        self._emit_metric(
            "[Metric] resume_end_timeout id=%s vad_id=%s timeout_s=%s",
            str(merge_id)[:8],
            str(utterance_id)[:8],
            self.low_latency_awaiting_vad_timeout_s,
        )
        self._clear_resume_state(buffer)
        self._cancel_finalize_wait(buffer)
        await self._try_commit_after_spec(buffer, reason="resume_end_timeout", allow_fallback=True)

    # ------------------------------------------------------------------
    # Post-end grace period
    # ------------------------------------------------------------------

    def _restart_post_end_grace(self, buffer: _MergeBuffer) -> None:
        if self.low_latency_finalize_wait_ms <= 0:
            self._cancel_finalize_wait(buffer)
            return
        self._cancel_finalize_wait(buffer)
        buffer.finalize_wait_started_at = self.clock.now()
        buffer.finalize_wait_task = asyncio.create_task(
            self._finalize_wait_timeout(buffer.merge_id, buffer.finalize_wait_started_at)
        )
        self._emit_metric(
            "[Metric] post_end_grace_start id=%s wait_ms=%s",
            str(buffer.merge_id)[:8],
            self.low_latency_finalize_wait_ms,
        )

    async def _finalize_wait_timeout(self, merge_id: UUID, started_at: float) -> None:
        try:
            await asyncio.sleep(self.low_latency_finalize_wait_ms / 1000.0)
        except asyncio.CancelledError:
            return
        buffer = self._merge_buffer
        if buffer is None or buffer.merge_id != merge_id:
            return
        if buffer.finalize_wait_started_at != started_at:
            return
        buffer.finalize_wait_task = None
        buffer.finalize_wait_started_at = None
        self._emit_metric(
            "[Metric] post_end_grace_timeout id=%s wait_ms=%s",
            str(merge_id)[:8],
            self.low_latency_finalize_wait_ms,
        )
        if self.llm is None or not self.translation_enabled:
            await self._commit_merge(buffer, reason="post_end_grace")
            return
        await self._try_commit_after_spec(buffer, reason="post_end_grace", allow_fallback=False)

    # ------------------------------------------------------------------
    # Resume confirmation logic
    # ------------------------------------------------------------------

    def _mark_resume_pending(self, event: SpeechStart) -> None:
        buffer = self._merge_buffer
        if buffer is None:
            return
        if buffer.resume_pending and buffer.resume_utterance_id == event.utterance_id:
            return
        # 새 resume 시작 시 이전 타임아웃 취소
        self._cancel_resume_end_timeout(buffer)
        buffer.resume_pending = True
        buffer.resume_confirmed = False
        buffer.resume_utterance_id = event.utterance_id
        buffer.resume_chunk_count = 0
        buffer.resume_started_at = self.clock.now()
        self._emit_metric(
            "[Metric] resume_pending id=%s vad_id=%s",
            str(buffer.merge_id)[:8],
            str(event.utterance_id)[:8],
        )

    def _maybe_confirm_resume(self, event: SpeechChunk) -> _MergeBuffer | None:
        buffer = self._merge_buffer
        if buffer is None or not buffer.resume_pending:
            return None
        if buffer.resume_utterance_id != event.utterance_id:
            return None
        if buffer.resume_confirmed:
            return None
        buffer.resume_chunk_count += 1
        if buffer.resume_chunk_count < 3:
            return None
        buffer.resume_confirmed = True
        confirm_ms = 0
        if buffer.resume_started_at is not None:
            confirm_ms = int((self.clock.now() - buffer.resume_started_at) * 1000)
        self._emit_metric(
            "[Metric] resume_confirmed id=%s confirm_ms=%s chunk_count=%s",
            str(buffer.merge_id)[:8],
            confirm_ms,
            buffer.resume_chunk_count,
        )
        cleared_spec_state = self._clear_spec_state(buffer, reason="resume_confirmed")
        if not cleared_spec_state:
            return None
        return buffer

    async def _maybe_clear_resume_on_end(self, event: SpeechEnd) -> None:
        buffer = self._merge_buffer
        if buffer is None:
            return
        if buffer.resume_utterance_id != event.utterance_id:
            return
        if buffer.resume_confirmed:
            # resume_confirmed 상태에서 SpeechEnd → STT Final 대기 타임아웃 시작
            self._start_resume_end_timeout(buffer, event.utterance_id)
            return
        if not buffer.resume_pending:
            return
        false_ms = 0
        if buffer.resume_started_at is not None:
            false_ms = int((self.clock.now() - buffer.resume_started_at) * 1000)
        self._emit_metric(
            "[Metric] resume_false_start id=%s false_ms=%s chunk_count=%s",
            str(buffer.merge_id)[:8],
            false_ms,
            buffer.resume_chunk_count,
        )
        self._clear_resume_state(buffer)
        await self._try_commit_after_spec(buffer, reason="resume_false_start", allow_fallback=True)

    # ------------------------------------------------------------------
    # Final transcript handling (low-latency mode)
    # ------------------------------------------------------------------

    async def _handle_low_latency_final(self, transcript: Transcript) -> None:
        text = transcript.text.strip()
        if not text:
            return

        self._latency._record_latency_stage(
            channel="self",
            utterance_id=transcript.utterance_id,
            stage="stt_final",
            publish_now=False,
        )

        now = self.clock.now()
        buffer = self._merge_buffer
        if buffer is None:
            buffer = _MergeBuffer(merge_id=uuid4(), start_time=now, last_final_at=now)
            self._merge_buffer = buffer
        if buffer.resume_pending or buffer.resume_confirmed:
            self._clear_resume_state(buffer)
        self._upsert_merge_part(buffer, transcript.utterance_id, text)
        buffer.last_final_at = now
        await self._sync_overlay_active_self(buffer, created_at=transcript.created_at)

        end_time = self._utterance_start_times.get(transcript.utterance_id)
        speech_already_ended = transcript.utterance_id in self._speech_ended_ids

        if end_time is None and not speech_already_ended:
            # SpeechEnd has not arrived yet - wait for it
            buffer.awaiting_vad_end = True
            buffer.awaiting_vad_utterance_id = transcript.utterance_id
            self._cancel_finalize_wait(buffer)
            self._start_awaiting_vad_timeout(buffer)
            self._emit_metric(
                "[Metric] final_phase id=%s phase=pre_end vad_id=%s",
                str(buffer.merge_id)[:8],
                str(transcript.utterance_id)[:8],
            )
        else:
            # SpeechEnd already arrived (or end_time exists) - proceed to post_end
            self._maybe_update_buffer_end_time(transcript.utterance_id)
            if (
                buffer.awaiting_vad_end
                and buffer.awaiting_vad_utterance_id == transcript.utterance_id
            ):
                buffer.awaiting_vad_end = False
                buffer.awaiting_vad_utterance_id = None
            self._restart_post_end_grace(buffer)
            self._emit_metric(
                "[Metric] final_phase id=%s phase=post_end vad_id=%s",
                str(buffer.merge_id)[:8],
                str(transcript.utterance_id)[:8],
            )

        if self.llm is None or not self.translation_enabled:
            await self._commit_merge(buffer, reason="final_no_llm")
            return

        await self._maybe_restart_spec(buffer)

    # ------------------------------------------------------------------
    # Merge commit
    # ------------------------------------------------------------------

    async def _commit_merge(self, buffer: _MergeBuffer, *, reason: str) -> None:
        if buffer.resume_pending or buffer.resume_confirmed:
            hold_ms = 0
            if buffer.spec_done_at is not None:
                hold_ms = int((self.clock.now() - buffer.spec_done_at) * 1000)
            self._emit_metric(
                "[Metric] commit_blocked id=%s reason=%s hold_ms=%s",
                str(buffer.merge_id)[:8],
                reason,
                hold_ms,
            )
            return
        if buffer.awaiting_vad_end:
            hold_ms = 0
            if buffer.finalize_wait_started_at is not None:
                hold_ms = int((self.clock.now() - buffer.finalize_wait_started_at) * 1000)
            self._emit_metric(
                "[Metric] commit_blocked id=%s reason=await_vad_end hold_ms=%s",
                str(buffer.merge_id)[:8],
                hold_ms,
            )
            return
        if buffer.finalize_wait_task is not None:
            hold_ms = 0
            if buffer.finalize_wait_started_at is not None:
                hold_ms = int((self.clock.now() - buffer.finalize_wait_started_at) * 1000)
            self._emit_metric(
                "[Metric] commit_deferred id=%s reason=post_end_grace hold_ms=%s",
                str(buffer.merge_id)[:8],
                hold_ms,
            )
            return
        self._cancel_finalize_wait(buffer)
        buffer.awaiting_vad_end = False
        buffer.awaiting_vad_utterance_id = None
        for utterance_id in buffer.utterance_ids:
            self._utterance_start_times.pop(utterance_id, None)
            self._speech_ended_ids.discard(utterance_id)
        if self._merge_buffer is buffer:
            self._merge_buffer = None

        final_text = self._merge_text(buffer.parts)
        if not final_text:
            await self.reset_overlay_preview()
            return

        reuse_mode = None
        if buffer.spec_translation is not None:
            reuse_mode = self._soft_reuse_mode(buffer.spec_text, final_text)

        if self._should_blank_stale_active_secondary_before_finalizing(
            final_text=final_text,
            reuse_mode=reuse_mode,
        ):
            source_language, target_language = self._self_overlay_languages_for_utterance(
                buffer.merge_id
            )
            self._record_overlay_emit(
                event_kind="active_self",
                utterance_id=buffer.merge_id,
                channel="self",
                secondary_len=0,
            )
            await self._emit_self_active_overlay_event(
                self.overlay_event_adapter.self_active_update(
                    text=final_text,
                    utterance_id=buffer.merge_id,
                    secondary_text="",
                    occupant_key=self._active_self_occupant_key(buffer),
                    source_language=source_language,
                    target_language=target_language,
                    created_at=self.clock.now(),
                )
            )

        if buffer.spec_task is not None and not buffer.spec_task.done():
            buffer.spec_task.cancel()

        if buffer.last_end_time is not None:
            self._utterance_start_times[buffer.merge_id] = buffer.last_end_time
        elif buffer.start_time is not None:
            self._utterance_start_times[buffer.merge_id] = buffer.start_time
        self._latency._inherit_latency_for_output(
            channel="self",
            output_utterance_id=buffer.merge_id,
            source_utterance_ids=buffer.utterance_ids,
        )
        for utterance_id in buffer.utterance_ids:
            self._latency._clear_latency_timeline(channel="self", utterance_id=utterance_id)

        transcript = Transcript(
            utterance_id=buffer.merge_id,
            text=final_text,
            is_final=True,
            created_at=self.clock.now(),
        )
        await self._handle_transcript(transcript, is_final=True, source="Mic")

        if self.llm is None or not self.translation_enabled:
            self._log_translation_skipped(
                stage="final",
                runtime=self.self_runtime,
                publish_chatbox=True,
            )
            await self._enqueue_osc(
                buffer.merge_id, transcript_text=final_text, translation_text=None
            )
            return

        reuse_spec = reuse_mode is not None
        commit_delay_ms = 0
        if buffer.start_time is not None:
            commit_delay_ms = int((self.clock.now() - buffer.start_time) * 1000)
        self._emit_metric(
            "[Metric] merge_commit id=%s used_spec=%s parts=%s text_len=%s commit_delay_ms=%s reason=%s",
            str(buffer.merge_id)[:8],
            reuse_spec,
            len(buffer.parts),
            len(final_text),
            commit_delay_ms,
            reason,
        )
        if reuse_spec:
            translation = buffer.spec_translation
            if translation is not None:
                self._promote_spec_latency_to_output(buffer)
                self._emit_metric(
                    "[Metric] spec_reuse id=%s translation_len=%s after_final=%s",
                    str(buffer.merge_id)[:8],
                    len(translation.text),
                    True,
                )
                bundle = self.get_or_create_bundle(buffer.merge_id)
                bundle.with_translation(translation)
                self._emit_translation_ready_for_output(
                    translation=translation,
                    runtime=self.self_runtime,
                )
                if self.translation_service is not None:
                    self.translation_service.remember_context(
                        final_text, self.clock.now(), runtime=self.self_runtime,
                    )
                await self.ui_events.put(
                    UIEvent(
                        type=UIEventType.TRANSLATION_DONE,
                        utterance_id=buffer.merge_id,
                        payload=translation,
                        source=self._get_source(buffer.merge_id),
                    )
                )
                await self._emit_translation_to_overlay(
                    translation=translation,
                    applied_context_mode=None,
                )
                await self._emit_overlay_utterance_closed(
                    utterance_id=buffer.merge_id,
                    channel="self",
                    is_final=True,
                )
                await self._enqueue_osc(
                    buffer.merge_id,
                    transcript_text=final_text,
                    translation_text=translation.text,
                )
                return

        if buffer.spec_translation is not None and reuse_mode is None:
            self._clear_spec_latency_state(buffer)
            self._emit_metric(
                "[Metric] spec_cancel id=%s reason=final_mismatch", str(buffer.merge_id)[:8]
            )

        await self._translate_and_enqueue(buffer.merge_id, final_text)

    # ------------------------------------------------------------------
    # Speculative translation lifecycle
    # ------------------------------------------------------------------

    async def _maybe_restart_spec(self, buffer: _MergeBuffer) -> None:
        if self.llm is None or not self.translation_enabled:
            return

        self._clear_spec_state(buffer, reason="spec_retry")

        merged_text = self._merge_text(buffer.parts)
        if not merged_text:
            return

        buffer.spec_attempts += 1
        buffer.spec_text = merged_text
        buffer.spec_started_at = self.clock.now()
        self._emit_metric(
            "[Metric] spec_start id=%s text_len=%s attempt=%s",
            str(buffer.merge_id)[:8],
            len(merged_text),
            buffer.spec_attempts,
        )
        buffer.spec_task = asyncio.create_task(
            self._run_spec_translation(buffer.merge_id, merged_text, buffer.spec_attempts)
        )

    async def _run_spec_translation(self, merge_id: UUID, text: str, attempt: int) -> None:
        if self.llm is None:
            return
        buffer = self._merge_buffer
        if buffer is None or buffer.merge_id != merge_id:
            return
        if buffer.spec_text != text or buffer.spec_attempts != attempt:
            return
        self._record_spec_latency_stage(buffer, stage="llm_request_start")
        try:
            translation = await self._translate_text(merge_id, text, record_latency=False)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._log_translation_failure(
                stage="spec",
                runtime=self.self_runtime,
                exc=exc,
                detailed=True,
            )
            buffer = self._merge_buffer
            if buffer is None or buffer.merge_id != merge_id:
                return
            if buffer.spec_text != text or buffer.spec_attempts != attempt:
                return
            self._clear_spec_latency_state(buffer)
            buffer.spec_done_at = self.clock.now()
            await self._try_commit_after_spec(buffer, reason="spec_failed", allow_fallback=True)
            return

        buffer = self._merge_buffer
        if buffer is None or buffer.merge_id != merge_id:
            return
        if buffer.spec_text != text or buffer.spec_attempts != attempt:
            return

        self._record_spec_latency_stage(buffer, stage="llm_done")
        buffer.spec_translation = translation
        buffer.spec_done_at = self.clock.now()
        if buffer.spec_started_at is None:
            latency_ms = 0
        else:
            latency_ms = int((self.clock.now() - buffer.spec_started_at) * 1000)
        self._emit_metric(
            "[Metric] spec_done id=%s spec_latency_ms=%s translation_len=%s",
            str(merge_id)[:8],
            latency_ms,
            len(translation.text),
        )
        await self._sync_overlay_active_self(buffer, created_at=translation.created_at)
        await self._try_commit_after_spec(buffer, reason="spec_done", allow_fallback=False)

    async def _try_commit_after_spec(
        self, buffer: _MergeBuffer, *, reason: str, allow_fallback: bool
    ) -> None:
        if self._merge_buffer is None or self._merge_buffer is not buffer:
            return
        if buffer.resume_pending or buffer.resume_confirmed:
            hold_ms = 0
            if buffer.spec_done_at is not None:
                hold_ms = int((self.clock.now() - buffer.spec_done_at) * 1000)
            self._emit_metric(
                "[Metric] commit_blocked id=%s reason=%s hold_ms=%s",
                str(buffer.merge_id)[:8],
                reason,
                hold_ms,
            )
            return
        if buffer.awaiting_vad_end:
            hold_ms = 0
            if buffer.finalize_wait_started_at is not None:
                hold_ms = int((self.clock.now() - buffer.finalize_wait_started_at) * 1000)
            self._emit_metric(
                "[Metric] commit_blocked id=%s reason=await_vad_end hold_ms=%s",
                str(buffer.merge_id)[:8],
                hold_ms,
            )
            return
        if buffer.finalize_wait_task is not None:
            hold_ms = 0
            if buffer.finalize_wait_started_at is not None:
                hold_ms = int((self.clock.now() - buffer.finalize_wait_started_at) * 1000)
            self._emit_metric(
                "[Metric] commit_deferred id=%s reason=post_end_grace hold_ms=%s",
                str(buffer.merge_id)[:8],
                hold_ms,
            )
            return

        final_text = self._merge_text(buffer.parts)
        if not final_text:
            return

        if buffer.spec_translation is None:
            if not allow_fallback:
                return
            await self._commit_merge(buffer, reason=reason)
            return

        if self._soft_reuse_mode(buffer.spec_text, final_text) is None:
            return

        await self._commit_merge(buffer, reason=reason)
