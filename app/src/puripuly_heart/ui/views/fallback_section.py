"""FallbackSectionMixin — Fallback translation OpenAI-compatible card controls."""

from __future__ import annotations

import flet as ft

from puripuly_heart.config.providers import load_providers
from puripuly_heart.config.settings import LLMProviderName
from puripuly_heart.ui.components.settings import ApiKeyField
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import COLOR_DIVIDER, COLOR_NEUTRAL_DARK, COLOR_PRIMARY

from puripuly_heart.ui.views.settings_helpers import _update_control_if_mounted


class FallbackSectionMixin:
    """Fallback OpenAI-compatible card: Provider, Model, API Key."""

    def _init_fallback_openai_controls(
        self,
        *,
        on_verify,
        on_save,
        show_snackbar,
    ) -> None:
        _providers = load_providers()
        _provider_options = []
        for key, info in _providers.items():
            _provider_options.append(ft.dropdown.Option(key=key, text=info.get("label", key)))

        self._fallback_openai_provider = ft.Dropdown(
            label=t("settings.openai_compatible.provider"),
            options=_provider_options,
            value=_provider_options[0].key if _provider_options else None,
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_fallback_provider_change,
        )
        self._fallback_openai_base_url = ft.TextField(
            label=t("settings.openai_compatible.base_url"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            visible=False,
            on_change=self._on_fallback_field_change,
            on_blur=self._on_fallback_base_url_change_end,
            on_submit=self._on_fallback_base_url_change_end,
        )
        self._fallback_openai_model = ft.TextField(
            label=t("settings.openai_compatible.model"),
            hint_text=t("settings.openai_compatible.model.hint"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_fallback_field_change,
            on_blur=self._on_fallback_model_change_end,
            on_submit=self._on_fallback_model_change_end,
        )
        self._fallback_openai_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip=t("settings.openai_compatible.fetch_models", default="Fetch models from API"),
            on_click=self._fetch_fallback_models,
        )
        self._fallback_openai_test_btn = ft.TextButton(
            text=t("settings.local_llm.test_connection", default="Test connection"),
            on_click=self._test_fallback_openai_connection,
        )
        self._fallback_api_key = ApiKeyField(
            "settings.backup_api_key",
            "backup_api_key",
            "openai_compatible",
            on_verify=on_verify,
            on_save=on_save,
            show_snackbar=show_snackbar,
        )

    def _on_fallback_field_change(self, e) -> None:
        _ = e
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if not current.backup_translation.enabled:
            return
        if current.backup_translation.mode != LLMProviderName.OPENAI_COMPATIBLE:
            return
        self._ensure_provider_settings_draft()
        self.has_provider_changes = True

    def _on_fallback_model_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw_value = (self._fallback_openai_model.value or "").strip()
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.openai_compatible.model != raw_value:
            draft = self._ensure_provider_settings_draft()
            draft.backup_translation.openai_compatible.model = raw_value
            self.has_provider_changes = True

    def _on_fallback_provider_change(self, e) -> None:
        from puripuly_heart.config.providers import load_providers
        selected = e.data if e else None
        if not selected:
            return
        providers = load_providers()
        provider_info = providers.get(selected)
        if not provider_info:
            return
        base_url = provider_info.get("base_url", "")
        if base_url and self._fallback_openai_base_url:
            self._fallback_openai_base_url.value = base_url
            _update_control_if_mounted(self._fallback_openai_base_url)
            if self._settings:
                current = self._provider_settings_draft or self._settings
                if current.backup_translation.openai_compatible.base_url != base_url:
                    draft = self._ensure_provider_settings_draft()
                    draft.backup_translation.openai_compatible.base_url = base_url
                    draft.backup_translation.openai_compatible.model = ""
                    self.has_provider_changes = True
            if self._fallback_openai_model:
                self._fallback_openai_model.value = ""
                _update_control_if_mounted(self._fallback_openai_model)

    def _fetch_fallback_models(self, e) -> None:
        import asyncio
        import logging
        from puripuly_heart.ui.components.settings.settings_modal import OptionItem, SettingsModal

        logger = logging.getLogger(__name__)

        base_url = (self._fallback_openai_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._fallback_api_key.value or "").strip()
        logger.info("[FetchModels][Fallback] Requesting %s/models (has_key=%s)", base_url, bool(api_key))

        if self.model_discovery is None:
            return

        try:
            model_ids = asyncio.run(self.model_discovery.fetch_models(base_url, api_key))
        except Exception as exc:
            logger.error("[FetchModels][Fallback] Failed: %s", exc)
            return

        if not model_ids:
            return

        if len(model_ids) == 1:
            self._fallback_openai_model.value = model_ids[0]
            if self._settings:
                draft = self._ensure_provider_settings_draft()
                draft.backup_translation.openai_compatible.model = model_ids[0]
                self.has_provider_changes = True
            _update_control_if_mounted(self._fallback_openai_model)
            return

        options = [OptionItem(value=m, label=m) for m in model_ids]
        current_value = (self._fallback_openai_model.value or "").strip()

        def _on_model_selected(value: str) -> None:
            self._fallback_openai_model.value = value
            logger.info("[FetchModels][Fallback] User selected %s", value)
            _update_control_if_mounted(self._fallback_openai_model)
            if self._settings:
                draft = self._ensure_provider_settings_draft()
                draft.backup_translation.openai_compatible.model = value
                self.has_provider_changes = True

        modal = SettingsModal(
            self.page,
            title=t("settings.openai_compatible.select_model", default="Select Model"),
            options=options,
            on_select=_on_model_selected,
            searchable=True,
            search_hint=t("settings.filter", default="Filter..."),
        )
        modal.open(current=current_value or model_ids[0])

    def _test_fallback_openai_connection(self, e) -> None:
        import asyncio
        import logging

        logger = logging.getLogger(__name__)

        base_url = (self._fallback_openai_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._fallback_api_key.value or "").strip()

        logger.info("[TestConnection][Fallback] Pinging %s (has_key=%s)", base_url, bool(api_key))

        if self.model_discovery is None:
            return

        try:
            status_code, body = asyncio.run(self.model_discovery.test_connection(base_url, api_key))
        except Exception as exc:
            logger.error("[TestConnection][Fallback] Failed: %s", exc)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.failed", default="Connection failed"), ft.Colors.RED_400)
            return

        if status_code == 200:
            logger.info("[TestConnection][Fallback] OK (%d)", status_code)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.ok", default="Server is responding"), ft.Colors.GREEN_400)
        elif status_code == 401:
            logger.info("[TestConnection][Fallback] 401 — needs API key")
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.needs_key", default="Server reachable, API key required"), ft.Colors.ORANGE_400)
        else:
            logger.warning("[TestConnection][Fallback] %d — %s", status_code, body)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.error", default=f"Server returned {status_code}"), ft.Colors.RED_400)

    def _on_fallback_base_url_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw_value = (self._fallback_openai_base_url.value or "").strip()
        if not raw_value:
            self._fallback_openai_base_url.error_text = t(
                "settings.openai_compatible.base_url.required", default="Base URL is required"
            )
            _update_control_if_mounted(self._fallback_openai_base_url)
            return
        self._fallback_openai_base_url.error_text = None
        self._fallback_openai_base_url.value = raw_value
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.openai_compatible.base_url != raw_value:
            draft = self._ensure_provider_settings_draft()
            draft.backup_translation.openai_compatible.base_url = raw_value
            self.has_provider_changes = True
        _update_control_if_mounted(self._fallback_openai_base_url)

    def _commit_fallback_fields_from_controls(self) -> None:
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if not current.backup_translation.enabled:
            return
        if current.backup_translation.mode != LLMProviderName.OPENAI_COMPATIBLE:
            return
        self._on_fallback_base_url_change_end(None)
        self._on_fallback_model_change_end(None)
