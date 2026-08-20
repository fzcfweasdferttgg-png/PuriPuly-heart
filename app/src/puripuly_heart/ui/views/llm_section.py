from __future__ import annotations

import contextlib
import copy
import json
from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.config.prompts import load_prompt_for_provider
from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    TranslationConnection,
    TranslationModel,
    _normalize_local_llm_base_url,
    default_translation_connection,
    supported_translation_connections,
)
from puripuly_heart.domain.settings_commands import (
    ChangeLocalLLMField,
    ChangeOpenAICompatibleField,
    ChangeTranslationSelection,
)
from puripuly_heart.domain.settings_validation import validate_extra_body_json
from puripuly_heart.ui.components.settings import ApiKeyField, OptionItem, SettingsModal
from puripuly_heart.domain.i18n import provider_label, t
from puripuly_heart.ui.theme import COLOR_DIVIDER, COLOR_NEUTRAL, COLOR_ON_BACKGROUND, COLOR_PRIMARY, COLOR_NEUTRAL_DARK

from puripuly_heart.ui.views.settings_helpers import _update_control_if_mounted

if TYPE_CHECKING:
    from puripuly_heart.ui.views.settings import SettingsView


_TRANSLATION_MODEL_LABEL_KEYS = {
    TranslationModel.LOCAL_LLM: "provider.local_llms",
    TranslationModel.OPENAI_COMPATIBLE: "provider.openai_compatible",
}

# ATTRIBUTE OWNERSHIP — widget builders create:
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

    def _get_llm_modal_value(self, settings: AppSettings) -> str:
        return settings.translation.model.value

    def _translation_model_display_label(self, model: TranslationModel) -> str:
        return t(_TRANSLATION_MODEL_LABEL_KEYS[model])

    def _get_llm_display_label(self, settings: AppSettings) -> str:
        return self._translation_model_display_label(settings.translation.model)

    # LOAD ORDER — must be called after _build_llm_widgets and _build_openai_compat_widgets
    # have created the text fields. hasattr guard on _llm_text protects against pre-build calls.
    # Loads both main translation AND OpenAI-compatible provider fields from settings.
    def _load_llm_from_settings(self, settings: "AppSettings") -> None:
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
        from puripuly_heart.app.services.settings_manager import load_providers
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

    # PROMPT KEY RESOLUTION — determines which system prompt to show based on
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
            self._command_executor.execute(ChangeLocalLLMField(field="base_url", value=normalized))
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
            self._command_executor.execute(ChangeLocalLLMField(field="model", value=model))
        _update_control_if_mounted(self._local_llm_model)

    def _on_local_llm_extra_body_change_end(self, e) -> None:
        _ = e
        if not self._settings:
            return
        raw = (self._local_llm_extra_body.value or "").strip()
        valid, error_key, result = validate_extra_body_json(raw)
        if not valid:
            if result is not None:
                self._show_local_llm_extra_body_error(error_key, key=result)
            else:
                self._show_local_llm_extra_body_error(error_key)
            return
        normalized = result  # validated + deepcopy'd dict

        current = self._provider_settings_draft or self._settings
        if current.local_llm.extra_body != normalized:
            self._command_executor.execute(ChangeLocalLLMField(field="extra_body", value=normalized))
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
            self._command_executor.execute(ChangeOpenAICompatibleField(field="model", value=raw_value))

    def _on_openai_compatible_provider_change(self, e) -> None:
        from puripuly_heart.app.services.settings_manager import load_providers
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
                    self._command_executor.execute(ChangeOpenAICompatibleField(field="base_url", value=base_url))
                    self._command_executor.execute(ChangeOpenAICompatibleField(field="model", value=""))
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

        if not self.on_fetch_models:
            logger.error("[FetchModels] on_fetch_models callback not set")
            return

        try:
            model_ids = self.on_fetch_models(base_url, api_key)
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
                self._command_executor.execute(ChangeOpenAICompatibleField(field="model", value=value))

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
        import logging

        logger = logging.getLogger(__name__)

        base_url = (self._local_llm_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._local_llm_api_key.value or "").strip()

        logger.info("[TestConnection] Pinging %s (has_key=%s)", base_url, bool(api_key))

        if not self.on_test_connection:
            logger.error("[TestConnection] on_test_connection callback not set")
            return

        try:
            status_code, body = self.on_test_connection(base_url, api_key)
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
        import logging

        logger = logging.getLogger(__name__)

        base_url = (self._openai_compatible_base_url.value or "").strip()
        if not base_url:
            return
        api_key = (self._openai_compatible_key.value or "").strip()

        logger.info("[TestConnection][OpenAI] Pinging %s (has_key=%s)", base_url, bool(api_key))

        if not self.on_test_connection:
            return

        try:
            status_code, body = self.on_test_connection(base_url, api_key)
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
            self._command_executor.execute(ChangeOpenAICompatibleField(field="base_url", value=raw_value))
        _update_control_if_mounted(self._openai_compatible_base_url)

    def _commit_openai_compatible_fields_from_controls(self) -> None:
        if not self._settings:
            return
        current = self._provider_settings_draft or self._settings
        if current.provider.llm != LLMProviderName.OPENAI_COMPATIBLE:
            return
        self._on_openai_compatible_base_url_change_end(None)

    def _on_llm_click(self, e) -> None:
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

    # TRANSLATION MODEL+CONNECTION CHANGE — the most complex mutation path.
    # When user selects a new translation model+connection:
    # 1. Validates connection is supported for model
    # 2. Delegates model/provider mutation to ChangeTranslationSelection command
    # 3. Calls _update_api_visibility (merged settings to avoid redundant deepcopy)
    # 4. If provider changed, switches prompt editor to new provider's prompt
    # 5. Syncs prompt tab copy text
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

        self._command_executor.execute(ChangeTranslationSelection(provider=model.value))
        new_provider = (self._provider_settings_draft or self._settings).provider.llm

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
            next_prompt = self._ensure_provider_prompt_value(merged, provider_name)
            self._prompt_editor.value = next_prompt
            self._draft_service.stage_prompt_draft(next_prompt)
        self._sync_prompt_tab_copy()

        if self.page:
            _update_control_if_mounted(self._llm_text)
            _update_control_if_mounted(self._translation_connection_row)
            _update_control_if_mounted(self._local_llm_connection_card)

    def _on_llm_selected(self, value: str) -> None:
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

    def _build_llm_widgets(self) -> ft.Control:
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

    # WIDGET BUILD — creates _local_llm_connection_card (initially visible=False).
    # Visibility is toggled by _update_api_visibility based on LLM provider.
    # The card contains: base_url, model, api_key, extra_body text fields.
    # _local_llm_api_key is an ApiKeyField (with verify/save/show_snackbar callbacks).
    def _build_local_llm_widgets(self) -> ft.Control:
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

    # WIDGET BUILD — creates _translation_openai_card (initially visible=False).
    # Includes a provider Dropdown that auto-fills base_url from config/providers.json.
    # When provider changes, model is cleared (provider-specific models differ).
    # _openai_compatible_key is an ApiKeyField with base_url_getter lambda.
    def _build_openai_compat_widgets(self) -> ft.Control:
        self._openai_compatible_title = ft.Text(
            t("settings.openai_compatible.connection", default="Translation Provider Settings"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        from puripuly_heart.app.services.settings_manager import load_providers
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
