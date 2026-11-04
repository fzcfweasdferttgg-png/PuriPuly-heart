"""Arbitrary fixture identities from the retired CPU-model family.

The models behind these ids are no longer shipped; the strings survive only
as test fixture identifiers.
"""

ORUKEET_MODEL_ID = "orukeet-q8-0-transcribe-cpu"
FUN_ASR_NANO_MODEL_ID = "fun-asr-nano-2512-q8-0-transcribe-cpu"
FUN_ASR_MLT_NANO_MODEL_ID = "fun-asr-mlt-nano-2512-q8-0-transcribe-cpu"
PARAKEET_JAPANESE_MODEL_ID = "parakeet-tdt-ctc-0.6b-ja-int8-sherpa"
REQUIRED_CPU_LOCAL_STT_MODEL_IDS = (
    ORUKEET_MODEL_ID,
    FUN_ASR_NANO_MODEL_ID,
    FUN_ASR_MLT_NANO_MODEL_ID,
    PARAKEET_JAPANESE_MODEL_ID,
)
