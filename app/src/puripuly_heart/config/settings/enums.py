from __future__ import annotations

from enum import Enum


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


def _parse_stt_provider(value: str) -> STTProviderName:
    try:
        return STTProviderName(value)
    except ValueError:
        return STTProviderName.LOCAL_QWEN


def _parse_peer_stt_provider(value: str) -> STTProviderName:
    return _parse_stt_provider(value)


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


def _parse_qwen_region(value: object) -> QwenRegion:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return QwenRegion(normalized)
        except ValueError:
            pass
    return QwenRegion.BEIJING


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
