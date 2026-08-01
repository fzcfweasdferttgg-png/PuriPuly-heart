"""LlmSectionMixin — LLM/translation provider, local LLM, and OpenAI-compatible controls."""

from __future__ import annotations

import contextlib
import copy
import json

import flet as ft

from puripuly_heart.config.prompts import load_prompt_for_provider
from puripuly_heart.config.settings import (
    LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS,
    LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS,
    AppSettings,
    LLMProviderName,
    TranslationConnection,
    TranslationModel,
    _normalize_local_llm_base_url,
    default_translation_connection,
    materialize_translation_settings,
    supported_translation_connections,
)
from puripuly_heart.ui.components.settings import OptionItem, SettingsModal
from puripuly_heart.ui.i18n import provider_label, t


def _update_control_if_mounted(control: ft.Control) -> None:
    """Update a Flet control only while it is attached to a page."""
    if getattr(control, "page", None) is None:
        return
    try:
        control.update()
    except AssertionError as exc:
        if "Control must be added" not in str(exc):
            raise


def _reject_json_constant(value: str) -> None:
    raise json.JSONDecodeError(f"invalid JSON constant: {value}", value, 0)


_TRANSLATION_MODEL_LABEL_KEYS = {
    TranslationModel.LOCAL_LLM: "provider.local_llms",
    TranslationModel.OPENAI_COMPATIBLE: "provider.openai_compatible",
}
_TRANSLATION_CONNECTION_LABEL_KEYS = {
    TranslationConnection.LOCAL: "settings.translation_connection.local",
    TranslationConnection.OPENAI_COMPATIBLE: "settings.translation_connection.openai_compatible",
}
_TRANSLATION_CONNECTION_DESCRIPTION_KEYS = {
    TranslationConnection.LOCAL: "settings.translation_connection.local.description",
    TranslationConnection.OPENAI_COMPATIBLE: "settings.translation_connection.openai_compatible.description",
}
_TRANSLATION_CONNECTION_ONLY_SUPPORTED_KEY = "settings.translation_connection.only_supported"


