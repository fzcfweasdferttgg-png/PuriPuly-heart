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


class LlmSectionMixin:
    """Mixin providing LLM/translation section methods for SettingsView."""

    def _get_llm_modal_value(self, settings: AppSettings) -> str:
        return settings.translation.model.value

    def _translation_model_display_label(self, model: TranslationModel) -> str:
        return t(_TRANSLATION_MODEL_LABEL_KEYS[model])

    def _get_llm_display_label(self, settings: AppSettings) -> str:
        return self._translation_model_display_label(settings.translation.model)

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

    def _on_openai_compatible_model_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw_value = (self._openai_compatible_model.value or "").strip()
        current = self._provider_settings_draft or self._settings
        if current.provider.openai_compatible.model != raw_value:
            draft = self._ensure_provider_settings_draft()
            draft.provider.openai_compatible.model = raw_value
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
                    draft.provider.openai_compatible.model = ""
                    self.has_provider_changes = True
            if self._openai_compatible_model:
                self._openai_compatible_model.value = ""
                _update_control_if_mounted(self._openai_compatible_model)

    def _fetch_models(self, e) -> None:
        self._do_fetch_models(
            base_url_field=self._openai_compatible_base_url,
            model_field=self._openai_compatible_model,
            api_key_field=self._openai_compatible_key,
        )

    def _do_fetch_models(self, *, base_url_field, model_field, api_key_field=None) -> None:
        import asyncio
        import logging
        from puripuly_heart.ui.components.settings.settings_modal import OptionItem, SettingsModal

        logger = logging.getLogger(__name__)

        base_url = (base_url_field.value or "").strip()
        if not base_url:
            return
        api_key = ""
        if api_key_field is not None:
            api_key = (api_key_field.value or "").strip()

        logger.info("[FetchModels] Requesting %s/models (has_key=%s)", base_url, bool(api_key))

        if self.model_discovery is None:
            logger.error("[FetchModels] model_discovery not initialized")
            return

        try:
            model_ids = asyncio.run(self.model_discovery.fetch_models(base_url, api_key))
        except Exception as exc:
            logger.error("[FetchModels] Failed: %s", exc)
            return

        logger.info("[FetchModels] Got %d models: %s", len(model_ids), model_ids[:5])

        if not model_ids:
            return

        if len(model_ids) == 1:
            model_field.value = model_ids[0]
            logger.info("[FetchModels] Single model, set to %s", model_ids[0])
            _update_control_if_mounted(model_field)
            return

        options = [OptionItem(value=m, label=m) for m in model_ids]
        current_value = (model_field.value or "").strip()

        def _on_model_selected(value: str) -> None:
            model_field.value = value
            logger.info("[FetchModels] User selected %s", value)
            _update_control_if_mounted(model_field)
            if self._settings:
                draft = self._ensure_provider_settings_draft()
                draft.provider.openai_compatible.model = value
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

    def _fetch_local_llm_models(self, e) -> None:
        self._do_fetch_models(
            base_url_field=self._local_llm_base_url,
            model_field=self._local_llm_model,
            api_key_field=self._local_llm_api_key,
        )

    def _test_local_llm_connection(self, e) -> None:
        import asyncio
        import logging

        logger = logging.getLogger(__name__)

        base_url = (self._local_llm_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._local_llm_api_key.value or "").strip()

        logger.info("[TestConnection] Pinging %s (has_key=%s)", base_url, bool(api_key))

        if self.model_discovery is None:
            logger.error("[TestConnection] model_discovery not initialized")
            return

        try:
            status_code, body = asyncio.run(self.model_discovery.test_connection(base_url, api_key))
        except Exception as exc:
            logger.error("[TestConnection] Failed: %s", exc)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.failed", default="Connection failed"), ft.Colors.RED_400)
            return

        if status_code == 200:
            logger.info("[TestConnection] OK (%d)", status_code)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.ok", default="Server is responding"), ft.Colors.GREEN_400)
        elif status_code == 401:
            logger.info("[TestConnection] 401 — server reachable but needs API key")
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.needs_key", default="Server reachable, API key required"), ft.Colors.ORANGE_400)
        else:
            logger.warning("[TestConnection] %d — %s", status_code, body)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.error", default=f"Server returned {status_code}"), ft.Colors.RED_400)

    def _test_openai_compatible_connection(self, e) -> None:
        import asyncio
        import logging

        logger = logging.getLogger(__name__)

        base_url = (self._openai_compatible_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._openai_compatible_key.value or "").strip()

        logger.info("[TestConnection][OpenAI] Pinging %s (has_key=%s)", base_url, bool(api_key))

        if self.model_discovery is None:
            return

        try:
            status_code, body = asyncio.run(self.model_discovery.test_connection(base_url, api_key))
        except Exception as exc:
            logger.error("[TestConnection][OpenAI] Failed: %s", exc)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.failed", default="Connection failed"), ft.Colors.RED_400)
            return

        if status_code == 200:
            logger.info("[TestConnection][OpenAI] OK (%d)", status_code)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.ok", default="Server is responding"), ft.Colors.GREEN_400)
        elif status_code == 401:
            logger.info("[TestConnection][OpenAI] 401 — needs API key")
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.needs_key", default="Server reachable, API key required"), ft.Colors.ORANGE_400)
        else:
            logger.warning("[TestConnection][OpenAI] %d — %s", status_code, body)
            if self.show_snackbar:
                self.show_snackbar(t("settings.local_llm.test_connection.error", default=f"Server returned {status_code}"), ft.Colors.RED_400)

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

    def _on_llm_click(self, e) -> None:
        """Open LLM provider selection modal."""
        if not self.page:
            return
        model_sections = (
            (TranslationModel.LOCAL_LLM, None),
            (TranslationModel.OPENAI_COMPATIBLE, None),
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
            _update_control_if_mounted(self._llm_text)
            _update_control_if_mounted(self._translation_connection_row)
            _update_control_if_mounted(self._local_llm_connection_card)

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
