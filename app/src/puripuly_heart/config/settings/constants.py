from __future__ import annotations

SETTINGS_SCHEMA_VERSION = 30
STT_INTERNAL_SAMPLE_RATE_HZ = 16000
STT_RESET_DEADLINE_S = 300.0
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
