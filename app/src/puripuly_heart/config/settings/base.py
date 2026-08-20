from __future__ import annotations

import copy
import json
import locale
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from puripuly_heart.config.audio_host_api import WINDOWS_WASAPI_COMPATIBILITY_HOST_API
from puripuly_heart.config.vad_defaults import DEFAULT_LOW_LATENCY_VAD_HANGOVER_MS
from puripuly_heart.domain.overlay_calibration import OverlayCalibration

from .constants import (
    DEFAULT_CUSTOM_VOCAB_TERMS,
    DEFAULT_DESKTOP_AUDIO_VAD_HANGOVER_MS,
    MAX_CUSTOM_VOCAB_TERMS,
    SETTINGS_SCHEMA_VERSION,
    STT_INTERNAL_SAMPLE_RATE_HZ,
)
from .enums import (
    LLMProviderName,
    STTProviderName,
    TranslationConnection,
    TranslationModel,
    _parse_llm_provider,
    _parse_peer_stt_provider,
    _parse_stt_provider,
    _parse_translation_connection,
    _parse_translation_connection_history,
    _parse_translation_model,
    _supported_translation_connections,
)
from .audio import AudioSettings, DesktopAudioSettings
from .language import LanguageSettings
from .llm import (
    LocalLLMSettings,
    OpenAICompatibleSettings,
    ProviderSettings,
    BackupTranslationSettings,
    _parse_local_llm_backend,
    _parse_local_llm_base_url,
    _parse_local_llm_extra_body,
    _parse_local_llm_model,
    _parse_openai_compatible_settings,
    _parse_backup_translation_settings,
    _normalize_local_llm_data,
)
from .overlay import (
    OverlaySettings,
    _parse_desktop_flet_settings,
    _desktop_flet_settings_to_dict,
    _parse_overlay_target,
)
from .simple import ApiKeyVerificationSettings, LLMSettings, OSCSettings, UiSettings
from .stt import STTSettings, _default_custom_terms
from .translation import (
    TranslationSettings,
    _default_translation_connection_history,
    _default_translation_settings_dict,
    _normalize_translation_settings,
    _translation_data_has_valid_model,
    _translation_settings_is_exact_default,
    _translation_settings_to_dict,
)


