from __future__ import annotations

import json
import logging
from pathlib import Path

from puripuly_heart.ports.secrets import SecretStore

__all__ = ["SecretStore", "PlainFileSecretStore", "migrate_encrypted_to_plain"]

logger = logging.getLogger(__name__)


class PlainFileSecretStore:
    """Stores secrets as plain JSON.  Implements the ``SecretStore`` port."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, str] = {}
        self._load()

    # -- SecretStore protocol ------------------------------------------------

    def get(self, key: str) -> str | None:
        return self._items.get(key)

    def set(self, key: str, value: str) -> None:
        self._items[key] = value
        self._save()

    def delete(self, key: str) -> None:
        if key in self._items:
            del self._items[key]
            self._save()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[Secrets] Failed to read %s: %s", self.path, exc)
            return
        if isinstance(raw, dict) and "items" in raw:
            self._items = dict(raw["items"])

    def _save(self) -> None:
        _atomic_write_json(self.path, {"items": self._items})


# -- migration from encrypted format ----------------------------------------

def migrate_encrypted_to_plain(
    path: Path,
    *,
    passphrase: str,
) -> None:
    """One-shot migration: decrypt legacy ``secrets.json`` → plain JSON.

    If the file does not exist or is already plain (no ``salt`` key), this is a
    no-op.  After successful migration the ``.secret_key`` file is deleted.
    """
    if not path.exists():
        return

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    if not isinstance(raw, dict) or "salt" not in raw:
        # Already plain or unrecognised — nothing to do.
        return

    # --- decrypt using the old logic (inline, no class needed) ---------------
    import base64

    try:
        from cryptography.fernet import Fernet, InvalidToken  # type: ignore[import-untyped]
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "[Secrets] Cannot migrate encrypted secrets — cryptography not installed. "
            "Install 'cryptography' and restart to migrate. Leaving encrypted file intact."
        )
        return

    salt = base64.b64decode(raw["salt"])
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
    fernet = Fernet(key)

    plain_items: dict[str, str] = {}
    for k, token in raw.get("items", {}).items():
        try:
            plain_items[k] = fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, Exception) as exc:
            logger.warning("[Secrets] Failed to decrypt key '%s': %s", k, exc)

    _atomic_write_json(path, {"items": plain_items})
    logger.info("[Secrets] Migrated %d secrets from encrypted to plain JSON", len(plain_items))

    # Clean up the passphrase file.
    key_file = path.parent / ".secret_key"
    if key_file.exists():
        try:
            key_file.unlink()
            logger.info("[Secrets] Removed .secret_key")
        except OSError as exc:
            logger.warning("[Secrets] Failed to remove .secret_key: %s", exc)


# -- helpers -----------------------------------------------------------------

def _atomic_write_json(path: Path, data: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.error("[Secrets] Failed to write secrets file %s: %s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
