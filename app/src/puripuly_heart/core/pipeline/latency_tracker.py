from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from puripuly_heart.core.clock import Clock
from puripuly_heart.core.pipeline.channel_runtime import ChannelRuntime
from puripuly_heart.core.runtime_logging import (
    LATENCY_CAUSE_E2E_THRESHOLD_MS,
    LATENCY_DOMINANT_STAGE_NORMAL,
    compute_latency_dominant_stage,
    format_basic_latency_summary,
    format_detailed_latency_breakdown,
    format_detailed_latency_trace,
    format_latency_cause_metric,
)
from puripuly_heart.domain.models import ChannelId

__all__ = ["LatencyTracker", "latency_key", "elapsed_latency_ms"]

_LATENCY_TRACE_ORDER = (
    "speech_end",
    "stt_final",
    "llm_request_start",
    "llm_first_chunk",
    "llm_done",
    "self_chatbox_enqueue",
    "peer_overlay_first_emit",
    "peer_overlay_first_render",
)
_LATENCY_SUMMARY_OUTPUT_STAGES = {"self_chatbox_enqueue", "peer_overlay_first_emit"}


@dataclass(slots=True)
class _LatencyTimeline:
    channel: ChannelId
    stage_times: dict[str, float] = field(default_factory=dict)
    emitted_trace_points: set[str] = field(default_factory=set)
    basic_summary_emitted: bool = False


def latency_key(channel: ChannelId, utterance_id: UUID) -> tuple[ChannelId, UUID]:
    return channel, utterance_id


def elapsed_latency_ms(start_at: float | None, end_at: float | None) -> int | None:
    if start_at is None or end_at is None:
        return None
    return max(0, int(round((end_at - start_at) * 1000)))