class LlmSectionMixin:
    """Mixin providing LLM/translation section methods for SettingsView."""

    def _get_llm_modal_value(self, settings: AppSettings) -> str:
        return settings.translation.model.value

    def _translation_model_display_label(self, model: TranslationModel) -> str:
        return t(_TRANSLATION_MODEL_LABEL_KEYS[model])

    def _translation_connection_display_label(self, connection: TranslationConnection) -> str:
        return t(_TRANSLATION_CONNECTION_LABEL_KEYS[connection])

    def _translation_connection_display_description(self, connection: TranslationConnection) -> str:
        return t(_TRANSLATION_CONNECTION_DESCRIPTION_KEYS[connection], default="")

    def _translation_connection_only_supported_description(self) -> str:
        return t(_TRANSLATION_CONNECTION_ONLY_SUPPORTED_KEY, default="")

    def _set_translation_connection_text(self, text: str) -> None:
        text_control = self._translation_connection_text.content
        text_control.value = text
        text_control.size = 28

    def _get_llm_display_label(self, settings: AppSettings) -> str:
        return self._translation_model_display_label(settings.translation.model)

    def _get_translation_connection_display_label(self, settings: AppSettings | None) -> str:
        if settings is None:
            return self._translation_connection_display_label(TranslationConnection.OPENAI_COMPATIBLE)
        return self._translation_connection_display_label(settings.translation.connection)

    def _active_prompt_key_for_settings(self, settings: AppSettings | None) -> str:
        if settings is None:
            return "openai_compatible"
        if settings.provider.llm == LLMProviderName.LOCAL_LLM:
            return "local_llm"
        if settings.provider.llm == LLMProviderName.OPENAI_COMPATIBLE:
            return "openai_compatible"
        return "openai_compatible"

    def _active_prompt_key(self) -> str:
        return self._active_prompt_key_for_settings(self._build_settings_with_provider_draft())

    def _ensure_provider_prompt_value(self, settings: AppSettings, provider_name: str) -> str:
        prompt = settings.system_prompt
        if prompt.strip():
            settings.system_prompts = {}
            return prompt
        prompt = load_prompt_for_provider(provider_name)
        settings.system_prompt = prompt
        settings.system_prompts = {}
        return prompt

    def _local_llm_extra_body_error_message(
        self,
        message_key: str,
        **kwargs: object,
    ) -> str:
        if "key" not in kwargs:
            return t(message_key, **kwargs)
        template = t(message_key)
        with contextlib.suppress(Exception):
            return template.format(**kwargs)
        return template

    def _show_local_llm_extra_body_error(self, message_key: str, **kwargs: object) -> None:
        message = self._local_llm_extra_body_error_message(message_key, **kwargs)
        self._local_llm_extra_body_error_key = message_key
        self._local_llm_extra_body_error_kwargs = dict(kwargs)
        self._local_llm_extra_body_error.value = message
        self._local_llm_extra_body_error.visible = True
        self._local_llm_extra_body.error_text = message
        _update_control_if_mounted(self._local_llm_extra_body)
        _update_control_if_mounted(self._local_llm_extra_body_error)

    def _on_local_llm_field_change(self, e) -> None:
        _ = e
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if current.provider.llm != LLMProviderName.LOCAL_LLM:
            return
        self._ensure_provider_settings_draft()
        self.has_provider_changes = True

    def _clear_local_llm_extra_body_error(self) -> None:
        self._local_llm_extra_body_error_key = ""
        self._local_llm_extra_body_error_kwargs = {}
        self._local_llm_extra_body_error.value = ""
        self._local_llm_extra_body_error.visible = False
        self._local_llm_extra_body.error_text = None
        _update_control_if_mounted(self._local_llm_extra_body)
        _update_control_if_mounted(self._local_llm_extra_body_error)

    def _on_local_llm_base_url_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw_value = self._local_llm_base_url.value or ""
        try:
            normalized = _normalize_local_llm_base_url(raw_value)
        except ValueError:
            self._local_llm_base_url.error_text = t("settings.local_llm.base_url.invalid")
            _update_control_if_mounted(self._local_llm_base_url)
            return

        self._local_llm_base_url.error_text = None
        self._local_llm_base_url.value = normalized
        current = self._provider_settings_draft or self._settings
        if current.local_llm.base_url != normalized:
            draft = self._ensure_provider_settings_draft()
            draft.local_llm.base_url = normalized
            self.has_provider_changes = True
        _update_control_if_mounted(self._local_llm_base_url)

    def _on_local_llm_model_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        model = (self._local_llm_model.value or "").strip()
        if not model:
            self._local_llm_model.error_text = t("settings.local_llm.model.required")
            _update_control_if_mounted(self._local_llm_model)
            return

        self._local_llm_model.error_text = None
        self._local_llm_model.value = model
        current = self._provider_settings_draft or self._settings
        if current.local_llm.model != model:
            draft = self._ensure_provider_settings_draft()
            draft.local_llm.model = model
            self.has_provider_changes = True
        _update_control_if_mounted(self._local_llm_model)

    def _on_local_llm_extra_body_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw = (self._local_llm_extra_body.value or "").strip()
        try:
            parsed = (
                {"reasoning_effort": "none"}
                if not raw
                else json.loads(raw, parse_constant=_reject_json_constant)
            )
        except json.JSONDecodeError:
            self._show_local_llm_extra_body_error("settings.local_llm.extra_body.invalid_json")
            return

        if not isinstance(parsed, dict):
            self._show_local_llm_extra_body_error("settings.local_llm.extra_body.must_be_object")
            return

        lowered = {str(key).lower() for key in parsed}
        reserved = LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS.intersection(lowered)
        if reserved:
            self._show_local_llm_extra_body_error(
                "settings.local_llm.extra_body.reserved_key",
                key=sorted(reserved)[0],
            )
            return

        sensitive = LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS.intersection(lowered)
        if sensitive:
            self._show_local_llm_extra_body_error(
                "settings.local_llm.extra_body.sensitive_key",
                key=sorted(sensitive)[0],
            )
            return

        try:
            json.dumps(parsed, allow_nan=False)
        except (TypeError, ValueError):
            self._show_local_llm_extra_body_error("settings.local_llm.extra_body.not_serializable")
            return

        normalized = copy.deepcopy(parsed)
        current = self._provider_settings_draft or self._settings
        if current.local_llm.extra_body != normalized:
            draft = self._ensure_provider_settings_draft()
            draft.local_llm.extra_body = normalized
            self.has_provider_changes = True
        self._local_llm_extra_body.value = json.dumps(
            normalized,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        self._clear_local_llm_extra_body_error()
        _update_control_if_mounted(self._local_llm_extra_body)

    def _commit_local_llm_fields_from_controls(self) -> None:
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if current.provider.llm != LLMProviderName.LOCAL_LLM:
            return
        self._on_local_llm_base_url_change_end(None)
        self._on_local_llm_model_change_end(None)
        self._on_local_llm_extra_body_change_end(None)

    def _on_openai_compatible_field_change(self, e) -> None:
        _ = e
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if current.provider.llm != LLMProviderName.OPENAI_COMPATIBLE:
            return
        self._ensure_provider_settings_draft()
        self.has_provider_changes = True

    def _on_openai_compatible_model_selected(self, e) -> None:
        model = e.data if e else None
        if not model or not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        draft.provider.openai_compatible.model = model
        self.has_provider_changes = True

    def _on_fallback_model_selected(self, e) -> None:
        model = e.data if e else None
        if not model or not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        draft.provider.openai_compatible.fallback_model = model
        self.has_provider_changes = True

    def _on_openai_compatible_provider_change(self, e) -> None:
        from puripuly_heart.config.providers import load_providers
        selected = e.data if e else None
        if not selected:
            return
        providers = load_providers()
        provider_info = providers.get(selected)
        if not provider_info:
            return
        base_url = provider_info.get("base_url", "")
        if base_url and self._openai_compatible_base_url:
            self._openai_compatible_base_url.value = base_url
            _update_control_if_mounted(self._openai_compatible_base_url)
            if self._settings:
                current = self._provider_settings_draft or self._settings
                if current.provider.openai_compatible.base_url != base_url:
                    draft = self._ensure_provider_settings_draft()
                    draft.provider.openai_compatible.base_url = base_url
                    self.has_provider_changes = True

    def _on_fallback_toggle(self, e) -> None:
        if not self._settings:
            return
        enabled = e.data if e else False
        draft = self._ensure_provider_settings_draft()
        draft.provider.openai_compatible.fallback_enabled = bool(enabled)
        self.has_provider_changes = True
        _update_control_if_mounted(self._fallback_card)

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
        if base_url and hasattr(self, "_fallback_base_url"):
            self._fallback_base_url.value = base_url
            _update_control_if_mounted(self._fallback_base_url)
            if self._settings:
                draft = self._ensure_provider_settings_draft()
                draft.provider.openai_compatible.fallback_base_url = base_url
                self.has_provider_changes = True

    def _on_fallback_field_change(self, e) -> None:
        _ = e
        if not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        if hasattr(self, "_fallback_base_url"):
            draft.provider.openai_compatible.fallback_base_url = (self._fallback_base_url.value or "").strip()
        if hasattr(self, "_fallback_model"):
            draft.provider.openai_compatible.fallback_model = (self._fallback_model.value or "").strip()
        self.has_provider_changes = True

    def _fetch_models(self, e) -> None:
        self._do_fetch_models(
            base_url_field=self._openai_compatible_base_url,
            model_dropdown=self._openai_compatible_model,
        )

    def _fetch_fallback_models(self, e) -> None:
        self._do_fetch_models(
            base_url_field=self._fallback_base_url,
            model_dropdown=self._fallback_model,
        )

    def _do_fetch_models(self, *, base_url_field, model_dropdown) -> None:
        import asyncio
        import httpx

        base_url = (base_url_field.value or "").strip()
        if not base_url:
            return
        api_key = ""
        if hasattr(self, "_openai_compatible_key") and self._openai_compatible_key:
            api_key = (self._openai_compatible_key.value or "").strip()

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
                    if model_ids and model_dropdown:
                        current_value = model_dropdown.value
                        model_dropdown.options = [
                            ft.dropdown.Option(key=m, text=m) for m in model_ids
                        ]
                        if current_value in model_ids:
                            model_dropdown.value = current_value
                        else:
                            model_dropdown.value = model_ids[0]
                        _update_control_if_mounted(model_dropdown)
            except Exception:
                pass

        asyncio.ensure_future(_do_fetch())

    def _on_openai_compatible_base_url_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw_value = (self._openai_compatible_base_url.value or "").strip()
        if not raw_value:
            self._openai_compatible_base_url.error_text = t(
                "settings.openai_compatible.base_url.required", default="Base URL is required"
            )
            _update_control_if_mounted(self._openai_compatible_base_url)
            return

        self._openai_compatible_base_url.error_text = None
        self._openai_compatible_base_url.value = raw_value
        current = self._provider_settings_draft or self._settings
        if current.provider.openai_compatible.base_url != raw_value:
            draft = self._ensure_provider_settings_draft()
            draft.provider.openai_compatible.base_url = raw_value
            self.has_provider_changes = True
        _update_control_if_mounted(self._openai_compatible_base_url)

    def _commit_openai_compatible_fields_from_controls(self) -> None:
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if current.provider.llm != LLMProviderName.OPENAI_COMPATIBLE:
            return
        self._on_openai_compatible_base_url_change_end(None)

    def _on_qwen_region_click(self, e) -> None:
        pass

    def _on_llm_click(self, e) -> None:
        """Open LLM provider selection modal."""
        if not self.page:
            return
        recommended_section = t("settings.translation_model.section.recommended")
        others_section = t("settings.translation_model.section.others")
        model_sections = (
            (TranslationModel.LOCAL_LLM, recommended_section),
            (TranslationModel.OPENAI_COMPATIBLE, recommended_section),
        )
        options = [
            OptionItem(
                value=model.value,
                label=self._translation_model_display_label(model),
                description=t(f"settings.translation_model.{model.value}.description", default=""),
                section=section,
            )
            for model, section in model_sections
        ]
        display_settings = self._build_settings_with_provider_draft()
        current = (
            self._get_llm_modal_value(display_settings)
            if display_settings is not None
            else TranslationModel.OPENAI_COMPATIBLE.value
        )
        modal = SettingsModal(
            self.page,
            t("settings.section.translation"),
            options,
            self._on_llm_selected,
            show_description=True,
        )
        modal.open(current)

    def _restore_translation_connection_for_model(
        self,
        model: TranslationModel,
        history: dict[str, TranslationConnection],
    ) -> TranslationConnection:
        connection = history.get(model.value)
        if not isinstance(connection, TranslationConnection):
            try:
                connection = TranslationConnection(str(connection))
            except (TypeError, ValueError):
                connection = None
        if connection in supported_translation_connections(model):
            return connection
        return default_translation_connection(model)

    def _sync_translation_selection_controls(self, settings: AppSettings) -> None:
        self._set_unit_card_value_text(
            self._llm_text,
            self._get_llm_display_label(settings),
        )
        self._set_translation_connection_text(
            self._get_translation_connection_display_label(settings),
        )

    def _apply_translation_selection(
        self,
        model: TranslationModel,
        connection: TranslationConnection,
    ) -> None:
        if not self._settings:
            return
        if connection not in supported_translation_connections(model):
            return

        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        old_model = current_settings.translation.model
        old_connection = current_settings.translation.connection
        old_provider = current_settings.provider.llm
        if old_model == model and old_connection == connection:
            return

        draft = self._ensure_provider_settings_draft()
        draft.translation = copy.deepcopy(current_settings.translation)
        draft.translation.model = model
        draft.translation.connection = connection
        draft.translation.connection_history = copy.deepcopy(
            current_settings.translation.connection_history
        )
        draft.translation.connection_history[model.value] = connection
        materialize_translation_settings(draft)
        new_provider = draft.provider.llm

        changes: list[str] = []
        if old_model != model:
            changes.append(f"model={old_model.value}->{model.value}")
        if old_connection != connection:
            changes.append(f"connection={old_connection.value}->{connection.value}")
        if old_provider != new_provider:
            changes.append(f"provider={old_provider.value}->{new_provider.value}")
            self._emit_runtime_basic(
                f"[Settings] LLM provider changed: {old_provider.value} -> {new_provider.value}"
            )
        if changes:
            self._emit_runtime_detailed(
                f"[Settings] Translation selection changed: {', '.join(changes)}"
            )

        self.has_provider_changes = True
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)

        display_settings = merged
        self._sync_translation_selection_controls(display_settings)

        if old_provider != display_settings.provider.llm:
            provider_name = self._active_prompt_key()
            self._prompt_editor.set_provider(provider_name)
            next_prompt = self._ensure_provider_prompt_value(draft, provider_name)
            self._prompt_editor.value = next_prompt
            draft.system_prompt = next_prompt
        self._sync_prompt_tab_copy()

        if self.page:
            self._qwen_region_btn.update()
            self._llm_text.update()
            self._translation_connection_row.update()
            self._local_llm_connection_card.update()
            self._api_keys_column.update()

    def _on_llm_selected(self, value: str) -> None:
        """Handle LLM provider selection from modal."""
        if not self._settings:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        try:
            model = TranslationModel(value)
        except (TypeError, ValueError):
            return

        if current_settings.translation.model == model:
            return
        history = copy.deepcopy(current_settings.translation.connection_history)
        connection = self._restore_translation_connection_for_model(model, history)
        self._apply_translation_selection(model, connection)

    def _on_translation_connection_click(self, e) -> None:
        if not self.page:
            return
        display_settings = self._build_settings_with_provider_draft()
        model = (
            display_settings.translation.model
            if display_settings is not None
            else TranslationModel.OPENAI_COMPATIBLE
        )
        connections = supported_translation_connections(model)
        options = [
            OptionItem(
                value=connection.value,
                label=self._translation_connection_display_label(connection),
            )
            for connection in connections
        ]
        current = (
            display_settings.translation.connection.value
            if display_settings is not None
            else default_translation_connection(model).value
        )
        modal = SettingsModal(
            self.page,
            t("settings.translation_connection"),
            options,
            self._on_translation_connection_selected,
            show_description=False,
        )
        modal.open(current)

    def _on_translation_connection_selected(self, value: str) -> None:
        if not self._settings:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        model = current_settings.translation.model
        try:
            connection = TranslationConnection(value)
        except (TypeError, ValueError):
            return
        if connection not in supported_translation_connections(model):
            return
        self._apply_translation_selection(model, connection)
