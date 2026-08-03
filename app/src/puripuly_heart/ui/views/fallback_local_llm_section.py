"""FallbackLocalLlmSectionMixin — Fallback local LLM card controls."""

from __future__ import annotations

import copy
import json

import flet as ft

from puripuly_heart.config.settings import (
    LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS,
    LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS,
    LLMProviderName,
)
from puripuly_heart.config.settings.llm import _normalize_local_llm_base_url
from puripuly_heart.ui.components.settings import ApiKeyField
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import COLOR_DIVIDER, COLOR_NEUTRAL, COLOR_NEUTRAL_DARK, COLOR_PRIMARY

from puripuly_heart.ui.views.settings_helpers import _update_control_if_mounted


def _reject_json_constant(value: str) -> None:
    raise json.JSONDecodeError(f"invalid JSON constant: {value}", value, 0)


class FallbackLocalLlmSectionMixin:
    """Fallback local LLM card: Base URL, Model, API Key, Extra Body."""

    def _init_fallback_local_llm_controls(
        self,
        *,
        on_save,
        show_snackbar,
    ) -> None:
        self._fallback_local_llm_base_url = ft.TextField(
            label=t("settings.local_llm.base_url"),
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
            label=t("settings.local_llm.model"),
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
            tooltip=t("settings.openai_compatible.fetch_models", default="Fetch models from API"),
            on_click=self._fetch_fallback_local_llm_models,
        )
        self._fallback_local_llm_test_btn = ft.TextButton(
            text=t("settings.local_llm.test_connection", default="Test connection"),
            on_click=self._test_fallback_local_llm_connection,
        )

        self._fallback_local_llm_api_key = ApiKeyField(
            "settings.fallback_local_llm_api_key",
            "fallback_local_llm_api_key",
            "fallback_local_llm",
            on_verify=None,
            on_save=on_save,
            show_snackbar=show_snackbar,
            show_status=False,
        )
        fallback_local_llm_api_key_description = t("settings.local_llm.api_key.description")
        self._fallback_local_llm_api_key_helper = ft.Text(
            fallback_local_llm_api_key_description,
            size=15,
            color=COLOR_NEUTRAL,
            visible=bool(fallback_local_llm_api_key_description.strip()),
        )
        self._fallback_local_llm_extra_body = ft.TextField(
            label=t("settings.local_llm.extra_body"),
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
            t("settings.local_llm.extra_body.description"),
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
            self._fallback_local_llm_base_url.error_text = t("settings.local_llm.base_url.invalid")
            _update_control_if_mounted(self._fallback_local_llm_base_url)
            return

        self._fallback_local_llm_base_url.error_text = None
        self._fallback_local_llm_base_url.value = normalized
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.local_llm.base_url != normalized:
            draft = self._ensure_provider_settings_draft()
            draft.backup_translation.local_llm.base_url = normalized
            self.has_provider_changes = True
        _update_control_if_mounted(self._fallback_local_llm_base_url)

    def _on_fallback_local_llm_model_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        model = (self._fallback_local_llm_model.value or "").strip()
        self._fallback_local_llm_model.error_text = None
        self._fallback_local_llm_model.value = model
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.local_llm.model != model:
            draft = self._ensure_provider_settings_draft()
            draft.backup_translation.local_llm.model = model
            self.has_provider_changes = True
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
        self._fallback_local_llm_extra_body.error_text = msg
        _update_control_if_mounted(self._fallback_local_llm_extra_body)
        _update_control_if_mounted(self._fallback_local_llm_extra_body_error)

    def _clear_fallback_extra_body_error(self) -> None:
        self._fallback_local_llm_extra_body_error_key = ""
        self._fallback_local_llm_extra_body_error_kwargs = {}
        self._fallback_local_llm_extra_body_error.value = ""
        self._fallback_local_llm_extra_body_error.visible = False
        self._fallback_local_llm_extra_body.error_text = None
        _update_control_if_mounted(self._fallback_local_llm_extra_body)
        _update_control_if_mounted(self._fallback_local_llm_extra_body_error)

    def _on_fallback_local_llm_extra_body_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw = (self._fallback_local_llm_extra_body.value or "").strip()
        try:
            parsed = (
                {"reasoning_effort": "none"}
                if not raw
                else json.loads(raw, parse_constant=_reject_json_constant)
            )
        except json.JSONDecodeError:
            self._set_fallback_extra_body_error("settings.local_llm.extra_body.invalid_json")
            return

        if not isinstance(parsed, dict):
            self._set_fallback_extra_body_error("settings.local_llm.extra_body.must_be_object")
            return

        lowered = {str(key).lower() for key in parsed}
        reserved = LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS.intersection(lowered)
        if reserved:
            self._set_fallback_extra_body_error(
                "settings.local_llm.extra_body.reserved_key", key=sorted(reserved)[0]
            )
            return

        sensitive = LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS.intersection(lowered)
        if sensitive:
            self._set_fallback_extra_body_error(
                "settings.local_llm.extra_body.sensitive_key", key=sorted(sensitive)[0]
            )
            return

        try:
            json.dumps(parsed, allow_nan=False)
        except (TypeError, ValueError):
            self._set_fallback_extra_body_error("settings.local_llm.extra_body.not_serializable")
            return

        normalized = copy.deepcopy(parsed)
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.local_llm.extra_body != normalized:
            draft = self._ensure_provider_settings_draft()
            draft.backup_translation.local_llm.extra_body = normalized
            self.has_provider_changes = True
        self._fallback_local_llm_extra_body.value = json.dumps(
            normalized,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
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
        import asyncio
        import logging
        from puripuly_heart.ui.components.settings.settings_modal import OptionItem, SettingsModal

        logger = logging.getLogger(__name__)

        base_url = (self._fallback_local_llm_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._fallback_local_llm_api_key.value or "").strip()
        logger.info("[FetchModels][FallbackLocal] Requesting %s/models (has_key=%s)", base_url, bool(api_key))

        if self.model_discovery is None:
            return

        try:
            model_ids = asyncio.run(self.model_discovery.fetch_models(base_url, api_key))
        except Exception as exc:
            logger.error("[FetchModels][FallbackLocal] Failed: %s", exc)
            return

        if not model_ids:
            return

        if len(model_ids) == 1:
            self._fallback_local_llm_model.value = model_ids[0]
            if self._settings:
                draft = self._ensure_provider_settings_draft()
                draft.backup_translation.local_llm.model = model_ids[0]
                self.has_provider_changes = True
            _update_control_if_mounted(self._fallback_local_llm_model)
            return

        options = [OptionItem(value=m, label=m) for m in model_ids]
        current_value = (self._fallback_local_llm_model.value or "").strip()

        def _on_model_selected(value: str) -> None:
            self._fallback_local_llm_model.value = value
            logger.info("[FetchModels][FallbackLocal] User selected %s", value)
            _update_control_if_mounted(self._fallback_local_llm_model)
            if self._settings:
                draft = self._ensure_provider_settings_draft()
                draft.backup_translation.local_llm.model = value
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

    def _test_fallback_local_llm_connection(self, e) -> None:
        import asyncio
        import logging

        logger = logging.getLogger(__name__)

        base_url = (self._fallback_local_llm_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._fallback_local_llm_api_key.value or "").strip()

        logger.info("[TestConnection][FallbackLocal] Pinging %s (has_key=%s)", base_url, bool(api_key))

        if self.model_discovery is None:
            return

        try:
            status_code, body = asyncio.run(self.model_discovery.test_connection(base_url, api_key))
        except Exception as exc:
            logger.error("[TestConnection][FallbackLocal] Failed: %s", exc)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.failed", default="Connection failed"), ft.Colors.RED_400)
            return

        if status_code == 200:
            logger.info("[TestConnection][FallbackLocal] OK (%d)", status_code)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.ok", default="Server is responding"), ft.Colors.GREEN_400)
        elif status_code == 401:
            logger.info("[TestConnection][FallbackLocal] 401 — needs API key")
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.needs_key", default="Server reachable, API key required"), ft.Colors.ORANGE_400)
        else:
            logger.warning("[TestConnection][FallbackLocal] %d — %s", status_code, body)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.error", default=f"Server returned {status_code}"), ft.Colors.RED_400)
