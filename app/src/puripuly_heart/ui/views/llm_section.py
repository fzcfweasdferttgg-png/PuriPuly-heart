"""LlmSectionMixin — LLM/translation provider, local LLM, and OpenAI-compatible controls."""

from __future__ import annotations

import contextlib
import copy
import json
from typing import TYPE_CHECKING

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
from puripuly_heart.ui.components.settings import ApiKeyField, OptionItem, SettingsModal
from puripuly_heart.ui.i18n import provider_label, t
from puripuly_heart.ui.theme import COLOR_DIVIDER, COLOR_NEUTRAL, COLOR_ON_BACKGROUND, COLOR_PRIMARY, COLOR_NEUTRAL_DARK

from puripuly_heart.ui.views.settings_helpers import _update_control_if_mounted

if TYPE_CHECKING:
    from puripuly_heart.ui.views.settings import SettingsView


def _reject_json_constant(value: str) -> None:
    raise json.JSONDecodeError(f"invalid JSON constant: {value}", value, 0)


_TRANSLATION_MODEL_LABEL_KEYS = {
    TranslationModel.LOCAL_LLM: "provider.local_llms",
    TranslationModel.OPENAI_COMPATIBLE: "provider.openai_compatible",
}

# AI: ATTRIBUTE OWNERSHIP — widget builders create:
#   _build_llm_widgets: _llm_text, _trans_title, _translation_provider_label,
#     _openai_compatible_key (ApiKeyField)
#   _build_local_llm_widgets: _local_llm_connection_title/base_url/model/fetch_btn/test_btn/
#     api_key/api_key_helper/extra_body/extra_body_helper/extra_body_error/...,
#     _local_llm_connection_card
#   _build_openai_compat_widgets: _openai_compatible_title/provider/base_url/model/
#     fetch_btn/test_btn, _translation_openai_card
#
# LOCALE: _apply_locale_llm handles LLM section labels only.
# Fallback locale handled by FallbackSectionMixin._apply_locale_fallback()
# and FallbackLocalLlmSectionMixin._apply_locale_fallback_local_llm().

