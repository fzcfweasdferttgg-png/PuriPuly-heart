"""LLM provider settings section.

Translation model selector, OpenAI-compatible base URL / model, and fallback
toggle. Fields map to ``controller.settings.provider.llm``,
``controller.settings.provider.openai_compatible.*``, and
``controller.settings.backup_translation.*``.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.domain.providers import LLMProviderName
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection

_LLM_LABELS: dict[str, str] = {
    "none": "None",
    "local_llm": "Local LLM",
    "openai_compatible": "OpenAI-Compatible",
}


class LLMSection(CollapsibleSection):
    """LLM provider, OpenAI-compatible connection, and fallback settings."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, t("settings.section.llm", default="LLM Providers"), **kwargs)
        self._controller = controller
        self._build()

    def _build(self) -> None:
        provider = self._controller.settings.provider
        oai = provider.openai_compatible
        backup = self._controller.settings.backup_translation

        # --- LLM Provider ---
        llm_values = [e.value for e in LLMProviderName]
        llm_names = [_LLM_LABELS.get(v, v) for v in llm_values]
        self._llm_menu = ctk.CTkOptionMenu(
            self._content,
            values=llm_names,
            width=200,
            command=lambda _: self._on_llm_change(),
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._llm_menu.set(_LLM_LABELS.get(provider.llm.value, provider.llm.value))
        self._llm_values = llm_values
        self._llm_names = llm_names
        self.add_row(t("settings.llm_provider", default="LLM Provider"), self._llm_menu)
        self._add_debug_label(self._llm_menu, "dropdown.llm_provider")

        # --- API Key (masked) ---
        self._api_key_entry = ctk.CTkEntry(self._content, width=240, show="*")
        self._api_key_entry.bind("<FocusOut>", lambda _: self._apply_api_key())
        self.add_row(t("settings.api_key", default="API Key"), self._api_key_entry)
        self._add_debug_label(self._api_key_entry, "input.api_key")

        # --- Verify button ---
        self._verify_btn = ctk.CTkButton(
            self._content,
            text=t("settings.verify", default="Verify"),
            width=80,
            command=self._on_verify,
            fg_color=th.COLOR_PRIMARY,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
        )
        self.add_row("", self._verify_btn)
        self._add_debug_label(self._verify_btn, "btn.verify_api")

        # --- Base URL ---
        self._base_url_entry = ctk.CTkEntry(self._content, width=300)
        self._base_url_entry.insert(0, oai.base_url)
        self._base_url_entry.bind("<FocusOut>", lambda _: self._apply_base_url())
        self._base_url_entry.bind("<Return>", lambda _: self._apply_base_url())
        self.add_row(t("settings.base_url", default="Base URL"), self._base_url_entry)
        self._add_debug_label(self._base_url_entry, "input.base_url")

        # --- Model ---
        self._model_entry = ctk.CTkEntry(self._content, width=240)
        self._model_entry.insert(0, oai.model)
        self._model_entry.bind("<FocusOut>", lambda _: self._apply_model())
        self._model_entry.bind("<Return>", lambda _: self._apply_model())
        self.add_row(t("settings.model", default="Model"), self._model_entry)
        self._add_debug_label(self._model_entry, "input.model")

        # --- Fallback toggle ---
        self._fallback_switch = ctk.CTkSwitch(
            self._content,
            text="",
            command=self._on_fallback_toggle,
        )
        if backup.enabled:
            self._fallback_switch.select()
        self.add_row(
            t("settings.fallback_enabled", default="Fallback Translation"),
            self._fallback_switch,
        )
        self._add_debug_label(self._fallback_switch, "switch.fallback")

        # --- Fallback provider ---
        fb_values = ["openai_compatible", "local_llm"]
        self._fallback_menu = ctk.CTkOptionMenu(
            self._content,
            values=[_LLM_LABELS.get(v, v) for v in fb_values],
            width=200,
            command=lambda _: self._on_fallback_provider_change(),
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._fallback_menu.set(_LLM_LABELS.get(backup.mode.value, backup.mode.value))
        self._fb_values = fb_values
        self.add_row(
            t("settings.fallback_provider", default="Fallback Provider"),
            self._fallback_menu,
        )
        self._add_debug_label(self._fallback_menu, "dropdown.fallback_provider")

    # --- Handlers ---

    def _on_llm_change(self) -> None:
        idx = self._llm_names.index(self._llm_menu.get())
        value = self._llm_values[idx]
        self._controller.settings.provider.llm = LLMProviderName(value)
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _apply_api_key(self) -> None:
        # API keys are handled by the secrets section; this is a convenience entry.
        pass

    def _on_verify(self) -> None:
        base_url = self._base_url_entry.get().strip()
        api_key = self._api_key_entry.get().strip()
        if not base_url:
            return
        try:
            status, _body = self._controller.test_connection(base_url, api_key)
            if status == 200:
                self._controller.settings.api_key_verified.set_verified("openai_compatible", True)
        except Exception:
            pass

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
