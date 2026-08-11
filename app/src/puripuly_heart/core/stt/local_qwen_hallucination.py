"""Known hallucinated strings emitted by local Qwen STT models.

is_known_local_qwen_hallucination() is called by stt/controller.py
_should_suppress_final_transcript() to filter out false transcripts.
The provider gate there must list all LOCAL_QWEN variants — adding a new
local Qwen provider without updating the gate disables suppression.

KNOWN_LOCAL_QWEN_HALLUCINATIONS is module-private (not consumed externally).
"""

from __future__ import annotations

# Empirically observed hallucinated tokens from qwen3-asr-0.6b GGUF model.
KNOWN_LOCAL_QWEN_HALLUCINATIONS = frozenset({"leşme", "acia"})


def is_known_local_qwen_hallucination(text: str) -> bool:
    return text.strip() in KNOWN_LOCAL_QWEN_HALLUCINATIONS


__all__ = ["is_known_local_qwen_hallucination"]
