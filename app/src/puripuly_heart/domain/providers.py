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
