from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from puripuly_heart.config.settings_vnext import defaults, migration, serialization
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.diagnostics import emit_failure, safe_exception_fields
from puripuly_heart.core.messages import ErrorDiagnostics

logger = logging.getLogger(__name__)


class SettingsPersistenceStatus(str, Enum):
    SUCCESS = "success"
    PARSE_FAILED = "parse_failed"
    MIGRATION_FAILED = "migration_failed"
    SAVE_FAILED = "save_failed"


@dataclass(frozen=True, slots=True)
class SettingsPersistenceError:
    stage: SettingsPersistenceStatus
    message: str
    diagnostics: ErrorDiagnostics | None = None
    secondary_diagnostics: tuple[ErrorDiagnostics, ...] = ()
    secondary_omitted_count: int = 0

    def __post_init__(self) -> None:
        excess = max(0, len(self.secondary_diagnostics) - 4)
        if excess:
            object.__setattr__(self, "secondary_diagnostics", self.secondary_diagnostics[:4])
            object.__setattr__(
                self, "secondary_omitted_count", self.secondary_omitted_count + excess
            )
        if self.diagnostics is None:
            object.__setattr__(
                self,
                "diagnostics",
                ErrorDiagnostics(
                    component="settings_repository",
                    operation=(
                        "save" if self.stage == SettingsPersistenceStatus.SAVE_FAILED else "load"
                    ),
                    code=(
                        "settings_save_failed"
                        if self.stage == SettingsPersistenceStatus.SAVE_FAILED
                        else f"settings_{self.stage.value}"
                    ),
                    category="transaction",
                    visibility="basic",
                    content_policy="metadata_only",
                    status_code=None,
                    retry_after_ms=None,
                    fields={
                        "phase": (
                            "settings_save"
                            if self.stage == SettingsPersistenceStatus.SAVE_FAILED
                            else self.stage.value
                        ),
                    },
                ),
            )


@dataclass(frozen=True, slots=True)
class VNextSettingsLoadResult:
    status: SettingsPersistenceStatus
    settings: AppSettingsVNext | None = None
    error: SettingsPersistenceError | None = None

    @property
    def ok(self) -> bool:
        return self.status == SettingsPersistenceStatus.SUCCESS


@dataclass(frozen=True, slots=True)
class VNextSettingsSaveResult:
    status: SettingsPersistenceStatus
    error: SettingsPersistenceError | None = None

    @property
    def ok(self) -> bool:
        return self.status == SettingsPersistenceStatus.SUCCESS


class _SettingsWriteFailure(Exception):
    def __init__(self, error: SettingsPersistenceError) -> None:
        self.error = error
        super().__init__("Settings disk operation failed")


