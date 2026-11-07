import asyncio
import contextlib
import inspect
import json
import logging
import webbrowser
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import flet as ft

from puripuly_heart.app.language_selection import LanguageSelectionChange
from puripuly_heart.app.ports.settings_view import (
    ImmediateSettingsIntent,
    LoopbackCaptureSettingsIntent,
    PromptApplyIntent,
    ProviderApplyIntent,
    TelemetrySettingsIntent,
)
from puripuly_heart.app.ports.ui_application import (
    UiApplicationFactoryPort,
    UiApplicationPort,
)
from puripuly_heart.app.services.application_shutdown import (
    ApplicationShutdownCallback,
    ApplicationShutdownCoordinator,
    application_shutdown_callback,
)
from puripuly_heart.app.services.telemetry_reporting import TelemetryReportingOwner
from puripuly_heart.config.provider_values import QwenRegion
from puripuly_heart.core.diagnostics import (
    diagnostic_operation,
    emit_failure,
    safe_exception_fields,
)
from puripuly_heart.core.language import get_stt_compatibility_warning
from puripuly_heart.core.lifecycle import (
    SHUTDOWN_PHASE_FREEZE_INGRESS,
    SHUTDOWN_PHASE_STOP_EXTERNAL_PRODUCERS,
)
from puripuly_heart.core.messages import ErrorDiagnostics
from puripuly_heart.ui.components.about_window import AboutWindow
from puripuly_heart.ui.components.provider_settings_window import ProviderSettingsWindow
from puripuly_heart.ui.components.audio_test_dialog import AudioTestDialog
from puripuly_heart.ui.components.bottom_nav import BottomNavBar
from puripuly_heart.ui.components.debug_preview_panel import DebugPreviewPanel
from puripuly_heart.ui.components.model_assets_dialog import ModelAssetsDialog
from puripuly_heart.ui.components.peer_translation_eula_dialog import PeerTranslationEulaDialog
from puripuly_heart.ui.components.title_bar import TitleBar
from puripuly_heart.ui.dashboard.contract import (
    DashboardCaptureIntents,
    DashboardTranslationIntents,
)
from puripuly_heart.ui.fonts import (
    font_for_language,
    register_ui_fonts,
    typeface_for_language,
)
from puripuly_heart.ui.foundation.adapter import FletFoundationAdapter
from puripuly_heart.ui.foundation.preview import FoundationPreviewSurface
from puripuly_heart.ui.foundation.resources import DEFAULT_FOUNDATION_RESOURCES
from puripuly_heart.ui.foundation.runtime import FletFoundationRuntime
from puripuly_heart.ui.foundation.tokens import FOUNDATION_DESIGN_TOKENS
from puripuly_heart.ui.gpu_device import GpuDeviceOption
from puripuly_heart.ui.gpu_notice import GpuDashboardNotice
from puripuly_heart.ui.i18n import (
    get_locale,
    language_name,
    t,
)
from puripuly_heart.ui.presentation_adapter import FletUiPresentationAdapter
from puripuly_heart.ui.settings.contract import (
    SettingsGeneralIntents,
    SettingsOverlayIntents,
    SettingsPromptIntents,
    SettingsProviderIntents,
    SettingsSurfaceIntents,
)
from puripuly_heart.ui.settings_queue import SettingsQueue
from puripuly_heart.ui.shell.contract import AppShellSlots
from puripuly_heart.ui.shell.renderer import compose_app_shell
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    get_app_theme,
    text_style,
)
from puripuly_heart.ui.views.dashboard import DashboardView
from puripuly_heart.ui.views.logs import LogsView
from puripuly_heart.ui.views.settings import SettingsView

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_WIDTH = FOUNDATION_DESIGN_TOKENS.window.width
DEFAULT_WINDOW_HEIGHT = FOUNDATION_DESIGN_TOKENS.window.height
APP_CONTENT_PADDING = FOUNDATION_DESIGN_TOKENS.spacing.page
APP_LOGS_CONTENT_PADDING = ft.Padding.symmetric(vertical=APP_CONTENT_PADDING)
GITHUB_STAR_REPOSITORY_URL = "https://github.com/kapitalismho/PuriPuly-heart"
GITHUB_STAR_PROMPT_DELAY_S = 2.5
GITHUB_STAR_PROMPT_DURATION_MS = 8000


async def _prepare_and_show_main_window(page: ft.Page) -> None:
    try:
        page.update()

        await page.window.wait_until_ready_to_show()
        await page.window.center()
    except Exception:
        logger.warning(
            "Failed to center the main window before showing it",
            exc_info=True,
        )
    finally:
        page.window.visible = True
        page.update()


