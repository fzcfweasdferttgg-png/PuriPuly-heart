from __future__ import annotations

import copy
import json
import locale
import math
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from puripuly_heart.config.audio_host_api import (
    WINDOWS_WASAPI_COMPATIBILITY_HOST_API,
)
from puripuly_heart.config.vad_defaults import (
    DEFAULT_LOW_LATENCY_VAD_HANGOVER_MS,
)
from puripuly_heart.ui.overlay_calibration import OverlayCalibration

SETTINGS_SCHEMA_VERSION = 30
STT_INTERNAL_SAMPLE_RATE_HZ = 16000
DEFAULT_DESKTOP_AUDIO_VAD_HANGOVER_MS = 500
MAX_CUSTOM_VOCAB_TERMS = 100
OVERLAY_TARGET_STEAMVR = "steamvr"
OVERLAY_TARGET_DESKTOP = "desktop"
OVERLAY_TARGET_VALUES = frozenset({OVERLAY_TARGET_STEAMVR, OVERLAY_TARGET_DESKTOP})
DESKTOP_FLET_MIN_WIDTH = 480
DESKTOP_FLET_MIN_HEIGHT = 160
DESKTOP_FLET_DEFAULT_TEXT_SCALE = 1.0
DESKTOP_FLET_MIN_TEXT_SCALE = 0.75
DESKTOP_FLET_MAX_TEXT_SCALE = 1.5
DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA = 0.6
DESKTOP_FLET_MIN_BACKGROUND_ALPHA = 0.0
DESKTOP_FLET_MAX_BACKGROUND_ALPHA = 1.0
DESKTOP_FLET_MIN_OUTLINE_WIDTH = 0.5
DESKTOP_FLET_MAX_OUTLINE_WIDTH = 8.0
DESKTOP_FLET_SIZE_PRESET_ORDER = ("tiny", "xsmall", "small", "medium", "large", "xlarge")
DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER = tuple(reversed(DESKTOP_FLET_SIZE_PRESET_ORDER))
DESKTOP_FLET_DEFAULT_SIZE_PRESET = "medium"
DESKTOP_FLET_SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "tiny": (640, 160),
    "xsmall": (960, 240),
    "small": (1152, 288),
    "medium": (1344, 336),
    "large": (1600, 400),
    "xlarge": (1792, 448),
}
DESKTOP_FLET_DEFAULT_WIDTH = DESKTOP_FLET_SIZE_PRESETS[DESKTOP_FLET_DEFAULT_SIZE_PRESET][0]
DESKTOP_FLET_DEFAULT_HEIGHT = DESKTOP_FLET_SIZE_PRESETS[DESKTOP_FLET_DEFAULT_SIZE_PRESET][1]
DEFAULT_CUSTOM_VOCAB_TERMS: dict[str, tuple[str, ...]] = {
    "ko": ("아이리", "시나노"),
    "en": ("airi", "shinano"),
    "zh-CN": ("airi", "shinano"),
    "ja": ("airi", "shinano"),
}
LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS = frozenset(
    {
        "model",
        "messages",
        "stream",
        "tools",
        "tool_choice",
        "functions",
        "function_call",
        "max_tokens",
    }
)
LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS = frozenset(
    {"api_key", "authorization", "headers", "token", "secret", "password"}
)


def _default_local_llm_extra_body() -> dict[str, object]:
    return {"reasoning_effort": "none"}


def _default_custom_terms() -> dict[str, list[str]]:
    return {language: list(terms) for language, terms in DEFAULT_CUSTOM_VOCAB_TERMS.items()}


class STTProviderName(str, Enum):
    LOCAL_QWEN = "local_qwen"
    LOCAL_QWEN_17B = "local_qwen_17b"
    LOCAL_GIGAAM_RNNT = "local_gigaam_rnnt"
    LOCAL_PARAKEET_TDT = "local_parakeet_tdt"
    LOCAL_GIGAAM_RNNT_GGUF = "local_gigaam_rnnt_gguf"
    LOCAL_PARAKEET_TDT_GGUF = "local_parakeet_tdt_gguf"
    LOCAL_QWEN3_ASR_GGUF = "local_qwen3_asr_gguf"
    LOCAL_QWEN_17B_GGUF = "local_qwen_17b_gguf"


class LLMProviderName(str, Enum):
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    QWEN = "qwen"
    DEEPSEEK = "deepseek"
    LOCAL_LLM = "local_llm"
    CEREBRAS = "cerebras"
    OPENAI_COMPATIBLE = "openai_compatible"


class SecretsBackend(str, Enum):
    KEYRING = "keyring"
    ENCRYPTED_FILE = "encrypted_file"


def _default_secrets_backend() -> SecretsBackend:
    """Return the default secrets backend — ENCRYPTED_FILE in portable mode."""
    from puripuly_heart.config.paths import is_portable

    if is_portable():
        return SecretsBackend.ENCRYPTED_FILE
    return SecretsBackend.KEYRING


class QwenRegion(str, Enum):
    BEIJING = "beijing"
    SINGAPORE = "singapore"


class LocalLLMBackend(str, Enum):
    OLLAMA = "ollama"


class TranslationFallbackSelectionAlias(str, Enum):
    NONE = "none"
    DEEPSEEK_V4_FLASH_OFFICIAL = "deepseek_v4_flash_official"
    OPENROUTER_DEEPSEEK_V4_FLASH = "openrouter_deepseek_v4_flash"
    CEREBRAS_GEMMA4_31B = "cerebras_gemma4_31b"
    OPENROUTER_GEMMA4_26B_A4B = "openrouter_gemma4_26b_a4b"


class TranslationModel(str, Enum):
    GEMMA4 = "gemma4"
    DEEPSEEK_V4_FLASH = "deepseek_v4_flash"
    DEEPSEEK_V4_PRO = "deepseek_v4_pro"
    GEMINI_3_FLASH = "gemini3_flash"
    GEMINI_31_FLASH_LITE = "gemini31_flash_lite"
    QWEN_35_PLUS = "qwen35_plus"
    LOCAL_LLM = "local_llm"
    GEMMA4_31B_CEREBRAS = "gemma4_31b_cerebras"
    OPENAI_COMPATIBLE = "openai_compatible"


