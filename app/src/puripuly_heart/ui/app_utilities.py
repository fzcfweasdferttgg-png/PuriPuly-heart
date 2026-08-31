"""Shared UI utilities mixin for TranslatorApp.

Contains logging, snackbar, settings mutation queue,
and module-level constants/helpers needed by other mixins.
"""

# BASE MIXIN — _AppUtilitiesMixin is FIRST in TranslatorApp MRO.
# Every mixin in the app calls _log_basic(), _show_snackbar(), _queue_settings_mutation_task().
# These methods exist ONLY here. Moving this mixin later in MRO = all mixins break.

from __future__ import annotations

import contextlib
import inspect
import logging
from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.domain.i18n import get_locale, t
from puripuly_heart.ui.theme import COLOR_PRIMARY, COLOR_SUCCESS

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# --- Module-level constants (shared with app.py via import) ---

APP_CONTENT_PADDING = 16

DEFAULT_WINDOW_WIDTH = 1136
DEFAULT_WINDOW_HEIGHT = 850
MIN_WINDOW_WIDTH = 1024
MIN_WINDOW_HEIGHT = 760

FOUNDER_README_BASE_URL = "https://github.com/kapitalismho/PuriPuly-heart/blob/main"
FOUNDER_README_PATH_BY_LOCALE = {
    "ko": "README.ko.md",
    "zh-CN": "README.zh-CN.md",
    "ja": "README.ja.md",
}
FOUNDER_README_API_KEYS_ANCHOR_BY_LOCALE = {
    "ko": "자신의-api-키-사용하기",
    "zh-CN": "使用您自己的-api-密钥",
    "ja": "自分のapiキーを使う",
}
FOUNDER_README_DEFAULT_API_KEYS_ANCHOR = "using-your-own-api-keys"

# CONSTANTS — imported by app.py (window sizes, padding) and app_navigation.py (padding).
# FOUNDER_README_* used only by founder_readme_url_for_locale() → consumed by app_debug.py.

# INTROSPECTION GUARD — used by app_mic_test.py to check if controller.start_microphone_test
# accepts meter_callback keyword. Falls back to True (assume accepted) on introspection failure.
# If you change start_microphone_test signature, this guard prevents silent callback omission.

def _callable_accepts_keyword(callable_obj: object, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return True
    return keyword in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )

# URL BUILDER — maps locale → README path + anchor. Used by app_debug.py
# for founder letter dialog and hallucination dialog "read guide" buttons.
# Falls back to English README if locale not in mapping.

def founder_readme_url_for_locale(locale: str | None) -> str:
    readme_path = FOUNDER_README_PATH_BY_LOCALE.get(locale or "", "README.md")
    anchor = FOUNDER_README_API_KEYS_ANCHOR_BY_LOCALE.get(
        locale or "", FOUNDER_README_DEFAULT_API_KEYS_ANCHOR
    )
    return f"{FOUNDER_README_BASE_URL}/{readme_path}#{anchor}"


# --- Module-level utility functions (extracted from _AppUtilitiesMixin) ---
# These functions break the MRO invariant: callers no longer need
# _AppUtilitiesMixin to be first in MRO. Thin wrapper methods on the
# mixin delegate here for backward compat (gui_controller getattr).


def show_snackbar(app, message: str, bgcolor, duration: int = 4000) -> None:
    """Create and display a floating SnackBar on the app's page.

    Module-level replacement for _AppUtilitiesMixin._show_snackbar.
    Calls app._mark_launch_high_priority_feedback_shown to track launch feedback.
    """
    snackbar = ft.SnackBar(
        ft.Text(message, size=18, color=ft.Colors.WHITE),
        bgcolor=bgcolor,
        duration=duration,
        behavior=ft.SnackBarBehavior.FLOATING,
        margin=ft.Margin.only(bottom=90),
        padding=20,
    )
    app._mark_launch_high_priority_feedback_shown("snackbar", snackbar)
    app.page.show_dialog(snackbar)


def _show_notification(app, message: str, level: str = "warning") -> None:
    """Framework-agnostic notification callback for GuiController.

    Maps semantic levels to Flet colors and delegates to show_snackbar.
    GuiController calls this via getattr(app, '_show_notification').
    """
    _LEVEL_COLORS = {
        "info": ft.Colors.GREEN_700,
        "warning": ft.Colors.ORANGE_700,
        "error": ft.Colors.RED_700,
    }
    color = _LEVEL_COLORS.get(level, ft.Colors.ORANGE_700)
    show_snackbar(app, message, color)


def log_basic(app, message: str, *, level: int = logging.INFO) -> None:
    """Log a basic message via controller or stdlib logger fallback.

    Module-level replacement for _AppUtilitiesMixin._log_basic.
    """
    controller = getattr(app, "controller", None)
    log_basic_fn = getattr(controller, "log_basic", None)
    if callable(log_basic_fn):
        log_basic_fn(message, level=level)
        return
    logger.log(level, message)


