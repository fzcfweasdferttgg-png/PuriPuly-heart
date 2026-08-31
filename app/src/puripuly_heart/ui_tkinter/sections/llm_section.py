"""LLM provider settings section.

Translation model selector, OpenAI-compatible base URL / model, and fallback
toggle. Fields map to ``controller.settings.provider.llm``,
``controller.settings.provider.openai_compatible.*``, and
``controller.settings.backup_translation.*``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.domain.providers import LLMProviderName
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection

logger = logging.getLogger(__name__)

_LLM_LABELS: dict[str, str] = {
    "none": "None",
    "local_llm": "Local LLM",
    "openai_compatible": "OpenAI-Compatible",
}


class LLMSection(CollapsibleSection):
    """LLM provider, OpenAI-compatible connection, and fallback settings."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(
            master,
            t("tk.settings.section.llm", default="LLM Providers"),
            title_i18n_key="tk.settings.section.llm",
            title_default="LLM Providers",
            **kwargs,
        )
        self._controller = controller
        self._build()

    def _build(self) -> None:
        provider = self._controller.settings.provider
        oai = provider.openai_compatible
        backup = self._controller.settings.backup_translation

        # --- LLM Provider ---
        llm_values = [e.value for e in LLMProviderName]
        llm_names = [_LLM_LABELS.get(v, v) for v in llm_values]
        self._llm_values = llm_values
        self._llm_names = llm_names

        def _make_llm_menu(row):
            menu = ctk.CTkOptionMenu(
                row,
                values=llm_names,
                width=200,
                command=lambda _: self._on_llm_change(),
                fg_color=th.COLOR_PRIMARY,
                button_color=th.COLOR_PRIMARY,
                button_hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_ON_PRIMARY,
                dropdown_fg_color=th.COLOR_SURFACE,
                dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
                dropdown_text_color=th.COLOR_TEXT,
            )
            menu.set(_LLM_LABELS.get(provider.llm.value, provider.llm.value))
            return menu

        self._llm_menu = self.add_row(
            t("tk.settings.llm_provider", default="LLM Provider"),
            _make_llm_menu,
            label_i18n_key="tk.settings.llm_provider",
            label_default="LLM Provider",
            label_id="label.llm_provider",
            control_id="dropdown.llm_provider",
        )

        # --- API Key (masked) ---
        def _make_api_key_entry(row):
            entry = ctk.CTkEntry(row, width=240, show="*")
            entry.bind("<FocusOut>", lambda _: self._apply_api_key())
            return entry

        self._api_key_entry = self.add_row(
            t("tk.settings.api_key", default="API Key"),
            _make_api_key_entry,
            label_i18n_key="tk.settings.api_key",
            label_default="API Key",
            label_id="label.api_key",
            control_id="input.api_key",
        )

        # --- Verify button ---
        self._verify_btn = ctk.CTkButton(
            self._content,
            text=t("tk.settings.verify", default="Verify"),
            width=80,
            command=self._on_verify,
            fg_color=th.COLOR_PRIMARY,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
        )
        self.add_row(
            "",
            self._verify_btn,
            control_id="btn.verify_api",
            full_width=True,
        )
        self._register_translatable(
            self._verify_btn,
            "tk.settings.verify",
            "Verify",
            formatter=self._format_verify,
        )

        # --- Base URL ---
        def _make_base_url_entry(row):
            entry = ctk.CTkEntry(row, width=300)
            entry.insert(0, oai.base_url)
            entry.bind("<FocusOut>", lambda _: self._apply_base_url())
            entry.bind("<Return>", lambda _: self._apply_base_url())
            return entry

        self._base_url_entry = self.add_row(
            t("tk.settings.base_url", default="Base URL"),
            _make_base_url_entry,
            label_i18n_key="tk.settings.base_url",
            label_default="Base URL",
            label_id="label.base_url",
            control_id="input.base_url",
        )

        # --- Model ---
        def _make_model_entry(row):
            entry = ctk.CTkEntry(row, width=240)
            entry.insert(0, oai.model)
            entry.bind("<FocusOut>", lambda _: self._apply_model())
            entry.bind("<Return>", lambda _: self._apply_model())
            return entry

        self._model_entry = self.add_row(
            t("tk.settings.model", default="Model"),
            _make_model_entry,
            label_i18n_key="tk.settings.model",
            label_default="Model",
            label_id="label.model",
            control_id="input.model",
        )

        # --- Fallback toggle ---
        def _make_fallback_switch(row):
            switch = ctk.CTkSwitch(
                row,
                text="",
                command=self._on_fallback_toggle,
            )
            if backup.enabled:
                switch.select()
            return switch

        self._fallback_switch = self.add_row(
            t("tk.settings.fallback_enabled", default="Fallback Translation"),
            _make_fallback_switch,
            label_i18n_key="tk.settings.fallback_enabled",
            label_default="Fallback Translation",
            label_id="label.fallback",
            control_id="switch.fallback",
        )

        # --- Fallback provider ---
        fb_values = ["openai_compatible", "local_llm"]
        self._fb_values = fb_values

        def _make_fallback_menu(row):
            menu = ctk.CTkOptionMenu(
                row,
                values=[_LLM_LABELS.get(v, v) for v in fb_values],
                width=200,
                command=lambda _: self._on_fallback_provider_change(),
                fg_color=th.COLOR_PRIMARY,
                button_color=th.COLOR_PRIMARY,
                button_hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_ON_PRIMARY,
                dropdown_fg_color=th.COLOR_SURFACE,
                dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
                dropdown_text_color=th.COLOR_TEXT,
            )
            menu.set(_LLM_LABELS.get(backup.mode.value, backup.mode.value))
            return menu

        self._fallback_menu = self.add_row(
            t("tk.settings.fallback_provider", default="Fallback Provider"),
            _make_fallback_menu,
            label_i18n_key="tk.settings.fallback_provider",
            label_default="Fallback Provider",
            label_id="label.fallback_provider",
            control_id="dropdown.fallback_provider",
        )

    # --- Handlers ---

    def _on_llm_change(self) -> None:
        idx = self._llm_names.index(self._llm_menu.get())
        value = self._llm_values[idx]
        self._controller.settings.provider.llm = LLMProviderName(value)
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _apply_api_key(self) -> None:
        key_value = self._api_key_entry.get().strip()
        if not key_value:
            return
        config_path = getattr(self._controller, "config_path", None)
        if config_path is None:
            return
        try:
            self._controller.write_secret("openai_compatible", key_value, config_path)
        except Exception:
            logger.debug("[LLM] Failed to write API key", exc_info=True)

    def _on_verify(self) -> None:
        base_url = self._base_url_entry.get().strip()
        api_key = self._api_key_entry.get().strip()
        if not base_url:
            return

        loop = getattr(self._controller, "_async_loop", None)

        async def _verify() -> None:
            try:
                model_disc = getattr(self._controller, "model_discovery", None)
                if model_disc is not None:
                    status, _body = await model_disc.test_connection(base_url, api_key)
                else:
                    status, _body = await asyncio.get_event_loop().run_in_executor(
                        None, self._controller.test_connection, base_url, api_key
                    )
                self._controller.settings.api_key_verified.set_verified("openai_compatible", status == 200)
            except Exception:
                self._controller.settings.api_key_verified.set_verified("openai_compatible", False)
            self.after(0, self._update_verify_button)

        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(_verify(), loop)
        else:
            threading.Thread(target=lambda: asyncio.run(_verify()), daemon=True).start()

    # --- Verify button helpers ---

    def _verify_text(self) -> str:
        """Compose verify button text with current status."""
        status = "[OK]" if self._controller.settings.api_key_verified.is_verified("openai_compatible") else ""
        return f"{t('tk.settings.verify', default='Verify')} {status}".strip()

    def _format_verify(self, base_text: str) -> str:
        """Formatter for _TranslatableEntry: append [OK] status to base text."""
        status = "[OK]" if self._controller.settings.api_key_verified.is_verified("openai_compatible") else ""
        return f"{base_text} {status}".strip()

    def _update_verify_button(self) -> None:
        """Update verify button text after verification."""
        self._verify_btn.configure(text=self._verify_text())

    def _apply_base_url(self) -> None:
        url = self._base_url_entry.get().strip()
        self._controller.settings.provider.openai_compatible.base_url = url
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _apply_model(self) -> None:
        model = self._model_entry.get().strip()
        self._controller.settings.provider.openai_compatible.model = model
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_fallback_toggle(self) -> None:
        enabled = self._fallback_switch.get() == 1
        self._controller.settings.backup_translation.enabled = enabled
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_fallback_provider_change(self) -> None:
        idx = [_LLM_LABELS.get(v, v) for v in self._fb_values].index(
            self._fallback_menu.get()
        )
        value = self._fb_values[idx]
        from puripuly_heart.config.settings.enums import _parse_llm_provider

        self._controller.settings.backup_translation.mode = _parse_llm_provider(value)
        self._controller.apply_settings_with_sync(self._controller.settings)
