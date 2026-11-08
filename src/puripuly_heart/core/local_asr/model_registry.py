"""Unified registry of managed models beyond the built-in manifest catalog.

Every model the user brings in — found through source search or added as a local
GGUF file — lands under the model root and gets one plain record here. There is
no difference at use time where a model came from: the manager, the settings
picker and the runtime all resolve registry models exactly like catalog ones.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

REGISTRY_FILENAME = "registry.json"
REGISTRY_VERSION = 1

REGISTRY_STATUS_INSTALLED = "installed"
REGISTRY_STATUS_MISSING = "missing"
REGISTRY_STATUS_INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class RegistryModelFile:
    name: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class RegistryModelRecord:
    model_id: str
    name: str
    files: tuple[RegistryModelFile, ...]
    origin: str = "local"
    source: str | None = None

    @property
    def size_bytes(self) -> int:
        return sum(file.size_bytes for file in self.files)

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "name": self.name,
            "files": [{"name": f.name, "size_bytes": f.size_bytes} for f in self.files],
            "origin": self.origin,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RegistryModelRecord":
        files = tuple(
            RegistryModelFile(
                name=str(item["name"]),
                size_bytes=int(item.get("size_bytes", 0) or 0),
            )
            for item in data.get("files", ())
        )
        return cls(
            model_id=str(data["model_id"]),
            name=str(data.get("name") or data["model_id"]),
            files=files,
            origin=str(data.get("origin") or "local"),
            source=(str(data["source"]) if data.get("source") else None),
        )


class ModelRegistryError(RuntimeError):
    def __init__(self, message: str, *, failure_code: str = "unknown") -> None:
        super().__init__(message)
        self.failure_code = failure_code


def sanitize_model_id(raw: str) -> str:
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", raw.lower()).strip("-._")
    return cleaned or "model"


class ModelRegistry:
    """Plain JSON registry of models installed outside the manifest catalog."""

    def __init__(self, model_root: Path) -> None:
        self._root = Path(model_root)
        self._path = self._root / REGISTRY_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> tuple[RegistryModelRecord, ...]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ()
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return ()
        records: list[RegistryModelRecord] = []
        for item in models:
            try:
                records.append(RegistryModelRecord.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(records)

    def save(self, records: tuple[RegistryModelRecord, ...]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": REGISTRY_VERSION,
            "models": [record.to_dict() for record in records],
        }
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def get(self, model_id: str) -> RegistryModelRecord | None:
        for record in self.load():
            if record.model_id == model_id:
                return record
        return None

    def add(self, record: RegistryModelRecord) -> None:
        records = [item for item in self.load() if item.model_id != record.model_id]
        records.append(record)
        self.save(tuple(records))

    def remove(self, model_id: str) -> None:
        records = tuple(item for item in self.load() if item.model_id != model_id)
        self.save(records)

    def allocate_model_id(self, base: str) -> str:
        base_id = sanitize_model_id(base)
        taken = {record.model_id for record in self.load()}
        candidate = base_id
        suffix = 2
        while candidate in taken or (self._root / candidate).exists():
            candidate = f"{base_id}-{suffix}"
            suffix += 1
        return candidate

    def status(self, record: RegistryModelRecord) -> str:
        directory = self._root / record.model_id
        if not directory.is_dir():
            return REGISTRY_STATUS_MISSING
        missing = False
        for file in record.files:
            path = directory / file.name
            if not path.is_file():
                missing = True
                continue
            if file.size_bytes and path.stat().st_size != file.size_bytes:
                return REGISTRY_STATUS_INVALID
        return REGISTRY_STATUS_MISSING if missing else REGISTRY_STATUS_INSTALLED

    def model_file_path(self, record: RegistryModelRecord) -> Path:
        return self._root / record.model_id / record.files[0].name


def default_model_registry() -> ModelRegistry:
    from puripuly_heart.core.local_asr.local_stt_assets import (
        default_local_stt_model_root,
    )

    return ModelRegistry(default_local_stt_model_root())


__all__ = [
    "REGISTRY_FILENAME",
    "REGISTRY_STATUS_INSTALLED",
    "REGISTRY_STATUS_INVALID",
    "REGISTRY_STATUS_MISSING",
    "ModelRegistry",
    "ModelRegistryError",
    "RegistryModelFile",
    "RegistryModelRecord",
    "default_model_registry",
    "sanitize_model_id",
]