def build_github_star_prompt_snackbar(on_click) -> ft.SnackBar:  # noqa: ANN001
    """Build the GitHub star prompt SnackBar.

    Module-level replacement for _AppUtilitiesMixin._build_github_star_prompt_snackbar.
    Pure function — does not access app state.
    """
    return ft.SnackBar(
        content=ft.Row(
            controls=[
                ft.Text(
                    t("flet.github_star.snackbar.message"),
                    size=18,
                    color=ft.Colors.WHITE,
                    font_family=font_for_language(get_locale()),
                    expand=True,
                ),
                ft.TextButton(
                    content=t("flet.github_star.snackbar.action"),
                    on_click=on_click,
                    style=ft.ButtonStyle(
                        color=ft.Colors.WHITE,
                        text_style=ft.TextStyle(
                            size=18,
                            font_family=font_for_language(get_locale()),
                        ),
                        overlay_color=COLOR_PRIMARY,
                    ),
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
        ),
        bgcolor=COLOR_SUCCESS,
        duration=8000,
        behavior=ft.SnackBarBehavior.FLOATING,
        margin=ft.Margin.only(bottom=90),
        padding=20,
    )


def close_github_star_prompt_snackbar(page, snackbar: ft.SnackBar) -> None:
    """Close the GitHub star prompt SnackBar.

    Module-level replacement for _AppUtilitiesMixin._close_github_star_prompt_snackbar.
    """
    with contextlib.suppress(Exception):
        page.pop_dialog()


class _AppUtilitiesMixin:
    """Logging, snackbar, settings mutation queue.

    Core methods are available as both module-level utility functions
    (show_snackbar, log_basic, etc.) and thin wrapper methods on this mixin.
    MRO order is no longer critical — direct callers use module-level functions.
    Wrappers exist for backward compat (gui_controller getattr pattern).
    """

    # METHODS BELOW are called by ALL other mixins via self.* — they form the shared
    # infrastructure layer. _log_basic/_log_detailed use getattr(self, "controller", None)
    # because they may be called during controller.start() when log_basic/log_detailed
    # don't exist yet. Other mixins access self.controller directly (safe post-init).

    # --- Logging ---

    # DEFENSIVE ACCESS — controller may not have log_basic yet (early startup).
    # Falls back to stdlib logger. _log_detailed follows the same pattern.
    # Other mixins access self.controller directly because they only run post-init.

    def _log_basic(self, message: str, *, level: int = logging.INFO) -> None:
        log_basic(self, message, level=level)

    def _log_detailed(self, message: str, *, level: int = logging.INFO) -> None:
        controller = getattr(self, "controller", None)
        log_detailed = getattr(controller, "log_detailed", None)
        if callable(log_detailed):
            log_detailed(message, level=level)
            return
        logger.log(level, message)

    # --- Snackbar ---

    # SNACKBAR LIFECYCLE — calls _mark_launch_high_priority_feedback_shown which sets
    # 3 attributes on self (defined in TranslatorApp.__init__). If you rename those attrs,
    # _mark_launch_high_priority_feedback_shown breaks.

    def _show_snackbar(self, message: str, bgcolor, duration: int = 4000) -> None:
        show_snackbar(self, message, bgcolor, duration)

    def _show_notification(self, message: str, level: str = "warning") -> None:
        _show_notification(self, message, level)

    def _mark_launch_high_priority_feedback_shown(
        self,
        reason: str,
        snackbar: object | None = None,
    ) -> None:
        self._launch_high_priority_feedback_shown = True
        self._launch_high_priority_feedback_reason = reason
        if snackbar is not None:
            self._launch_high_priority_snackbar = snackbar

    def _build_github_star_prompt_snackbar(self, on_click) -> ft.SnackBar:  # noqa: ANN001
        return build_github_star_prompt_snackbar(on_click)

    def _close_github_star_prompt_snackbar(self, snackbar: ft.SnackBar) -> None:
        try:
            page = self.page
        except (AssertionError, RuntimeError):
            return
        close_github_star_prompt_snackbar(page, snackbar)

    # --- Settings mutation queue ---

    # MUTATION QUEUE — sequential FIFO queue for settings changes.
    # Prevents race conditions when user rapidly toggles settings.
    # _settings_mutation_queue and _settings_mutation_worker_active are LAZILY created here
    # (not in __init__). Safe because all calls happen post-init.
    # worker runs via page.run_task which is Flet's async executor.
    # Individual task exceptions are caught and logged but don't stop the queue.
    # CRITICAL: the worker is a closure over self — it captures the TranslatorApp instance.

    def _queue_settings_mutation_task(self, task_factory) -> None:
        queue = getattr(self, "_settings_mutation_queue", None)
        if queue is None:
            queue = []
            self._settings_mutation_queue = queue
        queue.append(task_factory)
        if getattr(self, "_settings_mutation_worker_active", False):
            return
        self._settings_mutation_worker_active = True

        async def _worker():
            try:
                while self._settings_mutation_queue:
                    next_task = self._settings_mutation_queue.pop(0)
                    try:
                        await next_task()
                    except Exception:
                        logger.exception("Settings mutation task failed")
            finally:
                self._settings_mutation_worker_active = False

        try:
            run_task = self.page.run_task
        except (AssertionError, RuntimeError):
            return
        run_task(_worker)

    # PRE-TOGGLE SYNC — called by _on_stt_toggle and _on_peer_translation_toggle
    # (in AppDashboardMixin) BEFORE queuing their async tasks. Ensures provider settings
    # changes from the Settings tab are applied before toggling features that depend on them.
    #
    # DUPLICATION: partially duplicates _auto_apply_pending_settings_on_leave in app_settings.py.
    # Difference: this method consumes but does NOT queue apply_providers — the caller does that.
    # If you change one, check the other.

    def _consume_pending_provider_settings(self) -> None:
        view_settings = getattr(self, "view_settings", None)
        if view_settings is None or not getattr(view_settings, "has_provider_changes", False):
            return
        pending = view_settings.consume_provider_apply_settings()
        if pending is not None:
            view_settings.has_provider_changes = False
            merged = self.controller.merge_settings_tab_apply_with_current_languages(pending)
            self.controller.settings = merged
            self.controller.save_settings()
            self._log_basic(
                f"[Dashboard] Consumed pending settings: "
                f"stt_compute={pending.provider.stt_compute} "
                f"peer_stt_compute={pending.provider.peer_stt_compute}"
            )