@dataclass(slots=True)
class AppSettings:
    settings_version: int = SETTINGS_SCHEMA_VERSION
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    translation: TranslationSettings = field(default_factory=TranslationSettings)
    languages: LanguageSettings = field(default_factory=LanguageSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    desktop_audio: DesktopAudioSettings = field(default_factory=DesktopAudioSettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    stt: STTSettings = field(default_factory=STTSettings)
    local_llm: LocalLLMSettings = field(default_factory=LocalLLMSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    osc: OSCSettings = field(default_factory=OSCSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    api_key_verified: ApiKeyVerificationSettings = field(default_factory=ApiKeyVerificationSettings)
    system_prompt: str = ""
    system_prompts: dict[str, str] = field(default_factory=dict)
    backup_translation: BackupTranslationSettings = field(default_factory=BackupTranslationSettings)

    @property
    def overlay_calibration(self) -> OverlayCalibration:
        return self.overlay.calibration

    @overlay_calibration.setter
    def overlay_calibration(self, value: OverlayCalibration) -> None:
        self.overlay.calibration = value

    def validate(self) -> None:
        if self.settings_version <= 0:
            raise ValueError("settings_version must be > 0")
        self.provider.validate()
        self.translation.validate()
        self.languages.validate()
        self.audio.validate()
        self.desktop_audio.validate()
        self.overlay.validate()
        self.stt.validate()
        self.local_llm.validate()
        self.backup_translation.validate()
        self.llm.validate()
        self.osc.validate()
        self.ui.validate()
        self.api_key_verified.validate()
        for key, value in self.system_prompts.items():
            if not isinstance(key, str):
                raise ValueError("system_prompts keys must be strings")
            if not isinstance(value, str):
                raise ValueError("system_prompts values must be strings")


def _enum_to_value(obj: object) -> object:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _enum_to_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_enum_to_value(v) for v in obj]
    return obj


def _coerce_int(value: object, fallback: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _normalize_quant(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower()
    if normalized in ("", "auto", "none"):
        return ""
    return normalized


def _normalize_internal_sample_rate_hz(value: object) -> int:
    normalized = _coerce_int(value, STT_INTERNAL_SAMPLE_RATE_HZ)
    if normalized == 8000:
        return STT_INTERNAL_SAMPLE_RATE_HZ
    return normalized


def _parse_custom_terms(value: object) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("custom_terms must be a dict[str, list[str]]")

    out: dict[str, list[str]] = {}
    for language, terms in value.items():
        if not isinstance(language, str):
            raise ValueError("custom_terms keys must be strings")
        if not isinstance(terms, list):
            raise ValueError("custom_terms values must be lists of strings")

        normalized_terms: list[str] = []
        seen_terms: set[str] = set()
        for term in terms:
            if not isinstance(term, str):
                raise ValueError("custom_terms values must be lists of strings")
            normalized_term = term.strip()
            if not normalized_term or normalized_term in seen_terms:
                continue
            if len(normalized_terms) >= MAX_CUSTOM_VOCAB_TERMS:
                break
            seen_terms.add(normalized_term)
            normalized_terms.append(normalized_term)

        out[language] = normalized_terms
    return out


def _shared_default_prompt() -> str:
    from puripuly_heart.config.prompts import get_translation_prompt_template

    return get_translation_prompt_template()


def ensure_prompt_defaults(settings: AppSettings) -> AppSettings:
    system_prompt_empty = not settings.system_prompt.strip()
    if system_prompt_empty:
        prompt = _shared_default_prompt()
        settings.system_prompt = prompt
    settings.system_prompts = {}
    return settings


def detect_system_locale() -> str | None:
    try:
        return locale.getlocale()[0]
    except (ValueError, locale.Error):
        return None


def _normalize_first_run_locale(system_locale: str | None) -> str:
    if system_locale is None:
        return ""
    normalized = system_locale.strip()
    if not normalized:
        return ""
    normalized = normalized.split(".", maxsplit=1)[0]
    normalized = normalized.split("@", maxsplit=1)[0]
    return normalized.replace("_", "-").casefold()


def resolve_first_run_ui_locale(system_locale: str | None) -> str:
    normalized = _normalize_first_run_locale(system_locale)
    _LOCALE_MAP: dict[str, str] = {
        "ar": "ar", "arabic": "ar",
        "bg": "bg", "bulgarian": "bg",
        "ca": "ca", "catalan": "ca", "valencian": "ca",
        "cs": "cs", "czech": "cs",
        "da": "da", "danish": "da",
        "de": "de", "german": "de",
        "el": "el", "greek": "el",
        "es": "es", "spanish": "es", "castilian": "es",
        "et": "et", "estonian": "et",
        "fi": "fi", "finnish": "fi",
        "fr": "fr", "french": "fr",
        "hi": "hi", "hindi": "hi",
        "hu": "hu", "hungarian": "hu",
        "id": "id", "indonesian": "id",
        "it": "it", "italian": "it",
        "ja": "ja", "japanese": "ja",
        "ko": "ko", "korean": "ko",
        "lt": "lt", "lithuanian": "lt",
        "lv": "lv", "latvian": "lv",
        "ms": "ms", "malay": "ms",
        "nl": "nl", "dutch": "nl",
        "no": "no", "norwegian": "no", "nb": "no", "nn": "no",
        "pl": "pl", "polish": "pl",
        "pt": "pt", "portuguese": "pt",
        "ro": "ro", "romanian": "ro",
        "ru": "ru", "russian": "ru",
        "sk": "sk", "slovak": "sk",
        "sv": "sv", "swedish": "sv",
        "th": "th", "thai": "th",
        "tr": "tr", "turkish": "tr",
        "uk": "uk", "ukrainian": "uk",
        "vi": "vi", "vietnamese": "vi",
    }
    base = normalized.split("-")[0].split("_")[0]
    if normalized.startswith("zh-tw") or normalized.startswith("zh-hant"):
        return "zh-TW"
    if base in _LOCALE_MAP:
        return _LOCALE_MAP[base]
    if normalized.startswith("zh"):
        return "zh-CN"
    for name, code in _LOCALE_MAP.items():
        if normalized.startswith(name):
            return code
    return "en"


def new_settings_for_first_run(system_locale: str | None = None) -> AppSettings:
    if system_locale is None:
        system_locale = detect_system_locale()
    settings = AppSettings()
    settings.ui.locale = resolve_first_run_ui_locale(system_locale)
    ensure_prompt_defaults(settings)
    settings.validate()
    return settings


def _derive_translation_settings_from_runtime_values(
    *, provider_llm: LLMProviderName, history: object = None,
) -> TranslationSettings:
    normalized_history = _parse_translation_connection_history(history)
    if provider_llm == LLMProviderName.LOCAL_LLM:
        return _normalize_translation_settings(
            model=TranslationModel.LOCAL_LLM, connection=TranslationConnection.LOCAL,
            history=normalized_history,
        )
    return _normalize_translation_settings(
        model=TranslationModel.OPENAI_COMPATIBLE, connection=TranslationConnection.OPENAI_COMPATIBLE,
        history=normalized_history,
    )


def _derive_translation_settings_from_runtime(
    settings: AppSettings,
    history: object = None,
) -> TranslationSettings:
    return _derive_translation_settings_from_runtime_values(
        provider_llm=settings.provider.llm,
        history=history,
    )


def materialize_translation_settings(settings: AppSettings) -> AppSettings:
    settings.translation = _normalize_translation_settings(
        model=_parse_translation_model(settings.translation.model),
        connection=_parse_translation_connection(settings.translation.connection),
        history=settings.translation.connection_history,
    )
    model = settings.translation.model
    if model == TranslationModel.LOCAL_LLM:
        settings.provider.llm = LLMProviderName.LOCAL_LLM
        return settings
    if model == TranslationModel.OPENAI_COMPATIBLE:
        settings.provider.llm = LLMProviderName.OPENAI_COMPATIBLE
        return settings
    settings.provider.llm = LLMProviderName.OPENAI_COMPATIBLE
    return settings


def _ensure_mapping_block(data: dict[str, Any], key: str) -> tuple[dict[str, Any], bool]:
    block = data.get(key)
    if isinstance(block, dict):
        return block, False
    block = {}
    data[key] = block
    return block, True


def _set_mapping_value(mapping: dict[str, Any], key: str, value: object) -> bool:
    if mapping.get(key) == value:
        return False
    mapping[key] = value
    return True


def _apply_materialized_translation_to_data(
    data: dict[str, Any], translation: TranslationSettings,
) -> bool:
    provider_data, changed = _ensure_mapping_block(data, "provider")
    translation = _normalize_translation_settings(
        model=_parse_translation_model(translation.model),
        connection=_parse_translation_connection(translation.connection),
        history=translation.connection_history,
    )
    if translation.model == TranslationModel.LOCAL_LLM:
        changed |= _set_mapping_value(provider_data, "llm", LLMProviderName.LOCAL_LLM.value)
        return changed
    changed |= _set_mapping_value(provider_data, "llm", LLMProviderName.OPENAI_COMPATIBLE.value)
    return changed


def to_dict(settings: AppSettings) -> dict[str, Any]:
    settings = copy.deepcopy(settings)
    if _translation_settings_is_exact_default(settings.translation):
        inferred_translation = _derive_translation_settings_from_runtime(
            settings,
            history=settings.translation.connection_history,
        )
        if not _translation_settings_is_exact_default(inferred_translation):
            settings.translation = inferred_translation
    materialize_translation_settings(settings)

    data: dict[str, Any] = {
        "settings_version": settings.settings_version,
        "provider": {
            "stt": settings.provider.stt.value,
            "peer_stt": _parse_peer_stt_provider(settings.provider.peer_stt.value).value,
            "stt_compute": settings.provider.stt_compute,
            "peer_stt_compute": settings.provider.peer_stt_compute,
            "stt_backend": settings.provider.stt_backend,
            "peer_stt_backend": settings.provider.peer_stt_backend,
            "stt_quant": settings.provider.stt_quant,
            "peer_stt_quant": settings.provider.peer_stt_quant,
            "llm": settings.provider.llm.value,
        },
        "translation": _translation_settings_to_dict(settings.translation),
        "languages": {
            "source_language": settings.languages.source_language,
            "target_language": settings.languages.target_language,
            "second_target_language": settings.languages.second_target_language,
            "peer_source_language": settings.languages.peer_source_language,
            "peer_target_language": settings.languages.peer_target_language,
            "recent_source_languages": settings.languages.recent_source_languages,
            "recent_target_languages": settings.languages.recent_target_languages,
        },
        "audio": {
            "internal_sample_rate_hz": settings.audio.internal_sample_rate_hz,
            "internal_channels": settings.audio.internal_channels,
            "ring_buffer_ms": settings.audio.ring_buffer_ms,
            "input_host_api": settings.audio.input_host_api,
            "input_device": settings.audio.input_device,
        },
        "desktop_audio": {
            "output_device": settings.desktop_audio.output_device,
            "vad_speech_threshold": settings.desktop_audio.vad_speech_threshold,
            "vad_hangover_ms": settings.desktop_audio.vad_hangover_ms,
            "vad_pre_roll_ms": settings.desktop_audio.vad_pre_roll_ms,
        },
        "overlay": {
            "target": _parse_overlay_target(settings.overlay.target),
            "show_translation": settings.overlay.show_translation,
            "show_peer_original": settings.overlay.show_peer_original,
            "calibration": settings.overlay.calibration.to_dict(),
            "desktop_flet": _desktop_flet_settings_to_dict(settings.overlay.desktop_flet),
        },
        "stt": {
            "drain_timeout_s": settings.stt.drain_timeout_s,
            "vad_speech_threshold": settings.stt.vad_speech_threshold,
            "low_latency_mode": settings.stt.low_latency_mode,
            "low_latency_vad_hangover_ms": settings.stt.low_latency_vad_hangover_ms,
            "low_latency_spec_retry_max": settings.stt.low_latency_spec_retry_max,
            "custom_vocabulary_enabled": settings.stt.custom_vocabulary_enabled,
            "custom_terms": _parse_custom_terms(settings.stt.custom_terms),
        },
        "local_llm": {
            "backend": settings.local_llm.backend.value,
            "base_url": _parse_local_llm_base_url(settings.local_llm.base_url),
            "model": _parse_local_llm_model(settings.local_llm.model),
            "extra_body": _parse_local_llm_extra_body(settings.local_llm.extra_body),
        },
        "openai_compatible": {
            "base_url": settings.provider.openai_compatible.base_url,
            "model": settings.provider.openai_compatible.model,
        },
        "backup_translation": {
            "enabled": settings.backup_translation.enabled,
            "mode": settings.backup_translation.mode.value,
            "openai_compatible": {
                "base_url": settings.backup_translation.openai_compatible.base_url,
                "model": settings.backup_translation.openai_compatible.model,
            },
            "local_llm": {
                "base_url": _parse_local_llm_base_url(settings.backup_translation.local_llm.base_url),
                "model": _parse_local_llm_model(settings.backup_translation.local_llm.model),
                "extra_body": _parse_local_llm_extra_body(settings.backup_translation.local_llm.extra_body),
            },
        },
        "llm": {"concurrency_limit": settings.llm.concurrency_limit},
        "osc": {
            "host": settings.osc.host,
            "port": settings.osc.port,
            "chatbox_address": settings.osc.chatbox_address,
            "chatbox_send": settings.osc.chatbox_send,
            "chatbox_clear": settings.osc.chatbox_clear,
            "chatbox_max_chars": settings.osc.chatbox_max_chars,
            "vrc_mic_intercept": settings.osc.vrc_mic_intercept,
            "chatbox_include_source": settings.osc.chatbox_include_source,
        },
        "ui": {
            "locale": settings.ui.locale,
            "peer_translation_eula_accepted": settings.ui.peer_translation_eula_accepted,
            "integrated_context_enabled": settings.ui.integrated_context_enabled,
            "integrated_context_bootstrapped": settings.ui.integrated_context_bootstrapped,
            "clipboard_auto_translate_enabled": settings.ui.clipboard_auto_translate_enabled,
        },
        "api_key_verified": dict(settings.api_key_verified.verified),
        "system_prompt": settings.system_prompt,
    }
    return _enum_to_value(data)  # type: ignore[return-value]


# Forward-only migration pipeline: normalizes raw JSON from any prior
# schema version to the current one. Migrates provider.local_llm nested
# structure (v0) → flat stt/translation fields (v1), fills missing keys
# with defaults. Returns (migrated_dict, was_changed).


def _migrate_settings_dict(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    data: dict[str, Any] = copy.deepcopy(raw)
    changed = False
    if _normalize_local_llm_data(data): changed = True
    stt = data.get("stt")
    if not isinstance(stt, dict): stt = {}; data["stt"] = stt; changed = True
    if "custom_terms" not in stt: stt["custom_terms"] = _default_custom_terms(); changed = True
    if "custom_vocabulary_enabled" not in stt:
        stt["custom_vocabulary_enabled"] = any(bool(t) for t in _parse_custom_terms(stt.get("custom_terms")).values()); changed = True
    rpd = data.get("provider"); pd = rpd if isinstance(rpd, dict) else {}
    if rpd is None: pd = {}; data["provider"] = pd; changed = True
    elif not isinstance(rpd, dict): pd = {"stt": STTProviderName.NONE.value, "llm": LLMProviderName.OPENAI_COMPATIBLE.value}; data["provider"] = pd; changed = True
    if isinstance(pd, dict) and "stt" in pd:
        rs = pd.get("stt"); ns = _parse_stt_provider(str(rs)).value
        if rs != ns: pd["stt"] = ns; changed = True
    if isinstance(pd, dict) and "peer_stt" not in pd: pd["peer_stt"] = STTProviderName.NONE.value; changed = True
    if isinstance(pd, dict) and "peer_stt" in pd:
        rp = pd.get("peer_stt"); np_ = _parse_peer_stt_provider(str(rp)).value
        if rp != np_: pd["peer_stt"] = np_; changed = True
    td = data.get("translation") if isinstance(data.get("translation"), dict) else {}
    th = _parse_translation_connection_history(td.get("connection_history") if isinstance(td, dict) else None)
    if _translation_data_has_valid_model(td):
        nts = _normalize_translation_settings(model=_parse_translation_model(td.get("model")), connection=_parse_translation_connection(td.get("connection")), history=th)
    else:
        nts = _derive_translation_settings_from_runtime_values(provider_llm=_parse_llm_provider(pd.get("llm", LLMProviderName.OPENAI_COMPATIBLE.value)), history=th)
    ntd = _translation_settings_to_dict(nts)
    if data.get("translation") != ntd: data["translation"] = ntd; changed = True
    if _apply_materialized_translation_to_data(data, nts): changed = True
    av = data.get("api_key_verified")
    if not isinstance(av, dict): av = {}; data["api_key_verified"] = av; changed = True
    for _k in ("openai_compatible", "local_llm", "backup_openai_compatible", "fallback_local_llm"):
        if _k not in av: av[_k] = False; changed = True
    od = data.get("overlay")
    if not isinstance(od, dict): od = {}; data["overlay"] = od; changed = True
    ocd = od.get("calibration") if isinstance(od.get("calibration"), dict) else {}
    lcd = data.get("overlay_calibration") if isinstance(data.get("overlay_calibration"), dict) else {}
    nc = OverlayCalibration().to_dict(); nc.update(lcd); nc.update(ocd)
    if od.get("calibration") != nc: od["calibration"] = nc; changed = True
    nt = _parse_overlay_target(od.get("target"))
    if od.get("target") != nt: od["target"] = nt; changed = True
    nf = _desktop_flet_settings_to_dict(_parse_desktop_flet_settings(od.get("desktop_flet")))
    if od.get("desktop_flet") != nf: od["desktop_flet"] = nf; changed = True
    ui = data.get("ui")
    if not isinstance(ui, dict): ui = {}; data["ui"] = ui; changed = True
    for k in ["show_overlay_translation", "show_overlay_peer_original", "overlay_enabled", "peer_translation_enabled"]:
        if k in ui: del ui[k]; changed = True
    if "overlay_calibration" in data: del data["overlay_calibration"]; changed = True
    if "system_prompts" in data: data.pop("system_prompts", None); changed = True
    if data.get("settings_version") != SETTINGS_SCHEMA_VERSION: data["settings_version"] = SETTINGS_SCHEMA_VERSION; changed = True
    return data, changed


def from_dict(data: dict[str, Any]) -> AppSettings:
    audio_data = data.get("audio") or {}
    desktop_audio_data = data.get("desktop_audio") or {}
    overlay_data = data.get("overlay") if isinstance(data.get("overlay"), dict) else {}
    legacy_overlay_calibration_data = (
        data.get("overlay_calibration") if isinstance(data.get("overlay_calibration"), dict) else {}
    )
    overlay_calibration_data = (
        overlay_data.get("calibration") if isinstance(overlay_data.get("calibration"), dict) else {}
    )
    merged_overlay_calibration_data = OverlayCalibration().to_dict()
    merged_overlay_calibration_data.update(legacy_overlay_calibration_data)
    merged_overlay_calibration_data.update(overlay_calibration_data)
    stt_data = data.get("stt") or {}
    ui_data = data.get("ui") or {}
    raw_provider_data = data.get("provider")
    provider_data = raw_provider_data if isinstance(raw_provider_data, dict) else {}
    if raw_provider_data is None:
        stt_provider_value = STTProviderName.NONE.value
    elif isinstance(raw_provider_data, dict):
        stt_provider_value = provider_data.get("stt", STTProviderName.NONE.value)
    else:
        stt_provider_value = STTProviderName.NONE.value
    raw_peer_provider = (
        provider_data.get("peer_stt", STTProviderName.NONE.value)
        if isinstance(raw_provider_data, dict)
        else STTProviderName.NONE.value
    )

    input_host_api_raw = (
        audio_data["input_host_api"]
        if "input_host_api" in audio_data
        else WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    )
    input_device_raw = audio_data.get("input_device")
    vad_threshold_raw = stt_data.get("vad_speech_threshold")
    legacy_system_prompt = str(data.get("system_prompt", ""))
    settings_version = _coerce_int(data.get("settings_version"), SETTINGS_SCHEMA_VERSION)
    parsed_custom_terms = _parse_custom_terms(stt_data.get("custom_terms", _default_custom_terms()))
    if "custom_vocabulary_enabled" in stt_data:
        custom_vocabulary_enabled = bool(stt_data.get("custom_vocabulary_enabled"))
    else:
        custom_vocabulary_enabled = any(bool(terms) for terms in parsed_custom_terms.values())

    local_llm_raw = data.get("local_llm") if isinstance(data.get("local_llm"), dict) else {}

    settings = AppSettings(
        settings_version=settings_version,
        provider=ProviderSettings(
            stt=_parse_stt_provider(str(stt_provider_value)),
            peer_stt=_parse_peer_stt_provider(str(raw_peer_provider)),
            stt_compute=str(provider_data.get("stt_compute", "gpu")),
            peer_stt_compute=str(provider_data.get("peer_stt_compute", "gpu")),
            stt_backend=str(provider_data.get("stt_backend", "onnx")),
            peer_stt_backend=str(provider_data.get("peer_stt_backend", "onnx")),
            stt_quant=_normalize_quant(provider_data.get("stt_quant")),
            peer_stt_quant=_normalize_quant(provider_data.get("peer_stt_quant")),
            llm=_parse_llm_provider(provider_data.get("llm", LLMProviderName.OPENAI_COMPATIBLE.value)),
            openai_compatible=_parse_openai_compatible_settings(
                data.get("openai_compatible") if isinstance(data.get("openai_compatible"), dict) else {}
            ),
        ),
        languages=LanguageSettings(
            source_language=data.get("languages", {}).get("source_language", "ko"),
            target_language=data.get("languages", {}).get("target_language", "en"),
            second_target_language=str(data.get("languages", {}).get("second_target_language", "")),
            peer_source_language=str(data.get("languages", {}).get("peer_source_language", "")),
            peer_target_language=str(data.get("languages", {}).get("peer_target_language", "")),
            recent_source_languages=list(
                dict.fromkeys(
                    list(data.get("languages", {}).get("recent_source_languages") or [])
                    + ["ko", "en", "zh-CN", "ja", "es", "fr"]
                )
            )[:6],
            recent_target_languages=list(
                dict.fromkeys(
                    list(data.get("languages", {}).get("recent_target_languages") or [])
                    + ["ko", "en", "zh-CN", "ja", "es", "fr"]
                )
            )[:6],
        ),
        audio=AudioSettings(
            internal_sample_rate_hz=_normalize_internal_sample_rate_hz(
                audio_data.get("internal_sample_rate_hz", STT_INTERNAL_SAMPLE_RATE_HZ)
            ),
            internal_channels=int(audio_data.get("internal_channels", 1)),
            ring_buffer_ms=int(audio_data.get("ring_buffer_ms", 500)),
            input_host_api=str(input_host_api_raw) if input_host_api_raw is not None else "",
            input_device=str(input_device_raw) if input_device_raw is not None else "",
        ),
        desktop_audio=DesktopAudioSettings(
            output_device=(
                str(desktop_audio_data.get("output_device"))
                if desktop_audio_data.get("output_device") is not None
                else ""
            ),
            vad_speech_threshold=float(desktop_audio_data.get("vad_speech_threshold", 0.4)),
            vad_hangover_ms=int(
                desktop_audio_data.get("vad_hangover_ms", DEFAULT_DESKTOP_AUDIO_VAD_HANGOVER_MS)
            ),
            vad_pre_roll_ms=int(desktop_audio_data.get("vad_pre_roll_ms", 500)),
        ),
        overlay=OverlaySettings(
            target=_parse_overlay_target(overlay_data.get("target")),
            show_translation=bool(
                overlay_data.get("show_translation", ui_data.get("show_overlay_translation", True))
            ),
            show_peer_original=bool(
                overlay_data.get(
                    "show_peer_original", ui_data.get("show_overlay_peer_original", True)
                )
            ),
            calibration=OverlayCalibration(
                anchor=str(
                    merged_overlay_calibration_data.get(
                        "anchor",
                        OverlayCalibration().anchor,
                    )
                ),
                offset_x=float(
                    merged_overlay_calibration_data.get(
                        "offset_x",
                        OverlayCalibration().offset_x,
                    )
                ),
                offset_y=float(
                    merged_overlay_calibration_data.get(
                        "offset_y",
                        OverlayCalibration().offset_y,
                    )
                ),
                distance=float(
                    merged_overlay_calibration_data.get(
                        "distance",
                        OverlayCalibration().distance,
                    )
                ),
                text_scale=float(
                    merged_overlay_calibration_data.get(
                        "text_scale",
                        OverlayCalibration().text_scale,
                    )
                ),
                background_alpha=float(
                    merged_overlay_calibration_data.get(
                        "background_alpha",
                        OverlayCalibration().background_alpha,
                    )
                ),
            ),
            desktop_flet=_parse_desktop_flet_settings(overlay_data.get("desktop_flet")),
        ),
        stt=STTSettings(
            drain_timeout_s=float(stt_data.get("drain_timeout_s", 2.0)),
            vad_speech_threshold=float(vad_threshold_raw) if vad_threshold_raw is not None else 0.5,
            low_latency_mode=bool(stt_data.get("low_latency_mode", False)),
            low_latency_vad_hangover_ms=int(
                stt_data.get(
                    "low_latency_vad_hangover_ms",
                    DEFAULT_LOW_LATENCY_VAD_HANGOVER_MS,
                )
            ),
            low_latency_spec_retry_max=int(stt_data.get("low_latency_spec_retry_max", 10)),
            custom_vocabulary_enabled=custom_vocabulary_enabled,
            custom_terms=parsed_custom_terms,
        ),
        local_llm=LocalLLMSettings(
            backend=_parse_local_llm_backend(local_llm_raw.get("backend")),
            base_url=_parse_local_llm_base_url(local_llm_raw.get("base_url")),
            model=_parse_local_llm_model(local_llm_raw.get("model")),
            extra_body=_parse_local_llm_extra_body(local_llm_raw.get("extra_body")),
        ),
        llm=LLMSettings(concurrency_limit=int(data.get("llm", {}).get("concurrency_limit", 5))),
        osc=OSCSettings(
            host=str(data.get("osc", {}).get("host", "127.0.0.1")),
            port=int(data.get("osc", {}).get("port", 9000)),
            chatbox_address=str(data.get("osc", {}).get("chatbox_address", "/chatbox/input")),
            chatbox_send=bool(data.get("osc", {}).get("chatbox_send", True)),
            chatbox_clear=bool(data.get("osc", {}).get("chatbox_clear", False)),
            chatbox_max_chars=int(data.get("osc", {}).get("chatbox_max_chars", 144)),
            vrc_mic_intercept=bool(data.get("osc", {}).get("vrc_mic_intercept", False)),
            chatbox_include_source=bool(data.get("osc", {}).get("chatbox_include_source", False)),
        ),
        ui=UiSettings(
            locale=str(ui_data.get("locale", "en")),
            overlay_enabled=False,
            peer_translation_enabled=False,
            peer_translation_eula_accepted=bool(
                ui_data.get("peer_translation_eula_accepted", False)
            ),
            integrated_context_enabled=bool(ui_data.get("integrated_context_enabled", True)),
            integrated_context_bootstrapped=bool(
                ui_data.get("integrated_context_bootstrapped", False)
            ),
            clipboard_auto_translate_enabled=bool(
                ui_data.get("clipboard_auto_translate_enabled", False)
            ),
        ),
        api_key_verified=ApiKeyVerificationSettings(
            verified=dict(data.get("api_key_verified", {})),
        ),
        system_prompt=legacy_system_prompt,
        system_prompts={},
        backup_translation=_parse_backup_translation_settings(
            data.get("backup_translation") if isinstance(data.get("backup_translation"), dict) else {}
        ),
    )

    translation_data = data.get("translation") if isinstance(data.get("translation"), dict) else {}
    translation_history = _parse_translation_connection_history(
        translation_data.get("connection_history") if isinstance(translation_data, dict) else None
    )
    if _translation_data_has_valid_model(translation_data):
        settings.translation = _normalize_translation_settings(
            model=_parse_translation_model(translation_data.get("model")),
            connection=_parse_translation_connection(translation_data.get("connection")),
            history=translation_history,
        )
    else:
        settings.translation = _derive_translation_settings_from_runtime(
            settings,
            history=translation_history,
        )
    materialize_translation_settings(settings)

    ensure_prompt_defaults(settings)
    settings.validate()
    return settings