class TranslatorApp:
    def __init__(
        self,
        page: ft.Page,
        *,
        config_path,
        application_factory: UiApplicationFactoryPort,
        debug_ui_preview: bool = False,
        runtime_logging_sinks=None,
        vrchat_osc_presence=None,
    ):
        self.page = page
        self._presentation_adapter = FletUiPresentationAdapter(self)
        application = application_factory(
            presentation=self._presentation_adapter,
            config_path=config_path,
            runtime_logging_sinks=runtime_logging_sinks,
            vrchat_osc_presence=vrchat_osc_presence,
        )
        if application is None:
            raise RuntimeError("Application factory did not compose an application boundary")
        self._ui_application = application
        self.application.bind_presentation_close(self.close_presentation)
        self._shutdown_lock: asyncio.Lock | None = None
        self._shutdown_complete = False
        self._shutting_down = False
        self._window_close_requested = False
        self.overlay_state = "off"
        self.overlay_failure_reason: str | None = None
        self.overlay_peer_contract = None
        self.debug_ui_preview = bool(debug_ui_preview)
        self.debug_preview_panel: DebugPreviewPanel | None = None
        self._after_launch_task_handle = None
        self._telemetry_reporting: TelemetryReportingOwner | None = None
        self._launch_high_priority_feedback_shown = False
        self._launch_high_priority_feedback_reason: str | None = None
        self._launch_high_priority_snackbar = None
        self._audio_test_dialog: AudioTestDialog | None = None
        self._foundation_preview_dialog: ft.AlertDialog | None = None
        self._foundation_adapter = FletFoundationAdapter(
            self.application,
            self._presentation_adapter,
        )
        self._foundation_runtime = FletFoundationRuntime(
            self.page,
            self._foundation_adapter,
        )
        self._application_lifecycle = self._compose_application_lifecycle()
        self._setup_page()
        self._build_layout()

        # Link Dashboard callbacks
        self.view_dashboard.bind_dashboard_intents(
            translation=DashboardTranslationIntents(
                submit_message=self._on_manual_submit,
                toggle_translation=self._on_translation_toggle,
                change_language=self._on_language_change,
                report_input_activity=self._on_message_input_activity,
            ),
            capture=DashboardCaptureIntents(
                toggle_self_capture=self._on_stt_toggle,
                toggle_peer_capture=self._on_peer_translation_toggle,
                toggle_overlay=self._on_overlay_toggle,
                retry_peer_process_capture=self._on_retry_peer_process_capture,
                run_gpu_notice_action=self.application.handle_gpu_notice_action,
                run_managed_gemma_notice_action=self.application.handle_managed_gemma_notice_action,
            ),
        )

        runtime_log_basic = self.application.log_basic
        calibration_begin = self.application.begin_overlay_calibration
        calibration_change = self.application.set_overlay_calibration_field
        calibration_apply = self.application.apply_overlay_calibration
        calibration_cancel = self.application.cancel_overlay_calibration
        self.view_settings.bind_settings_intents(
            surface=SettingsSurfaceIntents(
                settings_changed=self._on_settings_changed,
                show_snackbar=self._show_snackbar,
                runtime_log_basic=(runtime_log_basic if callable(runtime_log_basic) else None),
            ),
            provider=SettingsProviderIntents(
                providers_changed=self._on_providers_changed,
                verify_api_key=self._on_verify_api_key,
                provider_secret_change=self._on_provider_secret_change,
                secret_cleared=self._on_secret_cleared,
                local_llm_secret_changed=self._on_local_llm_secret_changed,
                gpu_discovery_requested=self._on_gpu_discovery_requested,
                open_model_assets=self._open_model_assets,
                settings_secrets=self.application.settings_secrets(),
            ),
            general=SettingsGeneralIntents(
                start_audio_test=self._on_start_audio_test,
                list_loopback_capture_options=(
                    lambda: self.application.list_loopback_capture_options()
                ),
                list_loopback_process_options=(
                    lambda: self.application.list_loopback_process_options()
                ),
                list_loopback_device_options=(
                    lambda: self.application.list_loopback_device_options()
                ),
                current_loopback_capture_option=(
                    lambda: self.application.current_loopback_capture_option_value()
                ),
                apply_loopback_capture_option=self._on_apply_loopback_capture_option,
                loopback_capture_summary=(lambda: self.application.loopback_capture_summary()),
                osc_effective_ports=self._effective_osc_ports,
            ),
            prompt=SettingsPromptIntents(
                prompt_apply_settings=self._on_prompt_apply_settings,
                pending_prompt_changed=self._on_pending_prompt_changed,
            ),
            overlay=SettingsOverlayIntents(
                desktop_overlay_lock_change=self._on_desktop_overlay_lock_change,
                desktop_overlay_size_change=self._on_desktop_overlay_size_change,
                desktop_overlay_recovery_action=self._on_desktop_overlay_recovery_action,
                desktop_overlay_position_reset=self._on_desktop_overlay_position_reset,
                view_logs=self._open_logs_tab,
                calibration_begin=(calibration_begin if callable(calibration_begin) else None),
                calibration_change=(calibration_change if callable(calibration_change) else None),
                calibration_apply=(calibration_apply if callable(calibration_apply) else None),
                calibration_cancel=(calibration_cancel if callable(calibration_cancel) else None),
            ),
        )

        set_overlay_calibration = getattr(self.view_settings, "set_overlay_calibration", None)
        overlay_calibration = self.application.overlay_calibration
        if callable(set_overlay_calibration) and overlay_calibration is not None:
            set_overlay_calibration(overlay_calibration)

    @property
    def application(self) -> UiApplicationPort:
        return self._ui_application

    def _effective_osc_ports(self) -> tuple[int | None, int | None]:
        try:
            value = self.application.effective_osc_ports()
        except Exception:
            return (None, None)
        if not isinstance(value, tuple) or len(value) != 2:
            return (None, None)
        return (
            value[0] if isinstance(value[0], int) and value[0] > 0 else None,
            value[1] if isinstance(value[1], int) and value[1] > 0 else None,
        )

    def _run_page_task(self, coroutine, *args):
        if getattr(self, "_shutting_down", False):
            close = getattr(coroutine, "close", None)
            if callable(close):
                close()
            return None
        runtime = self._ensure_foundation_runtime()
        if not runtime.snapshot.lifecycle_bound:
            runtime.bind_application_lifecycle(self._get_application_lifecycle())
        return runtime.run_page_task(coroutine, *args)

    def _ensure_foundation_runtime(self) -> FletFoundationRuntime:
        runtime = getattr(self, "_foundation_runtime", None)
        if runtime is not None:
            return runtime
        presentation = getattr(self, "_presentation_adapter", None)
        if presentation is None:
            presentation = FletUiPresentationAdapter(self)
            self._presentation_adapter = presentation
        adapter = FletFoundationAdapter(self.application, presentation)
        runtime = FletFoundationRuntime(self.page, adapter)
        self._foundation_adapter = adapter
        self._foundation_runtime = runtime
        return runtime

    async def shutdown(self) -> None:
        await self._close_about_window()
        await self._close_provider_settings_window()
        lifecycle = self._get_application_lifecycle()
        try:
            await lifecycle.shutdown()
        finally:
            self._shutdown_complete = lifecycle.is_terminal

    async def _close_about_window(self) -> None:
        about_window = getattr(self, "about_window", None)
        if about_window is not None:
            await about_window.close()

    async def _close_provider_settings_window(self) -> None:
        provider_settings_window = getattr(self, "provider_settings_window", None)
        if provider_settings_window is not None:
            await provider_settings_window.close()

    def _compose_application_lifecycle(self) -> ApplicationShutdownCoordinator:
        foundation_runtime = self._ensure_foundation_runtime()
        return TranslatorApp.compose_application_lifecycle(
            self,
            foundation_runtime,
            self._application_shutdown_callbacks(),
        )

    def _get_application_lifecycle(self) -> ApplicationShutdownCoordinator:
        lifecycle = getattr(self, "_application_lifecycle", None)
        if lifecycle is None:
            lifecycle = self._compose_application_lifecycle()
            self._application_lifecycle = lifecycle
        return lifecycle

    @staticmethod
    def compose_application_lifecycle(
        application: Any,
        foundation_runtime: Any,
        callbacks: tuple[ApplicationShutdownCallback, ...],
    ) -> ApplicationShutdownCoordinator:
        application.application.register_application_shutdown_callbacks(callbacks)
        lifecycle = application.application.application_lifecycle()
        foundation_runtime.bind_application_lifecycle(lifecycle)
        return lifecycle

    def _application_shutdown_callbacks(self) -> tuple[ApplicationShutdownCallback, ...]:
        foundation_runtime = self._ensure_foundation_runtime()
        return (
            application_shutdown_callback(
                phase=SHUTDOWN_PHASE_FREEZE_INGRESS,
                owner_name="TranslatorApp",
                callback_name="freeze_ui_ingress",
                callback=self._freeze_ui_ingress,
            ),
            *foundation_runtime.application_shutdown_callbacks(),
            application_shutdown_callback(
                phase=SHUTDOWN_PHASE_STOP_EXTERNAL_PRODUCERS,
                owner_name="TranslatorApp",
                callback_name="close_after_launch_tasks",
                callback=self._close_after_launch_ui_tasks,
            ),
        )

    def _freeze_ui_ingress(self) -> None:
        self._shutting_down = True
        self._settings_mutation_queue = []
        settings_queue = getattr(self, "_settings_queue", None)
        if settings_queue is not None:
            settings_queue.stop()

    async def _on_page_lifecycle_end(self, _event=None) -> None:
        await self.shutdown()

    async def close_presentation(self) -> None:
        self._window_close_requested = True
        result = self.page.window.destroy()
        if inspect.isawaitable(result):
            await result

    def _on_window_event(self, event) -> None:
        event_type = getattr(event, "type", getattr(event, "data", None))
        if event_type not in {ft.WindowEventType.CLOSE, ft.WindowEventType.CLOSE.value}:
            return
        self._request_window_close()

    def _request_window_close(self) -> None:
        if self._window_close_requested:
            return
        self._window_close_requested = True
        try:
            self.page.window.visible = False
            self.page.update()
        except Exception as exc:
            logger.warning("Failed to hide the main window during shutdown: %s", type(exc).__name__)
        self._ensure_foundation_runtime().run_application_shutdown_task(
            self._close_after_window_request
        )

    async def _close_after_window_request(self) -> None:
        try:
            try:
                await self.shutdown()
            except asyncio.CancelledError:
                await self.shutdown()
                raise
        finally:
            destroy_result = self.page.window.destroy()
            if inspect.isawaitable(destroy_result):
                await destroy_result

    def _setup_page(self):
        self.page.title = t("app.title")
        self.page.theme_mode = ft.ThemeMode.LIGHT
        register_ui_fonts(self.page)
        self.page.theme = get_app_theme(face=typeface_for_language(get_locale()))
        self.page.bgcolor = COLOR_BACKGROUND
        self.page.padding = 0
        self.page.window.frameless = FOUNDATION_DESIGN_TOKENS.window.frameless
        self.page.window.resizable = FOUNDATION_DESIGN_TOKENS.window.resizable
        self.page.window.maximizable = FOUNDATION_DESIGN_TOKENS.window.maximizable
        self.page.window.width = DEFAULT_WINDOW_WIDTH
        self.page.window.height = DEFAULT_WINDOW_HEIGHT
        self.page.window.min_width = DEFAULT_WINDOW_WIDTH
        self.page.window.max_width = DEFAULT_WINDOW_WIDTH
        self.page.window.min_height = DEFAULT_WINDOW_HEIGHT
        self.page.window.max_height = DEFAULT_WINDOW_HEIGHT
        self.page.window.prevent_close = True
        self.page.window.on_event = self._on_window_event
        self.page.window.icon = str(
            DEFAULT_FOUNDATION_RESOURCES.require_file(FOUNDATION_DESIGN_TOKENS.icon_asset)
        )
        self.page.on_keyboard_event = self._on_keyboard_event

    def _build_layout(self):
        self.view_dashboard = DashboardView()
        self.view_settings = SettingsView(
            http_extension_registry=self.application.http_extension_registry()
        )
        self.view_settings.bind_recent_language_queue(self.view_dashboard)
        self.view_logs = LogsView()
        self.about_window = AboutWindow()
        self.provider_settings_window = ProviderSettingsWindow()
        self.view_settings.attach_provider_settings_window(
            self.provider_settings_window, self._run_page_task
        )
        self.view_settings.set_overlay_runtime_state(self.overlay_state)

        # Custom title bar
        self.title_bar = TitleBar(
            self.page,
            on_close=self._request_window_close,
            on_about=self._open_about_window,
        )

        # Bottom navigation (order: Home, Settings, Logs)
        self.bottom_nav = BottomNavBar(on_change=self._on_nav_change)

        self.debug_preview_panel = (
            self._build_debug_preview_panel() if self.debug_ui_preview else None
        )
        shell = compose_app_shell(
            AppShellSlots(
                title_bar=self.title_bar,
                content=self.view_dashboard,
                bottom_nav=self.bottom_nav,
                content_padding=APP_CONTENT_PADDING,
                debug_panel=self.debug_preview_panel,
            )
        )
        self.content_area = shell.content_area
        self.layout = shell.layout
        self.page.add(shell.root)
        self._sync_dashboard_channels()

    def _open_about_window(self) -> None:
        self.about_window.open(self._run_page_task)

    def _open_model_assets(self) -> None:
        if getattr(self, "_model_assets_dialog", None) is None:
            self._model_assets_dialog = ModelAssetsDialog(
                self.page, self.application, self._run_page_task
            )
        self._model_assets_dialog.open()

    def _build_debug_preview_panel(self) -> DebugPreviewPanel:
        return DebugPreviewPanel(
            on_display_turn_cycle=self._cycle_debug_preview_display_turn,
            on_peer_translation_eula=self._preview_peer_translation_eula,
            on_github_star_snackbar=self._debug_preview_noop,
            on_foundation_primitives=self._preview_foundation_primitives,
            on_http_extension_form=self._preview_http_extension_form,
            on_capture_fault_cycle=self._preview_capture_fault_cycle,
            on_stt_fault_cycle=self._preview_stt_fault_cycle,
            on_audio_fault_clear=self._preview_audio_fault_clear,
            on_gpu_state_cycle=self._cycle_debug_preview_gpu_state,
            on_stt_loading_button_cycle=self._cycle_debug_preview_stt_loading_button,
        )

    def _cycle_debug_preview_display_turn(self) -> None:
        dashboard = getattr(self, "view_dashboard", None)
        if dashboard is None:
            return
        steps = (
            ("source", "ko", "오늘 같이 뭐 하고 놀까?"),
            ("translation", "en", "What should we do together today?"),
            (
                "source",
                "ko",
                "아까 말한 그 월드 이름이 뭐였지? 다시 가보고 싶은데 검색해도 안 나오더라.",
            ),
            (
                "translation",
                "en",
                "What was the name of that world you mentioned earlier? "
                "I want to visit again but searching turns up nothing.",
            ),
        )
        index = int(getattr(self, "_debug_preview_display_turn_index", -1)) + 1
        index %= len(steps)
        self._debug_preview_display_turn_index = index
        kind, language_code, text = steps[index]
        if kind == "source":
            dashboard.set_display_text(text, language_code=language_code)
            return
        dashboard.set_display_translation_text(text, language_code=language_code)

    def _mark_launch_high_priority_feedback_shown(
        self,
        reason: str,
        snackbar: object | None = None,
    ) -> None:
        self._launch_high_priority_feedback_shown = True
        self._launch_high_priority_feedback_reason = reason
        if snackbar is not None:
            self._launch_high_priority_snackbar = snackbar

    def schedule_after_launch_tasks(self) -> None:
        handle = getattr(self, "_after_launch_task_handle", None)
        if handle is not None and not handle.done():
            return
        self._schedule_app_active_day_report()
        self._after_launch_task_handle = self._run_page_task(self._run_after_launch_tasks)

    async def _run_after_launch_tasks(self) -> None:
        await asyncio.gather(
            self._run_launch_notification_flow(),
            self._run_after_launch_runtime_preparation(),
        )
        self._sync_dashboard_channels()

    async def _run_launch_notification_flow(self) -> None:
        await asyncio.gather(
            self._check_for_update_after_launch(),
        )
    async def _check_for_update_after_launch(self) -> None:
        update_kwargs = {"log_diagnostic": self._log_diagnostic}
        try:
            update_parameters = inspect.signature(_check_and_notify_update).parameters
        except TypeError, ValueError:
            update_parameters = {}
        if "load_update_info" in update_parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in update_parameters.values()
        ):
            update_kwargs["load_update_info"] = self.application.check_for_update
        if "on_launch_snackbar_shown" in update_parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in update_parameters.values()
        ):
            update_kwargs["on_launch_snackbar_shown"] = lambda snackbar: (
                self._mark_launch_high_priority_feedback_shown("update", snackbar)
            )
        try:
            await _check_and_notify_update(self.page, **update_kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log_diagnostic(
                f"[Startup] Update check failed: {exc!r}",
                level=logging.WARNING,
            )

    async def _run_after_launch_runtime_preparation(self) -> None:
        try:
            await self.application.prepare_runtime_after_launch()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log_diagnostic(
                f"[Startup] Runtime preparation failed: {exc!r}",
                level=logging.WARNING,
            )

    async def _close_ui_task_handle(self, attribute_name: str) -> None:
        handle = getattr(self, attribute_name, None)
        if handle is None:
            return
        if not handle.done():
            handle.cancel()
        try:
            if isinstance(handle, asyncio.Future):
                await asyncio.gather(handle, return_exceptions=True)
            elif inspect.isawaitable(handle):
                await handle
            else:
                await asyncio.wrap_future(handle)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            if getattr(self, attribute_name, None) is handle:
                setattr(self, attribute_name, None)

    async def _close_after_launch_ui_tasks(self) -> None:
        await self._close_ui_task_handle("_after_launch_task_handle")
        owner = getattr(self, "_telemetry_reporting", None)
        if owner is not None:
            await owner.cancel_retry()

    async def close_after_launch_tasks(self) -> None:
        await self._close_after_launch_ui_tasks()
    def _debug_preview_noop(self) -> None:
        return None


    def _preview_peer_translation_eula(self) -> None:
        self._show_peer_translation_eula(self._debug_preview_noop)

    def _preview_capture_fault_cycle(self) -> None:
        profile = self.application.cycle_debug_capture_fault_profile()
        self._show_snackbar(
            t("debug_preview.capture_fault_snackbar", profile=profile), COLOR_WARNING
        )

    def _preview_stt_fault_cycle(self) -> None:
        profile = self.application.cycle_debug_stt_fault_profile()
        self._show_snackbar(t("debug_preview.stt_fault_snackbar", profile=profile), COLOR_WARNING)

    def _preview_audio_fault_clear(self) -> None:
        self.application.clear_debug_audio_fault_profiles()
        self._show_snackbar(t("debug_preview.audio_fault_clear"), COLOR_SUCCESS)

    def _preview_foundation_primitives(self) -> None:
        if not self._foundation_adapter.debug_preview_enabled:
            return
        dialog = ft.AlertDialog(
            modal=False,
            content=FoundationPreviewSurface(get_locale()),
            bgcolor=COLOR_BACKGROUND,
        )
        self._foundation_preview_dialog = dialog
        self.page.show_dialog(dialog)

    def _preview_http_extension_form(self) -> None:
        registry_service = self.application.http_extension_registry()
        if registry_service is None:
            return
        directory = registry_service.directory
        directory.mkdir(parents=True, exist_ok=True)
        demo_path = directory / "debug_demo.json"
        if not demo_path.exists():
            demo_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "debug_demo",
                        "name": "Debug Demo Translator",
                        "description": "Debug preview: HTTP extension credential form",
                        "url": "https://example.com/translate",
                        "request": {
                            "body": {
                                "type": "json",
                                "value": {
                                    "q": "{{text}}",
                                    "api_key": "{{secret:api_key}}",
                                },
                            }
                        },
                        "response": {"type": "text"},
                        "secrets": [{"id": "api_key", "label": "API Key"}],
                    }
                ),
                encoding="utf-8",
            )
        registry_service.reload()
        view = self.view_settings
        set_registry = getattr(view, "set_http_extension_registry", None)
        if callable(set_registry):
            set_registry(registry_service)
        select_llm = getattr(view, "_on_llm_selected", None)
        if callable(select_llm):
            select_llm("custom_http")
        select_extension = getattr(view, "_on_http_extension_selected", None)
        if callable(select_extension):
            select_extension("debug_demo")
        if self._current_tab != 1:
            self._open_settings_tab()

    def _show_peer_translation_eula(self, on_accept) -> None:
        dialog = PeerTranslationEulaDialog(
            self.page,
            on_accept=on_accept,
            on_cancel=self._debug_preview_noop,
        )
        self._peer_translation_eula_dialog = dialog
        dialog.open()

    def _accept_peer_translation_eula_and_enable(self) -> None:
        async def _task():
            await self.application.accept_peer_translation_eula_and_enable()

        self._run_page_task(_task)

    def _close_open_dialog_for_navigation(self) -> None:
        audio_test_dialog = getattr(self, "_audio_test_dialog", None)
        if audio_test_dialog is not None and getattr(
            audio_test_dialog,
            "is_open",
            False,
        ):
            audio_test_dialog.close(notify=True)
            return

        try:
            self.page.pop_dialog()
        except Exception:
            logger.exception("Failed to close dialog during navigation")

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
                    operation_id = uuid4().hex
                    try:
                        with diagnostic_operation(
                            self.application.diagnostic_instance_id, operation_id, operation_id
                        ):
                            result = await next_task()
                        if result is False:
                            self._show_snackbar(
                                t("settings.mutation.operation_failed"), COLOR_WARNING
                            )
                    except Exception as exc:
                        emit_failure(
                            logger,
                            ErrorDiagnostics(
                                component="gui_settings_queue",
                                operation="queued_mutation",
                                code="settings_background_task_failed",
                                category="transaction",
                                visibility="basic",
                                content_policy="metadata_only",
                                status_code=None,
                                retry_after_ms=None,
                                fields={"phase": "queued_mutation", **safe_exception_fields(exc)},
                                operation_id=operation_id,
                                request_id=operation_id,
                            ),
                            stage="queued_mutation",
                        )
                        try:
                            self._show_snackbar(
                                t("settings.mutation.operation_failed"), COLOR_WARNING
                            )
                        except Exception:
                            pass
            finally:
                self._settings_mutation_worker_active = False

        self._run_page_task(_worker)

    def _ensure_settings_queue(self) -> SettingsQueue:
        settings_queue = getattr(self, "_settings_queue", None)
        if settings_queue is None:
            settings_queue = SettingsQueue(
                self.application.apply_settings_change,
                self._on_settings_committed,
            )
            self._settings_queue = settings_queue
        return settings_queue

    def _queue_settings_apply(self, intent: object, *, resubmit: bool = True) -> None:
        if getattr(self, "_shutting_down", False):
            return
        settings_queue = self._ensure_settings_queue()
        if settings_queue.submit(intent, resubmit=resubmit):
            self._queue_settings_mutation_task(settings_queue.drain)

    def _on_settings_committed(self, intent: object) -> None:
        view = getattr(self, "view_settings", None)
        if isinstance(intent, ProviderApplyIntent) and view is not None:
            view.acknowledge_provider_apply_settings(intent)
            consume = getattr(view, "consume_alibaba_key_verification", None)
            verification = consume() if callable(consume) else None
            if verification is not None:
                self._run_page_task(verification)
        elif isinstance(intent, PromptApplyIntent) and view is not None:
            view.acknowledge_prompt_apply_settings(intent)
        self.application.refresh_settings_projection()
        self._sync_dashboard_channels()
        self._sync_audio_test_dialog_if_stopped()

    def _sync_dashboard_channels(self) -> None:
        dashboard = getattr(self, "view_dashboard", None)
        if dashboard is None:
            return
        settings = self.application.compatibility_settings()
        if settings is None:
            return
        intent = settings.intent
        none_ids = {"", "none"}
        local_ready = bool(intent.stt.model)
        stt_configured = intent.stt.provider not in none_ids and (
            intent.stt.provider != "local_qwen_gpu" or local_ready
        )
        peer_configured = intent.peer_stt.provider not in none_ids and (
            intent.peer_stt.provider != "local_qwen_gpu" or local_ready
        )
        translation_configured = intent.translation.model not in none_ids
        setter = getattr(dashboard, "set_channels_configured", None)
        if not callable(setter):
            return
        setter(
            stt=stt_configured,
            peer=peer_configured,
            translation=translation_configured,
            overlay=translation_configured or peer_configured,
        )

    def _content_padding_for_index(self, index: int) -> int | ft.Padding:
        if index == 1:
            return 0
        if index == 2:
            return APP_LOGS_CONTENT_PADDING
        return APP_CONTENT_PADDING

    def _on_nav_change(self, index: int):
        # Track previous tab for Settings auto-apply
        previous_tab = getattr(self, "_current_tab", 0)
        if previous_tab != index:
            self._close_open_dialog_for_navigation()
        self._current_tab = index

        # Auto-apply Settings changes when leaving Settings (tab 1)
        if previous_tab == 1 and index != 1:
            if self.view_settings.has_provider_changes:
                pending_settings = self.view_settings.build_provider_apply_settings()
                if pending_settings is not None:
                    self._queue_settings_apply(pending_settings, resubmit=False)
            elif getattr(self.view_settings, "has_pending_prompt_changes", False):
                pending = PromptApplyIntent(self.view_settings._prompt_editor.value)

                self._queue_settings_apply(pending, resubmit=False)

        if index == 0:
            self.content_area.content = self.view_dashboard
        elif index == 1:
            self.content_area.content = self.view_settings
        elif index == 2:
            self.content_area.content = self.view_logs

        self.content_area.padding = self._content_padding_for_index(index)
        self.content_area.update()
        if index == 1:
            self.view_settings.refresh_prompt_if_empty()
        elif index == 2:
            # Async scroll after rendering completes
            async def _scroll():
                import asyncio

                await asyncio.sleep(0.05)
                await self.view_logs.scroll_to_bottom()

            self._run_page_task(_scroll)

    def _open_logs_tab(self) -> None:
        self._on_nav_change(2)
        self._set_bottom_nav_selected(2)

    def _open_settings_tab(self) -> None:
        self._on_nav_change(1)
        self._set_bottom_nav_selected(1)

    def _set_bottom_nav_selected(self, index: int) -> None:
        selected_attr = getattr(self.bottom_nav, "_selected", None)
        if selected_attr != index and hasattr(self.bottom_nav, "_selected"):
            self.bottom_nav._selected = index
        update_visuals = getattr(self.bottom_nav, "_update_visuals", None)
        if callable(update_visuals):
            with contextlib.suppress(Exception):
                update_visuals()

    def apply_locale(self) -> None:
        self.page.title = t("app.title")
        self.page.theme = get_app_theme(face=typeface_for_language(get_locale()))
        self.view_dashboard.apply_locale()
        self.view_settings.apply_locale()
        self.refresh_overlay_peer_contract()
        self.view_logs.apply_locale()
        self.title_bar.apply_locale()
        about_window = getattr(self, "about_window", None)
        if about_window is not None:
            about_window.apply_locale()
        debug_preview_panel = getattr(self, "debug_preview_panel", None)
        apply_debug_locale = getattr(debug_preview_panel, "apply_locale", None)
        if callable(apply_debug_locale):
            apply_debug_locale()
        foundation_preview_dialog = getattr(self, "_foundation_preview_dialog", None)
        if foundation_preview_dialog is not None:
            foundation_preview_dialog.content = FoundationPreviewSurface(get_locale())
        self.page.update()

    def _cycle_debug_preview_stt_loading_button(self) -> None:
        states = ("off", "starting", "on")
        index = int(getattr(self, "_debug_preview_stt_loading_button_index", -1)) + 1
        index %= len(states)
        self._debug_preview_stt_loading_button_index = index
        state = states[index]
        self.view_dashboard.stt_button.set_state(
            state == "on",
            is_starting=state == "starting",
        )

    def _cycle_debug_preview_gpu_state(self) -> None:
        states = (
            "discovery_failed",
            "not_installed",
            "invalid",
            "installing",
            "install_failed",
            "unsupported",
            "unavailable_device",
            "activation_failed",
        )
        index = int(getattr(self, "_debug_preview_gpu_state_index", -1)) + 1
        index %= len(states)
        self._debug_preview_gpu_state_index = index
        devices = (
            GpuDeviceOption("vulkan-index-0", "Debug GPU 0", "Vulkan0"),
            GpuDeviceOption("vulkan-index-1", "Debug GPU 1", "Vulkan1"),
        )
        set_devices = getattr(self.view_settings, "set_gpu_devices", None)
        if callable(set_devices):
            set_devices(devices=devices)
        action_by_state = {
            "discovery_failed": "rediscover",
            "activation_failed": "restart",
        }
        notice = GpuDashboardNotice(
            status=states[index],
            progress_percent=42 if states[index] == "installing" else None,
            action=action_by_state.get(states[index]),
        )
        set_notice = getattr(getattr(self, "view_dashboard", None), "set_gpu_notice", None)
        if callable(set_notice):
            set_notice(notice)
        else:
            legacy_setter = getattr(self.view_settings, "set_gpu_runtime_state", None)
            if callable(legacy_setter):
                legacy_setter(
                    states[index],
                    devices=devices,
                    progress_percent=notice.progress_percent,
                )

    def refresh_overlay_peer_contract(self) -> None:
        presentation = getattr(self, "_presentation_adapter", None)
        if presentation is None:
            presentation = FletUiPresentationAdapter(self)
            self._presentation_adapter = presentation
        presentation.refresh_overlay_peer_contract(
            self.application.overlay_peer_presentation_state()
        )

    def _sync_settings_overlay_runtime_state(self) -> None:
        view_settings = getattr(self, "view_settings", None)
        set_state = getattr(view_settings, "set_overlay_runtime_state", None)
        if not callable(set_state):
            return
        state = self.application.state()
        set_state(
            self.overlay_state,
            failure_reason=self.overlay_failure_reason,
            overlay_target=state.overlay_target,
            desktop_captions_locked=state.desktop_overlay_captions_locked,
        )

    def _on_desktop_overlay_lock_change(self, locked: bool) -> None:
        async def _task():
            await self.application.set_desktop_overlay_captions_locked(bool(locked))
            self._refresh_settings_desktop_overlay_state()

        self._run_page_task(_task)

    def _on_desktop_overlay_size_change(self, size_preset: str) -> None:
        async def _task():
            await self.application.set_desktop_overlay_size_preset(size_preset)
            self._refresh_settings_desktop_overlay_state()

        self._run_page_task(_task)

    def _on_desktop_overlay_recovery_action(self, action: str) -> None:
        if action not in {"retry", "reopen"}:
            return

        async def _task():
            await self.application.set_overlay_enabled(True)

        self._run_page_task(_task)

    def _on_desktop_overlay_position_reset(self) -> None:
        async def _task():
            await self.application.reset_desktop_overlay_position()
            self._refresh_settings_desktop_overlay_state()

        self._run_page_task(_task)

    def _refresh_settings_desktop_overlay_state(self) -> None:
        settings = self.application.settings_overlay_snapshot()
        view_settings = getattr(self, "view_settings", None)
        sync_settings = getattr(view_settings, "sync_desktop_overlay_settings", None)
        if settings is not None and callable(sync_settings):
            sync_settings(settings)
        self._sync_settings_overlay_runtime_state()

    def on_desktop_overlay_state_changed(
        self,
        *,
        interaction_mode: str | None = None,
        captions_locked: bool | None = None,
    ) -> None:
        _ = (interaction_mode, captions_locked)
        self._sync_settings_overlay_runtime_state()

    def _on_manual_submit(self, _source: str, text: str) -> None:
        async def _task():
            await self.application.submit_text(text)

        self._run_page_task(_task)

    def _on_message_input_activity(self, has_text: bool) -> None:
        async def _task():
            self.application.set_manual_input_activity(has_text)

        self._run_page_task(_task)

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

    def _log_basic(self, message: str, *, level: int = logging.INFO) -> None:
        self.application.log_basic(message, level=level)

    def _log_diagnostic(self, message: str, *, level: int = logging.INFO) -> None:
        self.application.log_diagnostic(message, level=level)

    def _revert_dashboard_translation_toggle(self) -> None:
        self._set_dashboard_translation_visual_state(False)

    def _set_dashboard_translation_visual_state(self, enabled: bool) -> None:
        dash = getattr(self, "view_dashboard", None)
        set_translation_enabled = getattr(dash, "set_translation_enabled", None)
        if callable(set_translation_enabled):
            try:
                set_translation_enabled(enabled)
            except Exception:
                logger.exception("Failed to update dashboard translation toggle")

    def _on_translation_toggle(self, enabled: bool) -> bool:
        async def _task():
            await self.application.set_translation_enabled(enabled)

        self._run_page_task(_task)
        return True

    def _on_stt_toggle(self, enabled: bool) -> None:

        async def _task():
            await self.application.set_stt_enabled(enabled)

        self._run_page_task(_task)

    def _on_overlay_toggle(self, enabled: bool) -> None:

        async def _task():
            await self.application.set_overlay_enabled(enabled)

        self._run_page_task(_task)

    def _on_peer_translation_toggle(self, enabled: bool) -> None:
        if enabled and self.application.state().peer_translation_eula_accepted is False:
            self._show_peer_translation_eula(self._accept_peer_translation_eula_and_enable)
            return

        async def _task():
            await self.application.set_peer_translation_enabled(enabled)

        self._run_page_task(_task)

    def _on_retry_peer_process_capture(self) -> None:

        async def _task():
            await self.application.retry_peer_process_capture()

        self._queue_settings_mutation_task(_task)

    def _on_apply_loopback_capture_option(self, value: str) -> None:
        self._queue_settings_apply(LoopbackCaptureSettingsIntent(value))

    def _on_gpu_discovery_requested(self) -> None:
        self._run_page_task(self.application.ensure_gpu_device_discovery)

    def _on_language_change(
        self,
        change: LanguageSelectionChange,
    ) -> None:
        settings = self.application.compatibility_settings()
        if settings is None:
            return
        languages = settings.intent.languages
        previous_source_code = languages.source_language

        # Check STT provider compatibility and show warning if needed
        warning = None
        if change.source_code != previous_source_code:
            stt_provider = settings.intent.stt.provider
            warning = get_stt_compatibility_warning(
                change.source_code,
                stt_provider,
            )
        if warning:
            snackbar = ft.SnackBar(
                ft.Text(t(warning.key, language=language_name(warning.language_code))),
                bgcolor=COLOR_WARNING,
                duration=4000,
                behavior=ft.SnackBarBehavior.FLOATING,
                elevation=0,
                margin=ft.Margin.only(bottom=90),
                padding=20,
            )
            self._mark_launch_high_priority_feedback_shown("stt_compatibility", snackbar)
            self.page.show_dialog(snackbar)

        self._queue_settings_apply(change)

    def _on_settings_changed(self, intent: ImmediateSettingsIntent) -> None:
        if getattr(self, "_shutting_down", False):
            return

        async def _task():
            await self._ensure_settings_queue().drain()
            self._sync_audio_test_dialog_if_stopped()

        self._ensure_settings_queue().submit(intent)
        self._queue_settings_mutation_task(_task)

    def _on_start_audio_test(self) -> None:
        admission_revision = self.application.audio_test_start_revision()

        async def _task():
            if admission_revision != self.application.audio_test_start_revision():
                return
            dialog = self._get_audio_test_dialog()
            if dialog.is_open:
                return
            thresholds = None
            view_settings = getattr(self, "view_settings", None)
            read_thresholds = getattr(view_settings, "vad_thresholds", None)
            if callable(read_thresholds):
                thresholds = read_thresholds()
            dialog.open(thresholds)
            started = await self.application.start_audio_test(
                observation_callback=dialog.add_samples,
                admission_revision=admission_revision,
            )
            if not started:
                dialog.show_failure()

        self._queue_settings_mutation_task(_task)

    def _on_stop_audio_test(self) -> None:
        async def _task() -> None:
            await self.application.stop_audio_test()
            self._close_audio_test_dialog()

        self._run_page_task(_task)

    def _get_audio_test_dialog(self) -> AudioTestDialog:
        dialog = getattr(self, "_audio_test_dialog", None)
        if dialog is None:
            dialog = AudioTestDialog(
                self.page,
                on_close=self._on_audio_test_dialog_dismiss,
                on_threshold_change=self._on_audio_test_threshold_change,
                snapshot_provider=self.application.audio_test_snapshot,
                run_task=self._run_page_task,
            )
            self._audio_test_dialog = dialog
        return dialog

    def _on_audio_test_threshold_change(self, channel: str, value: float) -> None:
        view_settings = getattr(self, "view_settings", None)
        apply_threshold = getattr(view_settings, "apply_vad_threshold", None)
        if callable(apply_threshold):
            apply_threshold(channel, value)

    def _close_audio_test_dialog(self) -> None:
        dialog = getattr(self, "_audio_test_dialog", None)
        if dialog is None:
            return
        dialog.close(notify=False)

    def _on_audio_test_dialog_dismiss(self) -> None:
        self._on_stop_audio_test()

    def _sync_audio_test_dialog_if_stopped(self) -> None:
        dialog = getattr(self, "_audio_test_dialog", None)
        if dialog is None or not dialog.is_open:
            return
        if self.application.audio_test_snapshot().get("state") != "off":
            return
        self._close_audio_test_dialog()

    def _on_prompt_apply_settings(self, intent: PromptApplyIntent) -> None:
        self._queue_settings_apply(intent)

    def _on_pending_prompt_changed(self, intent: PromptApplyIntent) -> None:
        settings_queue = getattr(self, "_settings_queue", None)
        if settings_queue is not None and settings_queue.replace_pending_prompt(intent):
            view = getattr(self, "view_settings", None)
            if view is not None:
                view.has_pending_prompt_changes = True
            self._queue_settings_mutation_task(settings_queue.drain)

    def _on_providers_changed(self) -> None:
        view_settings = getattr(self, "view_settings", None)
        consume_immediate = getattr(
            view_settings, "consume_immediate_provider_apply_settings", None
        )
        pending_intent = consume_immediate() if callable(consume_immediate) else None
        consume_http_extension_runtime_reload = getattr(
            view_settings,
            "consume_http_extension_runtime_reload",
            None,
        )
        runtime_reload = (
            consume_http_extension_runtime_reload()
            if callable(consume_http_extension_runtime_reload)
            else False
        )
        if pending_intent is None and runtime_reload:
            self._queue_settings_apply(None)
            return

        build_provider_apply_settings = getattr(
            view_settings, "build_provider_apply_settings", None
        )
        if (
            pending_intent is None
            and callable(build_provider_apply_settings)
            and getattr(view_settings, "has_provider_changes", False)
        ):
            pending_intent = build_provider_apply_settings()
        if pending_intent is not None:
            self._log_basic("[Settings] Applying staged provider intent")

        self._queue_settings_apply(pending_intent)

    def _on_local_llm_secret_changed(self) -> None:
        async def _task():
            if not self.application.local_llm_selected():
                return
            await self.application.apply_providers(force_rebuild_llm=True)

        self._queue_settings_mutation_task(_task)

    def _on_custom_stt_secret_changed(self) -> None:
        async def _task():
            await self.application.apply_providers(
                persist_settings=False,
                refresh_ui=False,
            )

        self._queue_settings_mutation_task(_task)
    async def _on_verify_api_key(self, provider: str, key: str) -> tuple[bool, str]:
        success, msg = await self.application.verify_api_key(provider, key)
        self.application.persist_api_key_verification(provider, key, success)
        return success, msg

    async def _on_provider_secret_change(self, key: str, value: str) -> bool:
        succeeded = await self.application.persist_provider_secret_change(key, value)
        if not succeeded:
            return False
        provider = {
            "deepgram_api_key": "deepgram",
            "gemini_transcribe_api_key": "gemini_transcribe",
            "elevenlabs_scribe_api_key": "elevenlabs_scribe",
            "soniox_api_key": "soniox",
            "google_api_key": "google",
            "openrouter_api_key": "openrouter",
            "openai_api_key": "openai",
            "deepseek_api_key": "deepseek",
            "alibaba_api_key_beijing": "alibaba_beijing",
            "alibaba_api_key_singapore": "alibaba_singapore",
        }.get(key)
        if provider in {
            "deepgram",
            "gemini_transcribe",
            "elevenlabs_scribe",
            "soniox",
            "alibaba_beijing",
            "alibaba_singapore",
        }:
            if provider in ("deepgram", "gemini_transcribe", "elevenlabs_scribe"):
                if self._is_self_rolling():
                    self.view_dashboard.set_stt_needs_key(
                        not self._rolling_member_verified(), update_ui=False
                    )
                else:
                    self.view_dashboard.set_stt_needs_key(True, update_ui=False)
            else:
                self.view_dashboard.set_stt_needs_key(True, update_ui=False)
        if provider in {
            "google",
            "openrouter",
            "openai",
            "deepseek",
            "alibaba_beijing",
            "alibaba_singapore",
        }:
            self.view_dashboard.set_translation_needs_key(True, update_ui=False)
        peer_tag = None
        if provider is not None and (
            provider == peer_tag
            or (
                peer_tag == "rolling_free"
                and provider in ("deepgram", "gemini_transcribe", "elevenlabs_scribe")
            )
        ):
            if peer_tag == "rolling_free":
                self.view_dashboard.set_peer_needs_key(
                    not self._rolling_member_verified(), update_ui=False
                )
            else:
                self.view_dashboard.set_peer_needs_key(True, update_ui=False)
        return True

    def _on_secret_cleared(self, key: str) -> None:
        """Reset verification status when API key is cleared."""
        # Map secret key name to provider name
        key_to_provider = {
            "deepgram_api_key": "deepgram",
            "gemini_transcribe_api_key": "gemini_transcribe",
            "elevenlabs_scribe_api_key": "elevenlabs_scribe",
            "soniox_api_key": "soniox",
            "google_api_key": "google",
            "openrouter_api_key": "openrouter",
            "openai_api_key": "openai",
            "deepseek_api_key": "deepseek",
            "alibaba_api_key": "alibaba_beijing",  # Use beijing as default
            "alibaba_api_key_beijing": "alibaba_beijing",
            "alibaba_api_key_singapore": "alibaba_singapore",
        }
        provider = key_to_provider.get(key)
        if provider:
            self.application.clear_provider_verification(provider)

            # Update dashboard needs_key flag
            if provider in (
                "deepgram",
                "gemini_transcribe",
                "elevenlabs_scribe",
                "soniox",
                "alibaba_beijing",
                "alibaba_singapore",
            ):
                if provider in ("deepgram", "gemini_transcribe", "elevenlabs_scribe"):
                    if self._is_self_rolling():
                        self.view_dashboard.set_stt_needs_key(
                            not self._rolling_member_verified(), update_ui=False
                        )
                    else:
                        self.view_dashboard.set_stt_needs_key(True, update_ui=False)
                else:
                    self.view_dashboard.set_stt_needs_key(True, update_ui=False)
            if provider in (
                "google",
                "openrouter",
                "openai",
                "deepseek",
                "alibaba_beijing",
                "alibaba_singapore",
            ):
                self.view_dashboard.set_translation_needs_key(True, update_ui=False)
            peer_tag = None
            if provider == peer_tag or (
                peer_tag == "rolling_free"
                and provider in ("deepgram", "gemini_transcribe", "elevenlabs_scribe")
            ):
                if peer_tag == "rolling_free":
                    self.view_dashboard.set_peer_needs_key(
                        not self._rolling_member_verified(), update_ui=False
                    )
                else:
                    self.view_dashboard.set_peer_needs_key(True, update_ui=False)

    def _show_snackbar(self, message: str, bgcolor, duration: int = 4000) -> None:
        """Show a snackbar above the bottom nav."""
        snackbar = ft.SnackBar(
            ft.Text(message, size=18, color=ft.Colors.WHITE),
            bgcolor=bgcolor,
            duration=duration,
            behavior=ft.SnackBarBehavior.FLOATING,
            elevation=0,
            margin=ft.Margin.only(bottom=90),
            padding=20,
        )
        self._mark_launch_high_priority_feedback_shown("snackbar", snackbar)
        self.page.show_dialog(snackbar)

    def show_snackbar(self, message: str, bgcolor) -> None:
        self._show_snackbar(message, bgcolor)
    def get_event_language_codes(self) -> tuple[str | None, str | None]:
        return self.application.get_event_language_codes()

    def is_event_translation_enabled(self) -> bool:
        return self.application.state().translation_enabled

    def get_event_stt_state(self) -> object | None:
        return self.application.state().stt_state

    def _schedule_app_active_day_report(self) -> None:
        self._telemetry_reporting_owner().schedule_app_active_day_report()

    def _sync_telemetry_settings_view(self, settings) -> None:
        view_settings = getattr(self, "view_settings", None)
        sync_telemetry = (
            getattr(view_settings, "sync_telemetry_settings", None)
            if view_settings is not None
            else None
        )
        if callable(sync_telemetry):
            sync_telemetry(settings)

    def _telemetry_reporting_owner(self) -> TelemetryReportingOwner:
        owner = getattr(self, "_telemetry_reporting", None)
        if owner is None:
            owner = TelemetryReportingOwner(
                self.application,
                run_background=self._run_page_task,
                queue_settings_mutation=self._queue_settings_mutation_task,
                is_shutting_down=lambda: getattr(self, "_shutting_down", False),
                sync_telemetry=self._sync_telemetry_settings_view,
            )
            self._telemetry_reporting = owner
        return owner

    def on_overlay_state_changed(
        self,
        *,
        state: str,
        failure_reason: str | None = None,
    ) -> None:
        self.overlay_state = state
        self.overlay_failure_reason = failure_reason
        self._sync_settings_overlay_runtime_state()
        self.refresh_overlay_peer_contract()


