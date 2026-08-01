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
            store = create_secret_store(settings.secrets, config_path=config_path)
        except Exception as exc:
            self._emit_runtime_basic(f"Failed to load secrets: {exc}", level=logging.WARNING)
            return

        self._openai_compatible_key.value = store.get("openai_compatible_api_key") or ""
        self._backup_api_key.value = store.get("backup_api_key") or ""
        self._local_llm_api_key.value = store.get("local_llm_api_key") or ""

        # Restore verification status icons from saved settings
        self._restore_api_key_icons(settings)

    def _restore_api_key_icons(self, settings: AppSettings) -> None:
        """Restore API key field icons based on saved verification status."""
        verified = settings.api_key_verified

        field_map = [
            (self._openai_compatible_key, self._openai_compatible_key.value, verified.openai_compatible),
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
            return False

        try:
            store = create_secret_store(self._settings.secrets, config_path=self._config_path)
            if value:
                store.set(key, value)
            else:
                store.delete(key)
            return True
        except Exception as exc:
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

    async def _verify_key(self, provider: str, key: str) -> tuple[bool, str]:
        """Verify API key."""
        if self.on_verify_api_key:
            result = await self.on_verify_api_key(provider, key)
            return result
        return False, "Verification not available"
