"""STT hallucination filter — suppresses known local Qwen hallucination transcripts.

Stateless functions that check if a transcript is a known hallucination and
handle suppressed transcripts with notification callbacks.

AI-CONTEXT: Call chain is controller._consume_session_events → should_suppress_final_transcript()
  → is_known_local_qwen_hallucination(). This module owns the SUPPRESS_PROVIDERS gate —
  adding a new local Qwen provider without updating _SUPPRESS_PROVIDERS disables suppression.

AI-CONTEXT: Circular dependency with controller.py resolved:
  FinalTranscriptSuppressedNotification lives in domain/events.py,
  SuppressionCallback Protocol lives in ports/stt.py. Both are top-level imports.
  inspect.isawaitable() guard handles sync callbacks (e.g. DiagnosticsManagerMixin).

AI-CONTEXT: handle_suppressed_final_transcript() has a redundant guard:
  if provider_name not in _SUPPRESS_PROVIDERS: return
  The caller (controller) already checks should_suppress_final_transcript() which gates on
  _SUPPRESS_PROVIDERS. The guard here is defensive — safe to keep, costs nothing.
"""

from __future__ import annotations

import inspect
import logging
from uuid import UUID

from puripuly_heart.core.stt.local_qwen_hallucination import is_known_local_qwen_hallucination
from puripuly_heart.core.stt.stt_log_sink import STTLogSink
from puripuly_heart.domain.events import FinalTranscriptSuppressedNotification
from puripuly_heart.domain.models import ChannelId
from puripuly_heart.domain.providers import STTProviderName
from puripuly_heart.ports.stt import SuppressionCallback

_SUPPRESS_PROVIDERS = frozenset({
    STTProviderName.LOCAL_QWEN,
    STTProviderName.LOCAL_QWEN_17B,
    STTProviderName.LOCAL_QWEN3_ASR_GGUF,
    STTProviderName.LOCAL_QWEN_17B_GGUF,
})


def should_suppress_final_transcript(
    provider_name: STTProviderName | None,
    text: str,
) -> bool:
    """Check whether a final transcript is a known local Qwen hallucination.

    Returns True if the provider is a known hallucinating model AND
    the text matches a known hallucination pattern.
    """
    return (
        provider_name in _SUPPRESS_PROVIDERS
        and is_known_local_qwen_hallucination(text)
    )


async def handle_suppressed_final_transcript(
    *,
    provider_name: STTProviderName | None,
    channel: ChannelId,
    utterance_id: UUID,
    on_callback: SuppressionCallback | None,
    log_sink: STTLogSink,
) -> None:
    """Handle a suppressed final transcript: emit notification callback + log.

    If the provider is not in _SUPPRESS_PROVIDERS, this is a no-op.
    """
    if provider_name not in _SUPPRESS_PROVIDERS:
        return

    notification_status = "not_configured"
    if on_callback is not None:
        notification = FinalTranscriptSuppressedNotification(
            utterance_id=utterance_id,
            channel=channel,
            stt_provider_name=provider_name,
        )
        try:
            maybe_awaitable = on_callback(notification)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception as exc:
            notification_status = "failed"
            log_sink.detailed(
                "[STT][%s][%s] Suppressed-final notification callback failed: %s",
                provider_name.value,
                channel,
                exc,
                level=logging.WARNING,
            )
        else:
            notification_status = "emitted"

    log_sink.basic(
        "[STT][%s][%s] Known hallucination suppressed: utterance_id=%s notification=%s",
        provider_name.value,
        channel,
        str(utterance_id)[:8],
        notification_status,
    )
