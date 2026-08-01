from __future__ import annotations

from dataclasses import dataclass


LATENCY_DOMINANT_STAGE_STT_FINALIZATION = "stt_finalization"
LATENCY_DOMINANT_STAGE_POST_STT_OUTPUT = "post_stt_output"
LATENCY_DOMINANT_STAGE_NORMAL = "normal"
LATENCY_DOMINANT_STAGE_THRESHOLD_MS = 1500
LATENCY_CAUSE_E2E_THRESHOLD_MS = 5000


@dataclass(frozen=True, slots=True)
class LatencyTracePointContract:
    name: str
    timing_semantics: str
    acceptance_expectation: str


LATENCY_TRACE_POINT_CONTRACTS: dict[str, LatencyTracePointContract] = {
    "speech_end": LatencyTracePointContract(
        name="speech_end",
        timing_semantics="Shared latency zero boundary recorded when the hub accepts SpeechEnd for the utterance.",
        acceptance_expectation="Record the post-VAD SpeechEnd boundary; published e2e_ms adds the channel-specific VAD hangover for user-facing latency.",
    ),
    "stt_final": LatencyTracePointContract(
        name="stt_final",
        timing_semantics="Recorded when the hub accepts the final STT transcript that will feed the final output path.",
        acceptance_expectation="Emit at most once per output path using the final transcript text that survives to output publication.",
    ),
    "llm_request_start": LatencyTracePointContract(
        name="llm_request_start",
        timing_semantics="Recorded immediately before the hub calls the translation provider for the output path.",
        acceptance_expectation="Use the request that contributes to the published output, not cancelled exploratory retries.",
    ),
    "llm_first_chunk": LatencyTracePointContract(
        name="llm_first_chunk",
        timing_semantics="Recorded when the hub receives the first streaming translation chunk for the output path.",
        acceptance_expectation="Emit only for streaming paths and only on the first chunk that belongs to the published output.",
    ),
    "llm_done": LatencyTracePointContract(
        name="llm_done",
        timing_semantics="Recorded when the hub has the completed translation text ready for publication.",
        acceptance_expectation="Use the completed translation that is about to be published, whether it came from a streaming or non-streaming provider.",
    ),
    "self_chatbox_enqueue": LatencyTracePointContract(
        name="self_chatbox_enqueue",
        timing_semantics="Recorded when the hub enqueues the final self output into ChatboxPaginator.",
        acceptance_expectation="This is the official self Basic latency end boundary because it is the final self output handoff point owned by the hub.",
    ),
    "peer_overlay_first_emit": LatencyTracePointContract(
        name="peer_overlay_first_emit",
        timing_semantics="Recorded at the first peer overlay output emitted by the hub: paired source+translation when translation succeeds, or source-only fallback when translation is unavailable, fails, or is cancelled.",
        acceptance_expectation="Use the first overlay_sink.emit call that carries peer-visible text for that peer logical turn; when translation is enabled and succeeds, wait for the paired source+translation overlay output.",
    ),
    "peer_overlay_first_render": LatencyTracePointContract(
        name="peer_overlay_first_render",
        timing_semantics="Recorded by the local overlay when the first local visible peer source or translation overlay output for the logical turn appears on this client.",
        acceptance_expectation="Emit once per peer logical turn after peer_overlay_first_emit at the first local visible peer source or translation overlay output for that turn; do not wait for lifecycle completion, cleanup, or any hub terminal summary stage.",
    ),
}


def format_basic_latency_summary(
    *,
    channel: str,
    e2e_ms: int,
) -> str:
    parts = [
        f"channel={channel}",
        f"e2e_ms={e2e_ms}",
    ]
    return f"[Basic][Latency] {' '.join(parts)}"


def format_detailed_latency_trace(
    *,
    channel: str,
    utterance_id: str,
    stage: str,
    elapsed_ms: int,
) -> str:
    return (
        f"[Detailed][Latency] channel={channel} utterance_id={utterance_id} "
        f"stage={stage} elapsed_ms={elapsed_ms}"
    )


def format_detailed_latency_breakdown(
    *,
    channel: str,
    e2e_ms: int,
    speech_end_to_stt_final_ms: int | None = None,
    stt_final_to_final_output_ms: int | None = None,
    dominant_stage: str | None = None,
) -> str:
    parts = [
        f"channel={channel}",
        f"e2e_ms={e2e_ms}",
    ]
    if speech_end_to_stt_final_ms is not None:
        parts.append(f"speech_end_to_stt_final_ms={speech_end_to_stt_final_ms}")
    if stt_final_to_final_output_ms is not None:
        parts.append(f"stt_final_to_final_output_ms={stt_final_to_final_output_ms}")
    if dominant_stage is not None:
        parts.append(f"dominant_stage={dominant_stage}")
    return f"[Detailed][LatencyBreakdown] {' '.join(parts)}"


def compute_latency_dominant_stage(
    *,
    speech_end_to_stt_final_ms: int | None,
    stt_final_to_final_output_ms: int | None,
) -> str:
    stt_final_ms = speech_end_to_stt_final_ms if speech_end_to_stt_final_ms is not None else 0
    post_stt_ms = stt_final_to_final_output_ms if stt_final_to_final_output_ms is not None else 0
    if stt_final_ms >= LATENCY_DOMINANT_STAGE_THRESHOLD_MS and stt_final_ms >= post_stt_ms:
        return LATENCY_DOMINANT_STAGE_STT_FINALIZATION
    if post_stt_ms >= LATENCY_DOMINANT_STAGE_THRESHOLD_MS:
        return LATENCY_DOMINANT_STAGE_POST_STT_OUTPUT
    return LATENCY_DOMINANT_STAGE_NORMAL


def format_latency_cause_metric(
    *,
    channel: str,
    e2e_ms: int,
    dominant_stage: str,
    speech_end_to_stt_final_ms: int | None = None,
    stt_final_to_final_output_ms: int | None = None,
) -> str:
    parts = [
        "[Metric] latency_cause",
        f"channel={channel}",
        f"e2e_ms={e2e_ms}",
        f"dominant_stage={dominant_stage}",
    ]
    if speech_end_to_stt_final_ms is not None:
        parts.append(f"speech_end_to_stt_final_ms={speech_end_to_stt_final_ms}")
    if stt_final_to_final_output_ms is not None:
        parts.append(f"stt_final_to_final_output_ms={stt_final_to_final_output_ms}")
    return " ".join(parts)


def format_translation_ready_for_output(
    *,
    channel: str,
    utterance_id: str,
    update_id: str,
    origin_wall_clock_ms: int | None,
    session_scope: str | None,
    source_text_hash: str | None,
    source_text_len: int | None,
    logical_turn_key: str | None,
    translation_len: int,
    elapsed_ms: int | None,
) -> str:
    parts = [
        "[Detailed][Hub] translation_ready_for_output",
        f"channel={channel}",
        f"utterance_id={utterance_id}",
        f"update_id={update_id}",
        f"origin_wall_clock_ms={origin_wall_clock_ms}",
        f"session_scope={session_scope}",
        f"source_text_hash={source_text_hash}",
        f"source_text_len={source_text_len}",
        f"logical_turn_key={logical_turn_key}",
        f"translation_len={translation_len}",
    ]
    if elapsed_ms is not None:
        parts.append(f"elapsed_ms={elapsed_ms}")
    return " ".join(parts)
