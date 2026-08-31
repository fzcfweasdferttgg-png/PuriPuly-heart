from __future__ import annotations

import json
from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.domain.providers import LLMProviderName
from puripuly_heart.config.settings.llm import _normalize_local_llm_base_url
from puripuly_heart.domain.settings_commands import ChangeFallbackLocalLLMField
from puripuly_heart.domain.settings_validation import validate_extra_body_json
from puripuly_heart.ui.components.settings import ApiKeyField
from puripuly_heart.domain.i18n import t
from puripuly_heart.ui.theme import COLOR_DIVIDER, COLOR_NEUTRAL, COLOR_NEUTRAL_DARK, COLOR_PRIMARY

from puripuly_heart.ui.views.settings_helpers import _update_control_if_mounted

if TYPE_CHECKING:
    from puripuly_heart.ui.views.settings import SettingsView


# ATTRIBUTE OWNERSHIP — _init_fallback_local_llm_controls creates:
#   _fallback_local_llm_base_url/model/fetch_btn/test_btn (TextField/IconButton/TextButton)
#   _fallback_local_llm_api_key (ApiKeyField), _fallback_local_llm_api_key_helper (Text)
#   _fallback_local_llm_extra_body/helper/error (TextField/Text/Text)
#   _fallback_local_llm_extra_body_error_key/_kwargs (error state for re-translation)
#
# EXTRA BODY PATTERN: mirrors LlmSectionMixin's local_llm extra_body handling.
# Same reserved/sensitive key validation, same JSON parsing, same error display.
# The error_key/kwargs pair enables _apply_locale_fallback_local_llm to re-translate visible errors.
#
# LOCALE: _apply_locale_fallback_local_llm updates all fallback-local-llm
# labels when locale changes (called from SettingsView._apply_locale).

