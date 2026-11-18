from __future__ import annotations

import copy
import json
import math
import types
from collections.abc import Mapping
from dataclasses import asdict, fields, is_dataclass
from functools import cache
from types import MappingProxyType
from typing import Any, Final, Literal, Union, get_args, get_origin, get_type_hints

from puripuly_heart.config.settings_vnext.schema import (
    VNEXT_SETTINGS_SCHEMA_VERSION,
    AppSettingsVNext,
    with_translation_runtime_policy,
)

_PROVIDER_VERIFICATION_FIELDS: Final = (
    "deepgram",
    "gemini_transcribe",
    "elevenlabs_scribe",
    "soniox",
    "google",
    "openrouter",
    "openai",
    "deepseek",
    "alibaba_beijing",
    "alibaba_singapore",
)

def to_dict(settings: AppSettingsVNext) -> dict[str, Any]:
    """Serialize canonical vNext settings.

    The vNext persisted schema intentionally writes no legacy projection keys. Runtime-only
    state and raw secret values are excluded by the schema itself.
    """

    if not isinstance(settings, AppSettingsVNext):
        raise TypeError("vNext settings serializer requires AppSettingsVNext")
    normalized = with_translation_runtime_policy(settings)
    data = asdict(normalized)
    return {
        "settings_version": VNEXT_SETTINGS_SCHEMA_VERSION,
        "intent": data["intent"],
        "state": data["state"],
    }


def to_json_text(settings: AppSettingsVNext) -> str:
    return json.dumps(
        to_persisted_dict(settings),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )


def to_persisted_dict(settings: AppSettingsVNext) -> dict[str, Any]:
    return normalize_persisted_dict(to_dict(settings))


def normalize_persisted_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(data))
    state = normalized.get("state")
    if not isinstance(state, dict):
        return normalized
    entries = state.get("provider_verification")
    if not isinstance(entries, dict):
        return normalized
    for provider in _PROVIDER_VERIFICATION_FIELDS:
        entry = entries.get(provider)
        if isinstance(entry, Mapping) and entry.get("status") == "unknown":
            entries[provider] = {"status": "unknown"}
    return normalized


def from_dict(data: Mapping[str, Any]) -> AppSettingsVNext:
    _validate_persisted_types(data)
    default = AppSettingsVNext(settings_version=VNEXT_SETTINGS_SCHEMA_VERSION)
    compatible_data = _with_current_settings_version(data)
    merged = _merge_dataclass(default, compatible_data, path="settings")
    if not isinstance(merged, AppSettingsVNext):
        raise TypeError("vNext settings merge produced unexpected type")
    return with_translation_runtime_policy(merged)


def _validate_persisted_types(data: Mapping[str, Any]) -> None:
    if not isinstance(data, Mapping):
        raise ValueError("vNext settings must be a JSON object")
    _validate_finite_json_value(data, path="settings")
    _validate_raw_value_type(data, AppSettingsVNext, path="settings")


def _with_current_settings_version(data: Mapping[str, Any]) -> Mapping[str, Any]:
    compatible = copy.deepcopy(dict(data))
    compatible["settings_version"] = VNEXT_SETTINGS_SCHEMA_VERSION
    return compatible


@cache
def _schema_type_hints(schema_type: type) -> Mapping[str, Any]:
    return MappingProxyType(get_type_hints(schema_type))


def _merge_dataclass(default: object, raw: object, *, path: str) -> object:
    if not is_dataclass(default) or isinstance(default, type):
        return copy.deepcopy(raw)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path} must be a JSON object")

    kwargs: dict[str, object] = {}
    type_hints = _schema_type_hints(type(default))
    for field in fields(default):
        default_value = getattr(default, field.name)
        child_path = f"{path}.{field.name}"
        if field.name not in raw:
            kwargs[field.name] = copy.deepcopy(default_value)
            continue
        raw_value = raw[field.name]
        _validate_raw_value_type(raw_value, type_hints[field.name], path=child_path)
        if is_dataclass(default_value) and not isinstance(default_value, type):
            kwargs[field.name] = _merge_dataclass(default_value, raw_value, path=child_path)
        elif isinstance(default_value, dict):
            if not isinstance(raw_value, Mapping):
                raise ValueError(f"{child_path} must be a JSON object")
            kwargs[field.name] = copy.deepcopy(dict(raw_value))
        elif isinstance(default_value, list):
            if not isinstance(raw_value, list):
                raise ValueError(f"{child_path} must be a JSON array")
            kwargs[field.name] = copy.deepcopy(raw_value)
        else:
            kwargs[field.name] = copy.deepcopy(raw_value)

    merged = type(default)(**kwargs)
    validate = getattr(merged, "validate", None)
    if callable(validate):
        validate()
    return merged


