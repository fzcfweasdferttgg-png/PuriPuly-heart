"""Known hallucinated strings emitted by local Qwen STT models.

is_known_local_qwen_hallucination() is called by stt_hallucination_filter.py
should_suppress_final_transcript() to filter out false transcripts.
The provider gate in stt_hallucination_filter._SUPPRESS_PROVIDERS must list
all LOCAL_QWEN variants — adding a new local Qwen provider without updating
the gate disables suppression.

AI-CONTEXT: This is a leaf node in the STT import graph — no imports from any STT module.
  Safe to import from anywhere. Only consumer: stt_hallucination_filter.py.

AI-CONTEXT: KNOWN_LOCAL_QWEN_HALLUCINATIONS is an empirical set observed from qwen3-asr-0.6b.
  New hallucinations should be added here after validation. The frozenset is module-private —
  external code uses only is_known_local_qwen_hallucination().

KNOWN_LOCAL_QWEN_HALLUCINATIONS is module-private (not consumed externally).
"""

from __future__ import annotations

# Empirically observed hallucinated tokens from qwen3-asr-0.6b GGUF model.
KNOWN_LOCAL_QWEN_HALLUCINATIONS = frozenset({"leşme", "acia"})


def is_known_local_qwen_hallucination(text: str) -> bool:
    return text.strip() in KNOWN_LOCAL_QWEN_HALLUCINATIONS


__all__ = ["is_known_local_qwen_hallucination"]