"""Debug preview panel and dialog handlers mixin for TranslatorApp.

Handles debug preview panel, peer EULA dialog, hallucination dialog.
"""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.ui.app_utilities import (
    founder_readme_url_for_locale,
    show_snackbar,
    build_github_star_prompt_snackbar,
    close_github_star_prompt_snackbar,
)
from puripuly_heart.ui.components.debug_preview_panel import DebugPreviewPanel
from puripuly_heart.ui.components.founder_letter_dialog import FounderLetterDialog
from puripuly_heart.ui.components.local_qwen_hallucination_dialog import (
    LocalQwenHallucinationDialog,
)
from puripuly_heart.ui.components.peer_translation_eula_dialog import PeerTranslationEulaDialog
from puripuly_heart.domain.i18n import get_locale, t

if TYPE_CHECKING:
    pass


class AppDebugPreviewMixin:
    """Debug preview panel, peer EULA dialog, hallucination dialog."""

    # DIALOG REFERENCES — self._founder_letter_dialog, self._peer_translation_eula_dialog,
    # self._local_qwen_hallucination_dialog are stored but NEVER read back.
    # They exist only to prevent garbage collection while dialog is open (Flet overlay
    # holds a reference too, but the dialog's close callbacks reference self).
    # If you remove these assignments, dialogs may GC prematurely on some Python runtimes.
    #
    # KNOWN BUG: AppNavigationMixin._close_open_dialog_for_navigation does NOT close these
    # dialogs. They use page.open() (warm_document_dialog pattern) which has no is_open
    # property. Navigating tabs with an open dialog leaves it visible on wrong tab.

    # DEBUG-ONLY — this entire file exists for the debug preview panel (Ctrl+D overlay).
    # It's never used in production builds. All preview methods are safe no-ops that
    # demonstrate UI components without side effects on real state.

    def _build_debug_preview_panel(self) -> DebugPreviewPanel:
        return DebugPreviewPanel(
            on_founder_letter=self._preview_founder_letter,
            on_peer_translation_eula=self._preview_peer_translation_eula,
            on_local_qwen_hallucination_modal=self._preview_local_qwen_hallucination_modal,
            on_capture_fault_cycle=self._preview_capture_fault_cycle,
            on_stt_fault_cycle=self._preview_stt_fault_cycle,
            on_audio_fault_clear=self._preview_audio_fault_clear,
            on_github_star_snackbar=self._preview_github_star_snackbar,
        )

    # CLOSURE CAPTURE — snackbar is a local variable, assigned AFTER _open_repository
    # is defined. This is safe because Python closures capture VARIABLE BINDINGS (not values),
    # and _open_repository won't be called until user clicks the snackbar button (after
    # snackbar is assigned). Don't "fix" by moving assignment before function def —
    # _build_github_star_prompt_snackbar needs the on_click callback to exist first.

    def _preview_github_star_snackbar(self) -> None:
        snackbar = None

        def _open_repository(_event) -> None:  # noqa: ANN001
            webbrowser.open("https://github.com/kapitalismho/PuriPuly-heart")
            if snackbar is not None:
                close_github_star_prompt_snackbar(self.page, snackbar)

        snackbar = build_github_star_prompt_snackbar(_open_repository)
        self.page.show_dialog(snackbar)

    def _debug_preview_noop(self) -> None:
        return None

    def _preview_founder_letter(self) -> None:
        dialog = FounderLetterDialog(self.page, on_readme=self._open_local_qwen_guide)
        self._founder_letter_dialog = dialog
        dialog.open()

    def _preview_peer_translation_eula(self) -> None:
        self._show_peer_translation_eula(self._debug_preview_noop)

    def _preview_local_qwen_hallucination_modal(self) -> None:
        self.show_local_qwen_hallucination_dialog()

    def _preview_capture_fault_cycle(self) -> None:
        profile = self.controller.cycle_debug_capture_fault_profile()
        show_snackbar(
            self, t("debug_preview.capture_fault_snackbar", profile=profile), ft.Colors.ORANGE_700
        )

    def _preview_stt_fault_cycle(self) -> None:
        profile = self.controller.cycle_debug_stt_fault_profile()
        show_snackbar(
            self, t("debug_preview.stt_fault_snackbar", profile=profile), ft.Colors.ORANGE_700
        )

    def _preview_audio_fault_clear(self) -> None:
        self.controller.clear_debug_audio_fault_profiles()
        show_snackbar(self, t("debug_preview.audio_fault_clear"), ft.Colors.GREEN_700)

    # DUAL USE — called both from preview (on_accept=_debug_preview_noop)
    # and from AppDashboardMixin._on_peer_translation_toggle (on_accept=enable callback).
    # on_cancel is always noop in both cases (cancel = just close, no action).
    # The preview path deliberately skips EULA acceptance and peer translation enable.

    def _show_peer_translation_eula(self, on_accept) -> None:  # noqa: ANN001
        dialog = PeerTranslationEulaDialog(
            self.page,
            on_accept=on_accept,
            on_cancel=self._debug_preview_noop,
        )
        self._peer_translation_eula_dialog = dialog
        dialog.open()

    # PUBLIC NAME (no underscore) — intentionally public. Called from
    # diagnostics_manager.py via getattr(self.app, "show_local_qwen_hallucination_dialog", None).
    # This is the ONLY public method on TranslatorApp that lives in a mixin instead of app.py.
    # If you rename it, update diagnostics_manager.py caller.

    def show_local_qwen_hallucination_dialog(self) -> None:
        dialog = LocalQwenHallucinationDialog(
            self.page,
            on_open_guide=self._open_local_qwen_guide,
        )
        self._local_qwen_hallucination_dialog = dialog
        dialog.open()

    # URL OPENER — single method for opening the founder readme URL.
    # Used by both hallucination dialog ("read guide") and founder letter dialog ("read more").

    def _open_local_qwen_guide(self) -> None:
        webbrowser.open(founder_readme_url_for_locale(get_locale()))
