from __future__ import annotations

import asyncio
import copy
import logging
import math
import sys

from puripuly_heart.config.settings import (
    DESKTOP_FLET_MIN_HEIGHT,
    DESKTOP_FLET_MIN_WIDTH,
    DESKTOP_FLET_SIZE_PRESETS,
    OVERLAY_TARGET_DESKTOP,
    OVERLAY_TARGET_STEAMVR,
    AppSettings,
)
from puripuly_heart.core.overlay.process import (
    DefaultOverlayProcessRunner,
    DesktopFletOverlayRunner,
    OverlayProcessRunner,
)

logger = logging.getLogger(__name__)

DESKTOP_BOUNDS_PERSIST_DEBOUNCE_S = 0.05
DESKTOP_INTERACTION_MODE_EDIT = "edit"
DESKTOP_INTERACTION_MODE_PASS_THROUGH = "pass_through"
DESKTOP_INTERACTION_MODES = frozenset(
    {DESKTOP_INTERACTION_MODE_EDIT, DESKTOP_INTERACTION_MODE_PASS_THROUGH}
)


class OverlayManagerMixin:
    """Mixin class containing overlay management methods."""

    @property
    def desktop_overlay_captions_locked(self) -> bool:
        return self.desktop_overlay_interaction_mode == DESKTOP_INTERACTION_MODE_PASS_THROUGH

    @staticmethod
    def _normalized_overlay_target(value: object) -> str:
        if value == OVERLAY_TARGET_DESKTOP:
            return OVERLAY_TARGET_DESKTOP
        return OVERLAY_TARGET_STEAMVR

    def _overlay_target_for_settings(self, settings: AppSettings | None = None) -> str:
        resolved_settings = settings or self.settings
        if resolved_settings is None:
            return OVERLAY_TARGET_STEAMVR
        return self._normalized_overlay_target(resolved_settings.overlay.target)

    def _overlay_runtime_is_active(self) -> bool:
        start_task = self._overlay_start_task
        return bool(
            self.overlay_state in {"starting", "connected"}
            or self._overlay_bridge is not None
            or self._overlay_manager is not None
            or (start_task is not None and not start_task.done())
        )

    def _previous_overlay_target_for_apply(self) -> str:
        if self._overlay_runtime_is_active() and self._active_overlay_target is not None:
            return self._active_overlay_target
        return self._overlay_target_for_settings(self.settings)

    def _overlay_process_runner_for_target(self, target: str) -> OverlayProcessRunner:
        from puripuly_heart.app.wiring import get_or_create_job_handle
        if target == OVERLAY_TARGET_DESKTOP:
            return DesktopFletOverlayRunner(job_handle=get_or_create_job_handle())
        return DefaultOverlayProcessRunner(job_handle=get_or_create_job_handle())

    def _build_initial_desktop_runtime_controls(
        self,
        settings: AppSettings,
    ) -> list[dict[str, object]]:
        desktop_settings = copy.deepcopy(settings.overlay.desktop_flet)
        desktop_settings.validate()
        bounds = self._desktop_launch_bounds_for_current_launch(desktop_settings)
        visual = desktop_settings.visual
        interaction_mode = DESKTOP_INTERACTION_MODE_EDIT
        self.log_detailed(
            "[DesktopOverlay][Launch] "
            f"target=desktop locked={desktop_settings.locked} "
            f"interaction_mode={interaction_mode} "
            f"size_preset={desktop_settings.size_preset} "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']} "
            f"text_scale={visual.text_scale} "
            f"background_alpha={visual.background_alpha} "
            f"outline_width={visual.outline_width}"
        )
        return [
            {
                "command": "apply_window_bounds",
                "x": bounds["x"],
                "y": bounds["y"],
                "width": bounds["width"],
                "height": bounds["height"],
            },
            {
                "command": "apply_visual_config",
                "text_scale": visual.text_scale,
                "background_alpha": visual.background_alpha,
                "outline_width": visual.outline_width,
            },
            {"command": "set_interaction_mode", "mode": interaction_mode},
        ]

    @staticmethod
    def _desktop_dimensions_for_size_preset(size_preset: object) -> tuple[int, int]:
        if isinstance(size_preset, str) and size_preset in DESKTOP_FLET_SIZE_PRESETS:
            return DESKTOP_FLET_SIZE_PRESETS[size_preset]
        return DESKTOP_FLET_SIZE_PRESETS["medium"]

    def _desktop_launch_bounds_for_current_launch(
        self,
        desktop_settings: object,
    ) -> dict[str, int | float]:
        position = getattr(desktop_settings, "position", None)
        x = getattr(position, "x", None)
        y = getattr(position, "y", None)
        width, height = self._desktop_dimensions_for_size_preset(
            getattr(desktop_settings, "size_preset", None)
        )
        if self._is_finite_non_bool_number(x) and self._is_finite_non_bool_number(y):
            return {"x": x, "y": y, "width": width, "height": height}  # type: ignore[dict-item]
        return self._desktop_centered_bounds_for_dimensions(width=width, height=height)

    def _desktop_centered_bounds_for_dimensions(
        self,
        *,
        width: int | float,
        height: int | float,
    ) -> dict[str, int | float]:
        work_area = self._desktop_work_area_for_current_launch()
        if work_area is None:
            return {"x": 0, "y": 0, "width": width, "height": height}
        left, top, work_width, work_height = work_area
        if not (
            self._is_finite_non_bool_number(left)
            and self._is_finite_non_bool_number(top)
            and self._is_finite_non_bool_number(work_width)
            and self._is_finite_non_bool_number(work_height)
            and work_width > 0
            and work_height > 0
        ):
            return {"x": 0, "y": 0, "width": width, "height": height}

        return {
            "x": left + ((work_width - width) / 2),
            "y": top + ((work_height - height) / 2),
            "width": width,
            "height": height,
        }

    @staticmethod
    def _is_finite_non_bool_number(value: object) -> bool:
        return (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )

    @staticmethod
    def _desktop_bounds_signature(
        bounds: dict[str, int | float],
    ) -> tuple[float, float, float, float]:
        return (
            float(bounds["x"]),
            float(bounds["y"]),
            float(bounds["width"]),
            float(bounds["height"]),
        )

    def _desktop_bounds_from_payload(
        self,
        payload: dict[object, object],
    ) -> dict[str, int | float] | None:
        x = payload.get("x")
        y = payload.get("y")
        width = payload.get("width")
        height = payload.get("height")
        if not (
            self._is_finite_non_bool_number(x)
            and self._is_finite_non_bool_number(y)
            and self._is_finite_non_bool_number(width)
            and self._is_finite_non_bool_number(height)
        ):
            return None
        if width < DESKTOP_FLET_MIN_WIDTH or height < DESKTOP_FLET_MIN_HEIGHT:  # type: ignore[operator]
            return None
        return {
            "x": x,  # type: ignore[dict-item]
            "y": y,  # type: ignore[dict-item]
            "width": width,  # type: ignore[dict-item]
            "height": height,  # type: ignore[dict-item]
        }

    def _is_valid_desktop_window_bounds_event_payload(
        self,
        payload: dict[object, object],
    ) -> bool:
        source = payload.get("source")
        persist = payload.get("persist")
        if source not in {"user", "reset", "programmatic", "launch_repair"}:
            return False
        expected_persist = source in {"user", "reset"}
        return bool(
            payload.get("event") == "window_bounds_changed"
            and isinstance(persist, bool)
            and persist is expected_persist
            and self._desktop_bounds_from_payload(payload) is not None
        )

    def _track_desktop_apply_window_bounds_control(self, payload: dict[str, object]) -> None:
        if payload.get("command") != "apply_window_bounds":
            return
        bounds = self._desktop_bounds_from_payload(payload)
        if bounds is None:
            return
        self._desktop_suppressed_bounds_signatures.add(self._desktop_bounds_signature(bounds))

    def _consume_suppressed_desktop_bounds(self, bounds: dict[str, int | float]) -> bool:
        signature = self._desktop_bounds_signature(bounds)
        if signature not in self._desktop_suppressed_bounds_signatures:
            return False
        self._desktop_suppressed_bounds_signatures.discard(signature)
        return True

    def _discard_suppressed_desktop_bounds(self, bounds: dict[str, int | float]) -> None:
        self._desktop_suppressed_bounds_signatures.discard(self._desktop_bounds_signature(bounds))

    @staticmethod
    def _is_desktop_user_window_bounds_event(event: object) -> bool:
        if not isinstance(event, dict):
            return False
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return False
        return bool(
            payload.get("event") == "window_bounds_changed"
            and payload.get("source") == "user"
            and payload.get("persist") is True
        )

    def _drain_pending_desktop_user_bounds_events(self) -> None:
        queue = self._desktop_renderer_events
        if queue is None:
            return
        retained: list[dict[str, object]] = []
        dropped = 0
        while True:
            try:
                event = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if self._is_desktop_user_window_bounds_event(event):
                dropped += 1
                continue
            retained.append(event)
        for event in retained:
            queue.put_nowait(event)
        if dropped:
            self.log_detailed(
                f"[DesktopOverlay][Bounds] drained_pending_user_bounds count={dropped}"
            )

    def _set_desktop_overlay_interaction_mode(self, mode: object) -> bool:
        if not isinstance(mode, str) or mode not in DESKTOP_INTERACTION_MODES:
            return False
        previous_mode = self.desktop_overlay_interaction_mode
        self.desktop_overlay_interaction_mode = mode
        if previous_mode != mode:
            self._notify_desktop_overlay_interaction_mode()
        return True

    def _notify_desktop_overlay_interaction_mode(self) -> None:
        handler = getattr(self.app, "on_desktop_overlay_state_changed", None)
        if callable(handler):
            handler(
                interaction_mode=self.desktop_overlay_interaction_mode,
                captions_locked=self.desktop_overlay_captions_locked,
            )

    async def set_desktop_overlay_captions_locked(self, locked: bool) -> None:
        if self.settings is None:
            return
        if self.overlay_state != "connected":
            return
        if self._active_overlay_target != OVERLAY_TARGET_DESKTOP or self._overlay_bridge is None:
            return

        mode = DESKTOP_INTERACTION_MODE_PASS_THROUGH if locked else DESKTOP_INTERACTION_MODE_EDIT
        if not await self._broadcast_desktop_runtime_control(
            {
                "command": "set_interaction_mode",
                "mode": mode,
            }
        ):
            return
        self._set_desktop_overlay_interaction_mode(mode)

    async def set_desktop_overlay_size_preset(self, size_preset: str) -> None:
        if self.settings is None:
            return
        normalized_size_preset = (
            size_preset if size_preset in DESKTOP_FLET_SIZE_PRESETS else "medium"
        )
        if self.settings.overlay.desktop_flet.size_preset == normalized_size_preset:
            return
        updated = copy.deepcopy(self.settings)
        updated.overlay.desktop_flet.size_preset = normalized_size_preset
        await self.apply_settings(updated)

    async def reset_desktop_overlay_position(self) -> None:
        await self._handle_desktop_overlay_reset_requested()

    async def _broadcast_desktop_runtime_control(self, payload: dict[str, object]) -> bool:
        if self._active_overlay_target != OVERLAY_TARGET_DESKTOP:
            return False
        bridge = self._overlay_bridge
        if bridge is None:
            return False
        broadcast = getattr(bridge, "broadcast_desktop_runtime_control", None)
        if not callable(broadcast):
            return False
        try:
            await broadcast(payload)
        except Exception as exc:
            self.log_detailed(
                "[Overlay] Failed to send desktop runtime control",
                level=logging.WARNING,
                exception=exc,
            )
            return False
        return True

    async def _broadcast_desktop_window_bounds_control(
        self,
        bounds: dict[str, int | float],
    ) -> None:
        payload: dict[str, object] = {
            "command": "apply_window_bounds",
            "x": bounds["x"],
            "y": bounds["y"],
            "width": bounds["width"],
            "height": bounds["height"],
        }
        if await self._broadcast_desktop_runtime_control(payload):
            self._track_desktop_apply_window_bounds_control(payload)

    async def _consume_desktop_renderer_events(
        self,
        queue: asyncio.Queue[dict[str, object]],
    ) -> None:
        try:
            while True:
                event = await queue.get()
                try:
                    await self._handle_desktop_renderer_event(event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.log_detailed(
                        "[Overlay] Ignoring desktop renderer event after controller error",
                        level=logging.WARNING,
                        exception=exc,
                    )
        except asyncio.CancelledError:
            raise

    async def _handle_desktop_renderer_event(self, event: object) -> None:
        if self._active_overlay_target != OVERLAY_TARGET_DESKTOP:
            return
        if not isinstance(event, dict):
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        event_type = payload.get("event")
        if event_type == "window_bounds_changed":
            await self._handle_desktop_window_bounds_changed(payload)
            return
        if event_type == "reset_to_bottom_center_requested":
            await self._handle_desktop_overlay_reset_requested()
            return
        if event_type == "interaction_mode_changed":
            self._set_desktop_overlay_interaction_mode(payload.get("mode"))

    async def _handle_desktop_window_bounds_changed(
        self,
        payload: dict[object, object],
    ) -> None:
        if not self._is_valid_desktop_window_bounds_event_payload(payload):
            self.log_detailed(
                "[DesktopOverlay][Bounds] ignored reason=invalid_payload "
                f"keys={sorted(str(key) for key in payload)} "
                f"source={payload.get('source')} persist={payload.get('persist')}"
            )
            return
        bounds = self._desktop_bounds_from_payload(payload)
        if bounds is None:
            self.log_detailed(
                "[DesktopOverlay][Bounds] ignored reason=invalid_bounds "
                f"source={payload.get('source')} persist={payload.get('persist')}"
            )
            return
        source = payload.get("source")
        interaction_mode = self.desktop_overlay_interaction_mode
        self.log_detailed(
            "[DesktopOverlay][Bounds] received "
            f"source={source} persist={payload.get('persist')} "
            f"interaction_mode={interaction_mode} "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']}"
        )
        if source in {"programmatic", "launch_repair"}:
            self.log_detailed(
                "[DesktopOverlay][Bounds] ignored reason=programmatic_source "
                f"source={source} x={bounds['x']} y={bounds['y']} "
                f"width={bounds['width']} height={bounds['height']}"
            )
            self._discard_suppressed_desktop_bounds(bounds)
            return
        if source == "reset":
            self.log_detailed(
                "[DesktopOverlay][Bounds] reset_requested "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            await self._handle_desktop_overlay_reset_requested(bounds=bounds)
            return
        if source == "user" and interaction_mode != DESKTOP_INTERACTION_MODE_EDIT:
            self.log_detailed(
                "[DesktopOverlay][Bounds] ignored reason=locked_interaction_mode "
                f"interaction_mode={interaction_mode} x={bounds['x']} y={bounds['y']} "
                f"width={bounds['width']} height={bounds['height']}"
            )
            return
        if self._consume_suppressed_desktop_bounds(bounds):
            self.log_detailed(
                "[DesktopOverlay][Bounds] ignored reason=suppressed_signature "
                f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
                f"height={bounds['height']}"
            )
            return
        self._schedule_desktop_bounds_persistence(bounds)
        self.log_detailed(
            "[DesktopOverlay][Bounds] scheduled_persist "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']}"
        )

    def _schedule_desktop_bounds_persistence(
        self,
        bounds: dict[str, int | float],
    ) -> None:
        self._pending_desktop_bounds = dict(bounds)
        task = self._desktop_bounds_persist_task
        if task is not None and not task.done():
            task.cancel()
        self._desktop_bounds_persist_task = asyncio.create_task(
            self._persist_desktop_bounds_after_debounce()
        )

    async def _persist_desktop_bounds_after_debounce(self) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(DESKTOP_BOUNDS_PERSIST_DEBOUNCE_S)
            bounds = self._pending_desktop_bounds
            self._pending_desktop_bounds = None
            if bounds is None:
                return
            self._persist_desktop_bounds(bounds)
        except asyncio.CancelledError:
            raise
        finally:
            if self._desktop_bounds_persist_task is current_task:
                self._desktop_bounds_persist_task = None

    def _persist_desktop_bounds(self, bounds: dict[str, int | float]) -> None:
        if self.settings is None or self._active_overlay_target != OVERLAY_TARGET_DESKTOP:
            return
        if self._desktop_bounds_from_payload({"event": "window_bounds_changed", **bounds}) is None:
            return
        desktop_settings = self.settings.overlay.desktop_flet
        desktop_settings.position.x = bounds["x"]
        desktop_settings.position.y = bounds["y"]
        desktop_settings.position.validate()
        self.log_detailed(
            "[DesktopOverlay][Bounds] persisted "
            f"x={bounds['x']} y={bounds['y']} width={bounds['width']} "
            f"height={bounds['height']} size_preset={desktop_settings.size_preset}"
        )
        self._save_settings()

    async def _handle_desktop_overlay_reset_requested(
        self,
        *,
        bounds: dict[str, int | float] | None = None,
    ) -> None:
        if self.settings is None:
            return
        configured_for_desktop = (
            self._overlay_target_for_settings(self.settings) == OVERLAY_TARGET_DESKTOP
        )
        desktop_renderer_active = bool(
            self._active_overlay_target == OVERLAY_TARGET_DESKTOP
            and self._overlay_bridge is not None
        )
        if not configured_for_desktop and not desktop_renderer_active:
            return
        await self._cancel_desktop_bounds_persistence()
        self._drain_pending_desktop_user_bounds_events()
        _ = bounds
        desktop_settings = self.settings.overlay.desktop_flet
        desktop_settings.position.x = None
        desktop_settings.position.y = None
        desktop_settings.locked = False
        desktop_settings.validate()
        self._set_desktop_overlay_interaction_mode(DESKTOP_INTERACTION_MODE_EDIT)
        self._save_settings()
        if not desktop_renderer_active:
            return
        await self._broadcast_desktop_runtime_control(
            {
                "command": "set_interaction_mode",
                "mode": DESKTOP_INTERACTION_MODE_EDIT,
            }
        )
        await self._broadcast_desktop_window_bounds_control(
            self._desktop_center_bounds_for_current_preset()
        )

    def _desktop_center_bounds_for_current_preset(self) -> dict[str, int | float]:
        assert self.settings is not None
        width, height = self._desktop_dimensions_for_size_preset(
            self.settings.overlay.desktop_flet.size_preset
        )
        return self._desktop_centered_bounds_for_dimensions(width=width, height=height)

    def _desktop_work_area_for_current_launch(
        self,
    ) -> tuple[int | float, int | float, int | float, int | float] | None:
        _ = self
        if sys.platform != "win32":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            # SPI_GETWORKAREA returns the primary monitor work area excluding taskbars.
            if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return None
            return (
                rect.left,
                rect.top,
                rect.right - rect.left,
                rect.bottom - rect.top,
            )
        except Exception:
            return None

    async def _cancel_desktop_renderer_event_task(self) -> None:
        current_task = asyncio.current_task()
        task = self._desktop_renderer_events_task
        self._desktop_renderer_events_task = None
        self._desktop_renderer_events = None
        if task is not None and task is not current_task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _cancel_desktop_bounds_persistence(self) -> None:
        current_task = asyncio.current_task()
        task = self._desktop_bounds_persist_task
        self._desktop_bounds_persist_task = None
        self._pending_desktop_bounds = None
        if task is not None and task is not current_task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _discard_pending_desktop_bounds_persistence(self) -> None:
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        task = self._desktop_bounds_persist_task
        self._desktop_bounds_persist_task = None
        self._pending_desktop_bounds = None
        if task is not None and task is not current_task and not task.done():
            task.cancel()

    def _desktop_runtime_is_running_for_settings_update(
        self,
        settings: AppSettings,
    ) -> bool:
        return bool(
            settings.ui.overlay_enabled
            and self._active_overlay_target == OVERLAY_TARGET_DESKTOP
            and self._overlay_bridge is not None
        )

    def _desktop_center_preserving_bounds_for_size_preset_change(
        self,
        *,
        previous_desktop_settings: object,
        next_size_preset: object,
    ) -> dict[str, int | float]:
        previous_bounds = self._desktop_launch_bounds_for_current_launch(previous_desktop_settings)
        next_width, next_height = self._desktop_dimensions_for_size_preset(next_size_preset)
        old_center_x = previous_bounds["x"] + (previous_bounds["width"] / 2)
        old_center_y = previous_bounds["y"] + (previous_bounds["height"] / 2)
        return {
            "x": old_center_x - (next_width / 2),
            "y": old_center_y - (next_height / 2),
            "width": next_width,
            "height": next_height,
        }

    def _prepare_desktop_runtime_settings_update(
        self,
        previous_settings: AppSettings | None,
        next_settings: AppSettings,
    ) -> list[dict[str, object]]:
        if previous_settings is None:
            return []
        previous_desktop = copy.deepcopy(previous_settings.overlay.desktop_flet)
        previous_desktop.validate()
        next_desktop = next_settings.overlay.desktop_flet
        next_desktop.validate()

        if not self._desktop_runtime_is_running_for_settings_update(next_settings):
            return []

        controls: list[dict[str, object]] = []
        if previous_desktop.size_preset != next_desktop.size_preset:
            self._discard_pending_desktop_bounds_persistence()
            self._drain_pending_desktop_user_bounds_events()
            bounds = self._desktop_center_preserving_bounds_for_size_preset_change(
                previous_desktop_settings=previous_desktop,
                next_size_preset=next_desktop.size_preset,
            )
            if previous_desktop.position.x is not None and previous_desktop.position.y is not None:
                next_desktop.position.x = bounds["x"]
                next_desktop.position.y = bounds["y"]
                next_desktop.position.validate()
            controls.append({"command": "apply_window_bounds", **bounds})

        previous_visual = previous_desktop.visual
        next_visual = next_desktop.visual
        if (
            previous_visual.text_scale != next_visual.text_scale
            or previous_visual.background_alpha != next_visual.background_alpha
            or previous_visual.outline_width != next_visual.outline_width
        ):
            controls.append(
                {
                    "command": "apply_visual_config",
                    "text_scale": next_visual.text_scale,
                    "background_alpha": next_visual.background_alpha,
                    "outline_width": next_visual.outline_width,
                }
            )
        return controls

    def _sync_desktop_overlay_interaction_mode_from_settings(
        self,
        settings: AppSettings,
    ) -> None:
        if self._overlay_target_for_settings(settings) != OVERLAY_TARGET_DESKTOP:
            return
        if (
            self._active_overlay_target == OVERLAY_TARGET_DESKTOP
            and self._overlay_bridge is not None
        ):
            return
        self._set_desktop_overlay_interaction_mode(DESKTOP_INTERACTION_MODE_EDIT)

    async def _broadcast_desktop_runtime_control_payloads(
        self,
        payloads: list[dict[str, object]],
    ) -> None:
        for payload in payloads:
            if payload.get("command") == "apply_window_bounds":
                bounds = self._desktop_bounds_from_payload(payload)
                if bounds is not None:
                    await self._broadcast_desktop_window_bounds_control(bounds)
                continue
            await self._broadcast_desktop_runtime_control(payload)

    async def _emit_overlay_runtime_logging_mode_update(self) -> None:
        bridge = self._overlay_bridge
        if bridge is None:
            return
        await bridge.broadcast_runtime_control(logging_mode=self.runtime_logging_mode)

    def _schedule_overlay_runtime_logging_mode_update(self) -> None:
        bridge = self._overlay_bridge
        if bridge is None:
            return

        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):
            try:
                run_task(self._emit_overlay_runtime_logging_mode_update)
                return
            except Exception as exc:
                self.log_detailed(
                    "[Overlay] Failed to schedule logging mode update via page.run_task",
                    level=logging.WARNING,
                    exception=exc,
                )
                return

        try:
            asyncio.get_running_loop().create_task(self._emit_overlay_runtime_logging_mode_update())
        except RuntimeError:
            self.log_detailed(
                "[Overlay] Skipping logging mode update; no running loop and page.run_task unavailable",
                level=logging.WARNING,
            )