class LlmSectionMixin:
    """Mixin providing LLM/translation section methods for SettingsView."""

    def _get_llm_modal_value(self, settings: AppSettings) -> str:
        return settings.translation.model.value

    def _translation_model_display_label(self, model: TranslationModel) -> str:
        return t(_TRANSLATION_MODEL_LABEL_KEYS[model])

    def _get_llm_display_label(self, settings: AppSettings) -> str:
        return self._translation_model_display_label(settings.translation.model)

    # AI: LOAD ORDER — must be called after _build_llm_widgets and _build_openai_compat_widgets
    # have created the text fields. hasattr guard on _llm_text protects against pre-build calls.
    # Loads both main translation AND OpenAI-compatible provider fields from settings.
    def _load_llm_from_settings(self, settings: "AppSettings") -> None:
        """Load LLM provider, local LLM, and OpenAI-compatible fields from settings."""
        if not hasattr(self, '_llm_text'):
            return
        self._set_unit_card_value_text(
            self._llm_text,
            self._get_llm_display_label(settings),
        )
        self._local_llm_base_url.value = settings.local_llm.base_url
        self._local_llm_base_url.error_text = None
        self._local_llm_model.value = settings.local_llm.model
        self._local_llm_model.error_text = None
        self._local_llm_extra_body.value = json.dumps(
            settings.local_llm.extra_body,
            ensure_ascii=False,
            indent=2,
        )
        self._clear_local_llm_extra_body_error()
        # OpenAI Compatible
        self._openai_compatible_base_url.value = settings.provider.openai_compatible.base_url
        self._openai_compatible_base_url.error_text = None
        self._openai_compatible_model.value = settings.provider.openai_compatible.model or ""
        from puripuly_heart.config.providers import load_providers
        _loaded_providers = load_providers()
        _opts = self._openai_compatible_provider.options or []
        _matched = _opts[0].key if _opts else None
        for _pk, _pi in _loaded_providers.items():
            if _pi.get("base_url") == settings.provider.openai_compatible.base_url:
                _matched = _pk
                break
        self._openai_compatible_provider.value = _matched

    def _active_prompt_key_for_settings(self, settings: AppSettings | None) -> str:
        if settings is None:
            return "openai_compatible"
        if settings.provider.llm == LLMProviderName.LOCAL_LLM:
            return "local_llm"
        if settings.provider.llm == LLMProviderName.OPENAI_COMPATIBLE:
            return "openai_compatible"
        return "openai_compatible"

    # AI: PROMPT KEY RESOLUTION — determines which system prompt to show based on
    # current LLM provider. "local_llm" or "openai_compatible". Used by
    # _ensure_provider_prompt_value and _on_llm_selected to sync prompt editor
    # when provider changes.
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
        import logging
        logger = logging.getLogger(__name__)
        _ = e
        if not self._settings:
            return
        raw_value = (self._openai_compatible_base_url.value or "").strip()
        if not raw_value:
            self._openai_compatible_base_url.error_text = t(
                "settings.openai_compatible.base_url.required", default="Base URL is required"
            )
            _update_control_if_mounted(self._openai_compatible_base_url)
            logger.warning(
                "[LLMSection] Main translation base_url is empty — validation error shown in UI"
            )
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

    # AI: TRANSLATION MODEL+CONNECTION CHANGE — the most complex mutation path.
    # When user selects a new translation model+connection:
    # 1. Validates connection is supported for model
    # 2. Creates draft, sets model+connection, updates connection_history
    # 3. Calls materialize_translation_settings to derive provider from model+connection
    # 4. Calls _update_api_visibility (merged settings to avoid redundant deepcopy)
    # 5. If provider changed, switches prompt editor to new provider's prompt
    # 6. Syncs prompt tab copy text
    #
    # connection_history persists user's last-used connection per model — enables
    # restoring previous selection when toggling back.
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

    # --- Widget builders (Phase 2.4) ---

    def _build_llm_widgets(self) -> ft.Control:
        """Section B: Translation Provider card and API key field."""
        self._llm_text = self._build_clickable_text(
            t("provider.gemini3_flash"),
            self._on_llm_click,
        )
        self._trans_title = ft.Text(
            t("settings.section.translation"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._translation_provider_label = ft.Text(
            t("settings.shared_translation_provider"), size=16, color=COLOR_ON_BACKGROUND
        )
        trans_card = self._wrap_unit_card(
            title=self._trans_title,
            value=self._llm_text,
        )

        # API Key for Translation card (inline)
        self._openai_compatible_key = ApiKeyField(
            "settings.openai_compatible_api_key",
            "openai_compatible_api_key",
            "openai_compatible",
            on_verify=self._verify_key,
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
            base_url_getter=lambda: self._openai_compatible_base_url.value,
        )
        return trans_card

    # AI: WIDGET BUILD — creates _local_llm_connection_card (initially visible=False).
    # Visibility is toggled by _update_api_visibility based on LLM provider.
    # The card contains: base_url, model, api_key, extra_body text fields.
    # _local_llm_api_key is an ApiKeyField (with verify/save/show_snackbar callbacks).
    def _build_local_llm_widgets(self) -> ft.Control:
        """Section M: Local LLM Connection card."""
        self._local_llm_connection_title = ft.Text(
            t("settings.openai_compatible.connection", default="Translation Settings"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._local_llm_base_url = ft.TextField(
            label=t("settings.local_llm.base_url"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_local_llm_field_change,
            on_blur=self._on_local_llm_base_url_change_end,
            on_submit=self._on_local_llm_base_url_change_end,
        )
        self._local_llm_model = ft.TextField(
            label=t("settings.local_llm.model"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_local_llm_field_change,
            on_blur=self._on_local_llm_model_change_end,
            on_submit=self._on_local_llm_model_change_end,
        )
        self._local_llm_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip=t("settings.openai_compatible.fetch_models", default="Fetch models from API"),
            on_click=self._fetch_local_llm_models,
        )
        self._local_llm_test_btn = ft.TextButton(
            text=t("settings.local_llm.test_connection", default="Test connection"),
            on_click=self._test_local_llm_connection,
        )
        self._local_llm_api_key = ApiKeyField(
            "settings.local_llm.api_key",
            "local_llm_api_key",
            "local_llm",
            on_verify=None,
            on_save=self._on_local_llm_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
            show_status=False,
        )
        local_llm_api_key_description = t("settings.local_llm.api_key.description")
        self._local_llm_api_key_helper = ft.Text(
            local_llm_api_key_description,
            size=15,
            color=COLOR_NEUTRAL,
            visible=bool(local_llm_api_key_description.strip()),
        )
        self._local_llm_extra_body = ft.TextField(
            label=t("settings.local_llm.extra_body"),
            value=json.dumps({"reasoning_effort": "none"}, ensure_ascii=False, indent=2),
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
            on_change=self._on_local_llm_field_change,
            on_blur=self._on_local_llm_extra_body_change_end,
            on_submit=self._on_local_llm_extra_body_change_end,
        )
        self._local_llm_extra_body_helper = ft.Text(
            t("settings.local_llm.extra_body.description"),
            size=15,
            color=COLOR_NEUTRAL,
        )
        self._local_llm_extra_body_error = ft.Text(
            "",
            size=13,
            color=ft.Colors.RED_600,
            visible=False,
        )
        self._local_llm_extra_body_error_key = ""
        self._local_llm_extra_body_error_kwargs: dict[str, object] = {}
        self._local_llm_connection_card = self._wrap_card(
            ft.Column(
                [
                    self._local_llm_connection_title,
                    ft.Container(height=4),
                    ft.Row([self._local_llm_base_url, self._local_llm_test_btn], spacing=4),
                    ft.Row([self._local_llm_model, self._local_llm_fetch_btn], spacing=4),
                    self._local_llm_api_key,
                    self._local_llm_api_key_helper,
                    self._local_llm_extra_body,
                    self._local_llm_extra_body_helper,
                    self._local_llm_extra_body_error,
                ],
                spacing=8,
            ),
            height=None,
        )
        self._local_llm_connection_card.visible = False
        return self._local_llm_connection_card

    # AI: WIDGET BUILD — creates _translation_openai_card (initially visible=False).
    # Includes a provider Dropdown that auto-fills base_url from config/providers.json.
    # When provider changes, model is cleared (provider-specific models differ).
    # _openai_compatible_key is an ApiKeyField with base_url_getter lambda.
    def _build_openai_compat_widgets(self) -> ft.Control:
        """Section N: OpenAI-Compatible translation card."""
        self._openai_compatible_title = ft.Text(
            t("settings.openai_compatible.connection", default="Translation Provider Settings"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        from puripuly_heart.config.providers import load_providers
        _providers = load_providers()
        _provider_options = []
        for key, info in _providers.items():
            _provider_options.append(ft.dropdown.Option(key=key, text=info.get("label", key)))
        self._openai_compatible_provider = ft.Dropdown(
            label=t("settings.openai_compatible.provider", default="Provider"),
            options=_provider_options,
            value=_provider_options[0].key if _provider_options else None,
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_openai_compatible_provider_change,
        )
        self._openai_compatible_base_url = ft.TextField(
            label=t("settings.openai_compatible.base_url", default="Base URL"),
            value="https://api.openai.com/v1",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_openai_compatible_field_change,
            on_blur=self._on_openai_compatible_base_url_change_end,
            on_submit=self._on_openai_compatible_base_url_change_end,
        )
        self._openai_compatible_model = ft.TextField(
            label=t("settings.openai_compatible.model", default="Model"),
            hint_text=t("settings.openai_compatible.model.hint", default="Enter model name or click refresh"),
            value="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            expand=True,
            text_size=24,
            color=COLOR_NEUTRAL_DARK,
            label_style=ft.TextStyle(size=18, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL_DARK),
            on_change=self._on_openai_compatible_field_change,
            on_blur=self._on_openai_compatible_model_change_end,
            on_submit=self._on_openai_compatible_model_change_end,
        )
        self._openai_compatible_fetch_btn = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip=t("settings.openai_compatible.fetch_models", default="Fetch models from API"),
            on_click=self._fetch_models,
        )
        self._openai_compatible_test_btn = ft.TextButton(
            text=t("settings.local_llm.test_connection", default="Test connection"),
            on_click=self._test_openai_compatible_connection,
        )
        self._translation_openai_card = self._wrap_card(
            ft.Column(
                [
                    self._openai_compatible_title,
                    ft.Container(height=4),
                    self._openai_compatible_provider,
                    ft.Row([self._openai_compatible_model, self._openai_compatible_fetch_btn], spacing=4),
                    self._openai_compatible_key,
                    self._openai_compatible_test_btn,
                ],
                spacing=8,
            ),
            height=None,
        )
        self._translation_openai_card.visible = False
        return self._translation_openai_card

    # ------------------------------------------------------------------
    # Locale helpers — LLM section only.
    # Fallback locale handled by FallbackSectionMixin._apply_locale_fallback()
    # and FallbackLocalLlmSectionMixin._apply_locale_fallback_local_llm().
    # ------------------------------------------------------------------

    def _apply_locale_llm(self) -> None:
        """Re-translate LLM / translation section labels (NOT fallback)."""
        if not hasattr(self, '_local_llm_connection_title'):
            return
        # Translation section labels (owned by LlmSectionMixin)
        self._trans_title.value = t("settings.section.translation")
        self._translation_provider_label.value = t("settings.shared_translation_provider")
        # Local LLM labels
        self._local_llm_connection_title.value = t("settings.openai_compatible.connection", default="Translation Settings")
        self._local_llm_base_url.label = t("settings.local_llm.base_url")
        self._local_llm_model.label = t("settings.local_llm.model")
        self._local_llm_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models", default="Fetch models from API")
        self._local_llm_test_btn.text = t("settings.local_llm.test_connection", default="Test connection")
        self._openai_compatible_test_btn.text = t("settings.local_llm.test_connection", default="Test connection")
        self._local_llm_api_key.apply_locale()
        local_llm_api_key_description = t("settings.local_llm.api_key.description")
        self._local_llm_api_key_helper.value = local_llm_api_key_description
        self._local_llm_api_key_helper.visible = bool(local_llm_api_key_description.strip())
        self._local_llm_extra_body.label = t("settings.local_llm.extra_body")
        self._local_llm_extra_body_helper.value = t("settings.local_llm.extra_body.description")
        # Translation OpenAI-compatible labels
        self._openai_compatible_title.value = t("settings.openai_compatible.connection", default="Translation Provider Settings")
        self._openai_compatible_provider.label = t("settings.openai_compatible.provider", default="Provider")
        self._openai_compatible_base_url.label = t("settings.openai_compatible.base_url", default="Base URL")
        self._openai_compatible_model.label = t("settings.openai_compatible.model", default="Model")
        self._openai_compatible_model.hint_text = t("settings.openai_compatible.model.hint", default="Enter model name or click refresh")
        self._openai_compatible_fetch_btn.tooltip = t("settings.openai_compatible.fetch_models", default="Fetch models from API")
        self._openai_compatible_key.apply_locale()
        # Error texts
        if self._local_llm_base_url.error_text:
            self._local_llm_base_url.error_text = t("settings.local_llm.base_url.invalid")
        if self._local_llm_model.error_text:
            self._local_llm_model.error_text = t("settings.local_llm.model.required")
        if self._local_llm_extra_body_error.visible:
            error_key = self._local_llm_extra_body_error_key
            error_kwargs = self._local_llm_extra_body_error_kwargs
            if error_key:
                message = self._local_llm_extra_body_error_message(error_key, **error_kwargs)
                self._local_llm_extra_body_error.value = message
                self._local_llm_extra_body.error_text = message
