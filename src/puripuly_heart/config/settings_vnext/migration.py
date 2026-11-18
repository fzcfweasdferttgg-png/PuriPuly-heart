from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from puripuly_heart.config.settings_vnext import serialization
from puripuly_heart.config.settings_vnext.schema import (
    VNEXT_SETTINGS_SCHEMA_VERSION,
    AppSettingsVNext,
)


def is_vnext_shape_dict(data: Mapping[str, Any]) -> bool:
    return isinstance(data, Mapping) and ("intent" in data or "state" in data)


def is_vnext_settings_dict(data: Mapping[str, Any]) -> bool:
    return is_vnext_shape_dict(data)


def from_dict(data: Mapping[str, Any]) -> AppSettingsVNext:
    if not isinstance(data, Mapping):
        raise ValueError("settings must be a JSON object")
    if not is_vnext_settings_dict(data):
        raise ValueError("canonical settings must contain intent and state")
    _validate_vnext_top_level_shape(data)
    _validate_supported_vnext_version(data)
    serialization._validate_persisted_types(data)
    return serialization.from_dict(data)


def _apply_changed_mapping_values(
    target: dict[str, Any],
    baseline: Mapping[str, object],
    next_values: Mapping[str, object],
) -> None:
    if "kind" in baseline and "kind" in next_values and baseline["kind"] != next_values["kind"]:
        target.clear()
        target.update(copy.deepcopy(dict(next_values)))
        return
    for key in baseline:
        if key not in next_values:
            target.pop(key, None)
    for key, next_value in next_values.items():
        previous_value = baseline.get(key)
        if isinstance(previous_value, Mapping) and isinstance(next_value, Mapping):
            target_value = target.get(key)
            if not isinstance(target_value, dict):
                target_value = {}
                target[key] = target_value
            _apply_changed_mapping_values(target_value, previous_value, next_value)
        elif previous_value != next_value:
            target[key] = copy.deepcopy(next_value)


def apply_canonical_delta(
    canonical: AppSettingsVNext,
    base_settings: AppSettingsVNext,
    next_settings: AppSettingsVNext,
) -> AppSettingsVNext:
    canonical_data = serialization.to_dict(canonical)
    base_data = serialization.to_dict(base_settings)
    next_data = serialization.to_dict(next_settings)
    original_verification = copy.deepcopy(canonical_data["state"]["provider_verification"])
    _apply_changed_mapping_values(canonical_data, base_data, next_data)
    verification_entries = canonical_data["state"]["provider_verification"]
    base_verification = base_data["state"]["provider_verification"]
    next_verification = next_data["state"]["provider_verification"]
    if (
        isinstance(verification_entries, dict)
        and isinstance(base_verification, dict)
        and isinstance(next_verification, dict)
        and isinstance(original_verification, dict)
    ):
        for provider, next_entry in next_verification.items():
            previous_entry = base_verification.get(provider)
            original_entry = original_verification.get(provider)
            originally_verified = (
                isinstance(original_entry, Mapping) and original_entry.get("status") == "verified"
            )
            was_verified = (
                isinstance(previous_entry, Mapping) and previous_entry.get("status") == "verified"
            )
            remains_verified = (
                isinstance(next_entry, Mapping) and next_entry.get("status") == "verified"
            )
            if remains_verified and not originally_verified:
                verification_entries[provider] = (
                    copy.deepcopy(original_entry)
                    if isinstance(original_entry, dict)
                    else {"status": "unknown"}
                )
            elif was_verified and not remains_verified:
                verification_entries[provider] = {"status": "unknown"}
    return serialization.from_dict(canonical_data)


def merge_canonical_payload(
    settings: AppSettingsVNext,
    payload: Mapping[str, Any],
) -> AppSettingsVNext:
    data = serialization.to_dict(settings)
    thawed = _json_compatible_mapping(payload)
    if not isinstance(thawed, dict):
        raise TypeError("canonical payload merge requires a mapping")
    intent_payload = thawed.get("intent")
    if isinstance(intent_payload, Mapping) and isinstance(data.get("intent"), dict):
        _merge_known_mapping(data["intent"], intent_payload)
    state_payload = thawed.get("state")
    if isinstance(state_payload, Mapping) and isinstance(data.get("state"), dict):
        _merge_known_mapping(data["state"], state_payload)
    return serialization.from_dict(data)


def _json_compatible_mapping(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_compatible_mapping(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible_mapping(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible_mapping(item) for item in value]
    return copy.deepcopy(value)


def _merge_known_mapping(
    target: dict[str, Any],
    incoming: Mapping[str, object],
) -> None:
    for key, value in incoming.items():
        if key not in target:
            continue
        current = target[key]
        if isinstance(current, dict) and isinstance(value, Mapping):
            _merge_known_mapping(current, value)
        else:
            target[key] = copy.deepcopy(value)


def _validate_vnext_top_level_shape(data: Mapping[str, Any]) -> None:
    for section in ("intent", "state"):
        if section not in data:
            raise ValueError(f"vNext settings missing required top-level {section!r} object")
        if not isinstance(data[section], Mapping):
            raise ValueError(f"vNext settings top-level {section!r} must be a JSON object")


def _validate_supported_vnext_version(data: Mapping[str, Any]) -> None:
    version = data.get("settings_version")
    if type(version) is not int or version < 1:
        raise ValueError("canonical settings_version must be a positive integer")
    if version > VNEXT_SETTINGS_SCHEMA_VERSION:
        raise ValueError(f"unsupported canonical settings_version: {version}")


__all__ = [
    "apply_canonical_delta",
    "from_dict",
    "is_vnext_shape_dict",
    "is_vnext_settings_dict",
    "merge_canonical_payload",
]
