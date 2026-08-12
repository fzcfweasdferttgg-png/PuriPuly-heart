"""Settings event handlers mixin for TranslatorApp.

Extracted from app.py during mixin-decomposition (Phase 3).
Handles settings changes, providers, API key verification.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from puripuly_heart.config.settings import LLMProviderName, save_settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AppSettingsMixin:
    """Settings changes, providers, API key verification."""

    # AI: SETTINGS LAYER — this mixin directly mutates controller.settings (writes to it)
    # and calls controller.save_settings(). This crosses the UI→controller boundary in both
    # directions. The pattern exists because settings changes need to be persisted BEFORE
    # the async apply runs (so crash between save and apply is safe).

    # AI: MUTATION QUEUE — all settings changes go through _queue_settings_mutation_task
    # to serialize them. Without queue, rapid changes could interleave apply operations.
    # _sync_microphone_test_dialog_if_inactive runs AFTER apply — closes mic dialog
    # if settings change invalidates the current mic test configuration.

    def _on_settings_changed(self, settings) -> None:
        async def _task():
            await self.controller.apply_settings(settings)
            self._sync_microphone_test_dialog_if_inactive()

        self._queue_settings_mutation_task(_task)

    def _on_prompt_apply_settings(self, settings) -> None:
        async def _task():
            merged_settings = self.controller.merge_settings_tab_apply_with_current_languages(
                settings
            )
            await self.controller.apply_settings(merged_settings)

        self._queue_settings_mutation_task(_task)

    # AI: PROVIDER CHANGE — consumes pending settings from view_settings before queuing.
    # consume_provider_apply_settings() resets has_provider_changes internally.
    # We also set it to False explicitly (redundant but defensive).
    # pending_settings is captured in the closure BEFORE the async _task — correct because
    # the settings object must not change between capture and apply.

    def _on_providers_changed(self) -> None:
        pending_settings = None
        view_settings = getattr(self, "view_settings", None)
        consume_provider_apply_settings = getattr(
            view_settings,
            "consume_provider_apply_settings",
            None,
        )
        if callable(consume_provider_apply_settings) and getattr(
            view_settings,
            "has_provider_changes",
            False,
        ):
            pending_settings = consume_provider_apply_settings()
            view_settings.has_provider_changes = False

        async def _task():
            if pending_settings is None:
                await self.controller.apply_providers()
            else:
                await self.controller.apply_providers(pending_settings)

        self._queue_settings_mutation_task(_task)

    # AI: GUARD — only triggers rebuild if current LLM provider is LOCAL_LLM.
    # Other providers don't use local LLM secrets. settings.provider.llm is always
    # a valid LLMProviderName enum (never None) — has default OPENAI_COMPATIBLE.
    # force_rebuild_llm=True forces the local LLM connection to reconnect with new credentials.

    def _on_local_llm_secret_changed(self) -> None:
        async def _task():
            settings = getattr(self.controller, "settings", None)
            if settings is None or settings.provider.llm != LLMProviderName.LOCAL_LLM:
                return
            await self.controller.apply_providers(force_rebuild_llm=True)

        self._queue_settings_mutation_task(_task)

    def _api_key_verification_matches_current_field(self, provider: str, key: str) -> bool:
        field_name_map = {
            "openai_compatible": "_openai_compatible_key",
            "backup_openai_compatible": "_fallback_api_key",
        }
        field_name = field_name_map.get(provider)
        if field_name is None:
            return True

        field = getattr(getattr(self, "view_settings", None), field_name, None)
        if field is None:
            return True

        current_key = getattr(field, "value", None)
        if current_key is None:
            return True

        return current_key == key

    # AI: STALENESS CHECK — _api_key_verification_matches_current_field prevents applying
    # verification results for a key that the user already changed. The check compares
    # the key passed to verify with the current field value. If different, result is
    # discarded (not saved to settings). This is a RACE CONDITION guard — verify is async,
    # user can type a new key while verify runs.
    #
    # base_url is keyword-only because ApiKeyField passes it when user overrides the URL.
    # The type annotation on view_settings.on_verify_api_key doesn't include base_url
    # (Callable[[str, str], object]) but keyword-only args don't conflict with positional.

    async def _on_verify_api_key(self, provider: str, key: str, *, base_url: str | None = None) -> tuple[bool, str]:
        logger.info("[VerifyKey][UI] provider=%s key_len=%d base_url=%s", provider, len(key), base_url or "(from settings)")
        success, msg = await self.controller.verify_api_key(provider, key, base_url=base_url)
        logger.info("[VerifyKey][UI] provider=%s success=%s msg=%s", provider, success, msg)

        if not self._api_key_verification_matches_current_field(provider, key):
            logger.info("[VerifyKey][UI] field changed since request, discarding result")
            return success, msg

        # Save verification result to settings
        self.controller.settings.api_key_verified.set_verified(provider, success)
        save_settings(self.controller.config_path, self.controller.settings)
        logger.info("[VerifyKey][UI] saved api_key_verified.%s=%s", provider, success)

        # Sync verification result with dashboard needs_key flags
        if provider == "openai_compatible":
            self.view_dashboard.set_translation_needs_key(not success, update_ui=False)
        elif provider == "local_llm":
            self.view_dashboard.set_translation_needs_key(not success, update_ui=False)

        return success, msg

    # AI: FIELD MAP — maps settings store keys (e.g., "openai_compatible_api_key")
    # to api_key_verified keys (e.g., "openai_compatible"). Must stay in sync with
    # secrets_section.py:_load_secrets store keys and _restore_api_key_icons verified keys.
    # Adding a new provider requires updating ALL THREE locations.

    def _on_secret_cleared(self, key: str) -> None:
        """Reset verification status when API key is cleared."""
        logger.info("[VerifyKey][UI] secret_cleared key=%s", key)
        field_map = {
            "openai_compatible_api_key": "openai_compatible",
            "local_llm_api_key": "local_llm",
            "backup_api_key": "backup_openai_compatible",
            "fallback_local_llm_api_key": "fallback_local_llm",
        }
        verified_key = field_map.get(key)
        if verified_key is not None:
            self.controller.settings.api_key_verified.set_verified(verified_key, False)
            save_settings(self.controller.config_path, self.controller.settings)
            if key in ("openai_compatible_api_key", "local_llm_api_key"):
                self.view_dashboard.set_translation_needs_key(True, update_ui=False)

    # AI: NAVIGATION TRIGGER — called by AppNavigationMixin._on_nav_change when leaving
    # Settings tab (tab 1). Handles TWO independent change types:
    # 1. Provider changes (has_provider_changes) — requires apply_providers
    # 2. Prompt changes (has_pending_prompt_changes) — requires apply_settings
    # Provider changes take priority (checked first with if/elif).
    #
    # DOUBLE MERGE: merge_settings_tab_apply_with_current_languages is called here AND
    # inside controller.apply_providers. The second merge is a no-op on already-merged data.
    # This redundancy is safe but wasteful.
    #
    # DIRECT CONTROLLER MUTATION: controller.settings = merged_pending writes to controller
    # state from UI layer. This is a hex architecture boundary crossing. Necessary because
    # settings must be persisted before the async apply (crash safety).

    def _auto_apply_pending_settings_on_leave(self) -> None:
        """Auto-apply pending settings when leaving Settings tab."""
        if self.view_settings.has_provider_changes:
            pending_settings = self.view_settings.consume_provider_apply_settings()
            if pending_settings is not None:
                self.view_settings.has_provider_changes = False
                merged_pending = self.controller.merge_settings_tab_apply_with_current_languages(pending_settings)
                self.controller.settings = merged_pending
                self.controller.save_settings()

                async def _task():
                    await self.controller.apply_providers(merged_pending)

                self._queue_settings_mutation_task(_task)
        elif getattr(self.view_settings, "has_pending_prompt_changes", False):
            pending_settings = self.view_settings.consume_prompt_apply_settings()
            if pending_settings is not None:

                async def _task():
                    merged_settings = (
                        self.controller.merge_settings_tab_apply_with_current_languages(
                            pending_settings
                        )
                    )
                    await self.controller.apply_settings(merged_settings)

                self._queue_settings_mutation_task(_task)