from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from puripuly_heart.config.settings import (
    AppSettings,
    OpenRouterCredentialSource,
    TranslationConnection,
)
from puripuly_heart.core.storage.secrets import SecretStore

logger = logging.getLogger(__name__)

OPENROUTER_BYOK_API_KEY_SECRET = "openrouter_api_key"
OPENROUTER_BYOK_API_KEY_ENV = "OPENROUTER_API_KEY"


@dataclass(frozen=True, slots=True)
class OpenRouterCredentialResolution:
    selected_source: OpenRouterCredentialSource
    api_key: str | None


def resolve_openrouter_credentials(
    settings: AppSettings,
    *,
    secrets: SecretStore,
    request_intent: str | None = None,
) -> OpenRouterCredentialResolution:
    selected_source = settings.openrouter.selected_source
    if selected_source == OpenRouterCredentialSource.NONE:
        return OpenRouterCredentialResolution(selected_source=selected_source, api_key=None)

    return OpenRouterCredentialResolution(
        selected_source=OpenRouterCredentialSource.BYOK,
        api_key=_get_byok_api_key(secrets),
    )


def require_openrouter_execution_api_key(settings: AppSettings, *, secrets: SecretStore) -> str:
    resolution = resolve_openrouter_credentials(settings, secrets=secrets)
    if resolution.api_key is not None:
        return resolution.api_key
    if resolution.selected_source == OpenRouterCredentialSource.NONE:
        raise ValueError("OpenRouter selected source must not be `none` for execution")
    raise ValueError(
        f"Missing secret `{OPENROUTER_BYOK_API_KEY_SECRET}` (or env var {OPENROUTER_BYOK_API_KEY_ENV})"
    )


def _get_byok_api_key(secrets: SecretStore) -> str | None:
    stored_key = _normalize_secret(secrets.get(OPENROUTER_BYOK_API_KEY_SECRET))
    if stored_key is not None:
        return stored_key
    return _normalize_secret(os.getenv(OPENROUTER_BYOK_API_KEY_ENV))


def _normalize_secret(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
