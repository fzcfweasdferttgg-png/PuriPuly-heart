"""TranslatorApp — main UI shell.

Mixin-decomposed: shared infrastructure, overlay, dashboard, settings,
mic test, debug preview, and navigation are in separate mixin modules.
"""

from __future__ import annotations

import logging

import flet as ft

from puripuly_heart.app.services.settings_manager import load_settings
from puripuly_heart.app.wiring import create_settings_draft_service
from puripuly_heart.config.settings import new_settings_for_first_run
from puripuly_heart.ui.app_debug import AppDebugPreviewMixin
from puripuly_heart.ui.app_navigation import AppNavigationMixin
from puripuly_heart.ui.app_utilities import (
    APP_CONTENT_PADDING,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    _AppUtilitiesMixin,
)
from puripuly_heart.ui.components.bottom_nav import BottomNavBar
from puripuly_heart.ui.components.debug_preview_panel import DebugPreviewPanel
from puripuly_heart.ui.components.microphone_test_dialog import MicrophoneTestDialog
from puripuly_heart.ui.components.title_bar import TitleBar
from puripuly_heart.app.services.gui_controller import GuiController
from puripuly_heart.ui.fonts import font_for_language, register_fonts
from puripuly_heart.domain.i18n import get_locale, set_locale, t
from puripuly_heart.ui.theme import COLOR_BACKGROUND, get_app_theme
from puripuly_heart.ui.views.about import AboutView
from puripuly_heart.ui.views.dashboard import DashboardView
from puripuly_heart.ui.views.logs import LogsView
from puripuly_heart.ui.views.logs import FletLogHandler
from puripuly_heart.ui.views.settings import SettingsView

logger = logging.getLogger(__name__)


