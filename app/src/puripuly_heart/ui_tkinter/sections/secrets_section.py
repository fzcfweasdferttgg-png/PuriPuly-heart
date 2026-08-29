"""API keys section.

Grouped by provider with masked entry fields and verify buttons.
Verification state stored in ``controller.settings.api_key_verified``.
Actual key storage is delegated to ``controller.write_secret``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection

logger = logging.getLogger(__name__)

# Provider keys that require API key management
_SECRET_PROVIDERS: list[tuple[str, str]] = [
    ("openai_compatible", "OpenAI-Compatible"),
    ("local_llm", "Local LLM"),
    ("backup_openai_compatible", "Backup OpenAI"),
]


class SecretsSection(CollapsibleSection):
    """API key management grouped by provider."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, t("settings.section.secrets", default="API Keys"), **kwargs)
        self._controller = controller
        self._entries: dict[str, ctk.CTkEntry] = {}
        self._build()

    def _build(self) -> None:
        verified = self._controller.settings.api_key_verified

        for provider_key, display_name in _SECRET_PROVIDERS:
            # --- Masked entry ---
            entry = ctk.CTkEntry(self._content, width=240, show="*")
            entry.bind("<FocusOut>", lambda _, pk=provider_key: self._on_key_change(pk))
            self._entries[provider_key] = entry
            self.add_row(display_name, entry)
            self._add_debug_label(entry, f"input.secret_{provider_key}")

            # --- Verify button ---
            status = "✓" if verified.is_verified(provider_key) else "—"
            btn = ctk.CTkButton(
                self._content,
                text=f"{t('settings.verify', default='Verify')} {status}",
                width=100,
                command=lambda pk=provider_key: self._on_verify(pk),
                fg_color=th.COLOR_PRIMARY,
                hover_color=th.COLOR_PRIMARY_CONTAINER,
            )
            self.add_row("", btn)
            self._add_debug_label(btn, f"btn.verify_{provider_key}")

    # --- Handlers ---

    def _on_key_change(self, provider_key: str) -> None:
        """Store API key via controller secret management."""
        entry = self._entries.get(provider_key)
        if entry is None:
            return
        key_value = entry.get().strip()
        if not key_value:
            return
        config_path = getattr(self._controller, "config_path", None)
        if config_path is None:
            return
        try:
            self._controller.write_secret(provider_key, key_value, config_path)
        except Exception:
            logger.debug("[Secrets] Failed to write key=%s", provider_key, exc_info=True)

    def _on_verify(self, provider_key: str) -> None:
        """Verify the API key for the given provider (async-safe)."""
        entry = self._entries.get(provider_key)
        if entry is None:
            return
        key_value = entry.get().strip()
        if not key_value:
            return

        # verify_api_key is async — dispatch to controller's event loop
        loop = getattr(self._controller, "_async_loop", None)

        async def _verify() -> None:
            try:
                result, _msg = await self._controller.verify_api_key(provider_key, key_value)
                self._controller.settings.api_key_verified.set_verified(provider_key, bool(result))
            except Exception:
                logger.debug("[Secrets] Verify failed for %s", provider_key, exc_info=True)
                self._controller.settings.api_key_verified.set_verified(provider_key, False)

        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(_verify(), loop)
        else:
            threading.Thread(target=lambda: asyncio.run(_verify()), daemon=True).start()
