"""File I/O for settings persistence — atomic writes, JSON serialization."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings

from puripuly_heart.config.settings.constants import SETTINGS_SCHEMA_VERSION

logger = logging.getLogger(__name__)


def _atomic_write_text(path: Path, content: str, *, encoding: str) -> None:
    """Write text to path atomically via temp file + rename."""
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


def save_settings(path: Path, settings: AppSettings) -> None:
    """Persist AppSettings to JSON file atomically."""
    from puripuly_heart.config.settings.base import to_dict

    settings.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        path,
        json.dumps(to_dict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_settings(path: Path) -> AppSettings:
    """Load AppSettings from JSON file, with migration if needed."""
    from puripuly_heart.config.settings.base import (
        _coerce_int,
        _migrate_settings_dict,
        from_dict,
    )

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


def _write_settings_migration_backup(path: Path, content: str, source_version: int) -> Path:
    """Write a backup of pre-migration settings."""
    backup_stem = f"{path.name}.v{source_version}.pre-v{SETTINGS_SCHEMA_VERSION}.bak"
    backup_path = path.with_name(backup_stem)
    index = 1
    while backup_path.exists():
        backup_path = path.with_name(f"{backup_stem}.{index}")
        index += 1
    _atomic_write_text(backup_path, content, encoding="utf-8")
    return backup_path
