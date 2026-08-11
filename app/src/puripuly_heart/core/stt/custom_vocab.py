"""Custom vocabulary processing — term normalization and deduplication.

Two paths:
- get_effective_custom_terms(): for cloud STT providers (OpenAI-compatible).
  Returns up to MAX_CUSTOM_VOCAB_TERMS normalized terms.
- get_effective_local_qwen_hotwords(): for local GGUF STT (qwen3-asr).
  Returns up to LOCAL_QWEN_MAX_HOTWORDS terms with comma→space normalization.

Both: filter by source_language (with base-language fallback), deduplicate,
cap at respective limits.

Called by app/wiring.py, ui/provider_signatures.py, ui/controller.py.
"""

from __future__ import annotations

from puripuly_heart.domain.custom_vocab import MAX_CUSTOM_VOCAB_TERMS

# LOCAL_QWEN_MAX_HOTWORDS=12: model-specific limit — exceeding may cause issues on model reload.
LOCAL_QWEN_MAX_HOTWORDS = 12


def _raw_terms_for_language(custom_terms: dict[str, list[str]], source_language: str) -> list[str]:
    if source_language in custom_terms:
        return custom_terms[source_language]
    base_language = source_language.split("-")[0].lower()
    return custom_terms.get(base_language, [])


def get_effective_custom_terms(custom_terms: dict[str, list[str]], custom_vocabulary_enabled: bool, source_language: str) -> list[str]:
    if not custom_vocabulary_enabled:
        return []

    raw_terms = _raw_terms_for_language(custom_terms, source_language)
    effective_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in raw_terms:
        normalized_term = term.strip()
        if not normalized_term or normalized_term in seen_terms:
            continue
        if len(effective_terms) >= MAX_CUSTOM_VOCAB_TERMS:
            break
        seen_terms.add(normalized_term)
        effective_terms.append(normalized_term)
    return effective_terms


def _normalize_local_qwen_hotword(term: str) -> str:
    # Normalization affects runtime signature (provider_signatures.py) —
    # signature change triggers STT backend restart on config reload.
    return " ".join(term.replace(",", " ").split())


def get_effective_local_qwen_hotwords(custom_terms: dict[str, list[str]], custom_vocabulary_enabled: bool, source_language: str) -> list[str]:
    if not custom_vocabulary_enabled:
        return []

    raw_terms = _raw_terms_for_language(custom_terms, source_language)
    effective_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in raw_terms:
        normalized_term = _normalize_local_qwen_hotword(term)
        if not normalized_term or normalized_term in seen_terms:
            continue
        if len(effective_terms) >= LOCAL_QWEN_MAX_HOTWORDS:
            break
        seen_terms.add(normalized_term)
        effective_terms.append(normalized_term)
    return effective_terms
