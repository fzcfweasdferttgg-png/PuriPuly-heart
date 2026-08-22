from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.app.services.settings_manager import load_providers
from puripuly_heart.domain.providers import LLMProviderName
from puripuly_heart.domain.settings_commands import ChangeFallbackOpenAIField
from puripuly_heart.ui.components.settings import ApiKeyField
from puripuly_heart.domain.i18n import t
from puripuly_heart.ui.theme import COLOR_DIVIDER, COLOR_NEUTRAL_DARK, COLOR_PRIMARY

from puripuly_heart.ui.views.settings_helpers import _update_control_if_mounted

if TYPE_CHECKING:
    from puripuly_heart.ui.views.settings import SettingsView


# ATTRIBUTE OWNERSHIP — _init_fallback_openai_controls creates:
#   _fallback_openai_provider (Dropdown), _fallback_openai_base_url (TextField),
#   _fallback_openai_model (TextField), _fallback_openai_fetch_btn (IconButton),
#   _fallback_openai_test_btn (TextButton), _fallback_api_key (ApiKeyField)
#
# CARDS ARE CREATED IN settings.py _build_api_tab, not here — this mixin only
# creates the controls that go INSIDE the card.
#
# LOCALE: _apply_locale_fallback() handles fallback OpenAI locale updates.

class FallbackSectionMixin:

    def _load_fallback_from_settings(self, settings: "AppSettings") -> None:
        if not hasattr(self, '_fallback_openai_base_url'):
            return
        bt = settings.backup_translation
        if bt.enabled and bt.mode == LLMProviderName.OPENAI_COMPATIBLE:
            self._fallback_openai_base_url.value = bt.openai_compatible.base_url
            self._fallback_openai_base_url.error = None
            self._fallback_openai_model.value = bt.openai_compatible.model or ""
            from puripuly_heart.app.services.settings_manager import load_providers
            _loaded_providers = load_providers()
            _fb_opts = self._fallback_openai_provider.options or []
            _fb_matched = _fb_opts[0].key if _fb_opts else None
            for _pk, _pi in _loaded_providers.items():
                if _pi.get("base_url") == bt.openai_compatible.base_url:
                    _fb_matched = _pk
                    break
            self._fallback_openai_provider.value = _fb_matched

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
            on_select=self._on_fallback_provider_change,
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
            content=ft.Text(t("settings.local_llm.test_connection", default="Test connection")),
            on_click=self._test_fallback_openai_connection,
        )
        async def _verify_with_base_url(provider: str, key: str, **kwargs: object):
            return await on_verify(provider, key, base_url=self._fallback_openai_base_url.value or None)

        self._fallback_api_key = ApiKeyField(
            "settings.backup_api_key",
            "backup_api_key",
            "backup_openai_compatible",
            on_verify=_verify_with_base_url if on_verify else None,
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
            self._command_executor.execute(ChangeFallbackOpenAIField(field="model", value=raw_value))

    def _on_fallback_provider_change(self, e) -> None:
        from puripuly_heart.app.services.settings_manager import load_providers
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
                    self._command_executor.execute(ChangeFallbackOpenAIField(field="base_url", value=base_url))
                    self._command_executor.execute(ChangeFallbackOpenAIField(field="model", value=""))
            if self._fallback_openai_model:
                self._fallback_openai_model.value = ""
                _update_control_if_mounted(self._fallback_openai_model)

    def _fetch_fallback_models(self, e) -> None:
        import logging
        from puripuly_heart.ui.components.settings.settings_modal import OptionItem, SettingsModal

        logger = logging.getLogger(__name__)

        base_url = (self._fallback_openai_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._fallback_api_key.value or "").strip()
        logger.info("[FetchModels][Fallback] Requesting %s/models (has_key=%s)", base_url, bool(api_key))

        if not self.on_fetch_models:
            return

        try:
            model_ids = self.on_fetch_models(base_url, api_key)
        except Exception as exc:
            logger.error("[FetchModels][Fallback] Failed: %s", exc)
            return

        if not model_ids:
            return

        if len(model_ids) == 1:
            self._fallback_openai_model.value = model_ids[0]
            if self._settings:
                self._command_executor.execute(ChangeFallbackOpenAIField(field="model", value=model_ids[0]))
            _update_control_if_mounted(self._fallback_openai_model)
            return

        options = [OptionItem(value=m, label=m) for m in model_ids]
        current_value = (self._fallback_openai_model.value or "").strip()

        def _on_model_selected(value: str) -> None:
            self._fallback_openai_model.value = value
            logger.info("[FetchModels][Fallback] User selected %s", value)
            _update_control_if_mounted(self._fallback_openai_model)
            if self._settings:
                self._command_executor.execute(ChangeFallbackOpenAIField(field="model", value=value))

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
        import logging

        logger = logging.getLogger(__name__)

        base_url = (self._fallback_openai_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._fallback_api_key.value or "").strip()

        logger.info("[TestConnection][Fallback] Pinging %s (has_key=%s)", base_url, bool(api_key))

        if not self.on_test_connection:
            return

        try:
            status_code, body = self.on_test_connection(base_url, api_key)
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
        import logging
        _ = e
        if not self._settings:
            return
        raw_value = (self._fallback_openai_base_url.value or "").strip()
        if not raw_value:
            self._fallback_openai_base_url.error = t(
                "settings.openai_compatible.base_url.required", default="Base URL is required"
            )
            _update_control_if_mounted(self._fallback_openai_base_url)
            logging.getLogger(__name__).warning(
                "[FallbackSection] Backup base_url is empty — validation error shown in UI"
            )
            return
        self._fallback_openai_base_url.error = None
        self._fallback_openai_base_url.value = raw_value
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.openai_compatible.base_url != raw_value:
            self._command_executor.execute(ChangeFallbackOpenAIField(field="base_url", value=raw_value))
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

    def _apply_locale_fallback(self) -> None:
        if not hasattr(self, '_fallback_api_key'):
            return
        self._fallback_api_key.apply_locale()
        self._fallback_openai_title.value = t("settings.backup_translation.connection", default="Backup Translation Settings")
        self._fallback_openai_test_btn.content.value = t("settings.local_llm.test_connection", default="Test connection")
        self._fallback_openai_provider.label = t("settings.openai_compatible.provider", default="Provider")
        self._fallback_openai_base_url.label = t("settings.openai_compatible.base_url", default="Base URL")
        self._fallback_openai_model.label = t("settings.openai_compatible.model", default="Model")
        self._fallback_openai_model.hint_text = t("settings.openai_compatible.model.hint", default="Enter model name or click refresh")
        self._fallback_openai_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models", default="Fetch models from API")
        if self._fallback_openai_base_url.error:
            self._fallback_openai_base_url.error = t(
                "settings.openai_compatible.base_url.required", default="Base URL is required"
            )
