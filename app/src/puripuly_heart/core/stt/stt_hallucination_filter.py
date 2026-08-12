"""STT hallucination filter — suppresses known local Qwen hallucination transcripts.

Stateless functions that check if a transcript is a known hallucination and
handle suppressed transcripts with notification callbacks.

AI-CONTEXT: Call chain is controller._consume_session_events → should_suppress_final_transcript()
  → is_known_local_qwen_hallucination(). This module owns the SUPPRESS_PROVIDERS gate —
  adding a new local Qwen provider without updating _SUPPRESS_PROVIDERS disables suppression.

AI-CONTEXT: Circular dependency with controller.py is broken by:
  1. TYPE_CHECKING guard for FinalTranscriptSuppressedNotification (static analysis only)
  2. Lazy import inside handle_suppressed_final_transcript() body (runtime, after all modules loaded)
  This is intentional — do not move the lazy import to top-level or the cycle will break at runtime.

AI-CONTEXT: handle_suppressed_final_transcript() has a redundant guard:
  if provider_name not in _SUPPRESS_PROVIDERS: return
  The caller (controller) already checks should_suppress_final_transcript() which gates on
  _SUPPRESS_PROVIDERS. The guard here is defensive — safe to keep, costs nothing.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Awaitable, Callable
from uuid import UUID

from puripuly_heart.core.stt.local_qwen_hallucination import is_known_local_qwen_hallucination
from puripuly_heart.domain.models import ChannelId
from puripuly_heart.domain.providers import STTProviderName

if TYPE_CHECKING:
    from puripuly_heart.core.stt.controller import FinalTranscriptSuppressedNotification
    from puripuly_heart.core.stt.stt_log_sink import STTLogSink

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
    on_callback: (
        Callable[[FinalTranscriptSuppressedNotification], Awaitable[None] | None] | None
    ),
    log_sink: STTLogSink,
) -> None:
    """Handle a suppressed final transcript: emit notification callback + log.

    If the provider is not in _SUPPRESS_PROVIDERS, this is a no-op.
    """
    if provider_name not in _SUPPRESS_PROVIDERS:
        return

    notification_status = "not_configured"
    if on_callback is not None:
        # Lazy import to avoid circular dependency
        from puripuly_heart.core.stt.controller import FinalTranscriptSuppressedNotification

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
