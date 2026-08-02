"""FallbackLocalLlmSectionMixin — Fallback local LLM card controls."""

from __future__ import annotations

import json

import flet as ft

from puripuly_heart.config.settings import LLMProviderName
from puripuly_heart.ui.components.settings import ApiKeyField
from puripuly_heart.ui.i18n import t

from puripuly_heart.ui.views.settings_helpers import _update_control_if_mounted


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"Invalid JSON constant: {value}")


class FallbackLocalLlmSectionMixin:
    """Fallback local LLM card: Base URL, Model, API Key, Extra Body."""

    def _init_fallback_local_llm_controls(
        self,
        *,
        on_verify,
        on_save,
        show_snackbar,
    ) -> None:
        self._fallback_local_llm_base_url = ft.TextField(
            label=t("settings.local_llm.base_url", default="Base URL"),
            value="",
            border_radius=12,
            expand=True,
            text_size=24,
            on_change=self._on_fallback_local_llm_field_change,
            on_blur=self._on_fallback_local_llm_base_url_change_end,
            on_submit=self._on_fallback_local_llm_base_url_change_end,
        )
        self._fallback_local_llm_model = ft.TextField(
            label=t("settings.local_llm.model", default="Model"),
            value="",
            border_radius=12,
            expand=True,
            text_size=24,
            on_change=self._on_fallback_local_llm_field_change,
            on_blur=self._on_fallback_local_llm_model_change_end,
            on_submit=self._on_fallback_local_llm_model_change_end,
        )
        self._fallback_local_llm_api_key = ApiKeyField(
            "settings.fallback_local_llm_api_key",
            "fallback_local_llm_api_key",
            "local_llm",
            on_verify=on_verify,
            on_save=on_save,
            show_snackbar=show_snackbar,
            show_status=False,
        )
        self._fallback_local_llm_api_key_helper = ft.Text(
            t("settings.local_llm.api_key.description", default=""),
            size=15,
            visible=False,
        )
        self._fallback_local_llm_extra_body = ft.TextField(
            label=t("settings.local_llm.extra_body", default="Extra Body"),
            value="",
            multiline=True,
            min_lines=3,
            max_lines=6,
            border_radius=12,
            expand=True,
            text_size=24,
            on_change=self._on_fallback_local_llm_field_change,
            on_blur=self._on_fallback_local_llm_extra_body_change_end,
            on_submit=self._on_fallback_local_llm_extra_body_change_end,
        )
        self._fallback_local_llm_extra_body_helper = ft.Text(
            t("settings.local_llm.extra_body.description", default=""),
            size=15,
        )
        self._fallback_local_llm_extra_body_error = ft.Text(
            "",
            size=13,
            color=ft.Colors.RED_600,
            visible=False,
        )

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
        raw_value = (self._fallback_local_llm_base_url.value or "").strip()
        self._fallback_local_llm_base_url.error_text = None
        self._fallback_local_llm_base_url.value = raw_value
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.local_llm.base_url != raw_value:
            draft = self._ensure_provider_settings_draft()
            draft.backup_translation.local_llm.base_url = raw_value
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

    def _on_fallback_local_llm_extra_body_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw = (self._fallback_local_llm_extra_body.value or "").strip()
        try:
            parsed = (
                {}
                if not raw
                else json.loads(raw, parse_constant=_reject_json_constant)
            )
        except (json.JSONDecodeError, ValueError):
            self._fallback_local_llm_extra_body_error.value = t(
                "settings.local_llm.extra_body.must_be_object", default="Enter a JSON object."
            )
            self._fallback_local_llm_extra_body_error.visible = True
            _update_control_if_mounted(self._fallback_local_llm_extra_body_error)
            return
        if not isinstance(parsed, dict):
            self._fallback_local_llm_extra_body_error.value = t(
                "settings.local_llm.extra_body.must_be_object", default="Enter a JSON object."
            )
            self._fallback_local_llm_extra_body_error.visible = True
            _update_control_if_mounted(self._fallback_local_llm_extra_body_error)
            return
        self._fallback_local_llm_extra_body_error.value = ""
        self._fallback_local_llm_extra_body_error.visible = False
        current = self._provider_settings_draft or self._settings
        if current.backup_translation.local_llm.extra_body != parsed:
            draft = self._ensure_provider_settings_draft()
            draft.backup_translation.local_llm.extra_body = parsed
            self.has_provider_changes = True
        _update_control_if_mounted(self._fallback_local_llm_extra_body_error)

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
