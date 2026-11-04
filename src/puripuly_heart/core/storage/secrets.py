from __future__ import annotations

import errno
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


@dataclass(slots=True)
class InMemorySecretStore:
    _items: dict[str, str]

    def __init__(self) -> None:
        self._items = {}

    def get(self, key: str) -> str | None:
        return self._items.get(key)

    def set(self, key: str, value: str) -> None:
        self._items[key] = value

    def delete(self, key: str) -> None:
        self._items.pop(key, None)


def mask_secret(value: str, *, unmasked_prefix: int = 3) -> str:
    if not value:
        return value
    if len(value) <= unmasked_prefix:
        return "*" * len(value)
    return value[:unmasked_prefix] + "****"


@dataclass(slots=True)
class PlaintextFileSecretStore:
    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> str | None:
        with _secret_file_lock(self.path):
            return self._read().get(key)

    def set(self, key: str, value: str) -> None:
        with _secret_file_lock(self.path):
            raw = self._read()
            raw[key] = value
            _atomic_write_json(self.path, raw)

    def delete(self, key: str) -> None:
        with _secret_file_lock(self.path):
            raw = self._read()
            if key in raw:
                del raw[key]
                _atomic_write_json(self.path, raw)

    def _read(self) -> dict[str, str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("secrets file is corrupted")
        return {str(k): str(v) for k, v in raw.items()}


@contextmanager
def _secret_file_lock(path: Path) -> Iterator[None]:
    with path.with_suffix(path.suffix + ".lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, data: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
