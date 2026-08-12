from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import math
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import dataclass, replace
from typing import Any

from puripuly_heart.config.settings import (
    DESKTOP_FLET_DEFAULT_HEIGHT,
    DESKTOP_FLET_DEFAULT_SIZE_PRESET,
    DESKTOP_FLET_DEFAULT_WIDTH,
    DESKTOP_FLET_MAX_BACKGROUND_ALPHA,
    DESKTOP_FLET_MAX_OUTLINE_WIDTH,
    DESKTOP_FLET_MAX_TEXT_SCALE,
    DESKTOP_FLET_MIN_BACKGROUND_ALPHA,
    DESKTOP_FLET_MIN_HEIGHT,
    DESKTOP_FLET_MIN_OUTLINE_WIDTH,
    DESKTOP_FLET_MIN_TEXT_SCALE,
    DESKTOP_FLET_MIN_WIDTH,
)
from puripuly_heart.domain.overlay_types import (
    OverlayPresentationSnapshot,
    normalize_overlay_logging_mode,
)
from puripuly_heart.ui.fonts import assets_dir
from puripuly_heart.domain.i18n import t_for_locale
from puripuly_heart.ui.desktop_overlay_caption_plan import (
    DesktopCaptionPlan,
    DesktopCaptionSlot,
    DesktopCaptionVisualState,
    DesktopOverlayPreviewCatalog,
    DesktopOverlayPreviewFixture,
    DesktopOverlayPreviewSizePreset,
    DesktopOverlayPreviewBackgroundSurface,
    _DESKTOP_INTERACTION_MODE_EDIT,
    _DESKTOP_INTERACTION_MODE_PASS_THROUGH,
    _DESKTOP_INTERACTION_MODES,
    _DESKTOP_PREVIEW_DEFAULT_BACKGROUND_ALPHA,
    _DESKTOP_PREVIEW_DEFAULT_BACKGROUND_SURFACE_ID,
    _caption_card_width_memory_key,
    _caption_width_key_label,
    _clamp,
    _desktop_snapshot_rows_summary,
    build_desktop_caption_plan,
    build_desktop_overlay_preview_catalog,
    desktop_empty_lock_action_label,
    preview_fixture_secret_findings,
)
from puripuly_heart.ui.desktop_overlay_widgets import (
    _background_transparency_label_for_alpha,
    build_desktop_caption_surface,
    build_desktop_empty_lock_action,
    build_desktop_transparent_sizing_host,
)

from puripuly_heart.ui.desktop_overlay_renderer import (
    _STARTUP_FAILURE_EXIT_CODE,
    _SUCCESS_EXIT_CODE,
    _redact_event,
    build_parser,
    run_renderer,
)

logger = logging.getLogger(__name__)

_DESKTOP_WINDOW_BOUNDS_EVENT_NAMES = {"MOVE", "MOVED", "RESIZE", "RESIZED"}
_PROGRAMMATIC_BOUNDS_ECHO_SUPPRESSION_S = 0.25
_PROGRAMMATIC_BOUNDS_ECHO_TOLERANCE_PX = 2.0
_DESKTOP_PREVIEW_STAGE_WIDTH = 1180
_DESKTOP_PREVIEW_STAGE_HEIGHT = 420


@dataclass(frozen=True, slots=True)
class _ProgrammaticBoundsEchoSuppression:
    signature: tuple[float, float, float, float]
    expires_at: float


@dataclass(frozen=True, slots=True)
class _DesktopRenderTrace:
    content_kind: str
    surface_visible: bool
    slot_count: int
    line_count: int
    window_width: int
    window_height: int
    background_alpha: float


type FletAppRunner = Callable[[Callable[[Any], object]], Awaitable[None]]
type OverlayEventSink = Callable[[dict[str, object]], Awaitable[None]]
type PreviewAppRunner = Callable[[Callable[[Any], object]], object]


async def _default_flet_app_runner(target: Callable[[Any], object]) -> None:
    import flet as ft

    with _patch_flet_view_hidden_launcher():
        await ft.app_async(
            target=target,
            view=ft.AppView.FLET_APP_HIDDEN,
            assets_dir=str(assets_dir()),
        )


@contextlib.contextmanager
def _patch_flet_view_hidden_launcher():
    import flet_desktop

    original = flet_desktop.open_flet_view_async
    flet_desktop.open_flet_view_async = _open_flet_view_hidden_without_startup_flash
    try:
        yield
    finally:
        flet_desktop.open_flet_view_async = original


async def _open_flet_view_hidden_without_startup_flash(
    page_url: str,
    assets_dir: str | None,
    hidden: bool,
) -> tuple[asyncio.subprocess.Process, str | None]:
    import flet_desktop

    args, flet_env, pid_file = flet_desktop.__locate_and_unpack_flet_view(
        page_url,
        assets_dir,
        hidden,
    )
    kwargs: dict[str, object] = {"env": flet_env}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    return (
        await asyncio.create_subprocess_exec(args[0], *args[1:], **kwargs),
        pid_file,
    )


def _default_preview_app_runner(target: Callable[[Any], object]) -> None:
    import flet as ft

    ft.app(target=target)


_REAL_DEFAULT_PREVIEW_APP_RUNNER = _default_preview_app_runner