def load_vnext_settings(path: Path) -> VNextSettingsLoadResult:
    try:
        source_text = path.read_bytes().decode("utf-8")
        raw = json.loads(source_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        _log_load_failure("unknown", SettingsPersistenceStatus.PARSE_FAILED)
        return VNextSettingsLoadResult(
            status=SettingsPersistenceStatus.PARSE_FAILED,
            error=_error(SettingsPersistenceStatus.PARSE_FAILED, exc),
        )
    if not isinstance(raw, dict):
        _log_load_failure("unknown", SettingsPersistenceStatus.PARSE_FAILED)
        return VNextSettingsLoadResult(
            status=SettingsPersistenceStatus.PARSE_FAILED,
            error=SettingsPersistenceError(
                SettingsPersistenceStatus.PARSE_FAILED,
                "settings file must contain a JSON object",
            ),
        )

    source_shape = "canonical" if migration.is_vnext_settings_dict(raw) else "pre_vnext"
    if source_shape == "canonical":
        try:
            raw = json.loads(
                source_text,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            _log_load_failure(source_shape, SettingsPersistenceStatus.PARSE_FAILED)
            return VNextSettingsLoadResult(
                status=SettingsPersistenceStatus.PARSE_FAILED,
                error=_error(SettingsPersistenceStatus.PARSE_FAILED, exc),
            )

    try:
        settings = (
            migration.from_dict(raw)
            if source_shape == "canonical"
            else defaults.new_settings_for_reset()
        )
    except Exception as exc:
        _log_load_failure(source_shape, SettingsPersistenceStatus.MIGRATION_FAILED)
        return VNextSettingsLoadResult(
            status=SettingsPersistenceStatus.MIGRATION_FAILED,
            error=_error(SettingsPersistenceStatus.MIGRATION_FAILED, exc),
        )

    if source_shape == "pre_vnext":
        _log_pre_vnext_reset()

    return VNextSettingsLoadResult(
        status=SettingsPersistenceStatus.SUCCESS,
        settings=settings,
    )


def save_vnext_settings(path: Path, settings: AppSettingsVNext) -> VNextSettingsSaveResult:
    try:
        _save_vnext_settings_or_raise(path, settings)
    except Exception as exc:
        return VNextSettingsSaveResult(
            status=SettingsPersistenceStatus.SAVE_FAILED,
            error=_error(SettingsPersistenceStatus.SAVE_FAILED, exc),
        )
    return VNextSettingsSaveResult(status=SettingsPersistenceStatus.SUCCESS)


def _save_vnext_settings_or_raise(path: Path, settings: AppSettingsVNext) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = serialization.to_json_text(settings)
    _validate_canonical_text(content, settings)
    _atomic_write_text(path, content, encoding="utf-8")


def _atomic_write_text(path: Path, content: str, *, encoding: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(temporary_name)
    phase = "settings_write"
    try:
        with os.fdopen(descriptor, "w", encoding=encoding) as handle:
            handle.write(content)
        phase = "settings_validate"
        _validate_canonical_text(tmp_path.read_text(encoding=encoding), None)
        phase = "settings_replace"
        tmp_path.replace(path)
    except Exception as exc:
        error = safe_persistence_error(SettingsPersistenceStatus.SAVE_FAILED, exc, phase=phase)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception as cleanup_exc:
            secondary = safe_persistence_error(
                SettingsPersistenceStatus.SAVE_FAILED, cleanup_exc, phase="temporary_cleanup"
            ).diagnostics
            error = replace(
                error,
                secondary_diagnostics=(
                    replace(
                        secondary,
                        cause_id=error.diagnostics.diagnostic_id,
                    ),
                ),
            )
        raise _SettingsWriteFailure(error) from exc


def _validate_canonical_text(content: str, expected: AppSettingsVNext | None) -> None:
    raw = json.loads(content, parse_constant=_reject_nonstandard_json_constant)
    if not isinstance(raw, dict) or not migration.is_vnext_settings_dict(raw):
        raise ValueError("persisted canonical settings must contain intent and state")
    restored = migration.from_dict(raw)
    if expected is not None and serialization.to_dict(restored) != serialization.to_dict(expected):
        raise ValueError("persisted canonical settings failed semantic validation")


def _reject_nonstandard_json_constant(value: str) -> object:
    raise ValueError(f"non-standard JSON constant: {value}")


def _log_load_failure(
    source_shape: str,
    status: SettingsPersistenceStatus,
) -> None:
    logger.warning(
        "settings_load source_shape=%s status=failure failure_category=%s",
        source_shape,
        status.value,
    )


def _log_pre_vnext_reset() -> None:
    emit_failure(
        logger,
        ErrorDiagnostics(
            component="settings_repository",
            operation="load",
            code="settings_pre_vnext_reset",
            category="transaction",
            visibility="basic",
            content_policy="metadata_only",
            status_code=None,
            retry_after_ms=None,
            fields={"phase": "load"},
        ),
        severity="warning",
        disposition="degraded",
        stage="load",
    )


def safe_persistence_error(
    status: SettingsPersistenceStatus,
    exc: Exception,
    *,
    phase: str | None = None,
) -> SettingsPersistenceError:
    if isinstance(exc, _SettingsWriteFailure):
        return exc.error
    fields = safe_exception_fields(exc)
    return SettingsPersistenceError(
        status,
        f"{status.value}:{fields['exception_class']}",
        diagnostics=ErrorDiagnostics(
            component="settings_repository",
            operation="save" if status == SettingsPersistenceStatus.SAVE_FAILED else "load",
            code=(
                "settings_save_failed"
                if status == SettingsPersistenceStatus.SAVE_FAILED
                else f"settings_{status.value}"
            ),
            category="transaction",
            visibility="basic",
            content_policy="metadata_only",
            status_code=None,
            retry_after_ms=None,
            fields={
                "phase": (
                    phase or "settings_save"
                    if status == SettingsPersistenceStatus.SAVE_FAILED
                    else status.value
                ),
                **fields,
            },
        ),
    )


def _error(status: SettingsPersistenceStatus, exc: Exception) -> SettingsPersistenceError:
    return safe_persistence_error(status, exc)


__all__ = [
    "SettingsPersistenceError",
    "SettingsPersistenceStatus",
    "VNextSettingsLoadResult",
    "VNextSettingsSaveResult",
    "load_vnext_settings",
    "save_vnext_settings",
]
