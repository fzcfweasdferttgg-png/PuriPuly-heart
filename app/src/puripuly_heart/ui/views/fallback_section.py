"""FallbackSectionMixin — Fallback translation OpenAI-compatible card controls."""

from __future__ import annotations

import flet as ft

from puripuly_heart.config.providers import load_providers
from puripuly_heart.config.settings import LLMProviderName
from puripuly_heart.ui.components.settings import ApiKeyField
from puripuly_heart.ui.i18n import t

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
            label=t("settings.openai_compatible.provider", default="Provider"),
            options=_provider_options,
            value=_provider_options[0].key if _provider_options else None,
            border_radius=12,
            expand=True,
            text_size=24,
            on_change=self._on_fallback_provider_change,
        )
        self._fallback_openai_base_url = ft.TextField(
            label=t("settings.openai_compatible.base_url", default="Base URL"),
            value="",
            border_radius=12,
            expand=True,
            text_size=24,
            visible=False,
            on_change=self._on_fallback_field_change,
            on_blur=self._on_fallback_base_url_change_end,
            on_submit=self._on_fallback_base_url_change_end,
        )
        self._fallback_openai_model = ft.TextField(
            label=t("settings.openai_compatible.model", default="Model"),
            hint_text=t("settings.openai_compatible.model.hint", default="Enter model name or click refresh"),
            value="",
            border_radius=12,
            expand=True,
            text_size=24,
            on_change=self._on_fallback_field_change,
            on_blur=self._on_fallback_model_change_end,
            on_submit=self._on_fallback_model_change_end,
        )
        self._fallback_openai_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip=t("settings.openai_compatible.fetch_models", default="Fetch models from API"),
            on_click=self._fetch_fallback_models,
        )
        self._fallback_api_key = ApiKeyField(
            "settings.backup_api_key",
            "backup_api_key",
            "openai_compatible",
            on_verify=on_verify,
            on_save=on_save,
            show_snackbar=show_snackbar,
            show_status=False,
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
        import httpx

        base_url = (self._fallback_openai_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._fallback_api_key.value or "").strip()
        models_url = base_url.rstrip("/") + "/models"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async def _do_fetch():
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(models_url, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    model_ids = sorted(
                        m.get("id", "") for m in data.get("data", []) if m.get("id")
                    )
                    if model_ids:
                        current_value = (self._fallback_openai_model.value or "").strip()
                        if not current_value or current_value not in model_ids:
                            self._fallback_openai_model.value = model_ids[0]
                            _update_control_if_mounted(self._fallback_openai_model)
                            if self._settings:
                                draft = self._ensure_provider_settings_draft()
                                draft.backup_translation.openai_compatible.model = model_ids[0]
                                self.has_provider_changes = True
            except Exception:
                pass

        asyncio.ensure_future(_do_fetch())

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