class TranslatorApp(
    _AppUtilitiesMixin,
    AppDebugPreviewMixin,
    AppNavigationMixin,
):
    # MRO ORDER — no longer critical. Core methods (_show_snackbar, _log_basic)
    # are now module-level utility functions in app_utilities.py. Mixin callers
    # (AppDebugPreviewMixin) use module-level functions directly. Thin wrapper
    # methods on _AppUtilitiesMixin exist for backward compat (gui_controller
    # getattr pattern, view_settings.show_snackbar assignment).

    # SHARED STATE — all mixin attributes are initialized HERE, not in mixin __init__.
    # No mixin defines __init__. If you add one, ensure it calls super().__init__()
    # or sets attributes BEFORE other mixins reference them.
    def __init__(self, page: ft.Page, *, config_path, debug_ui_preview: bool = False):
        self.page = page
        self.config_path = config_path
        self.controller = GuiController(
            page=page,
            app=self,
            config_path=config_path,
            log_handler_factory=FletLogHandler,
        )
        self.overlay_state = "off"
        self.overlay_failure_reason: str | None = None
        self.overlay_peer_contract = None
        self.debug_ui_preview = bool(debug_ui_preview)
        self.debug_preview_panel: DebugPreviewPanel | None = None
        self._launch_high_priority_feedback_shown = False
        self._launch_high_priority_feedback_reason: str | None = None
        self._launch_high_priority_snackbar = None
        self._microphone_test_dialog: MicrophoneTestDialog | None = None
        # ATTRIBUTES BELOW are read by mixins in this exact order:
        # overlay_state/overlay_failure_reason → AppOverlayMixin.on_overlay_state_changed
        # _launch_high_priority_* → _AppUtilitiesMixin._show_snackbar → _mark_launch_high_priority_feedback_shown
        # _microphone_test_dialog → AppMicTestMixin._get_microphone_test_dialog (lazy init)
        #   + AppNavigationMixin._close_open_dialog_for_navigation
        # Removing or renaming any breaks the corresponding mixin.
        # INIT SEQUENCE — _setup_page → _build_layout → _wire_callbacks.
        # _build_layout creates view_* instances; _wire_callbacks assigns callbacks TO them.
        # Reversing this order = AttributeError on view_* during wiring.
        self._setup_page()
        try:
            self._build_layout()
        except Exception as exc:
            import traceback
            logger.error("[UI] _build_layout failed: %s\n%s", exc, traceback.format_exc())
            raise

        # VIEW CALLBACK ASSIGNMENTS — these bridge controller→mixin→view.
        # view_settings.show_snackbar goes to _AppUtilitiesMixin (not controller directly).
        # runtime_log_basic/detailed come from controller if available (getattr guard).
        # Calibration callbacks are getattr-guarded because controller may not have them.
        self.view_settings.show_snackbar = self._show_snackbar
        runtime_log_basic = getattr(self.controller, "log_basic", None)
        runtime_log_detailed = getattr(self.controller, "log_detailed", None)
        if callable(runtime_log_basic):
            self.view_settings.runtime_log_basic = runtime_log_basic
        if callable(runtime_log_detailed):
            self.view_settings.runtime_log_detailed = runtime_log_detailed

        # Overlay calibration block
        calibration_begin = getattr(self.controller, "begin_overlay_calibration", None)
        calibration_change = getattr(self.controller, "set_overlay_calibration_field", None)
        calibration_apply = getattr(self.controller, "apply_overlay_calibration", None)
        calibration_cancel = getattr(self.controller, "cancel_overlay_calibration", None)
        if callable(calibration_begin):
            self.view_settings.on_overlay_calibration_begin = calibration_begin
        if callable(calibration_change):
            self.view_settings.on_overlay_calibration_change = calibration_change
        if callable(calibration_apply):
            self.view_settings.on_overlay_calibration_apply = calibration_apply
        if callable(calibration_cancel):
            self.view_settings.on_overlay_calibration_cancel = calibration_cancel

        set_overlay_calibration = getattr(self.view_settings, "set_overlay_calibration", None)
        overlay_calibration = getattr(self.controller, "overlay_calibration", None)
        if callable(set_overlay_calibration) and overlay_calibration is not None:
            set_overlay_calibration(overlay_calibration)

        # Wire all view callbacks
        self._wire_callbacks()

    def _setup_page(self):
        self.page.title = t("app.title")
        self.page.theme_mode = ft.ThemeMode.LIGHT
        register_fonts(self.page)
        self.page.theme = get_app_theme(font_family=font_for_language(get_locale()))
        self.page.bgcolor = COLOR_BACKGROUND
        self.page.padding = 0
        self.page.window.frameless = True
        self.page.window.resizable = True
        self.page.window.width = DEFAULT_WINDOW_WIDTH
        self.page.window.height = DEFAULT_WINDOW_HEIGHT
        self.page.window.min_width = MIN_WINDOW_WIDTH
        self.page.window.min_height = MIN_WINDOW_HEIGHT
        self.page.window.icon = "icons/icon.ico"
        self.page.on_keyboard_event = self._on_keyboard_event

    def _build_layout(self):
        self.view_dashboard = DashboardView()
        # Load settings early so SettingsView can set correct initial button states
        try:
            _initial_settings = load_settings(self.config_path) if self.config_path.exists() else new_settings_for_first_run()
        except Exception as exc:
            logger.warning("[UI] Failed to load initial settings: %s", exc)
            _initial_settings = None
        # Set locale BEFORE creating SettingsView so t() returns translated labels
        if _initial_settings is not None:
            set_locale(_initial_settings.ui.locale)
        self.view_settings = SettingsView(
            initial_settings=_initial_settings,
            draft_service=create_settings_draft_service(),
        )
        self.view_logs = LogsView()
        self.view_about = AboutView()
        self.view_settings.set_overlay_runtime_state(self.overlay_state)

        # Custom title bar
        self.title_bar = TitleBar(self.page, on_about_click=self._open_about_window)

        # Bottom navigation (order: Home, Settings, Logs, About)
        self.bottom_nav = BottomNavBar(on_change=self._on_nav_change)

        # Content area
        self.content_area = ft.Container(
            expand=True,
            padding=APP_CONTENT_PADDING,
            content=self.view_dashboard,
        )

        # Main layout: TitleBar -> Content -> BottomNav
        self.layout = ft.Column(
            controls=[
                self.title_bar,
                self.content_area,
                self.bottom_nav,
            ],
            expand=True,
            spacing=0,
        )

        root_content = ft.Container(content=self.layout, expand=True, padding=0)
        if self.debug_ui_preview:
            self.debug_preview_panel = self._build_debug_preview_panel()
            self.page.add(
                ft.Container(
                    content=ft.Stack(
                        controls=[root_content, self.debug_preview_panel],
                        fit=ft.StackFit.EXPAND,
                        expand=True,
                    ),
                    expand=True,
                    padding=0,
                )
            )
        else:
            self.page.add(root_content)

    # --- Methods staying in app.py shell ---

    # apply_locale stays in app.py shell because it orchestrates ALL views + mixins.
    # It calls refresh_overlay_peer_contract (from AppOverlayMixin) which propagates
    # contract to BOTH view_settings and view_dashboard.
    # If you move this to a mixin, it must still reach all 5 targets.
    def apply_locale(self) -> None:
        self.page.title = t("app.title")
        self.page.theme = get_app_theme(font_family=font_for_language(get_locale()))
        self.title_bar.set_title(t("app.title"))
        self.view_dashboard.apply_locale()
        self.view_settings.apply_locale()
        self.controller.refresh_overlay_peer_contract()
        self.view_logs.apply_locale()
        debug_preview_panel = getattr(self, "debug_preview_panel", None)
        apply_debug_locale = getattr(debug_preview_panel, "apply_locale", None)
        if callable(apply_debug_locale):
            apply_debug_locale()
        try:
            self.page.update()
        except (AssertionError, RuntimeError):
            pass

    # Tab key intercept — only active when Dashboard is the current view.
    # Shift/Ctrl/Alt/Tab are ignored (system shortcuts). Plain Tab on dashboard
    # triggers handle_message_input_tab_key which inserts tab character in input.
    # This handler is assigned in _setup_page (before _build_layout), which is fine
    # because it only reads self.page which is already set.
    def _on_keyboard_event(self, event) -> None:
        if getattr(event, "key", None) != "Tab":
            return
        if any(
            bool(getattr(event, modifier, False)) for modifier in ("shift", "ctrl", "alt", "meta")
        ):
            return

        dashboard = getattr(self, "view_dashboard", None)
        content_area = getattr(self, "content_area", None)
        if dashboard is None or getattr(content_area, "content", None) is not dashboard:
            return

        handler = getattr(dashboard, "handle_message_input_tab_key", None)
        if callable(handler):
            handler()

    # Bridge — controller.set_runtime_logging_mode persists mode,
    # then view_logs.set_runtime_logging_mode updates UI display.
    # Wired in _wire_navigation_callbacks → view_logs.on_mode_change.
    # If controller doesn't have runtime_logging_mode, this crashes — but
    # controller always has it (property on GuiController).
    def _on_runtime_logging_mode_change(self, mode: str) -> None:
        self.controller.set_runtime_logging_mode(mode)
        self.view_logs.set_runtime_logging_mode(self.controller.runtime_logging_mode)

    # --- Callback wiring ---

    # WIRING HUB — delegates to 6 mixin-specific wireup methods.
    # Each mixin owns its own callbacks; this method is the assembly point.
    # _wire_mic_test_callbacks and _wire_debug_callbacks are empty stubs —
    # mic_test is wired inside _wire_settings_callbacks (it's a settings sub-feature),
    # debug is wired inside _build_layout (build-time, not callback-time).
    def _wire_callbacks(self) -> None:
        """Wire all view callbacks — delegates to mixin wireup methods."""
        self._wire_dashboard_callbacks()
        self._wire_settings_callbacks()
        self._wire_overlay_callbacks()
        self._wire_mic_test_callbacks()
        self._wire_debug_callbacks()
        self._wire_navigation_callbacks()
        # view_logs wiring stays in __init__ (trivial)

    def _wire_dashboard_callbacks(self) -> None:
        self.view_dashboard.on_send_message = self.controller._on_manual_submit_async
        self.view_dashboard.on_toggle_translation = self.controller._on_translation_toggle_async
        self.view_dashboard.on_toggle_stt = self.controller._on_stt_toggle_async
        self.view_dashboard.on_toggle_overlay = self.controller._on_overlay_toggle_async
        self.view_dashboard.on_toggle_peer_translation = self.controller._on_peer_translation_toggle_async
        self.view_dashboard.on_language_change = self.controller._on_language_change_async
        self.view_dashboard.on_message_input_activity = self.controller._on_manual_input_activity_async
        self.view_dashboard.runtime_log_detailed = self._log_detailed

    def _wire_settings_callbacks(self) -> None:
        self.view_settings.on_settings_changed = self.controller.apply_settings_with_sync
        self.view_settings.on_prompt_apply_settings = self.controller.apply_prompt_settings
        self.view_settings.on_providers_changed = self.controller.apply_pending_providers
        self.view_settings.on_verify_api_key = self.controller.verify_and_persist_api_key
        self.view_settings.on_secret_cleared = self.controller.clear_secret_verification
        self.view_settings.on_local_llm_secret_changed = self.controller.rebuild_local_llm_if_needed
        self.view_settings.on_start_microphone_test = self.controller._on_start_microphone_test_async
        self.view_settings.on_load_secrets = self.controller.load_secrets
        self.view_settings.on_write_secret = self.controller.write_secret
        self.view_settings.on_fetch_models = self.controller.fetch_models
        self.view_settings.on_test_connection = self.controller.test_connection

    def _wire_overlay_callbacks(self) -> None:
        self.view_settings.on_desktop_overlay_lock_change = self.controller._on_desktop_overlay_lock_change_async
        self.view_settings.on_desktop_overlay_size_change = self.controller._on_desktop_overlay_size_change_async
        self.view_settings.on_desktop_overlay_recovery_action = self.controller._on_desktop_overlay_recovery_action
        self.view_settings.on_desktop_overlay_position_reset = self.controller._on_desktop_overlay_position_reset_async

    def _wire_mic_test_callbacks(self) -> None:
        pass  # on_start_microphone_test wired in _wire_settings_callbacks

    def _wire_debug_callbacks(self) -> None:
        pass  # Debug preview wired in _build_layout via _build_debug_preview_panel

    def _wire_navigation_callbacks(self) -> None:
        self.view_settings.on_view_logs = self._open_logs_tab
        self.view_logs.on_mode_change = self._on_runtime_logging_mode_change
        self.view_logs.set_runtime_logging_mode(self.controller.runtime_logging_mode)

    def _open_about_window(self) -> None:
        """Open About in a separate window via subprocess."""
        import subprocess
        import sys
        from puripuly_heart.ui.fonts import assets_dir

        about_script = '''
import flet as ft
from puripuly_heart.ui.views.about import AboutView

async def main(page: ft.Page):
    page.title = "About — PuriPuly Heart"
    page.window.width = 700
    page.window.height = 600
    page.window.resizable = True
    page.bgcolor = "#1a1a2e"
    page.padding = 16
    page.add(AboutView())
    page.update()

ft.app(target=main)
'''
        subprocess.Popen(
            [sys.executable, "-c", about_script],
            cwd=str(assets_dir().parent),
        )


# ENTRY POINT — main_gui is the ONLY public API of this module.
# External callers (main.py) import ONLY main_gui, never TranslatorApp directly.
# on_close/on_disconnect share the same closure — both call controller.stop().
# stop() has no re-entry guard; Flet fires them sequentially so this is safe,
# but if you make them concurrent, add a lock.
async def main_gui(page: ft.Page, *, config_path, debug_ui_preview: bool = False):
    import asyncio
    import traceback

    try:
        app = TranslatorApp(
            page,
            config_path=config_path,
            debug_ui_preview=debug_ui_preview,
        )
    except Exception as exc:
        logger.error("[UI] TranslatorApp init failed: %s\n%s", exc, traceback.format_exc())
        raise
    await app.controller.start()

    async def _on_close(_e):
        try:
            await asyncio.wait_for(app.controller.stop(), timeout=10.0)
        except Exception as exc:
            logger.error("[UI] Controller shutdown failed: %s", exc)

    page.on_close = _on_close
    page.on_disconnect = _on_close