class TranslationConnection(str, Enum):
    OPENROUTER = "openrouter"
    OFFICIAL_BYOK = "official_byok"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(slots=True)
class TranslationSettings:
    model: TranslationModel = TranslationModel.OPENAI_COMPATIBLE
    connection: TranslationConnection = TranslationConnection.OPENAI_COMPATIBLE
    fallback_selection_alias: TranslationFallbackSelectionAlias = (
        TranslationFallbackSelectionAlias.NONE
    )
    connection_history: dict[str, TranslationConnection] = field(
        default_factory=lambda: _default_translation_connection_history()
    )

    def validate(self) -> None:
        if not isinstance(self.model, TranslationModel):
            raise ValueError("invalid translation model")
        if not isinstance(self.connection, TranslationConnection):
            raise ValueError("invalid translation connection")
        if not isinstance(self.fallback_selection_alias, TranslationFallbackSelectionAlias):
            raise ValueError("invalid translation fallback selection")
        if self.connection not in _supported_translation_connections(self.model):
            raise ValueError("translation connection is not supported for model")
        if not isinstance(self.connection_history, dict):
            raise ValueError("translation connection_history must be a dict")
        for model_value, connection in self.connection_history.items():
            model = _parse_translation_model(model_value)
            if model is None:
                raise ValueError("invalid translation connection_history model")
            if not isinstance(connection, TranslationConnection):
                raise ValueError("invalid translation connection_history connection")
            if connection not in _supported_translation_connections(model):
                raise ValueError("translation connection_history connection is not supported")


TRANSLATION_CONNECTIONS_BY_MODEL: dict[TranslationModel, tuple[TranslationConnection, ...]] = {
    TranslationModel.GEMMA4: (
        TranslationConnection.OPENROUTER,
    ),
    TranslationModel.DEEPSEEK_V4_FLASH: (
        TranslationConnection.OPENROUTER,
        TranslationConnection.OFFICIAL_BYOK,
    ),
    TranslationModel.DEEPSEEK_V4_PRO: (TranslationConnection.OFFICIAL_BYOK,),
    TranslationModel.GEMINI_3_FLASH: (
        TranslationConnection.OFFICIAL_BYOK,
        TranslationConnection.OPENROUTER,
    ),
    TranslationModel.GEMINI_31_FLASH_LITE: (
        TranslationConnection.OFFICIAL_BYOK,
        TranslationConnection.OPENROUTER,
    ),
    TranslationModel.QWEN_35_PLUS: (TranslationConnection.OFFICIAL_BYOK,),
    TranslationModel.LOCAL_LLM: (TranslationConnection.OLLAMA,),
    TranslationModel.GEMMA4_31B_CEREBRAS: (TranslationConnection.OFFICIAL_BYOK,),
    TranslationModel.OPENAI_COMPATIBLE: (TranslationConnection.OPENAI_COMPATIBLE,),
}
TRANSLATION_CONNECTION_PRIORITY: tuple[TranslationConnection, ...] = (
    TranslationConnection.OPENROUTER,
    TranslationConnection.OFFICIAL_BYOK,
)


def supported_translation_connections(
    model: TranslationModel,
) -> tuple[TranslationConnection, ...]:
    return TRANSLATION_CONNECTIONS_BY_MODEL[model]


def default_translation_connection(model: TranslationModel) -> TranslationConnection:
    if model in (TranslationModel.GEMINI_3_FLASH, TranslationModel.GEMINI_31_FLASH_LITE):
        return TranslationConnection.OFFICIAL_BYOK
    supported_connections = supported_translation_connections(model)
    for connection in TRANSLATION_CONNECTION_PRIORITY:
        if connection in supported_connections:
            return connection
    return supported_connections[0]


def _supported_translation_connections(
    model: TranslationModel,
) -> tuple[TranslationConnection, ...]:
    return supported_translation_connections(model)


def _default_translation_connection(model: TranslationModel) -> TranslationConnection:
    return default_translation_connection(model)


def _default_translation_connection_history() -> dict[str, TranslationConnection]:
    return {TranslationModel.OPENAI_COMPATIBLE.value: TranslationConnection.OPENAI_COMPATIBLE}


def _parse_translation_model(value: object) -> TranslationModel | None:
    if isinstance(value, TranslationModel):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return TranslationModel(normalized)
        except ValueError:
            pass
    return None


def _parse_translation_connection(value: object) -> TranslationConnection | None:
    if isinstance(value, TranslationConnection):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return TranslationConnection(normalized)
        except ValueError:
            pass
    return None


def _parse_translation_fallback_selection_alias(
    value: object,
) -> TranslationFallbackSelectionAlias:
    if isinstance(value, TranslationFallbackSelectionAlias):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return TranslationFallbackSelectionAlias(normalized)
        except ValueError:
            pass
    return TranslationFallbackSelectionAlias.NONE


def _parse_translation_connection_history(value: object) -> dict[str, TranslationConnection]:
    if not isinstance(value, dict):
        return {}

    history: dict[str, TranslationConnection] = {}
    for raw_model, raw_connection in value.items():
        model = _parse_translation_model(raw_model)
        connection = _parse_translation_connection(raw_connection)
        if model is None or connection is None:
            continue
        if connection not in _supported_translation_connections(model):
            continue
        history[model.value] = connection
    return history


def _normalize_translation_settings(
    *,
    model: TranslationModel | None,
    connection: TranslationConnection | None,
    fallback_selection_alias: object = None,
    history: object = None,
) -> TranslationSettings:
    normalized_model = model or TranslationModel.OPENAI_COMPATIBLE
    normalized_history = _parse_translation_connection_history(history)
    if connection not in _supported_translation_connections(normalized_model):
        connection = _default_translation_connection(normalized_model)
    normalized_history[normalized_model.value] = connection
    return TranslationSettings(
        model=normalized_model,
        connection=connection,
        fallback_selection_alias=_parse_translation_fallback_selection_alias(
            fallback_selection_alias
        ),
        connection_history=normalized_history,
    )


