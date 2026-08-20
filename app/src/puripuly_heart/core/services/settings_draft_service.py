"""SettingsDraftService — draft/consume state machine for settings editing.

Decouples provider-field draft management from the Flet UI layer.
Section mixins call methods here instead of reaching into SettingsView state.

State machine: CLEAN (draft=None, both flags False) → DIRTY (draft exists,
≥1 flag True) → CLEAN (consume/reset clears draft and flags).
Threading: Flet is single-threaded; no locks needed.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from puripuly_heart.config.settings import AppSettings

    OverlayStateFn = Callable[[AppSettings], AppSettings] | None

logger = logging.getLogger(__name__)


class SettingsDraftService:
    """Manages a lazy-deepcopy draft of provider fields within AppSettings.

    Lifecycle: set_settings() → mutate via _ensure_provider_settings_draft() →
    consume_*() or reset().
    """

    def __init__(self) -> None:
        self._settings: AppSettings | None = None
        self._provider_settings_draft: AppSettings | None = None
        self.has_provider_changes: bool = False
        self.has_pending_prompt_changes: bool = False

    def set_settings(self, settings: AppSettings) -> None:
        """Update the backing settings reference and reset all draft state."""
        self._settings = settings
        self.reset()

    def reset(self) -> None:
        """Clear all draft state — used by load_from_settings and set_settings."""
        self._provider_settings_draft = None
        self.has_provider_changes = False
        self.has_pending_prompt_changes = False

    # Lazily creates _provider_settings_draft on first mutation.
    # Section mixins stage changes through this gate.
    # Persists until consume_*() or reset().

    def _ensure_provider_settings_draft(self) -> AppSettings:
        """Return the draft, creating it as a deep copy of _settings if absent."""
        assert self._settings is not None, (
            "set_settings() must be called before _ensure_provider_settings_draft()"
        )
        if self._provider_settings_draft is None:
            self._provider_settings_draft = copy.deepcopy(self._settings)
        return self._provider_settings_draft

    def copy_provider_draft_fields(
        self, source: AppSettings, target: AppSettings
    ) -> None:
        """Copy provider fields from source to target (in-place).

        Shallow for scalar enums; deepcopy for nested objects.
        system_prompts always reset to {} to prevent stale prompt-map leakage.
        """
        target.provider.stt = source.provider.stt
        target.provider.peer_stt = source.provider.peer_stt
        target.provider.llm = source.provider.llm
        target.provider.stt_compute = source.provider.stt_compute
        target.provider.peer_stt_compute = source.provider.peer_stt_compute
        target.provider.stt_backend = source.provider.stt_backend
        target.provider.peer_stt_backend = source.provider.peer_stt_backend
        target.provider.stt_quant = source.provider.stt_quant
        target.provider.peer_stt_quant = source.provider.peer_stt_quant
        target.provider.openai_compatible = copy.deepcopy(source.provider.openai_compatible)
        target.translation = copy.deepcopy(source.translation)
        target.local_llm = copy.deepcopy(source.local_llm)
        target.backup_translation = copy.deepcopy(source.backup_translation)
        target.system_prompt = source.system_prompt
        target.system_prompts = {}

    # One deepcopy per call.  Mixin callers build merged once and pass to
    # _update_api_visibility to avoid double-deepcopy.

    def build_settings_with_provider_draft(self) -> AppSettings | None:
        """Merge draft fields into a copy of _settings, or return _settings as-is."""
        if self._settings is None:
            return None
        if self._provider_settings_draft is None:
            return self._settings
        merged = copy.deepcopy(self._settings)
        self.copy_provider_draft_fields(self._provider_settings_draft, merged)
        return merged

    def sanitize_provider_apply_settings(
        self, settings: AppSettings | None
    ) -> AppSettings | None:
        """Clear system_prompts dict to avoid stale prompt-map leakage."""
        if settings is not None:
            settings.system_prompts = {}
        return settings

    def stage_prompt_draft(self, value: str) -> None:
        """Stage a prompt change in the draft.

        Draft elision: if value matches committed and no provider changes
        exist, discards draft to keep state CLEAN and avoid unnecessary deepcopy.
        """
        if not self._settings:
            return
        committed = self.committed_prompt_value()
        draft = self._ensure_provider_settings_draft()
        draft.system_prompt = value
        draft.system_prompts = {}
        self.has_pending_prompt_changes = value != committed
        if not self.has_pending_prompt_changes and not self.has_provider_changes:
            self._provider_settings_draft = None

    def committed_prompt_value(self) -> str:
        """Return the currently committed system_prompt (not the draft copy)."""
        if not self._settings:
            return ""
        return self._settings.system_prompt

    def build_provider_apply_settings(
        self, overlay_state_fn: OverlayStateFn = None
    ) -> AppSettings | None:
        """Build final settings for provider apply.

        Merges draft → applies overlay runtime state → sanitizes.
        Does NOT commit (does not update self._settings or reset flags).
        """
        merged = self.build_settings_with_provider_draft()
        if overlay_state_fn is not None:
            merged = overlay_state_fn(merged)
        return self.sanitize_provider_apply_settings(merged)

    # Commits built settings as canonical state, resets ALL flags.
    # Only path that transitions DIRTY → CLEAN after user clicks "Apply".

    def consume_provider_apply_settings(
        self, overlay_state_fn: OverlayStateFn = None
    ) -> AppSettings | None:
        """Build, commit, and reset all provider + prompt draft state."""
        settings = self.build_provider_apply_settings(overlay_state_fn)
        if settings is None:
            logger.warning("[Settings] consume: build returned None")
            return None
        logger.info(
            "[Settings] consume: backup.enabled=%s backup.mode=%s "
            "backup.oc.base_url=%s backup.oc.model=%s "
            "backup.llm.base_url=%s backup.llm.model=%s has_changes=%s "
            "stt=%s stt_quant=%s peer_stt=%s peer_stt_quant=%s llm=%s",
            settings.backup_translation.enabled,
            settings.backup_translation.mode.value,
            settings.backup_translation.openai_compatible.base_url,
            settings.backup_translation.openai_compatible.model,
            settings.backup_translation.local_llm.base_url,
            settings.backup_translation.local_llm.model,
            self.has_provider_changes,
            settings.provider.stt.value,
            settings.provider.stt_quant,
            settings.provider.peer_stt.value,
            settings.provider.peer_stt_quant,
            settings.provider.llm.value,
        )
        self._settings = settings
        self._provider_settings_draft = None
        self.has_provider_changes = False
        self.has_pending_prompt_changes = False
        return settings

    def consume_prompt_apply_settings(
        self, overlay_state_fn: OverlayStateFn = None
    ) -> AppSettings | None:
        """Consume prompt changes only — returns None if no prompt pending.

        Provisional draft: draft survives after prompt consume if
        has_provider_changes is True (provider apply may follow).
        """
        if not self.has_pending_prompt_changes:
            return None
        merged = self.build_settings_with_provider_draft()
        if overlay_state_fn is not None:
            merged = overlay_state_fn(merged)
        settings = self.sanitize_provider_apply_settings(merged)
        if settings is None:
            return None
        self._settings = settings
        self.has_pending_prompt_changes = False
        if not self.has_provider_changes:
            self._provider_settings_draft = None
        return settings
