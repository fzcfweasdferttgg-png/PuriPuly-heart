"""Domain validation for settings.

Pure functions — no side effects, no UI dependencies, no state.
validate_extra_body_json is called from 2 section mixins (llm,
fallback_local_llm) to validate extra_body JSON before applying settings.
check_reserved_keys and check_sensitive_keys guard against user overwriting
internal or dangerous keys in the local_llm provider config.

All 2 callers use identical logic.  Any change to
validation rules here automatically applies to all callers.
"""

from __future__ import annotations

import copy
import json

LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS: frozenset[str] = frozenset(
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

LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS: frozenset[str] = frozenset(
    {"api_key", "authorization", "headers", "token", "secret", "password"}
)


def _reject_json_constant(value: str) -> None:
    """json.loads parse_constant hook — rejects NaN, Infinity, -Infinity."""
    raise json.JSONDecodeError(f"invalid JSON constant: {value}", value, 0)


def validate_extra_body_json(raw: str) -> tuple[bool, str | None, object]:
    """Validate and normalize extra_body JSON string.

    Returns:
        (True, None, normalized_dict) on success
        (False, error_key, error_param_or_None) on failure

    Error keys (i18n):
        "settings.local_llm.extra_body.invalid_json"
        "settings.local_llm.extra_body.must_be_object"
        "settings.local_llm.extra_body.reserved_key" — error_param is the key name
        "settings.local_llm.extra_body.sensitive_key" — error_param is the key name
        "settings.local_llm.extra_body.not_serializable"
    """
    raw = (raw or "").strip()
    try:
        parsed = (
            {"reasoning_effort": "none"}
            if not raw
            else json.loads(raw, parse_constant=_reject_json_constant)
        )
    except json.JSONDecodeError:
        return False, "flet.settings.local_llm.extra_body.invalid_json", None

    if not isinstance(parsed, dict):
        return False, "flet.settings.local_llm.extra_body.must_be_object", None

    lowered = {str(key).lower() for key in parsed}

    reserved = LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS.intersection(lowered)
    if reserved:
        return False, "flet.settings.local_llm.extra_body.reserved_key", sorted(reserved)[0]

    sensitive = LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS.intersection(lowered)
    if sensitive:
        return False, "flet.settings.local_llm.extra_body.sensitive_key", sorted(sensitive)[0]

    try:
        json.dumps(parsed, allow_nan=False)
    except (TypeError, ValueError):
        return False, "flet.settings.local_llm.extra_body.not_serializable", None

    return True, None, copy.deepcopy(parsed)