class FallbackLocalLlmSectionMixin:

    def _load_fallback_local_llm_from_settings(self, settings: "AppSettings") -> None:
        if not hasattr(self, '_fallback_local_llm_base_url'):
            return
        bt = settings.backup_translation
        if bt.enabled and bt.mode == LLMProviderName.LOCAL_LLM:
            self._fallback_local_llm_base_url.value = bt.local_llm.base_url
            self._fallback_local_llm_base_url.error = None
            self._fallback_local_llm_model.value = bt.local_llm.model or ""
            self._fallback_local_llm_model.error = None
            self._fallback_local_llm_extra_body.value = (
                json.dumps(bt.local_llm.extra_body, ensure_ascii=False, indent=2)
                if bt.local_llm.extra_body else ""
            )
            self._fallback_local_llm_extra_body_error.visible = False

    def _init_fallback_local_llm_controls(
        self,
        *,
        on_save,
        show_snackbar,
    ) -> None:
        self._fallback_local_llm_base_url = ft.TextField(
            label=t("flet.settings.local_llm.base_url"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_fallback_local_llm_field_change,
            on_blur=self._on_fallback_local_llm_base_url_change_end,
            on_submit=self._on_fallback_local_llm_base_url_change_end,
        )
        self._fallback_local_llm_model = ft.TextField(
            label=t("flet.settings.local_llm.model"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_fallback_local_llm_field_change,
            on_blur=self._on_fallback_local_llm_model_change_end,
            on_submit=self._on_fallback_local_llm_model_change_end,
        )
        self._fallback_local_llm_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip=t("flet.settings.openai_compatible.fetch_models", default="Fetch models from API"),
            on_click=self._fetch_fallback_local_llm_models,
        )
        self._fallback_local_llm_test_btn = ft.TextButton(
            content=ft.Text(t("flet.settings.local_llm.test_connection", default="Test connection")),
            on_click=self._test_fallback_local_llm_connection,
        )

        self._fallback_local_llm_api_key = ApiKeyField(
            "flet.settings.fallback_local_llm_api_key",
            "fallback_local_llm_api_key",
            "fallback_local_llm",
            on_verify=None,
            on_save=on_save,
            show_snackbar=show_snackbar,
            show_status=False,
        )
        fallback_local_llm_api_key_description = t("flet.settings.local_llm.api_key.description")
        self._fallback_local_llm_api_key_helper = ft.Text(
            fallback_local_llm_api_key_description,
            size=15,
            color=COLOR_NEUTRAL,
            visible=bool(fallback_local_llm_api_key_description.strip()),
        )
        self._fallback_local_llm_extra_body = ft.TextField(
            label=t("flet.settings.local_llm.extra_body"),
            value="",
            multiline=True,
            min_lines=3,
            max_lines=6,
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_fallback_local_llm_field_change,
            on_blur=self._on_fallback_local_llm_extra_body_change_end,
            on_submit=self._on_fallback_local_llm_extra_body_change_end,
        )
        self._fallback_local_llm_extra_body_helper = ft.Text(
            t("flet.settings.local_llm.extra_body.description"),
            size=15,
            color=COLOR_NEUTRAL,
        )
        self._fallback_local_llm_extra_body_error = ft.Text(
            "",
            size=13,
            color=ft.Colors.RED_600,
            visible=False,
        )
        self._fallback_local_llm_extra_body_error_key = ""
        self._fallback_local_llm_extra_body_error_kwargs: dict[str, object] = {}

    def _on_fallback_local_llm_field_change(self, e) -> None:
        _ = e
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if not current.backup_translation.enabled:
            return
        if current.backup_translation.mode != LLMProviderName.LOCAL_LLM:
            return
        self._ensure_provider_settings_draft()
        self.has_provider_changes = True

    def _on_fallback_local_llm_base_url_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw_value = self._fallback_local_llm_base_url.value or ""
        try:
            normalized = _normalize_local_llm_base_url(raw_value)
        except ValueError:
            self._fallback_local_llm_base_url.error = t("flet.settings.local_llm.base_url.invalid")
            _update_control_if_mounted(self._fallback_local_llm_base_url)
            return

        self._fallback_local_llm_base_url.error = None
        self._fallback_local_llm_base_url.value = normalized
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.local_llm.base_url != normalized:
            self._command_executor.execute(ChangeFallbackLocalLLMField(field="base_url", value=normalized))
        _update_control_if_mounted(self._fallback_local_llm_base_url)

    def _on_fallback_local_llm_model_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        model = (self._fallback_local_llm_model.value or "").strip()
        self._fallback_local_llm_model.error = None
        self._fallback_local_llm_model.value = model
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.local_llm.model != model:
            self._command_executor.execute(ChangeFallbackLocalLLMField(field="model", value=model))
        _update_control_if_mounted(self._fallback_local_llm_model)

    def _set_fallback_extra_body_error(self, message_key: str, **kwargs: object) -> None:
        if "key" not in kwargs:
            msg = t(message_key, default="")
        else:
            template = t(message_key, default="")
            try:
                msg = template.format(**kwargs)
            except Exception:
                msg = template
        self._fallback_local_llm_extra_body_error_key = message_key
        self._fallback_local_llm_extra_body_error_kwargs = dict(kwargs)
        self._fallback_local_llm_extra_body_error.value = msg
        self._fallback_local_llm_extra_body_error.visible = True
        self._fallback_local_llm_extra_body.error = msg
        _update_control_if_mounted(self._fallback_local_llm_extra_body)
        _update_control_if_mounted(self._fallback_local_llm_extra_body_error)

    def _clear_fallback_extra_body_error(self) -> None:
        self._fallback_local_llm_extra_body_error_key = ""
        self._fallback_local_llm_extra_body_error_kwargs = {}
        self._fallback_local_llm_extra_body_error.value = ""
        self._fallback_local_llm_extra_body_error.visible = False
        self._fallback_local_llm_extra_body.error = None
        _update_control_if_mounted(self._fallback_local_llm_extra_body)
        _update_control_if_mounted(self._fallback_local_llm_extra_body_error)

    def _on_fallback_local_llm_extra_body_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw = (self._fallback_local_llm_extra_body.value or "").strip()

        valid, error_key, result = validate_extra_body_json(raw)
        if not valid:
            if result is not None:
                self._set_fallback_extra_body_error(error_key, key=result)
            else:
                self._set_fallback_extra_body_error(error_key)
            return

        normalized = result
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.local_llm.extra_body != normalized:
            self._command_executor.execute(ChangeFallbackLocalLLMField(field="extra_body", value=normalized))
            self._fallback_local_llm_extra_body.value = json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False)
            self._clear_fallback_extra_body_error()

    def _commit_fallback_local_llm_fields_from_controls(self) -> None:
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if not current.backup_translation.enabled:
            return
        if current.backup_translation.mode != LLMProviderName.LOCAL_LLM:
            return
        self._on_fallback_local_llm_base_url_change_end(None)
        self._on_fallback_local_llm_model_change_end(None)
        self._on_fallback_local_llm_extra_body_change_end(None)

    def _fetch_fallback_local_llm_models(self, e) -> None:
        import logging
        from puripuly_heart.ui.components.settings.settings_modal import OptionItem, SettingsModal

        logger = logging.getLogger(__name__)

        base_url = (self._fallback_local_llm_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._fallback_local_llm_api_key.value or "").strip()
        logger.info("[FetchModels][FallbackLocal] Requesting %s/models (has_key=%s)", base_url, bool(api_key))

        if not self.on_fetch_models:
            return

        try:
            model_ids = self.on_fetch_models(base_url, api_key)
        except Exception as exc:
            logger.error("[FetchModels][FallbackLocal] Failed: %s", exc)
            return

        if not model_ids:
            return

        if len(model_ids) == 1:
            self._fallback_local_llm_model.value = model_ids[0]
            if self._settings:
                self._command_executor.execute(ChangeFallbackLocalLLMField(field="model", value=model_ids[0]))
            _update_control_if_mounted(self._fallback_local_llm_model)
            return

        options = [OptionItem(value=m, label=m) for m in model_ids]
        current_value = (self._fallback_local_llm_model.value or "").strip()

        def _on_model_selected(value: str) -> None:
            self._fallback_local_llm_model.value = value
            logger.info("[FetchModels][FallbackLocal] User selected %s", value)
            _update_control_if_mounted(self._fallback_local_llm_model)
            if self._settings:
                self._command_executor.execute(ChangeFallbackLocalLLMField(field="model", value=value))

        modal = SettingsModal(
            self.page,
            title=t("flet.settings.openai_compatible.select_model", default="Select Model"),
            options=options,
            on_select=_on_model_selected,
            searchable=True,
            search_hint=t("flet.settings.filter", default="Filter..."),
        )
        modal.open(current=current_value or model_ids[0])

    def _test_fallback_local_llm_connection(self, e) -> None:
        import logging

        logger = logging.getLogger(__name__)

        base_url = (self._fallback_local_llm_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._fallback_local_llm_api_key.value or "").strip()

        logger.info("[TestConnection][FallbackLocal] Pinging %s (has_key=%s)", base_url, bool(api_key))

        if not self.on_test_connection:
            return

        try:
            status_code, body = self.on_test_connection(base_url, api_key)
        except Exception as exc:
            logger.error("[TestConnection][FallbackLocal] Failed: %s", exc)
            if self.show_snackbar:
                self.show_snackbar(t("flet.settings.local_llm.test_connection.failed", default="Connection failed"), ft.Colors.RED_400)
            return

        if status_code == 200:
            logger.info("[TestConnection][FallbackLocal] OK (%d)", status_code)
            if self.show_snackbar:
                self.show_snackbar(t("flet.settings.local_llm.test_connection.ok", default="Server is responding"), ft.Colors.GREEN_400)
        elif status_code == 401:
            logger.info("[TestConnection][FallbackLocal] 401 — needs API key")
            if self.show_snackbar:
                self.show_snackbar(t("flet.settings.local_llm.test_connection.needs_key", default="Server reachable, API key required"), ft.Colors.ORANGE_400)
        else:
            logger.warning("[TestConnection][FallbackLocal] %d — %s", status_code, body)
            if self.show_snackbar:
                self.show_snackbar(t("flet.settings.local_llm.test_connection.error", default=f"Server returned {status_code}"), ft.Colors.RED_400)

    def _apply_locale_fallback_local_llm(self) -> None:
        if not hasattr(self, '_fallback_local_llm_api_key'):
            return
        self._fallback_local_llm_api_key.apply_locale()
        self._fallback_local_llm_title.value = t("flet.settings.backup_translation.connection", default="Backup Translation Settings")
        self._fallback_local_llm_test_btn.content.value = t("flet.settings.local_llm.test_connection", default="Test connection")
        self._fallback_local_llm_base_url.label = t("flet.settings.local_llm.base_url", default="Base URL")
        self._fallback_local_llm_model.label = t("flet.settings.local_llm.model", default="Model")
        self._fallback_local_llm_extra_body.label = t("flet.settings.local_llm.extra_body", default="Extra Body")
        self._fallback_local_llm_extra_body_helper.value = t("flet.settings.local_llm.extra_body.description", default="")
        self._fallback_local_llm_fetch_btn.tooltip = t("flet.settings.openai_compatible.fetch_models", default="Fetch models from API")
        _fb_helper = t("flet.settings.local_llm.api_key.description", default="")
        self._fallback_local_llm_api_key_helper.value = _fb_helper
        self._fallback_local_llm_api_key_helper.visible = bool(_fb_helper.strip())
        if self._fallback_local_llm_base_url.error:
            self._fallback_local_llm_base_url.error = t("flet.settings.local_llm.base_url.invalid")
        if self._fallback_local_llm_model.error:
            self._fallback_local_llm_model.error = t("flet.settings.local_llm.model.required")
        if self._fallback_local_llm_extra_body_error.visible:
            fb_error_key = self._fallback_local_llm_extra_body_error_key
            fb_error_kwargs = self._fallback_local_llm_extra_body_error_kwargs
            if fb_error_key:
                if "key" not in fb_error_kwargs:
                    fb_msg = t(fb_error_key, default="")
                else:
                    template = t(fb_error_key, default="")
                    try:
                        fb_msg = template.format(**fb_error_kwargs)
                    except Exception:
                        fb_msg = template
                self._fallback_local_llm_extra_body_error.value = fb_msg
                self._fallback_local_llm_extra_body.error = fb_msg
