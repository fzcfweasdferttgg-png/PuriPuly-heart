"""Unified language mapper for UI, STT, and LLM.

Provides consistent language codes and names across:
- LLM prompts (OpenAI-compatible, Local LLM)
- UI display
- Local STT providers (Parakeet, GigaAM, Qwen)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class LanguageInfo:
    """Language information for mapping."""

    code: str  # ISO 639-1 code: "ko", "en", etc.
    name: str  # English name: "Korean", "English"


@dataclass(frozen=True, slots=True)
class SttCompatibilityWarning:
    key: str
    language_code: str


# Supported languages for UI
SUPPORTED_LANGUAGES: dict[str, LanguageInfo] = {
    "ar": LanguageInfo(code="ar", name="Arabic"),
    "bg": LanguageInfo(code="bg", name="Bulgarian"),
    "ca": LanguageInfo(code="ca", name="Catalan"),
    "cs": LanguageInfo(code="cs", name="Czech"),
    "da": LanguageInfo(code="da", name="Danish"),
    "de": LanguageInfo(code="de", name="German"),
    "el": LanguageInfo(code="el", name="Greek"),
    "en": LanguageInfo(code="en", name="English"),
    "es": LanguageInfo(code="es", name="Spanish"),
    "et": LanguageInfo(code="et", name="Estonian"),
    "fi": LanguageInfo(code="fi", name="Finnish"),
    "fr": LanguageInfo(code="fr", name="French"),
    "hi": LanguageInfo(code="hi", name="Hindi"),
    "hu": LanguageInfo(code="hu", name="Hungarian"),
    "id": LanguageInfo(code="id", name="Indonesian"),
    "it": LanguageInfo(code="it", name="Italian"),
    "ja": LanguageInfo(code="ja", name="Japanese"),
    "ko": LanguageInfo(code="ko", name="Korean"),
    "lt": LanguageInfo(code="lt", name="Lithuanian"),
    "lv": LanguageInfo(code="lv", name="Latvian"),
    "ms": LanguageInfo(code="ms", name="Malay"),
    "nl": LanguageInfo(code="nl", name="Dutch"),
    "no": LanguageInfo(code="no", name="Norwegian"),
    "pl": LanguageInfo(code="pl", name="Polish"),
    "pt": LanguageInfo(code="pt", name="Portuguese"),
    "ro": LanguageInfo(code="ro", name="Romanian"),
    "ru": LanguageInfo(code="ru", name="Russian"),
    "sk": LanguageInfo(code="sk", name="Slovak"),
    "sv": LanguageInfo(code="sv", name="Swedish"),
    "th": LanguageInfo(code="th", name="Thai"),
    "tr": LanguageInfo(code="tr", name="Turkish"),
    "uk": LanguageInfo(code="uk", name="Ukrainian"),
    "vi": LanguageInfo(code="vi", name="Vietnamese"),
    "zh-CN": LanguageInfo(code="zh-CN", name="Chinese (Simplified)"),
    "zh-TW": LanguageInfo(code="zh-TW", name="Chinese (Traditional)"),
}


def get_language_info(code: str) -> LanguageInfo | None:
    """Get language info by code. Returns None if not supported."""
    # 1. Try exact match (e.g. "zh-CN", "zh-TW")
    if code in SUPPORTED_LANGUAGES:
        return SUPPORTED_LANGUAGES[code]

    # 2. Normalize: strip regional suffix (e.g., "ko-KR" -> "ko")
    normalized = code.split("-")[0].lower()
    return SUPPORTED_LANGUAGES.get(normalized)


def get_llm_language_name(code: str) -> str:
    """Get human-readable language name for LLM prompts. Falls back to 'English'."""
    info = get_language_info(code)
    return info.name if info else "English"


_LOCAL_QWEN_LANGUAGE_HINT_MAP: dict[str, str] = {
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "zh-CN": "Chinese",
    "zh-TW": "Chinese",
}


def get_local_qwen_language_hint(code: str) -> str | None:
    """Get a conservative human-readable language hint for local Qwen STT."""
    normalized = code.strip()
    if normalized in _LOCAL_QWEN_LANGUAGE_HINT_MAP:
        return _LOCAL_QWEN_LANGUAGE_HINT_MAP[normalized]
    base_code = normalized.split("-")[0].lower()
    return _LOCAL_QWEN_LANGUAGE_HINT_MAP.get(base_code)


def get_all_language_options() -> Sequence[tuple[str, str]]:
    """Get all supported languages as (code, name) tuples for UI dropdowns.

    Returns sorted list by English name.
    """
    return tuple(
        sorted(
            ((info.code, info.name) for info in SUPPORTED_LANGUAGES.values()), key=lambda x: x[1]
        )
    )


def get_stt_compatibility_warning(code: str, stt_provider: str) -> SttCompatibilityWarning | None:
    """Return a warning key if the language is not supported by the STT provider."""
    lang_info = get_language_info(code)
    lang_code = lang_info.code if lang_info else code

    _PARAKEET_TDT_LANGS = {
        "en", "es", "fr", "de", "bg", "hr", "cs", "da", "nl", "et", "fi", "el",
        "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "sv", "ru", "uk",
    }
    if stt_provider == "local_parakeet_tdt" and lang_code not in _PARAKEET_TDT_LANGS:
        return SttCompatibilityWarning("flet.warning.parakeet_tdt_not_supported", lang_code)

    if stt_provider in ("local_gigaam_rnnt", "local_gigaam_rnnt_gguf") and lang_code != "ru":
        return SttCompatibilityWarning("flet.warning.gigaam_not_supported", lang_code)

    if stt_provider == "local_parakeet_tdt_gguf" and lang_code not in _PARAKEET_TDT_LANGS:
        return SttCompatibilityWarning("flet.warning.parakeet_tdt_not_supported", lang_code)

    if stt_provider == "local_qwen3_asr_gguf":
        return None

    if stt_provider == "local_qwen_17b_gguf":
        return None

    return None
