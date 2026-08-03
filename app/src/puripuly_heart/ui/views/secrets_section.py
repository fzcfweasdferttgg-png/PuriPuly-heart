"""Secrets section mixin — API key loading, saving, and verification."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.app.wiring import create_secret_store
from puripuly_heart.ui.i18n import t

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings

logger = logging.getLogger(__name__)


class SecretsSectionMixin:
    """Secret/API-key event handlers extracted from SettingsView."""

    def _load_secrets(self, settings: AppSettings, config_path: Path) -> None:
        """Load secret values into fields."""
        try:
            store = create_secret_store(config_path=config_path)
        except Exception as exc:
            logger.warning("[Secrets] Failed to create store: %s", exc)
            self._emit_runtime_basic(f"Failed to load secrets: {exc}", level=logging.WARNING)
            return

        oc_key = store.get("openai_compatible_api_key") or ""
        backup_key = store.get("backup_api_key") or ""
        fallback_llm_key = store.get("fallback_local_llm_api_key") or ""
        local_llm_key = store.get("local_llm_api_key") or ""
        logger.info(
            "[Secrets] Loaded keys: oc=%s backup=%s fallback_llm=%s local_llm=%s",
            bool(oc_key), bool(backup_key), bool(fallback_llm_key), bool(local_llm_key),
        )

        self._openai_compatible_key.value = oc_key
        self._fallback_api_key.value = backup_key
        self._fallback_local_llm_api_key.value = fallback_llm_key
        self._local_llm_api_key.value = local_llm_key

        # Restore verification status icons from saved settings
        self._restore_api_key_icons(settings)

    def _restore_api_key_icons(self, settings: AppSettings) -> None:
        """Restore API key field icons based on saved verification status."""
        verified = settings.api_key_verified

        field_map = [
            (self._openai_compatible_key, self._openai_compatible_key.value, verified.is_verified("openai_compatible")),
            (self._fallback_api_key, self._fallback_api_key.value, verified.is_verified("backup_openai_compatible")),
            (self._local_llm_api_key, self._local_llm_api_key.value, verified.is_verified("local_llm")),
        ]

        for field, has_key, is_verified in field_map:
            if not has_key:
                field._set_status("idle")
                field._last_verified_hash = ""
            elif is_verified:
                field._set_status("success")
                field._last_verified_hash = field._get_key_hash(has_key)
            else:
                field._set_status("error")
                field._last_verified_hash = ""

    def _write_secret_value(self, key: str, value: str) -> bool:
        if not self._settings or not self._config_path:
            logger.warning("[Secrets] Write skipped: no settings or config_path")
            return False

        try:
            store = create_secret_store(config_path=self._config_path)
            if value:
                store.set(key, value)
                logger.info("[Secrets] Saved key=%s len=%d", key, len(value))
            else:
                store.delete(key)
                logger.info("[Secrets] Deleted key=%s", key)
            return True
        except Exception as exc:
            logger.warning("[Secrets] Failed to write key=%s: %s", key, exc)
            self._emit_runtime_basic(
                f"Failed to update secret {key}: {type(exc).__name__}",
                level=logging.WARNING,
            )
            return False

    def _on_local_llm_secret_change(self, key: str, value: str) -> None:
        if key != "local_llm_api_key":
            return
        stripped = value.strip()
        if not self._write_secret_value(key, stripped):
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.api_key.save_failed"), ft.Colors.RED_400)
            return
        self._local_llm_api_key.value = stripped
        if self.on_local_llm_secret_changed:
            self.on_local_llm_secret_changed()

    def _on_secret_change(self, key: str, value: str) -> None:
        if not self._settings or not self._config_path:
            return

        if not self._write_secret_value(key, value):
            return
        if not value and self.on_secret_cleared:
            with contextlib.suppress(Exception):
                self.on_secret_cleared(key)

    async def _verify_key(self, provider: str, key: str, *, base_url: str | None = None) -> tuple[bool, str]:
        """Verify API key."""
        if self.on_verify_api_key:
            result = await self.on_verify_api_key(provider, key, base_url=base_url)
            return result
        return False, "Verification not available"
