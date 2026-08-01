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
    LOCAL_LLM = "local_llm"
    OPENAI_COMPATIBLE = "openai_compatible"


class SecretsBackend(str, Enum):
    KEYRING = "keyring"
    ENCRYPTED_FILE = "encrypted_file"


class QwenRegion(str, Enum):
    BEIJING = "beijing"
    SINGAPORE = "singapore"


class LocalLLMBackend(str, Enum):
    OLLAMA = "ollama"


class TranslationModel(str, Enum):
    LOCAL_LLM = "local_llm"
    OPENAI_COMPATIBLE = "openai_compatible"


class TranslationConnection(str, Enum):
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
    TranslationModel.LOCAL_LLM: (TranslationConnection.OLLAMA,),
    TranslationModel.OPENAI_COMPATIBLE: (TranslationConnection.OPENAI_COMPATIBLE,),
}


def supported_translation_connections(
    model: TranslationModel,
) -> tuple[TranslationConnection, ...]:
    return TRANSLATION_CONNECTIONS_BY_MODEL[model]


def default_translation_connection(model: TranslationModel) -> TranslationConnection:
    return TRANSLATION_CONNECTIONS_BY_MODEL[model][0]


def _supported_translation_connections(
    model: TranslationModel,
) -> tuple[TranslationConnection, ...]:
    return supported_translation_connections(model)


def _default_translation_connection(model: TranslationModel) -> TranslationConnection:
    return default_translation_connection(model)
