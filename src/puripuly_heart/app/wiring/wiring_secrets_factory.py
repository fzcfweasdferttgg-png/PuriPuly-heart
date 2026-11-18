from __future__ import annotations

import os
from pathlib import Path

from puripuly_heart.config.paths import STABLE_APP_DIR_NAME
from puripuly_heart.config.settings_vnext.schema import SecretsIntent
from puripuly_heart.core.diagnostics import safe_exception_fields
from puripuly_heart.core.messages import ErrorDiagnostics
from puripuly_heart.core.storage.secrets import PlaintextFileSecretStore, SecretStore

SECRETS_PASSPHRASE_ENV = "PURIPULY_HEART_SECRETS_PASSPHRASE"
STABLE_KEYRING_SERVICE_NAME = STABLE_APP_DIR_NAME


class SecretStoreCreationError(ValueError):
    def __init__(self, code: str, exception: BaseException | None = None) -> None:
        self.diagnostics = ErrorDiagnostics(
            component="settings_secrets",
            operation="create_store",
            code=code,
            category="transaction",
            visibility="basic",
            content_policy="metadata_only",
            status_code=None,
            retry_after_ms=None,
            fields={
                "phase": "create_store",
                **(safe_exception_fields(exception) if exception is not None else {}),
            },
        )
        super().__init__(code)


def create_secret_store(
    settings: SecretsIntent,
    *,
    config_path: Path,
    passphrase: str | None = None,
    keyring_service_name: str = STABLE_KEYRING_SERVICE_NAME,
) -> SecretStore:
    del passphrase, keyring_service_name
    path = Path(settings.encrypted_file_path)
    if not path.is_absolute():
        path = config_path.parent / path
    try:
        return PlaintextFileSecretStore(path=path)
    except Exception as exc:
        raise SecretStoreCreationError("settings_secret_failed", exc) from exc


def _get_secret(
    secrets: SecretStore,
    *,
    key: str,
    env_var: str,
) -> str | None:
    value = secrets.get(key)
    if value:
        return value
    env = os.getenv(env_var)
    if env:
        return env
    return None


def require_secret(
    secrets: SecretStore,
    *,
    key: str,
    env_var: str,
) -> str:
    value = _get_secret(secrets, key=key, env_var=env_var)
    if value:
        return value
    raise ValueError(f"Missing secret `{key}` (or env var {env_var})")
