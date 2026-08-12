"""Dashboard event handlers mixin for TranslatorApp.

Extracted from app.py during mixin-decomposition (Phase 2B).
Handles dashboard toggles, manual submit, language change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.config.settings import save_settings
from puripuly_heart.domain.language import get_stt_compatibility_warning
from puripuly_heart.ui.i18n import language_name, t

if TYPE_CHECKING:
    pass


class AppDashboardMixin:
    """Dashboard toggle handlers, manual submit, language change."""

    # AI: DEFENSIVE vs DIRECT ACCESS — this mixin mixes two styles:
    # - getattr(self, 'controller', None) in _on_peer_translation_toggle (EULA check, sync context)
    # - self.controller directly in _on_language_change (callback context, post-init guaranteed)
    # Both are correct for their contexts. DON'T normalize to one style — the defensive
    # access exists because EULA check runs in sync context where settings might be stale.

    # AI: TEXT SUBMISSION — submits typed text to the translation pipeline.
    # _source is the origin identifier (unused here, passed by dashboard view).
    # Runs async via page.run_task — not queued through mutation queue because
    # text submission is independent of settings state.

    def _on_manual_submit(self, _source: str, text: str) -> None:
        async def _task():
            await self.controller.submit_text(text)

        self.page.run_task(_task)

    def _on_manual_input_activity(self, has_text: bool) -> None:
        handler = getattr(self.controller, "note_manual_input_activity", None)
        if callable(handler):
            handler(bool(has_text))

    def _on_translation_toggle(self, enabled: bool) -> bool:
        # AI: RETURN VALUE — always returns True (fire-and-forget indicator).
        # The dashboard caller does NOT consume this return value.
        # If controller.set_translation_enabled returns False (toggle failed),
        # the async task explicitly resets dashboard state to False as a safety net.
        # controller also resets it internally, so this is a DOUBLE SAFETY NET.
        self._log_basic(f"[Dashboard] Translation toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Translation toggle detail: "
            f"dashboard_state={getattr(getattr(self, 'view_dashboard', None), 'is_translation_on', None)} "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )

        async def _task():
            result = await self.controller.set_translation_enabled(enabled)
            if not result and self.view_dashboard is not None:
                self.view_dashboard.set_translation_enabled(False)

        self.page.run_task(_task)
        return True

    # AI: CONSUME BEFORE TOGGLE — calls _consume_pending_provider_settings() (from _AppUtilitiesMixin)
    # synchronously BEFORE queuing the async task. This ensures the STT provider configuration
    # (which provider, which compute backend) is applied before enabling STT.
    # Without this, toggling STT would use STALE provider settings from the last Apply click.

    def _on_stt_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] STT toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] STT toggle detail: "
            f"dashboard_state={getattr(getattr(self, 'view_dashboard', None), 'is_stt_on', None)} "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )
        self._consume_pending_provider_settings()

        async def _task():
            await self.controller.set_stt_enabled(enabled)

        self.page.run_task(_task)

    def _on_overlay_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] Overlay toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Overlay toggle detail: "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')} "
            f"failure_reason={getattr(self, 'overlay_failure_reason', None)}"
        )

        async def _task():
            await self.controller.set_overlay_enabled(enabled)

        self.page.run_task(_task)

    # AI: EULA GATE — peer translation requires EULA acceptance. This is the ONLY toggle
    # that checks a prerequisite before enabling. The EULA dialog is shown via
    # _show_peer_translation_eula (from AppDebugPreviewMixin) with
    # _accept_peer_translation_eula_and_enable as the accept callback.
    # The EULA acceptance is persisted to settings.json immediately.

    def _on_peer_translation_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] Peer toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Peer toggle detail: "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')} "
            f"failure_reason={getattr(self, 'overlay_failure_reason', None)}"
        )

        controller = getattr(self, "controller", None)
        settings = getattr(controller, "settings", None)
        ui_settings = getattr(settings, "ui", None)
        if (
            enabled
            and ui_settings is not None
            and not getattr(ui_settings, "peer_translation_eula_accepted", False)
        ):
            self._show_peer_translation_eula(self._accept_peer_translation_eula_and_enable)
            return
        self._consume_pending_provider_settings()

        async def _task():
            await self.controller.set_peer_translation_enabled(enabled)

        self.page.run_task(_task)

    # AI: MUTATION QUEUE — language changes go through _queue_settings_mutation_task (not page.run_task)
    # because they must be serialized with other settings mutations. Rapid language switching
    # could cause race conditions if each change triggered an async pipeline restart independently.
    #
    # STT WARNING — changing source language triggers get_stt_compatibility_warning which checks
    # if the current STT provider supports the new language. Warning is shown as snackbar,
    # NOT as a blocker. User can still proceed with incompatible STT.

    def _on_language_change(
        self,
        source_code: str,
        target_code: str,
        peer_source_code: str = "",
        peer_target_code: str = "",
        second_target_code: str = "",
    ) -> None:
        if self.controller.settings is None:
            return
        settings = self.controller.settings
        previous_source_code = settings.languages.source_language
        previous_target_code = settings.languages.target_language
        previous_peer_source_code = getattr(settings.languages, "peer_source_language", "")
        previous_peer_target_code = getattr(settings.languages, "peer_target_language", "")
        self._log_basic(
            "[Dashboard] Language change requested: "
            f"source={previous_source_code}->{source_code} "
            f"target={previous_target_code}->{target_code} "
            f"peer_source={previous_peer_source_code}->{peer_source_code} "
            f"peer_target={previous_peer_target_code}->{peer_target_code} "
            f"second_target={second_target_code}"
        )
        self._log_detailed(
            f"[Dashboard] Language change detail: overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )

        # Check STT provider compatibility and show warning if needed
        warning = None
        if source_code != previous_source_code:
            stt_provider = settings.provider.stt.value
            warning = get_stt_compatibility_warning(source_code, stt_provider)
        if warning:
            snackbar = ft.SnackBar(
                ft.Text(t(warning.key, language=language_name(warning.language_code))),
                bgcolor=ft.Colors.ORANGE_700,
                duration=4000,
                behavior=ft.SnackBarBehavior.FLOATING,
                margin=ft.margin.only(bottom=90),
                padding=20,
            )
            self._mark_launch_high_priority_feedback_shown("stt_compatibility", snackbar)
            self.page.open(snackbar)

        async def _task():
            await self.controller.on_dashboard_language_change(
                source_code=source_code,
                target_code=target_code,
                peer_source_code=peer_source_code,
                peer_target_code=peer_target_code,
                second_target_code=second_target_code,
            )

        self._queue_settings_mutation_task(_task)

    # AI: EULA PERSISTENCE — saves EULA acceptance to disk immediately (save_settings),
    # THEN enables peer translation via controller. If enable fails, EULA is still accepted
    # (no rollback). This is intentional — EULA acceptance is a one-way flag.
    # Uses getattr guards because this runs as a callback that could theoretically
    # fire after controller teardown (though unlikely in practice).

    def _accept_peer_translation_eula_and_enable(self) -> None:
        async def _task():
            settings = getattr(self.controller, "settings", None)
            if settings is not None:
                settings.ui.peer_translation_eula_accepted = True
                config_path = getattr(self.controller, "config_path", None)
                if config_path is not None:
                    save_settings(config_path, settings)
            await self.controller.set_peer_translation_enabled(True)

        self.page.run_task(_task)