async def main_gui(
    page: ft.Page,
    *,
    config_path,
    application_factory: UiApplicationFactoryPort,
    debug_ui_preview: bool = False,
    runtime_logging_sinks=None,
    vrchat_osc_presence=None,
):
    app = TranslatorApp(
        page,
        config_path=config_path,
        application_factory=application_factory,
        debug_ui_preview=debug_ui_preview,
        runtime_logging_sinks=runtime_logging_sinks,
        vrchat_osc_presence=vrchat_osc_presence,
    )
    page.on_disconnect = app._on_page_lifecycle_end
    page.on_close = app._on_page_lifecycle_end
    try:
        await _prepare_and_show_main_window(page)
        await app.application.start()
    except BaseException:
        with contextlib.suppress(BaseException):
            await app.shutdown()
        raise
    app.schedule_after_launch_tasks()


async def _check_and_notify_update(
    page: ft.Page,
    log_diagnostic=None,
    on_launch_snackbar_shown=None,
    load_update_info=None,
) -> None:
    """Check for updates and show notification as a toast."""
    try:
        if not callable(load_update_info):
            return
        update_info = await load_update_info()
        if update_info is None:
            return

        def _open_download(_e):
            webbrowser.open(update_info.download_url)
            snackbar.open = False
            page.update()

        snackbar = ft.SnackBar(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        icon=ft.Icons.SYSTEM_UPDATE,
                        color=ft.Colors.WHITE,
                        size=28,
                    ),
                    ft.Text(
                        t("update.available", version=update_info.version),
                        color=ft.Colors.WHITE,
                        size=18,
                        font_family=font_for_language(get_locale()),
                        expand=True,
                    ),
                    ft.TextButton(
                        content=t("update.download"),
                        on_click=_open_download,
                        style=ft.ButtonStyle(
                            color=ft.Colors.WHITE,
                            text_style=text_style(size=18),
                            overlay_color=COLOR_PRIMARY,
                        ),
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            bgcolor=COLOR_SUCCESS,
            behavior=ft.SnackBarBehavior.FLOATING,
            elevation=0,
            margin=ft.Margin.only(bottom=90),
            padding=20,
            duration=30000,  # 30초
            show_close_icon=True,
            close_icon_color=ft.Colors.WHITE,
        )
        page.show_dialog(snackbar)
        if callable(on_launch_snackbar_shown):
            on_launch_snackbar_shown(snackbar)

    except Exception as exc:
        message = f"[Update] Check notification failed: {exc}"
        if callable(log_diagnostic):
            log_diagnostic(message)
            return
        logger.debug(message)