def _translation_data_has_valid_model(value: object) -> bool:
    return isinstance(value, dict) and _parse_translation_model(value.get("model")) is not None


def _translation_settings_to_dict(settings: TranslationSettings) -> dict[str, Any]:
    return {
        "model": settings.model.value,
        "connection": settings.connection.value,
        "fallback_selection_alias": settings.fallback_selection_alias.value,
        "connection_history": {
            model: connection.value for model, connection in settings.connection_history.items()
        },
    }


def _default_translation_settings_dict() -> dict[str, Any]:
    return {
        "model": TranslationModel.OPENAI_COMPATIBLE.value,
        "connection": TranslationConnection.OPENAI_COMPATIBLE.value,
        "fallback_selection_alias": TranslationFallbackSelectionAlias.NONE.value,
        "connection_history": {
            TranslationModel.OPENAI_COMPATIBLE.value: TranslationConnection.OPENAI_COMPATIBLE.value,
        },
    }


def _translation_settings_is_exact_default(settings: TranslationSettings) -> bool:
    return _translation_settings_to_dict(settings) == _default_translation_settings_dict()


@dataclass(slots=True)
class LanguageSettings:
    source_language: str = "ko"
    target_language: str = "en"
    second_target_language: str = ""
    peer_source_language: str = "en"
    peer_target_language: str = "ko"
    recent_source_languages: list[str] = field(default_factory=lambda: ["en", "zh-CN", "ja"])
    recent_target_languages: list[str] = field(default_factory=lambda: ["en", "zh-CN", "ja"])

    def validate(self) -> None:
        if not self.source_language:
            raise ValueError("source_language must be non-empty")
        if not self.target_language:
            raise ValueError("target_language must be non-empty")

    @property
    def effective_peer_source(self) -> str:
        return self.peer_source_language or self.source_language

    @property
    def effective_peer_target(self) -> str:
        return self.peer_target_language or self.target_language


@dataclass(slots=True)
class AudioSettings:
    internal_sample_rate_hz: int = STT_INTERNAL_SAMPLE_RATE_HZ
    internal_channels: int = 1
    ring_buffer_ms: int = 500
    input_host_api: str = WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    input_device: str = ""

    def validate(self) -> None:
        if self.internal_sample_rate_hz != STT_INTERNAL_SAMPLE_RATE_HZ:
            raise ValueError(f"internal_sample_rate_hz must be {STT_INTERNAL_SAMPLE_RATE_HZ}")
        if self.internal_channels != 1:
            raise ValueError("internal_channels must be 1 (mono)")
        if self.ring_buffer_ms <= 0:
            raise ValueError("ring_buffer_ms must be > 0")
        if self.input_host_api is None:
            raise ValueError("input_host_api must be a string")
        if self.input_device is None:
            raise ValueError("input_device must be a string")


@dataclass(slots=True)
class DesktopAudioSettings:
    output_device: str = ""
    vad_speech_threshold: float = 0.4
    vad_hangover_ms: int = DEFAULT_DESKTOP_AUDIO_VAD_HANGOVER_MS
    vad_pre_roll_ms: int = 500

    def validate(self) -> None:
        if self.output_device is None:
            raise ValueError("output_device must be a string")
        if not (0.0 <= self.vad_speech_threshold <= 1.0):
            raise ValueError("vad_speech_threshold must be in 0.0..1.0")
        if self.vad_hangover_ms < 0:
            raise ValueError("vad_hangover_ms must be >= 0")
        if self.vad_pre_roll_ms < 0:
            raise ValueError("vad_pre_roll_ms must be >= 0")


@dataclass(slots=True)
class STTSettings:
    drain_timeout_s: float = 2.0
    vad_speech_threshold: float = 0.5
    low_latency_mode: bool = True
    low_latency_vad_hangover_ms: int = DEFAULT_LOW_LATENCY_VAD_HANGOVER_MS
    low_latency_merge_gap_ms: int = 600
    low_latency_spec_retry_max: int = 10
    custom_vocabulary_enabled: bool = True
    custom_terms: dict[str, list[str]] = field(default_factory=_default_custom_terms)

    def validate(self) -> None:
        if self.drain_timeout_s <= 0:
            raise ValueError("drain_timeout_s must be > 0")
        if not (0.0 <= self.vad_speech_threshold <= 1.0):
            raise ValueError("vad_speech_threshold must be in 0.0..1.0")
        if self.low_latency_vad_hangover_ms < 0:
            raise ValueError("low_latency_vad_hangover_ms must be >= 0")
        if self.low_latency_merge_gap_ms < 0:
            raise ValueError("low_latency_merge_gap_ms must be >= 0")
        if self.low_latency_spec_retry_max < 0:
            raise ValueError("low_latency_spec_retry_max must be >= 0")
        if not isinstance(self.custom_vocabulary_enabled, bool):
            raise ValueError("custom_vocabulary_enabled must be a bool")
        if not isinstance(self.custom_terms, dict):
            raise ValueError("custom_terms must be a dict[str, list[str]]")
        for language, terms in self.custom_terms.items():
            if not isinstance(language, str):
                raise ValueError("custom_terms keys must be strings")
            if not isinstance(terms, list):
                raise ValueError("custom_terms values must be lists of strings")
            for term in terms:
                if not isinstance(term, str):
                    raise ValueError("custom_terms values must be lists of strings")


@dataclass(slots=True)
class LLMSettings:
    concurrency_limit: int = 5

    def validate(self) -> None:
        if self.concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be > 0")


@dataclass(slots=True)
class OSCSettings:
    host: str = "127.0.0.1"
    port: int = 9000
    chatbox_address: str = "/chatbox/input"
    chatbox_send: bool = True
    chatbox_clear: bool = False
    chatbox_max_chars: int = 144
    vrc_mic_intercept: bool = False
    chatbox_include_source: bool = False

    def validate(self) -> None:
        if not self.host:
            raise ValueError("host must be non-empty")
        if not (0 < self.port <= 65535):
            raise ValueError("port must be in 1..65535")
        if not self.chatbox_address or not self.chatbox_address.startswith("/"):
            raise ValueError("chatbox_address must start with '/'")
        if self.chatbox_max_chars <= 0:
            raise ValueError("chatbox_max_chars must be > 0")