def _validate_raw_value_type(value: object, expected: object, *, path: str) -> None:
    if expected is Any or expected is object:
        _validate_finite_json_value(value, path=path)
        return
    origin = get_origin(expected)
    args = get_args(expected)
    if origin in (types.UnionType, Union):
        for option in args:
            try:
                _validate_raw_value_type(value, option, path=path)
            except ValueError:
                continue
            return
        raise ValueError(f"{path} has an invalid type")
    if origin is Literal:
        if any(type(value) is type(option) and value == option for option in args):
            return
        raise ValueError(f"{path} has an unsupported value")
    if origin is list:
        if not isinstance(value, list):
            raise ValueError(f"{path} must be a JSON array")
        item_type = args[0] if args else object
        for index, item in enumerate(value):
            _validate_raw_value_type(item, item_type, path=f"{path}[{index}]")
        return
    if origin is tuple:
        if not isinstance(value, list | tuple):
            raise ValueError(f"{path} must be a JSON array")
        if args and args[-1] is Ellipsis:
            for index, item in enumerate(value):
                _validate_raw_value_type(item, args[0], path=f"{path}[{index}]")
        elif args:
            if len(value) != len(args):
                raise ValueError(f"{path} has an invalid array length")
            for index, (item, item_type) in enumerate(zip(value, args, strict=True)):
                _validate_raw_value_type(item, item_type, path=f"{path}[{index}]")
        return
    if origin in (dict, Mapping):
        if not isinstance(value, Mapping):
            raise ValueError(f"{path} must be a JSON object")
        key_type, value_type = args if len(args) == 2 else (object, object)
        for key, item in value.items():
            _validate_raw_value_type(key, key_type, path=f"{path}.<key>")
            _validate_raw_value_type(item, value_type, path=f"{path}.{key}")
        return
    if isinstance(expected, type) and is_dataclass(expected):
        if not isinstance(value, Mapping):
            raise ValueError(f"{path} must be a JSON object")
        type_hints = _schema_type_hints(expected)
        for field in fields(expected):
            if field.name in value:
                _validate_raw_value_type(
                    value[field.name],
                    type_hints[field.name],
                    path=f"{path}.{field.name}",
                )
        return
    if not _raw_value_matches_type(value, expected):
        raise ValueError(f"{path} has an invalid type")
    _validate_finite_json_value(value, path=path)


def _raw_value_matches_type(value: object, expected: object) -> bool:
    origin = get_origin(expected)
    if origin in (types.UnionType, Union):
        return any(_raw_value_matches_type(value, option) for option in get_args(expected))
    if origin is Literal:
        return any(type(value) is type(option) and value == option for option in get_args(expected))
    if expected is None or expected is type(None):
        return value is None
    if expected is bool:
        return type(value) is bool
    if expected is int:
        return type(value) is int
    if expected is float:
        return type(value) in (int, float) and not isinstance(value, bool)
    if expected is str:
        return isinstance(value, str)
    if expected is Any or expected is object:
        return True
    if isinstance(expected, type) and is_dataclass(expected):
        return isinstance(value, Mapping)
    if isinstance(expected, type):
        return isinstance(value, expected)
    return True


def _validate_finite_json_value(value: object, *, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} must contain a finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_finite_json_value(key, path=f"{path}.<key>")
            _validate_finite_json_value(item, path=f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _validate_finite_json_value(item, path=f"{path}[{index}]")


__all__ = [
    "from_dict",
    "to_dict",
    "to_json_text",
]