class LatencyTracker:
    def __init__(
        self,
        *,
        clock: Clock,
        hangover_s: float,
        peer_hangover_s: float,
        emit_basic: Callable[[str], None],
        emit_detailed: Callable[[str], bool],
    ) -> None:
        self._timelines: dict[tuple[ChannelId, UUID], _LatencyTimeline] = {}
        self.clock = clock
        self.hangover_s = hangover_s
        self.peer_hangover_s = peer_hangover_s
        self._emit_basic = emit_basic
        self._emit_detailed = emit_detailed

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _latency_key(channel: ChannelId, utterance_id: UUID) -> tuple[ChannelId, UUID]:
        return channel, utterance_id

    @staticmethod
    def _elapsed_latency_ms(start_at: float | None, end_at: float | None) -> int | None:
        if start_at is None or end_at is None:
            return None
        return max(0, int(round((end_at - start_at) * 1000)))

    def _get_latency_timeline(
        self,
        *,
        channel: ChannelId,
        utterance_id: UUID,
        create: bool = False,
    ) -> _LatencyTimeline | None:
        key = self._latency_key(channel, utterance_id)
        timeline = self._timelines.get(key)
        if timeline is None and create:
            timeline = _LatencyTimeline(channel=channel)
            self._timelines[key] = timeline
        return timeline

    def _latency_hangover_ms(self, channel: ChannelId) -> int:
        hangover_s = self.peer_hangover_s if channel == "peer" else self.hangover_s
        return max(0, int(round(hangover_s * 1000)))

    # -- emission ---------------------------------------------------------

    def _emit_latency_trace_if_ready(
        self,
        *,
        channel: ChannelId,
        utterance_id: UUID,
        stage: str,
    ) -> None:
        timeline = self._get_latency_timeline(channel=channel, utterance_id=utterance_id)
        if timeline is None or stage in timeline.emitted_trace_points:
            return
        speech_end_at = timeline.stage_times.get("speech_end")
        stage_at = timeline.stage_times.get(stage)
        elapsed_ms = self._elapsed_latency_ms(speech_end_at, stage_at)
        if elapsed_ms is None:
            return
        emitted = self._emit_detailed(
            format_detailed_latency_trace(
                channel=channel,
                utterance_id=str(utterance_id)[:8],
                stage=stage,
                elapsed_ms=elapsed_ms,
            )
        )
        if emitted:
            timeline.emitted_trace_points.add(stage)

    def _emit_latency_summary_if_ready(
        self,
        *,
        channel: ChannelId,
        utterance_id: UUID,
        final_output_stage: str,
    ) -> None:
        timeline = self._get_latency_timeline(channel=channel, utterance_id=utterance_id)
        if timeline is None or timeline.basic_summary_emitted:
            return
        speech_end_at = timeline.stage_times.get("speech_end")
        final_output_at = timeline.stage_times.get(final_output_stage)
        measured_speech_end_to_final_output_ms = self._elapsed_latency_ms(
            speech_end_at, final_output_at
        )
        if measured_speech_end_to_final_output_ms is None:
            return
        e2e_ms = measured_speech_end_to_final_output_ms + self._latency_hangover_ms(channel)

        stt_final_at = timeline.stage_times.get("stt_final")
        speech_end_to_stt_final_ms = self._elapsed_latency_ms(speech_end_at, stt_final_at)
        stt_reference_at = None
        if speech_end_at is not None and stt_final_at is not None:
            stt_reference_at = max(speech_end_at, stt_final_at)
        stt_final_to_final_output_ms = self._elapsed_latency_ms(stt_reference_at, final_output_at)

        self._emit_basic(
            format_basic_latency_summary(
                channel=channel,
                e2e_ms=e2e_ms,
            )
        )
        dominant_stage = compute_latency_dominant_stage(
            speech_end_to_stt_final_ms=speech_end_to_stt_final_ms,
            stt_final_to_final_output_ms=stt_final_to_final_output_ms,
        )
        self._emit_detailed(
            format_detailed_latency_breakdown(
                channel=channel,
                e2e_ms=e2e_ms,
                speech_end_to_stt_final_ms=speech_end_to_stt_final_ms,
                stt_final_to_final_output_ms=stt_final_to_final_output_ms,
                dominant_stage=dominant_stage,
            )
        )
        if (
            dominant_stage != LATENCY_DOMINANT_STAGE_NORMAL
            or e2e_ms >= LATENCY_CAUSE_E2E_THRESHOLD_MS
        ):
            self._emit_basic(
                format_latency_cause_metric(
                    channel=channel,
                    e2e_ms=e2e_ms,
                    dominant_stage=dominant_stage,
                    speech_end_to_stt_final_ms=speech_end_to_stt_final_ms,
                    stt_final_to_final_output_ms=stt_final_to_final_output_ms,
                )
            )
        timeline.basic_summary_emitted = True

    def _emit_latency_contract_if_ready(
        self,
        *,
        channel: ChannelId,
        utterance_id: UUID,
    ) -> None:
        for trace_stage in _LATENCY_TRACE_ORDER:
            self._emit_latency_trace_if_ready(
                channel=channel,
                utterance_id=utterance_id,
                stage=trace_stage,
            )
        for output_stage in _LATENCY_SUMMARY_OUTPUT_STAGES:
            self._emit_latency_summary_if_ready(
                channel=channel,
                utterance_id=utterance_id,
                final_output_stage=output_stage,
            )

    # -- recording --------------------------------------------------------

    def _record_latency_stage(
        self,
        *,
        channel: ChannelId,
        utterance_id: UUID,
        stage: str,
        timestamp: float | None = None,
        overwrite: bool = True,
        publish_now: bool = True,
    ) -> None:
        timeline = self._get_latency_timeline(
            channel=channel, utterance_id=utterance_id, create=True
        )
        assert timeline is not None
        if not overwrite and stage in timeline.stage_times:
            return
        timeline.stage_times[stage] = self.clock.now() if timestamp is None else timestamp

        if not publish_now:
            return

        self._emit_latency_contract_if_ready(
            channel=channel,
            utterance_id=utterance_id,
        )

    def _inherit_latency_for_output(
        self,
        *,
        channel: ChannelId,
        output_utterance_id: UUID,
        source_utterance_ids: list[UUID],
    ) -> None:
        output_timeline = self._get_latency_timeline(
            channel=channel,
            utterance_id=output_utterance_id,
            create=True,
        )
        assert output_timeline is not None
        for source_utterance_id in source_utterance_ids:
            source_timeline = self._get_latency_timeline(
                channel=channel,
                utterance_id=source_utterance_id,
            )
            if source_timeline is None:
                continue
            for stage in ("speech_end", "stt_final"):
                source_time = source_timeline.stage_times.get(stage)
                if source_time is None:
                    continue
                existing_time = output_timeline.stage_times.get(stage)
                if existing_time is None:
                    output_timeline.stage_times[stage] = source_time
                else:
                    output_timeline.stage_times[stage] = max(existing_time, source_time)
        self._emit_latency_contract_if_ready(
            channel=channel,
            utterance_id=output_utterance_id,
        )

    # -- clearing ---------------------------------------------------------

    def _clear_latency_timeline(self, *, channel: ChannelId, utterance_id: UUID) -> None:
        self._timelines.pop(self._latency_key(channel, utterance_id), None)

    def _clear_latency_state(self, *, channel: ChannelId | None = None) -> None:
        if channel is None:
            self._timelines.clear()
            return
        keys_to_remove = [key for key in self._timelines if key[0] == channel]
        for key in keys_to_remove:
            self._timelines.pop(key, None)

    def _clear_runtime_latency_bookkeeping(self, *, runtime: ChannelRuntime, utterance_id: UUID) -> None:
        runtime.utterance_start_times.pop(utterance_id, None)
        runtime.speech_ended_ids.discard(utterance_id)

    def _finalize_latency_timeline(self, *, runtime: ChannelRuntime, channel: ChannelId, utterance_id: UUID) -> None:
        self._clear_runtime_latency_bookkeeping(runtime=runtime, utterance_id=utterance_id)
        self._clear_latency_timeline(channel=channel, utterance_id=utterance_id)

    # -- query ------------------------------------------------------------

    def _translation_ready_elapsed_ms(
        self,
        *,
        channel: ChannelId,
        utterance_id: UUID,
    ) -> int | None:
        timeline = self._get_latency_timeline(channel=channel, utterance_id=utterance_id)
        if timeline is None:
            return None
        ready_at = timeline.stage_times.get("llm_done")
        if ready_at is None:
            return None
        return self._elapsed_latency_ms(timeline.stage_times.get("speech_end"), ready_at)