@dataclass(slots=True)
class OpenAICompatibleSettings:
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"

    def validate(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("openai_compatible base_url must be a non-empty string")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("openai_compatible model must be a non-empty string")


@dataclass(slots=True)
class ProviderSettings:
    stt: STTProviderName = STTProviderName.LOCAL_QWEN
    peer_stt: STTProviderName = STTProviderName.LOCAL_QWEN
    stt_compute: str = "gpu"
    peer_stt_compute: str = "gpu"
    stt_backend: str = "onnx"
    peer_stt_backend: str = "onnx"
    stt_quant: str = "auto"
    peer_stt_quant: str = "auto"
    llm: LLMProviderName = LLMProviderName.OPENAI_COMPATIBLE
    openai_compatible: OpenAICompatibleSettings = field(default_factory=OpenAICompatibleSettings)

    def validate(self) -> None:
        if not isinstance(self.stt, STTProviderName):
            raise ValueError("invalid stt provider")
        if not isinstance(self.peer_stt, STTProviderName):
            raise ValueError("invalid peer stt provider")
        if self.stt_compute not in ("gpu", "cpu"):
            raise ValueError("stt_compute must be 'gpu' or 'cpu'")
        if self.peer_stt_compute not in ("gpu", "cpu"):
            raise ValueError("peer_stt_compute must be 'gpu' or 'cpu'")
        if self.stt_backend not in ("onnx", "gguf"):
            raise ValueError("stt_backend must be 'onnx' or 'gguf'")
        if self.peer_stt_backend not in ("onnx", "gguf"):
            raise ValueError("peer_stt_backend must be 'onnx' or 'gguf'")
        if not isinstance(self.stt_quant, str) or not self.stt_quant:
            raise ValueError("stt_quant must be a non-empty string")
        if not isinstance(self.peer_stt_quant, str) or not self.peer_stt_quant:
            raise ValueError("peer_stt_quant must be a non-empty string")
        if not isinstance(self.llm, LLMProviderName):
            raise ValueError("invalid llm provider")
        if not isinstance(self.openai_compatible, OpenAICompatibleSettings):
            raise ValueError("invalid openai_compatible settings")
        if self.llm == LLMProviderName.OPENAI_COMPATIBLE:
            self.openai_compatible.validate()


@dataclass(slots=True)
class SecretsSettings:
    backend: SecretsBackend = SecretsBackend.KEYRING
    encrypted_file_path: str = "secrets.json"

    def validate(self) -> None:
        if not isinstance(self.backend, SecretsBackend):
            raise ValueError("invalid secrets backend")
        if self.backend == SecretsBackend.ENCRYPTED_FILE and not self.encrypted_file_path:
            raise ValueError("encrypted_file_path must be set for encrypted_file backend")


@dataclass(slots=True)
class QwenSettings:
    region: QwenRegion = QwenRegion.BEIJING

    def validate(self) -> None:
        if not isinstance(self.region, QwenRegion):
            raise ValueError("invalid qwen region")


@dataclass(slots=True)
class LocalLLMSettings:
    backend: LocalLLMBackend = LocalLLMBackend.OLLAMA
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "llama3.1:8b"
    extra_body: dict[str, object] = field(default_factory=_default_local_llm_extra_body)

    def validate(self) -> None:
        if not isinstance(self.backend, LocalLLMBackend):
            raise ValueError("invalid local llm backend")
        self.base_url = _normalize_local_llm_base_url(self.base_url)
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("invalid local llm base url")
        self.model = _normalize_local_llm_model(self.model)
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("invalid local llm model")
        if not isinstance(self.extra_body, dict):
            raise ValueError("invalid local llm extra body")
        normalized = {key: value for key, value in self.extra_body.items() if isinstance(key, str)}
        if len(normalized) != len(self.extra_body):
            raise ValueError("local llm extra body keys must be strings")
        lowered = {key.lower() for key in normalized}
        reserved = LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS.intersection(lowered)
        if reserved:
            key = sorted(reserved)[0]
            raise ValueError(f"reserved local llm extra_body key: {key}")
        sensitive = LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS.intersection(lowered)
        if sensitive:
            key = sorted(sensitive)[0]
            raise ValueError(f"sensitive local llm extra_body key: {key}")
        try:
            json.dumps(normalized, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("local llm extra body must be JSON serializable") from exc
        self.extra_body = copy.deepcopy(normalized)


@dataclass(slots=True)
class UiSettings:
    locale: str = "en"
    overlay_enabled: bool = False
    peer_translation_enabled: bool = False
    peer_translation_eula_accepted: bool = False
    integrated_context_enabled: bool = True
    integrated_context_bootstrapped: bool = False
    clipboard_auto_translate_enabled: bool = False

    def validate(self) -> None:
        if not self.locale:
            raise ValueError("locale must be non-empty")
        if not isinstance(self.clipboard_auto_translate_enabled, bool):
            raise ValueError("clipboard_auto_translate_enabled must be a bool")


@dataclass(slots=True)
class DesktopFletOverlayBounds:
    x: int | float | None = None
    y: int | float | None = None
    width: int | float = DESKTOP_FLET_DEFAULT_WIDTH
    height: int | float = DESKTOP_FLET_DEFAULT_HEIGHT

    def validate(self) -> None:
        self.x, self.y = _normalize_desktop_flet_bounds_position(self.x, self.y)
        self.width = _normalize_desktop_flet_dimension(
            self.width,
            default=DESKTOP_FLET_DEFAULT_WIDTH,
            minimum=DESKTOP_FLET_MIN_WIDTH,
        )
        self.height = _normalize_desktop_flet_dimension(
            self.height,
            default=DESKTOP_FLET_DEFAULT_HEIGHT,
            minimum=DESKTOP_FLET_MIN_HEIGHT,
        )


@dataclass(slots=True)
class DesktopFletOverlayPosition:
    x: int | float | None = None
    y: int | float | None = None

    def validate(self) -> None:
        self.x, self.y = _normalize_desktop_flet_position(self.x, self.y)


@dataclass(slots=True, init=False)
class DesktopFletOverlayVisualSettings:
    background_alpha: float = DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA

    def __init__(
        self,
        text_scale: object = None,
        background_alpha: object = DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
        outline_width: object = None,
    ) -> None:
        _ = (text_scale, outline_width)
        self.background_alpha = background_alpha

    def validate(self) -> None:
        self.background_alpha = _normalize_desktop_flet_range(
            self.background_alpha,
            default=DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
            minimum=DESKTOP_FLET_MIN_BACKGROUND_ALPHA,
            maximum=DESKTOP_FLET_MAX_BACKGROUND_ALPHA,
        )

    @property
    def text_scale(self) -> float:
        return DESKTOP_FLET_DEFAULT_TEXT_SCALE

    @text_scale.setter
    def text_scale(self, _value: object) -> None:
        return

    @property
    def outline_width(self) -> None:
        return None

    @outline_width.setter
    def outline_width(self, _value: object) -> None:
        return


@dataclass(slots=True)
class DesktopFletOverlaySettings:
    size_preset: str = DESKTOP_FLET_DEFAULT_SIZE_PRESET
    position: DesktopFletOverlayPosition = field(default_factory=DesktopFletOverlayPosition)
    locked: bool = False
    visual: DesktopFletOverlayVisualSettings = field(
        default_factory=DesktopFletOverlayVisualSettings
    )

    def validate(self) -> None:
        self.size_preset = _parse_desktop_flet_size_preset(self.size_preset)
        if not isinstance(self.position, DesktopFletOverlayPosition):
            self.position = DesktopFletOverlayPosition()
        if not isinstance(self.locked, bool):
            self.locked = False
        if not isinstance(self.visual, DesktopFletOverlayVisualSettings):
            self.visual = DesktopFletOverlayVisualSettings()
        self.position.validate()
        self.visual.validate()

    @property
    def bounds(self) -> DesktopFletOverlayBounds:
        width, height = _desktop_flet_dimensions_for_preset(self.size_preset)
        return DesktopFletOverlayBounds(
            x=self.position.x,
            y=self.position.y,
            width=width,
            height=height,
        )

    @bounds.setter
    def bounds(self, value: object) -> None:
        bounds = _parse_desktop_flet_bounds(value)
        self.size_preset = _nearest_desktop_flet_size_preset(bounds.width, bounds.height)
        self.position = DesktopFletOverlayPosition(x=bounds.x, y=bounds.y)


@dataclass(slots=True)
class OverlaySettings:
    target: str = OVERLAY_TARGET_STEAMVR
    show_translation: bool = True
    show_peer_original: bool = True
    calibration: OverlayCalibration = field(default_factory=OverlayCalibration)
    desktop_flet: DesktopFletOverlaySettings = field(default_factory=DesktopFletOverlaySettings)

    def validate(self) -> None:
        self.target = _parse_overlay_target(self.target)
        if not isinstance(self.show_translation, bool):
            raise ValueError("overlay show_translation must be a bool")
        if not isinstance(self.show_peer_original, bool):
            raise ValueError("overlay show_peer_original must be a bool")
        self.calibration.validate()
        if not isinstance(self.desktop_flet, DesktopFletOverlaySettings):
            self.desktop_flet = DesktopFletOverlaySettings()
        self.desktop_flet.validate()


@dataclass(slots=True)
class ApiKeyVerificationSettings:
    """Stores API key verification status for each provider."""

    openai_compatible: bool = False

    def validate(self) -> None:
        pass  # No validation needed


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
    qwen: QwenSettings = field(default_factory=QwenSettings)
    local_llm: LocalLLMSettings = field(default_factory=LocalLLMSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    osc: OSCSettings = field(default_factory=OSCSettings)
    secrets: SecretsSettings = field(default_factory=SecretsSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    api_key_verified: ApiKeyVerificationSettings = field(default_factory=ApiKeyVerificationSettings)
    system_prompt: str = ""
    system_prompts: dict[str, str] = field(default_factory=dict)

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
        self.qwen.validate()
        self.local_llm.validate()
        self.llm.validate()
        self.osc.validate()
        self.secrets.validate()
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


def _parse_overlay_target(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in OVERLAY_TARGET_VALUES:
            return normalized
    return OVERLAY_TARGET_STEAMVR


def _finite_non_bool_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number):
        return None
    return value


def _clamp_float(value: float, *, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _normalize_desktop_flet_bounds_position(
    x_value: object,
    y_value: object,
) -> tuple[int | float | None, int | float | None]:
    return _normalize_desktop_flet_position(x_value, y_value)


def _normalize_desktop_flet_position(
    x_value: object,
    y_value: object,
) -> tuple[int | float | None, int | float | None]:
    x = _finite_non_bool_number(x_value)
    y = _finite_non_bool_number(y_value)
    if x is None or y is None:
        return None, None
    return x, y


def _normalize_desktop_flet_dimension(
    value: object,
    *,
    default: int,
    minimum: int,
) -> int | float:
    number = _finite_non_bool_number(value)
    if number is None:
        return default
    return max(number, minimum)


def _normalize_desktop_flet_range(
    value: object,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    number = _finite_non_bool_number(value)
    if number is None:
        return default
    return _clamp_float(number, minimum=minimum, maximum=maximum)


def _parse_desktop_flet_size_preset(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in DESKTOP_FLET_SIZE_PRESET_ORDER:
            return normalized
    return DESKTOP_FLET_DEFAULT_SIZE_PRESET


def _desktop_flet_dimensions_for_preset(preset: object) -> tuple[int, int]:
    return DESKTOP_FLET_SIZE_PRESETS[_parse_desktop_flet_size_preset(preset)]


def _valid_desktop_flet_legacy_dimension(value: object) -> int | float | None:
    number = _finite_non_bool_number(value)
    if number is None or number <= 0:
        return None
    return number


def _nearest_desktop_flet_size_preset(width_value: object, height_value: object) -> str:
    width = _valid_desktop_flet_legacy_dimension(width_value)
    height = _valid_desktop_flet_legacy_dimension(height_value)
    if width is None or height is None:
        return DESKTOP_FLET_DEFAULT_SIZE_PRESET

    scores: list[tuple[str, float]] = []
    for preset in DESKTOP_FLET_SIZE_PRESET_ORDER:
        preset_width, preset_height = DESKTOP_FLET_SIZE_PRESETS[preset]
        score = (
            abs(width - preset_width) / preset_width + abs(height - preset_height) / preset_height
        )
        scores.append((preset, score))

    lowest_score = min(score for _preset, score in scores)
    tied = [
        preset
        for preset, score in scores
        if math.isclose(score, lowest_score, rel_tol=0.0, abs_tol=1e-12)
    ]
    if DESKTOP_FLET_DEFAULT_SIZE_PRESET in tied:
        return DESKTOP_FLET_DEFAULT_SIZE_PRESET
    return tied[0]


def _parse_desktop_flet_bounds(value: object) -> DesktopFletOverlayBounds:
    if isinstance(value, DesktopFletOverlayBounds):
        bounds = copy.deepcopy(value)
        bounds.validate()
        return bounds
    data = value if isinstance(value, dict) else {}
    x, y = _normalize_desktop_flet_bounds_position(data.get("x"), data.get("y"))
    return DesktopFletOverlayBounds(
        x=x,
        y=y,
        width=_normalize_desktop_flet_dimension(
            data.get("width"),
            default=DESKTOP_FLET_DEFAULT_WIDTH,
            minimum=DESKTOP_FLET_MIN_WIDTH,
        ),
        height=_normalize_desktop_flet_dimension(
            data.get("height"),
            default=DESKTOP_FLET_DEFAULT_HEIGHT,
            minimum=DESKTOP_FLET_MIN_HEIGHT,
        ),
    )


def _parse_desktop_flet_position(value: object) -> DesktopFletOverlayPosition:
    if isinstance(value, DesktopFletOverlayPosition):
        position = copy.deepcopy(value)
        position.validate()
        return position
    data: dict[str, object]
    if isinstance(value, DesktopFletOverlayBounds):
        data = {"x": value.x, "y": value.y}
    else:
        data = value if isinstance(value, dict) else {}
    x, y = _normalize_desktop_flet_position(data.get("x"), data.get("y"))
    return DesktopFletOverlayPosition(x=x, y=y)


def _parse_desktop_flet_visual(value: object) -> DesktopFletOverlayVisualSettings:
    if isinstance(value, DesktopFletOverlayVisualSettings):
        visual = copy.deepcopy(value)
        visual.validate()
        return visual
    data = value if isinstance(value, dict) else {}
    return DesktopFletOverlayVisualSettings(
        background_alpha=_normalize_desktop_flet_range(
            data.get("background_alpha"),
            default=DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
            minimum=DESKTOP_FLET_MIN_BACKGROUND_ALPHA,
            maximum=DESKTOP_FLET_MAX_BACKGROUND_ALPHA,
        ),
    )


def _parse_desktop_flet_settings(value: object) -> DesktopFletOverlaySettings:
    if isinstance(value, DesktopFletOverlaySettings):
        settings = copy.deepcopy(value)
        settings.validate()
        return settings
    data = value if isinstance(value, dict) else {}
    bounds_data = data.get("bounds") if isinstance(data.get("bounds"), dict) else {}
    size_preset = (
        _parse_desktop_flet_size_preset(data.get("size_preset"))
        if "size_preset" in data
        else _nearest_desktop_flet_size_preset(
            bounds_data.get("width"),
            bounds_data.get("height"),
        )
    )
    position = (
        _parse_desktop_flet_position(data.get("position"))
        if "position" in data
        else _parse_desktop_flet_position(bounds_data)
    )
    return DesktopFletOverlaySettings(
        size_preset=size_preset,
        position=position,
        locked=False,
        visual=_parse_desktop_flet_visual(data.get("visual")),
    )


def _desktop_flet_visual_to_dict(
    visual: DesktopFletOverlayVisualSettings,
) -> dict[str, float]:
    if not isinstance(visual, DesktopFletOverlayVisualSettings):
        visual = DesktopFletOverlayVisualSettings()
    visual = copy.deepcopy(visual)
    visual.validate()
    return {"background_alpha": visual.background_alpha}


def _desktop_flet_settings_to_dict(settings: DesktopFletOverlaySettings) -> dict[str, object]:
    if not isinstance(settings, DesktopFletOverlaySettings):
        settings = DesktopFletOverlaySettings()
    settings = copy.deepcopy(settings)
    settings.validate()
    return {
        "size_preset": settings.size_preset,
        "position": {"x": settings.position.x, "y": settings.position.y},
        "visual": _desktop_flet_visual_to_dict(settings.visual),
    }


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
            "low_latency_merge_gap_ms": settings.stt.low_latency_merge_gap_ms,
            "low_latency_spec_retry_max": settings.stt.low_latency_spec_retry_max,
            "custom_vocabulary_enabled": settings.stt.custom_vocabulary_enabled,
            "custom_terms": _parse_custom_terms(settings.stt.custom_terms),
        },
        "qwen": {
            "region": settings.qwen.region.value,
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
        "secrets": {
            "backend": settings.secrets.backend.value,
            "encrypted_file_path": settings.secrets.encrypted_file_path,
        },
        "ui": {
            "locale": settings.ui.locale,
            "peer_translation_eula_accepted": settings.ui.peer_translation_eula_accepted,
            "integrated_context_enabled": settings.ui.integrated_context_enabled,
            "integrated_context_bootstrapped": settings.ui.integrated_context_bootstrapped,
            "clipboard_auto_translate_enabled": settings.ui.clipboard_auto_translate_enabled,
        },
        "api_key_verified": {
            "openai_compatible": settings.api_key_verified.openai_compatible,
        },
        "system_prompt": settings.system_prompt,
    }
    return _enum_to_value(data)  # type: ignore[return-value]


def _parse_stt_provider(value: str) -> STTProviderName:
    """Parse STT provider, mapping legacy values to supported providers."""
    try:
        return STTProviderName(value)
    except ValueError:
        return STTProviderName.LOCAL_QWEN


def _parse_peer_stt_provider(value: str) -> STTProviderName:
    return _parse_stt_provider(value)


def _parse_openai_compatible_settings(data: dict[str, Any]) -> OpenAICompatibleSettings:
    base_url = str(data.get("base_url", "https://api.openai.com/v1")).strip()
    model = str(data.get("model", "gpt-4o-mini")).strip()
    return OpenAICompatibleSettings(base_url=base_url, model=model)


def _parse_llm_provider(value: object) -> LLMProviderName:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return LLMProviderName(normalized)
        except ValueError:
            pass
    return LLMProviderName.OPENAI_COMPATIBLE


def _parse_local_llm_backend(value: object) -> LocalLLMBackend:
    if isinstance(value, str):
        try:
            return LocalLLMBackend(value.strip())
        except ValueError:
            pass
    return LocalLLMBackend.OLLAMA


def _normalize_local_llm_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid local llm base url")
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("invalid local llm base url") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("invalid local llm base url")
    if not parsed.hostname:
        raise ValueError("invalid local llm base url")
    if (
        "@" in parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid local llm base url")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _parse_local_llm_base_url(value: object) -> str:
    if isinstance(value, str):
        try:
            return _normalize_local_llm_base_url(value)
        except ValueError:
            pass
    return "http://127.0.0.1:11434/v1"


def _normalize_local_llm_model(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid local llm model")
    normalized = value.strip()
    if not normalized:
        raise ValueError("invalid local llm model")
    return normalized


def _parse_local_llm_model(value: object) -> str:
    if isinstance(value, str):
        try:
            return _normalize_local_llm_model(value)
        except ValueError:
            pass
    return "llama3.1:8b"


def _parse_local_llm_extra_body(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return _default_local_llm_extra_body()
    normalized = {key: val for key, val in value.items() if isinstance(key, str)}
    lowered = {key.lower() for key in normalized}
    if LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS.intersection(lowered):
        return _default_local_llm_extra_body()
    if LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS.intersection(lowered):
        return _default_local_llm_extra_body()
    try:
        json.dumps(normalized, allow_nan=False)
    except (TypeError, ValueError):
        return _default_local_llm_extra_body()
    return copy.deepcopy(normalized)


def _normalize_local_llm_data(data: dict[str, Any]) -> bool:
    raw_local_llm = data.get("local_llm")
    local_llm_data = raw_local_llm if isinstance(raw_local_llm, dict) else {}
    normalized = {
        "backend": _parse_local_llm_backend(local_llm_data.get("backend")).value,
        "base_url": _parse_local_llm_base_url(local_llm_data.get("base_url")),
        "model": _parse_local_llm_model(local_llm_data.get("model")),
        "extra_body": _parse_local_llm_extra_body(local_llm_data.get("extra_body")),
    }
    if raw_local_llm != normalized:
        data["local_llm"] = normalized
        return True
    return False


def _derive_translation_settings_from_runtime_values(
    *, provider_llm: LLMProviderName, fallback_selection_alias: object = None, history: object = None,
) -> TranslationSettings:
    normalized_history = _parse_translation_connection_history(history)
    if provider_llm == LLMProviderName.LOCAL_LLM:
        return _normalize_translation_settings(
            model=TranslationModel.LOCAL_LLM, connection=TranslationConnection.OLLAMA,
            fallback_selection_alias=fallback_selection_alias, history=normalized_history,
        )
    return _normalize_translation_settings(
        model=TranslationModel.OPENAI_COMPATIBLE, connection=TranslationConnection.OPENAI_COMPATIBLE,
        fallback_selection_alias=fallback_selection_alias, history=normalized_history,
    )


def _derive_translation_settings_from_runtime(
    settings: AppSettings,
    history: object = None,
) -> TranslationSettings:
    return _derive_translation_settings_from_runtime_values(
        provider_llm=settings.provider.llm,
        fallback_selection_alias=settings.translation.fallback_selection_alias,
        history=history,
    )


def materialize_translation_settings(settings: AppSettings) -> AppSettings:
    settings.translation = _normalize_translation_settings(
        model=_parse_translation_model(settings.translation.model),
        connection=_parse_translation_connection(settings.translation.connection),
        fallback_selection_alias=settings.translation.fallback_selection_alias,
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
        fallback_selection_alias=translation.fallback_selection_alias,
        history=translation.connection_history,
    )
    if translation.model == TranslationModel.LOCAL_LLM:
        changed |= _set_mapping_value(provider_data, "llm", LLMProviderName.LOCAL_LLM.value)
        return changed
    changed |= _set_mapping_value(provider_data, "llm", LLMProviderName.OPENAI_COMPATIBLE.value)
    return changed


def _parse_qwen_region(value: object) -> QwenRegion:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return QwenRegion(normalized)
        except ValueError:
            pass
    return QwenRegion.BEIJING


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
    settings.translation.fallback_selection_alias = (
        TranslationFallbackSelectionAlias.NONE
    )
    settings.ui.locale = resolve_first_run_ui_locale(system_locale)
    ensure_prompt_defaults(settings)
    settings.validate()
    return settings


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


def _coerce_int(value: object, fallback: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _normalize_internal_sample_rate_hz(value: object) -> int:
    normalized = _coerce_int(value, STT_INTERNAL_SAMPLE_RATE_HZ)
    if normalized == 8000:
        return STT_INTERNAL_SAMPLE_RATE_HZ
    return normalized


def _parse_bool(value: object, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return fallback


def _parse_non_negative_int(value: object, fallback: int = 0) -> int:
    if type(value) is not int:
        return fallback
    if value < 0:
        return fallback
    return value


def _parse_utc_iso8601_timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    parse_value = f"{normalized[:-1]}+00:00" if normalized.endswith("Z") else normalized
    try:
        parsed = datetime.fromisoformat(parse_value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return normalized


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
    elif not isinstance(rpd, dict): pd = {"stt": STTProviderName.LOCAL_QWEN.value, "llm": LLMProviderName.OPENAI_COMPATIBLE.value}; data["provider"] = pd; changed = True
    if isinstance(pd, dict) and "stt" in pd:
        rs = pd.get("stt"); ns = _parse_stt_provider(str(rs)).value
        if rs != ns: pd["stt"] = ns; changed = True
    if isinstance(pd, dict) and "peer_stt" not in pd: pd["peer_stt"] = STTProviderName.LOCAL_QWEN.value; changed = True
    if isinstance(pd, dict) and "peer_stt" in pd:
        rp = pd.get("peer_stt"); np_ = _parse_peer_stt_provider(str(rp)).value
        if rp != np_: pd["peer_stt"] = np_; changed = True
    td = data.get("translation") if isinstance(data.get("translation"), dict) else {}
    th = _parse_translation_connection_history(td.get("connection_history") if isinstance(td, dict) else None)
    if _translation_data_has_valid_model(td):
        nts = _normalize_translation_settings(model=_parse_translation_model(td.get("model")), connection=_parse_translation_connection(td.get("connection")), fallback_selection_alias=td.get("fallback_selection_alias"), history=th)
    else:
        nts = _derive_translation_settings_from_runtime_values(provider_llm=_parse_llm_provider(pd.get("llm", LLMProviderName.OPENAI_COMPATIBLE.value)), history=th)
    ntd = _translation_settings_to_dict(nts)
    if data.get("translation") != ntd: data["translation"] = ntd; changed = True
    if _apply_materialized_translation_to_data(data, nts): changed = True
    av = data.get("api_key_verified")
    if not isinstance(av, dict): av = {}; data["api_key_verified"] = av; changed = True
    if "openai_compatible" not in av: av["openai_compatible"] = False; changed = True
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
        stt_provider_value = STTProviderName.LOCAL_QWEN.value
    elif isinstance(raw_provider_data, dict):
        stt_provider_value = provider_data.get("stt", STTProviderName.LOCAL_QWEN.value)
    else:
        stt_provider_value = STTProviderName.LOCAL_QWEN.value
    raw_peer_provider = (
        provider_data.get("peer_stt", STTProviderName.LOCAL_QWEN.value)
        if isinstance(raw_provider_data, dict)
        else STTProviderName.LOCAL_QWEN.value
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

    qwen_raw = data.get("qwen") if isinstance(data.get("qwen"), dict) else {}
    local_llm_raw = data.get("local_llm") if isinstance(data.get("local_llm"), dict) else {}
    qwen_settings = QwenSettings(
        region=_parse_qwen_region(
            qwen_raw.get("region"),
        ),
    )

    settings = AppSettings(
        settings_version=settings_version,
        provider=ProviderSettings(
            stt=_parse_stt_provider(str(stt_provider_value)),
            peer_stt=_parse_peer_stt_provider(str(raw_peer_provider)),
            stt_compute=str(provider_data.get("stt_compute", "gpu")),
            peer_stt_compute=str(provider_data.get("peer_stt_compute", "gpu")),
            stt_backend=str(provider_data.get("stt_backend", "onnx")),
            peer_stt_backend=str(provider_data.get("peer_stt_backend", "onnx")),
            stt_quant=str(provider_data.get("stt_quant", "auto")),
            peer_stt_quant=str(provider_data.get("peer_stt_quant", "auto")),
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
            low_latency_merge_gap_ms=int(stt_data.get("low_latency_merge_gap_ms", 600)),
            low_latency_spec_retry_max=int(stt_data.get("low_latency_spec_retry_max", 10)),
            custom_vocabulary_enabled=custom_vocabulary_enabled,
            custom_terms=parsed_custom_terms,
        ),
        qwen=qwen_settings,
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
        secrets=SecretsSettings(
            backend=SecretsBackend(
                data.get("secrets", {}).get("backend", _default_secrets_backend().value)
            ),
            encrypted_file_path=data.get("secrets", {}).get("encrypted_file_path", "secrets.json"),
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
            openai_compatible=bool(data.get("api_key_verified", {}).get("openai_compatible", False)),
        ),
        system_prompt=legacy_system_prompt,
        system_prompts={},
    )

    translation_data = data.get("translation") if isinstance(data.get("translation"), dict) else {}
    translation_history = _parse_translation_connection_history(
        translation_data.get("connection_history") if isinstance(translation_data, dict) else None
    )
    if _translation_data_has_valid_model(translation_data):
        settings.translation = _normalize_translation_settings(
            model=_parse_translation_model(translation_data.get("model")),
            connection=_parse_translation_connection(translation_data.get("connection")),
            fallback_selection_alias=translation_data.get("fallback_selection_alias"),
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


def load_settings(path: Path) -> AppSettings:
    raw_text = path.read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    if not isinstance(raw, dict):
        raise ValueError("settings file must contain a JSON object")
    raw_version = _coerce_int(raw.get("settings_version"), 1)
    if raw_version < 1:
        raw_version = 1
    migrated, changed = _migrate_settings_dict(raw)
    settings = from_dict(migrated)
    if changed:
        if raw_version < SETTINGS_SCHEMA_VERSION:
            _write_settings_migration_backup(path, raw_text, raw_version)
        save_settings(path, settings)
    return settings


def save_settings(path: Path, settings: AppSettings) -> None:
    settings.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        path,
        json.dumps(to_dict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_settings_migration_backup(path: Path, content: str, source_version: int) -> Path:
    backup_stem = f"{path.name}.v{source_version}.pre-v{SETTINGS_SCHEMA_VERSION}.bak"
    backup_path = path.with_name(backup_stem)
    index = 1
    while backup_path.exists():
        backup_path = path.with_name(f"{backup_stem}.{index}")
        index += 1
    _atomic_write_text(backup_path, content, encoding="utf-8")
    return backup_path


def _atomic_write_text(path: Path, content: str, *, encoding: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding=encoding)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