class FletDesktopRendererWindow:
    """Flet 0.28.3 transparent desktop overlay window boundary.

    The renderer remains persistence-free: this class only applies runtime
    controls to the Flet page/window and emits renderer-originated overlay
    events for the parent/controller to decide whether and how to persist.
    """

    def __init__(
        self,
        *,
        app_runner: FletAppRunner | None = None,
        event_sink: OverlayEventSink | None = None,
        locale: str | None = None,
        logging_mode: str = "basic",
        bounds_debounce_s: float = 0.15,
        startup_timeout_s: float = 5.0,
        preview_catalog: DesktopOverlayPreviewCatalog | None = None,
    ) -> None:
        self._app_runner = app_runner or _default_flet_app_runner
        self._event_sink = event_sink
        self._locale = locale
        self._logging_mode = normalize_overlay_logging_mode(logging_mode)
        self._bounds_debounce_s = max(0.0, float(bounds_debounce_s))
        self._startup_timeout_s = max(0.1, float(startup_timeout_s))
        self._preview_catalog = preview_catalog
        self._preview_fixture_id = preview_catalog.fixtures[0].id if preview_catalog else None
        self._preview_background_surface_id = _DESKTOP_PREVIEW_DEFAULT_BACKGROUND_SURFACE_ID
        self._preview_background_alpha = _DESKTOP_PREVIEW_DEFAULT_BACKGROUND_ALPHA
        self._preview_size_preset_id = DESKTOP_FLET_DEFAULT_SIZE_PRESET
        self._snapshot = OverlayPresentationSnapshot()
        self._visual_state = DesktopCaptionVisualState()
        self._interaction_mode = _DESKTOP_INTERACTION_MODE_EDIT
        self._startup_visual_state: DesktopCaptionVisualState | None = None
        self._startup_window_bounds: dict[str, int | float] | None = None
        self._page: Any | None = None
        self._page_ready = asyncio.Event()
        self._closed = asyncio.Event()
        self._app_task: asyncio.Task[None] | None = None
        self._page_start_error: BaseException | None = None
        self._bounds_sample_task: asyncio.Task[None] | None = None
        self._scheduled_callback_tasks: set[asyncio.Future[Any] | ConcurrentFuture[Any]] = set()
        self._programmatic_bounds_echo_suppression: _ProgrammaticBoundsEchoSuppression | None = None
        self._last_reported_bounds: tuple[float, float, float, float] | None = None
        self._caption_card_width_floor_by_block: dict[tuple[str, str, int], float] = {}
        self._last_render_trace: _DesktopRenderTrace | None = None

    def prime_startup_runtime_controls(
        self,
        payloads: tuple[dict[str, object], ...],
    ) -> tuple[dict[str, object], ...]:
        """Apply startup controls that must affect the first Flet page render.

        Returns controls that were not consumed during priming and still need
        normal runtime dispatch after the Flet page exists.
        """

        self._startup_visual_state = None
        self._startup_window_bounds = None
        residual: list[dict[str, object]] = []
        for payload in payloads:
            command = payload.get("command")
            if command is None and "logging_mode" in payload:
                if self._set_logging_mode(payload.get("logging_mode")):
                    continue
                residual.append(payload)
                continue
            if command == "set_interaction_mode":
                continue
            if command == "apply_visual_config":
                visual_state = _parse_runtime_visual_state(payload)
                if visual_state is not None:
                    self._startup_visual_state = visual_state
                else:
                    residual.append(payload)
                continue
            if command == "apply_window_bounds":
                bounds = _parse_runtime_window_bounds(payload)
                if bounds is not None:
                    self._startup_window_bounds = bounds
                else:
                    residual.append(payload)
                continue
            residual.append(payload)
        return tuple(residual)

    async def start(self, initial_snapshot: OverlayPresentationSnapshot) -> None:
        if self._preview_catalog is not None:
            self._snapshot = self._preview_selected_fixture().snapshot
            self._visual_state = self._preview_visual_state()
        else:
            self._snapshot = initial_snapshot
            self._visual_state = self._startup_visual_state or DesktopCaptionVisualState()
        self._page_ready.clear()
        self._closed.clear()
        self._page_start_error = None
        self._interaction_mode = _DESKTOP_INTERACTION_MODE_EDIT
        if self._app_task is None or self._app_task.done():
            self._app_task = asyncio.create_task(self._app_runner(self._handle_page))

        ready_task = asyncio.create_task(self._page_ready.wait())
        try:
            done, _pending = await asyncio.wait(
                {ready_task, self._app_task},
                timeout=self._startup_timeout_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ready_task not in done:
                if self._app_task in done:
                    await self._app_task
                raise RuntimeError("desktop overlay Flet page was not created")
            if self._page_start_error is not None:
                raise RuntimeError(
                    "desktop overlay Flet page configuration failed"
                ) from self._page_start_error
        finally:
            if not ready_task.done():
                ready_task.cancel()
            await asyncio.gather(ready_task, return_exceptions=True)

    async def run_until_closed(self) -> None:
        task = self._app_task
        if task is None:
            await self._closed.wait()
            return
        try:
            await task
        finally:
            self._closed.set()

    async def close(self) -> None:
        self._closed.set()
        page = self._page
        if page is not None:
            page.window.on_event = None
        await self._cancel_scheduled_callback_tasks()
        await self._cancel_bounds_sample()

        if page is not None:
            window = page.window
            try:
                window.close()
            except Exception:
                destroy = getattr(window, "destroy", None)
                if callable(destroy):
                    with contextlib.suppress(Exception):
                        destroy()

        task = self._app_task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
            except TimeoutError:
                if page is not None:
                    destroy = getattr(page.window, "destroy", None)
                    if callable(destroy):
                        with contextlib.suppress(Exception):
                            destroy()
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                    except TimeoutError:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    async def dispatch_snapshot(self, snapshot: OverlayPresentationSnapshot) -> None:
        self._emit_detailed_log(
            f"snapshot_update revision={snapshot.revision} blocks={len(snapshot.blocks)} "
            f"rows=[{_desktop_snapshot_rows_summary(snapshot)}]"
        )
        self._snapshot = snapshot
        self._render_page()

    async def dispatch_runtime_control(self, payload: dict[str, object]) -> None:
        if "logging_mode" in payload and payload.get("command") is None:
            self._set_logging_mode(payload.get("logging_mode"))
            return
        command = payload.get("command")
        if command == "set_interaction_mode":
            mode = payload.get("mode")
            if not isinstance(mode, str) or mode not in _DESKTOP_INTERACTION_MODES:
                logger.warning("[DesktopOverlay] Ignoring invalid interaction mode control")
                return
            await self._set_interaction_mode(mode, emit_event=True)
            return
        if command == "apply_window_bounds":
            bounds = _parse_runtime_window_bounds(payload)
            if bounds is None:
                logger.warning("[DesktopOverlay] Ignoring invalid window bounds control")
                return
            self._emit_detailed_log(
                "runtime_control command=apply_window_bounds "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            await self._cancel_bounds_sample()
            self._apply_window_bounds(bounds)
            return
        if command == "apply_visual_config":
            visual_state = _parse_runtime_visual_state(payload)
            if visual_state is None:
                logger.warning("[DesktopOverlay] Ignoring invalid visual config control")
                return
            self._visual_state = visual_state
            self._emit_detailed_log(
                "runtime_control command=apply_visual_config "
                f"text_scale={visual_state.text_scale} "
                f"background_alpha={visual_state.background_alpha} "
                f"outline_width={visual_state.outline_width}"
            )
            self._render_page()
            return
        logger.warning("[DesktopOverlay] Ignoring unsupported desktop runtime control: %r", command)

    def _handle_page(self, page: Any) -> None:
        self._page = page
        try:
            self._configure_base_window(page)
            self._render_page()
            self._page_ready.set()
        except Exception as exc:
            self._page_start_error = exc
            self._page_ready.set()
            raise

    def _configure_base_window(self, page: Any) -> None:
        import flet as ft

        window = page.window
        page.title = t_for_locale(
            self._locale,
            "desktop_overlay.window.title",
            default="PuriPuly Overlay",
        )
        window.icon = "icons/icon.ico"
        window.frameless = True
        window.always_on_top = True
        window.shadow = False
        window.skip_task_bar = False
        window.resizable = False
        window.maximizable = False
        window.bgcolor = ft.Colors.TRANSPARENT
        window.ignore_mouse_events = (
            self._interaction_mode == _DESKTOP_INTERACTION_MODE_PASS_THROUGH
        )
        if self._preview_catalog is not None:
            size_preset = self._preview_selected_size_preset()
            window.width = max(
                size_preset.window_width,
                _DESKTOP_PREVIEW_STAGE_WIDTH,
            )
            window.height = max(size_preset.window_height, _DESKTOP_PREVIEW_STAGE_HEIGHT)
        elif self._startup_window_bounds is not None:
            bounds = self._startup_window_bounds
            window.left = bounds["x"]
            window.top = bounds["y"]
            window.width = bounds["width"]
            window.height = bounds["height"]
            self._programmatic_bounds_echo_suppression = _ProgrammaticBoundsEchoSuppression(
                signature=_bounds_signature(bounds),
                expires_at=time.monotonic() + _PROGRAMMATIC_BOUNDS_ECHO_SUPPRESSION_S,
            )
        elif _finite_non_bool_number(getattr(window, "width", None)) in {None, 0}:
            window.width = DESKTOP_FLET_DEFAULT_WIDTH
        if self._preview_catalog is None and _finite_non_bool_number(
            getattr(window, "height", None)
        ) in {None, 0}:
            window.height = DESKTOP_FLET_DEFAULT_HEIGHT
        window.on_event = self._on_window_event
        if hasattr(window, "min_width"):
            window.min_width = DESKTOP_FLET_MIN_WIDTH
        if hasattr(window, "min_height"):
            window.min_height = DESKTOP_FLET_MIN_HEIGHT
        if self._preview_catalog is not None:
            page.on_keyboard_event = self._on_preview_keyboard_event
        page.bgcolor = ft.Colors.TRANSPARENT
        if hasattr(page, "padding"):
            page.padding = 0
        if hasattr(page, "spacing"):
            page.spacing = 0

    def _on_empty_lock_action_click(self, _event: object | None = None) -> None:
        self._run_page_task(self._lock_from_empty_action)

    async def _lock_from_empty_action(self) -> None:
        await self._set_interaction_mode(
            _DESKTOP_INTERACTION_MODE_PASS_THROUGH,
            emit_event=True,
        )

    def _render_page(self) -> None:
        page = self._page
        if page is None:
            return
        import flet as ft

        if self._preview_catalog is not None:
            root = self._build_preview_root(ft)
            if hasattr(page, "clean"):
                page.clean()
            else:
                page.controls.clear()
            page.add(root)
            self._apply_interaction_window_chrome()
            self._reveal_window_if_supported()
            page.update()
            return

        raw_plan = build_desktop_caption_plan(
            self._snapshot,
            window_width=_page_window_number(page, "width", DESKTOP_FLET_DEFAULT_WIDTH),
            window_height=_page_window_number(page, "height", DESKTOP_FLET_DEFAULT_HEIGHT),
            visual_state=self._visual_state,
            interaction_mode=self._interaction_mode,
            locale=self._locale,
        )
        previous_width_floors = dict(self._caption_card_width_floor_by_block)
        plan = self._plan_with_grow_only_caption_card_widths(raw_plan)
        self._emit_caption_width_diagnostics(raw_plan, plan, previous_width_floors)
        caption_surface = build_desktop_caption_surface(plan)
        if self._interaction_mode == _DESKTOP_INTERACTION_MODE_EDIT:
            drag_area = ft.WindowDragArea(
                content=caption_surface,
                maximizable=False,
            )
            if plan.full_window_background_visible and not plan.slots:
                content_kind = "drag_area_with_empty_lock_action"
                content = ft.Stack(
                    controls=[
                        drag_area,
                        build_desktop_empty_lock_action(
                            plan,
                            label=desktop_empty_lock_action_label(self._locale),
                            on_click=self._on_empty_lock_action_click,
                        ),
                    ],
                    width=plan.window_width,
                    height=plan.window_height,
                    alignment=ft.alignment.center,
                )
            else:
                content_kind = "drag_area"
                content = drag_area
        else:
            if plan.surface_visible:
                content_kind = "caption_surface"
                content = caption_surface
            else:
                content_kind = "transparent_host"
                content = build_desktop_transparent_sizing_host(plan)
        self._emit_detailed_log(
            "render "
            f"revision={self._snapshot.revision} "
            f"blocks={len(self._snapshot.blocks)} "
            f"interaction_mode={self._interaction_mode} "
            f"surface_visible={plan.surface_visible} "
            f"line_count={len(plan.lines)} "
            f"content_kind={content_kind} "
            f"window={plan.window_width}x{plan.window_height} "
            f"background_alpha={plan.background_alpha}"
        )
        self._emit_render_transition(
            _DesktopRenderTrace(
                content_kind=content_kind,
                surface_visible=plan.surface_visible,
                slot_count=len(plan.slots),
                line_count=len(plan.lines),
                window_width=plan.window_width,
                window_height=plan.window_height,
                background_alpha=plan.background_alpha,
            )
        )
        root = ft.Container(
            content=content,
            padding=0,
            bgcolor=ft.Colors.TRANSPARENT,
            alignment=ft.alignment.center,
        )

        if hasattr(page, "clean"):
            page.clean()
        else:
            page.controls.clear()
        page.add(root)
        self._apply_interaction_window_chrome()
        self._reveal_window_if_supported()
        page.update()

    def _apply_interaction_window_chrome(self) -> None:
        page = self._page
        if page is None:
            return
        locked = self._interaction_mode == _DESKTOP_INTERACTION_MODE_PASS_THROUGH
        window = page.window
        window.ignore_mouse_events = locked

    def _reveal_window_if_supported(self) -> None:
        page = self._page
        if page is None:
            return
        window = page.window
        if hasattr(window, "visible"):
            window.visible = True

    def _build_preview_root(self, ft: Any) -> Any:
        preview_plan = self._current_preview_caption_plan()
        caption_surface = build_desktop_caption_surface(preview_plan)
        if preview_plan.full_window_background_visible and not preview_plan.slots:
            caption_surface = ft.Stack(
                controls=[
                    caption_surface,
                    build_desktop_empty_lock_action(
                        preview_plan,
                        label=desktop_empty_lock_action_label(self._locale),
                        on_click=self._on_empty_lock_action_click,
                    ),
                ],
                width=preview_plan.window_width,
                height=preview_plan.window_height,
                alignment=ft.alignment.center,
            )
        return ft.Container(
            content=ft.Column(
                controls=[
                    self._build_preview_controls(ft),
                    self._build_preview_surface_backdrop(ft, caption_surface),
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=16,
            bgcolor="#101827",
            alignment=ft.alignment.center,
        )

    def _build_preview_controls(self, ft: Any) -> Any:
        catalog = self._preview_catalog
        if catalog is None:
            return ft.Container()
        labels = catalog.labels
        return ft.Column(
            controls=[
                self._build_preview_button_group(
                    ft,
                    labels.fixture,
                    [
                        (fixture.id, fixture.label, fixture.id == self._preview_fixture_id)
                        for fixture in catalog.fixtures
                    ],
                    self._set_preview_fixture,
                ),
                self._build_preview_button_group(
                    ft,
                    labels.size_preset,
                    [
                        (preset.id, preset.label, preset.id == self._preview_size_preset_id)
                        for preset in catalog.size_presets
                    ],
                    self._set_preview_size_preset,
                ),
                self._build_preview_button_group(
                    ft,
                    labels.background_alpha,
                    [
                        (
                            str(value),
                            _background_transparency_label_for_alpha(value),
                            value == self._preview_background_alpha,
                        )
                        for value in catalog.background_alpha_presets
                    ],
                    lambda value: self._set_preview_background_alpha(float(value)),
                ),
                self._build_preview_button_group(
                    ft,
                    labels.background_surface,
                    [
                        (
                            surface.id,
                            surface.label,
                            surface.id == self._preview_background_surface_id,
                        )
                        for surface in catalog.background_surfaces
                    ],
                    self._set_preview_background_surface,
                ),
            ],
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )

    def _build_preview_button_group(
        self,
        ft: Any,
        label: str,
        items: list[tuple[str, str, bool]],
        on_select: Callable[[str], None],
    ) -> Any:
        return ft.Column(
            controls=[
                ft.Text(label, size=12, weight=ft.FontWeight.BOLD, color="#FFE7D6"),
                ft.Row(
                    controls=[
                        ft.ElevatedButton(
                            text=text,
                            on_click=lambda _event, selected=value: self._select_preview(
                                selected,
                                on_select,
                            ),
                            disabled=selected,
                        )
                        for value, text, selected in items
                    ],
                    spacing=6,
                    alignment=ft.MainAxisAlignment.CENTER,
                    wrap=True,
                ),
            ],
            spacing=4,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )

    def _build_preview_surface_backdrop(self, ft: Any, caption_surface: Any) -> Any:
        surface = self._preview_selected_background_surface()
        size_preset = self._preview_selected_size_preset()
        controls: list[Any] = []
        if surface.id == "busy":
            controls.append(self._build_preview_busy_background(ft, size_preset))
        controls.append(caption_surface)
        content: Any = caption_surface
        if len(controls) > 1:
            content = ft.Stack(
                controls=controls,
                width=size_preset.window_width,
                height=size_preset.window_height,
                alignment=ft.alignment.center,
            )
        return ft.Container(
            content=content,
            width=size_preset.window_width,
            height=size_preset.window_height,
            bgcolor=surface.bgcolor,
            padding=24,
            border_radius=20,
            alignment=ft.alignment.center,
        )

    def _build_preview_busy_background(
        self,
        ft: Any,
        size_preset: DesktopOverlayPreviewSizePreset,
    ) -> Any:
        colors = (
            "#475569",
            "#7C3AED",
            "#0EA5E9",
            "#F97316",
            "#22C55E",
            "#334155",
        )
        rows = []
        for row_index in range(5):
            rows.append(
                ft.Row(
                    controls=[
                        ft.Container(
                            width=140 + (column_index % 3) * 46,
                            height=54 + ((row_index + column_index) % 2) * 22,
                            bgcolor=colors[(row_index + column_index) % len(colors)],
                            border_radius=14,
                            opacity=0.72,
                        )
                        for column_index in range(5)
                    ],
                    spacing=12,
                    alignment=ft.MainAxisAlignment.CENTER,
                )
            )
        return ft.Container(
            content=ft.Column(
                controls=rows,
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            width=size_preset.window_width,
            height=size_preset.window_height,
            alignment=ft.alignment.center,
        )

    def _select_preview(self, value: str, on_select: Callable[[str], None]) -> None:
        on_select(value)
        self._render_page()

    def _set_preview_fixture(self, fixture_id: str) -> None:
        catalog = self._preview_catalog
        if catalog is None or not any(fixture.id == fixture_id for fixture in catalog.fixtures):
            return
        self._preview_fixture_id = fixture_id
        self._snapshot = self._preview_selected_fixture().snapshot

    def _set_preview_background_alpha(self, value: float) -> None:
        catalog = self._preview_catalog
        if catalog is None or value not in catalog.background_alpha_presets:
            return
        self._preview_background_alpha = value
        self._visual_state = self._preview_visual_state()

    def _set_preview_size_preset(self, preset_id: str) -> None:
        catalog = self._preview_catalog
        if catalog is None or not any(preset.id == preset_id for preset in catalog.size_presets):
            return
        self._preview_size_preset_id = preset_id
        self._apply_preview_window_size()
        self._visual_state = self._preview_visual_state()

    def _set_preview_background_surface(self, surface_id: str) -> None:
        catalog = self._preview_catalog
        if catalog is None or not any(
            surface.id == surface_id for surface in catalog.background_surfaces
        ):
            return
        self._preview_background_surface_id = surface_id

    def _preview_selected_fixture(self) -> DesktopOverlayPreviewFixture:
        catalog = self._preview_catalog
        assert catalog is not None
        for fixture in catalog.fixtures:
            if fixture.id == self._preview_fixture_id:
                return fixture
        return catalog.fixtures[0]

    def _preview_selected_background_surface(self) -> DesktopOverlayPreviewBackgroundSurface:
        catalog = self._preview_catalog
        assert catalog is not None
        for surface in catalog.background_surfaces:
            if surface.id == self._preview_background_surface_id:
                return surface
        return catalog.background_surfaces[0]

    def _preview_selected_size_preset(self) -> DesktopOverlayPreviewSizePreset:
        catalog = self._preview_catalog
        assert catalog is not None
        for preset in catalog.size_presets:
            if preset.id == self._preview_size_preset_id:
                return preset
        return catalog.size_presets[1]

    def _apply_preview_window_size(self) -> None:
        page = self._page
        if page is None or self._preview_catalog is None:
            return
        preset = self._preview_selected_size_preset()
        page.window.width = preset.window_width
        page.window.height = preset.window_height

    def _current_preview_caption_plan(self) -> DesktopCaptionPlan:
        preset = self._preview_selected_size_preset()
        plan = build_desktop_caption_plan(
            self._preview_selected_fixture().snapshot,
            window_width=preset.window_width,
            window_height=preset.window_height,
            visual_state=self._preview_visual_state(),
            interaction_mode=self._interaction_mode,
            locale=self._locale,
        )
        return self._plan_with_grow_only_caption_card_widths(plan)

    def _plan_with_grow_only_caption_card_widths(
        self,
        plan: DesktopCaptionPlan,
    ) -> DesktopCaptionPlan:
        if not plan.slots:
            self._caption_card_width_floor_by_block.clear()
            return plan
        if plan.full_window_background_visible:
            return plan

        active_keys = {_caption_card_width_memory_key(slot) for slot in plan.slots}
        for key in tuple(self._caption_card_width_floor_by_block):
            if key not in active_keys:
                del self._caption_card_width_floor_by_block[key]

        grown_slots: list[DesktopCaptionSlot] = []
        for slot in plan.slots:
            key = _caption_card_width_memory_key(slot)
            previous_width = self._caption_card_width_floor_by_block.get(key, 0.0)
            card_width = _clamp(max(slot.card_width, previous_width), 1.0, float(plan.window_width))
            self._caption_card_width_floor_by_block[key] = card_width
            grown_slots.append(
                replace(
                    slot,
                    card_width=card_width,
                    card_text_width=max(1.0, card_width - (plan.padding_horizontal * 2)),
                )
            )
        return replace(
            plan,
            slots=tuple(grown_slots),
            lines=tuple(line for slot in grown_slots for line in slot.lines),
        )

    def _emit_caption_width_diagnostics(
        self,
        raw_plan: DesktopCaptionPlan,
        applied_plan: DesktopCaptionPlan,
        previous_width_floors: dict[tuple[str, str, int], float],
    ) -> None:
        if self._logging_mode != "detailed":
            return
        raw_slots_by_key = {_caption_card_width_memory_key(slot): slot for slot in raw_plan.slots}
        for slot_index, slot in enumerate(applied_plan.slots):
            key = _caption_card_width_memory_key(slot)
            raw_slot = raw_slots_by_key.get(key)
            if raw_slot is None:
                continue
            previous_floor = previous_width_floors.get(key, 0.0)
            floor_hit = slot.card_width > raw_slot.card_width + 0.01
            self._emit_detailed_log(
                "render_width "
                f"revision={self._snapshot.revision} "
                f"slot={slot_index} "
                f"key={_caption_width_key_label(key)} "
                f"raw_card_width={raw_slot.card_width:.1f} "
                f"applied_card_width={slot.card_width:.1f} "
                f"raw_text_width={raw_slot.card_text_width:.1f} "
                f"applied_text_width={slot.card_text_width:.1f} "
                f"previous_floor={previous_floor:.1f} "
                f"floor_hit={floor_hit} "
                f"line_count={len(slot.lines)} "
                f"primary_len={sum(len(line.text) for line in slot.lines if line.slot == 'primary')} "
                f"secondary_len={sum(len(line.text) for line in slot.lines if line.slot == 'secondary')}"
            )

    def _emit_render_transition(self, trace: _DesktopRenderTrace) -> None:
        previous = self._last_render_trace
        self._last_render_trace = trace
        if previous is None:
            return
        self._emit_detailed_log(
            "render_transition "
            f"revision={self._snapshot.revision} "
            f"content_kind {previous.content_kind}->{trace.content_kind} "
            f"surface_visible {previous.surface_visible}->{trace.surface_visible} "
            f"slot_count {previous.slot_count}->{trace.slot_count} "
            f"line_count {previous.line_count}->{trace.line_count} "
            f"window {previous.window_width}x{previous.window_height}->"
            f"{trace.window_width}x{trace.window_height} "
            f"background_alpha {previous.background_alpha:.3f}->{trace.background_alpha:.3f}"
        )

    def _preview_visual_state(self) -> DesktopCaptionVisualState:
        return DesktopCaptionVisualState(
            background_alpha=self._preview_background_alpha,
        )

    def _on_preview_keyboard_event(self, event: object) -> None:
        key = str(getattr(event, "key", "")).lower()
        if key not in {"e", "escape"}:
            return
        self._run_page_task(self._return_preview_to_edit_mode)

    async def _return_preview_to_edit_mode(self) -> None:
        await self._set_interaction_mode(_DESKTOP_INTERACTION_MODE_EDIT, emit_event=True)

    async def _set_interaction_mode(self, mode: str, *, emit_event: bool) -> None:
        if mode not in _DESKTOP_INTERACTION_MODES:
            return
        if mode == self._interaction_mode:
            return
        previous_mode = self._interaction_mode
        self._interaction_mode = mode
        self._emit_detailed_log(f"interaction_mode {previous_mode}->{mode}")
        self._render_page()
        if emit_event:
            await self._emit_overlay_event({"event": "interaction_mode_changed", "mode": mode})

    def _apply_window_bounds(self, bounds: dict[str, int | float]) -> None:
        page = self._page
        if page is None:
            return
        if _page_window_size_differs_from_bounds(page, bounds):
            self._caption_card_width_floor_by_block.clear()
        self._emit_detailed_log(
            "apply_window_bounds "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']}"
        )
        self._apply_window_bounds_without_rerender(bounds)
        self._programmatic_bounds_echo_suppression = _ProgrammaticBoundsEchoSuppression(
            signature=_bounds_signature(bounds),
            expires_at=time.monotonic() + _PROGRAMMATIC_BOUNDS_ECHO_SUPPRESSION_S,
        )
        self._render_page()

    def _apply_window_bounds_without_rerender(self, bounds: dict[str, int | float]) -> None:
        page = self._page
        if page is None:
            return
        window = page.window
        window.left = bounds["x"]
        window.top = bounds["y"]
        window.width = bounds["width"]
        window.height = bounds["height"]

    def _on_window_event(self, event: object) -> None:
        if self._closed.is_set():
            return
        if not _is_window_bounds_event(event):
            return
        self._emit_detailed_log(
            f"window_event type={getattr(event, 'type', getattr(event, 'data', None))} "
            f"interaction_mode={self._interaction_mode}"
        )
        if self._interaction_mode != _DESKTOP_INTERACTION_MODE_EDIT:
            self._emit_detailed_log(
                "bounds_sample dropped reason=event_interaction_mode "
                f"interaction_mode={self._interaction_mode}"
            )
            return
        self._run_page_task(self._schedule_bounds_sample)

    async def _schedule_bounds_sample(self) -> None:
        if self._closed.is_set():
            return
        await self._cancel_bounds_sample()
        if self._closed.is_set():
            return
        self._emit_detailed_log(
            f"bounds_sample scheduled interaction_mode={self._interaction_mode}"
        )
        self._bounds_sample_task = asyncio.create_task(self._emit_debounced_bounds_sample())

    async def _cancel_bounds_sample(self) -> None:
        task = self._bounds_sample_task
        self._bounds_sample_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _emit_debounced_bounds_sample(self) -> None:
        if self._closed.is_set():
            return
        if self._bounds_debounce_s > 0:
            await asyncio.sleep(self._bounds_debounce_s)
        if self._closed.is_set():
            return
        bounds = _sample_page_window_bounds(self._page)
        if bounds is None:
            self._emit_detailed_log("bounds_sample dropped reason=no_bounds")
            return
        signature = _bounds_signature(bounds)
        if self._is_programmatic_bounds_echo(signature):
            self._emit_detailed_log(
                "bounds_sample dropped reason=programmatic_echo "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            return
        if self._interaction_mode != _DESKTOP_INTERACTION_MODE_EDIT:
            self._emit_detailed_log(
                "bounds_sample dropped reason=interaction_mode "
                f"interaction_mode={self._interaction_mode} "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            return
        if signature == self._last_reported_bounds:
            self._emit_detailed_log(
                "bounds_sample dropped reason=unchanged "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            return
        self._programmatic_bounds_echo_suppression = None
        self._last_reported_bounds = signature
        self._emit_detailed_log(
            "bounds_sample emitted source=user persist=True "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']}"
        )
        await self._emit_overlay_event(
            {
                "event": "window_bounds_changed",
                "source": "user",
                "persist": True,
                **bounds,
            }
        )

    def _run_page_task(self, func: Callable[[], Awaitable[None]]) -> None:
        if self._closed.is_set():
            return
        page = self._page
        if page is not None:
            run_task = getattr(page, "run_task", None)
            if callable(run_task):
                self._track_scheduled_callback_task(run_task(func))
                return
        self._track_scheduled_callback_task(asyncio.create_task(func()))

    async def _emit_overlay_event(self, payload: dict[str, object]) -> None:
        if self._closed.is_set():
            return
        if self._event_sink is None:
            return
        await self._event_sink({"type": "overlay_event", "payload": payload})

    def _set_logging_mode(self, mode: object) -> bool:
        try:
            normalized_mode = normalize_overlay_logging_mode(mode)
        except Exception:
            return False
        self._logging_mode = normalized_mode
        self._emit_detailed_log(f"logging_mode mode={normalized_mode}")
        return True

    def _emit_detailed_log(self, message: str) -> None:
        if self._logging_mode != "detailed":
            return
        print(f"[DesktopOverlay][Detail] {message}", flush=True)

    def _is_programmatic_bounds_echo(
        self,
        signature: tuple[float, float, float, float],
    ) -> bool:
        suppression = self._programmatic_bounds_echo_suppression
        if suppression is None:
            return False
        if time.monotonic() > suppression.expires_at:
            self._programmatic_bounds_echo_suppression = None
            return False
        return _bounds_signatures_close(signature, suppression.signature)

    def _track_scheduled_callback_task(self, task: object) -> None:
        if not isinstance(task, (asyncio.Future, ConcurrentFuture)):
            return
        self._scheduled_callback_tasks.add(task)
        task.add_done_callback(self._scheduled_callback_tasks.discard)

    async def _cancel_scheduled_callback_tasks(self) -> None:
        tasks = tuple(self._scheduled_callback_tasks)
        self._scheduled_callback_tasks.clear()
        if not tasks:
            return
        current_task = asyncio.current_task()
        awaitables: list[asyncio.Future[Any]] = []
        for task in tasks:
            if task is current_task:
                continue
            task.cancel()
            if isinstance(task, asyncio.Future):
                awaitables.append(task)
            else:
                awaitables.append(asyncio.wrap_future(task))
        if awaitables:
            await asyncio.gather(*awaitables, return_exceptions=True)


def _page_window_number(page: Any, field_name: str, default: int) -> int | float:
    return getattr(page.window, field_name, default) or default


def _page_window_size_differs_from_bounds(
    page: Any,
    bounds: dict[str, int | float],
) -> bool:
    window = page.window
    current_width = _finite_non_bool_number(getattr(window, "width", None))
    current_height = _finite_non_bool_number(getattr(window, "height", None))
    if current_width is None or current_height is None:
        return True
    width_changed = float(current_width) != float(bounds["width"])
    height_changed = float(current_height) != float(bounds["height"])
    return width_changed or height_changed


def _parse_runtime_window_bounds(
    payload: dict[str, object],
) -> dict[str, int | float] | None:
    x = _finite_non_bool_number(payload.get("x"))
    y = _finite_non_bool_number(payload.get("y"))
    width = _finite_non_bool_number(payload.get("width"))
    height = _finite_non_bool_number(payload.get("height"))
    if x is None or y is None or width is None or height is None:
        return None
    if width < DESKTOP_FLET_MIN_WIDTH or height < DESKTOP_FLET_MIN_HEIGHT:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _parse_runtime_visual_state(payload: dict[str, object]) -> DesktopCaptionVisualState | None:
    text_scale = _finite_non_bool_number(payload.get("text_scale"))
    background_alpha = _finite_non_bool_number(payload.get("background_alpha"))
    outline_width_raw = payload.get("outline_width")
    if text_scale is None or background_alpha is None:
        return None
    if not DESKTOP_FLET_MIN_TEXT_SCALE <= text_scale <= DESKTOP_FLET_MAX_TEXT_SCALE:
        return None
    if (
        not DESKTOP_FLET_MIN_BACKGROUND_ALPHA
        <= background_alpha
        <= DESKTOP_FLET_MAX_BACKGROUND_ALPHA
    ):
        return None
    outline_width: float | None = None
    if outline_width_raw is not None:
        outline_number = _finite_non_bool_number(outline_width_raw)
        if outline_number is None:
            return None
        if not DESKTOP_FLET_MIN_OUTLINE_WIDTH <= outline_number <= DESKTOP_FLET_MAX_OUTLINE_WIDTH:
            return None
        outline_width = float(outline_number)
    return DesktopCaptionVisualState(
        text_scale=float(text_scale),
        background_alpha=float(background_alpha),
        outline_width=outline_width,
    )


def _sample_page_window_bounds(page: Any | None) -> dict[str, int | float] | None:
    if page is None:
        return None
    window = page.window
    bounds = {
        "x": _finite_non_bool_number(getattr(window, "left", None)),
        "y": _finite_non_bool_number(getattr(window, "top", None)),
        "width": _finite_non_bool_number(getattr(window, "width", None)),
        "height": _finite_non_bool_number(getattr(window, "height", None)),
    }
    if any(value is None for value in bounds.values()):
        return None
    typed_bounds = {key: value for key, value in bounds.items() if value is not None}
    if (
        typed_bounds["x"] == 0
        and typed_bounds["y"] == 0
        and typed_bounds["width"] == 0
        and typed_bounds["height"] == 0
    ):
        return None
    if typed_bounds["width"] <= 0 or typed_bounds["height"] <= 0:
        return None
    return typed_bounds


def _is_window_bounds_event(event: object) -> bool:
    event_type = getattr(event, "type", None)
    if event_type is None:
        event_type = getattr(event, "data", None)
    event_name = getattr(event_type, "name", None)
    if event_name is None:
        event_name = getattr(event_type, "value", None)
    if event_name is None:
        event_name = str(event_type)
    return str(event_name).split(".")[-1].upper() in _DESKTOP_WINDOW_BOUNDS_EVENT_NAMES


def _bounds_signature(bounds: dict[str, int | float]) -> tuple[float, float, float, float]:
    return (
        float(bounds["x"]),
        float(bounds["y"]),
        float(bounds["width"]),
        float(bounds["height"]),
    )


def _bounds_signatures_close(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return all(
        abs(left - right) <= _PROGRAMMATIC_BOUNDS_ECHO_TOLERANCE_PX
        for left, right in zip(first, second, strict=True)
    )


def _finite_non_bool_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


def run_preview(
    *,
    app_runner: PreviewAppRunner | None = None,
    locale: str | None = None,
) -> int:
    catalog = build_desktop_overlay_preview_catalog(locale=locale)
    secret_findings = preview_fixture_secret_findings(catalog)
    if secret_findings:
        for finding in secret_findings:
            logger.error("Unsafe desktop overlay preview fixture data: %s", finding)
        return _STARTUP_FAILURE_EXIT_CODE
    runner = app_runner or _default_preview_app_runner
    return asyncio.run(
        _run_preview_async(
            catalog=catalog,
            app_runner=runner,
            locale=locale,
            allow_no_page=app_runner is not None or runner is not _REAL_DEFAULT_PREVIEW_APP_RUNNER,
        )
    )


async def _run_preview_async(
    *,
    catalog: DesktopOverlayPreviewCatalog,
    app_runner: PreviewAppRunner,
    locale: str | None,
    allow_no_page: bool,
) -> int:
    async def preview_app_runner(target: Callable[[Any], object]) -> None:
        result = app_runner(target)
        if inspect.isawaitable(result):
            await result

    async def preview_event_sink(event: dict[str, object]) -> None:
        logger.debug("Desktop overlay preview event: %r", _redact_event(event))

    window = FletDesktopRendererWindow(
        app_runner=preview_app_runner,
        event_sink=preview_event_sink,
        locale=locale,
        preview_catalog=catalog,
    )
    try:
        try:
            await window.start(catalog.fixtures[0].snapshot)
        except RuntimeError:
            if allow_no_page and window._page is None:
                return _SUCCESS_EXIT_CODE
            raise
        await window.run_until_closed()
    finally:
        await window.close()
    return _SUCCESS_EXIT_CODE


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.preview:
        return run_preview()
    return run_renderer(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
