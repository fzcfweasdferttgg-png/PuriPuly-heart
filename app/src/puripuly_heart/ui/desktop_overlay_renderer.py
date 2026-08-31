from __future__ import annotations

import argparse
import asyncio
import contextlib
from dataclasses import dataclass
import json
import logging
import re
import sys
import traceback
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from puripuly_heart.domain.overlay_types import (
    OVERLAY_CONTRACT_VERSION,
    OverlayLaunchManifest,
    OverlayPresentationSnapshot,
)
from puripuly_heart.ports.ui import LifecycleSink, ParentMonitor, RendererWindow
from puripuly_heart.ui.desktop_overlay_parent_monitors import create_parent_monitor
from puripuly_heart.ui.desktop_overlay_caption_plan import _DESKTOP_PREVIEW_SECRET_PATTERNS

logger = logging.getLogger(__name__)

_STARTUP_FAILURE_EXIT_CODE = 1
_RUNTIME_FAILURE_EXIT_CODE = 1
_SUCCESS_EXIT_CODE = 0
_LOOPBACK_BRIDGE_HOSTS = {"127.0.0.1", "::1"}
_SENSITIVE_EVENT_KEYS = {
    "accesstoken",
    "apikey",
    "authorization",
    "authorizationheader",
    "bearer",
    "secret",
    "sessiontoken",
    "token",
}
_REQUIRED_MANIFEST_STRING_FIELDS = {
    "app_version",
    "bridge_url",
    "locale",
    "log_dir",
    "log_level",
    "logging_mode",
    "overlay_instance_id",
    "session_token",
}
_REQUIRED_MANIFEST_INT_FIELDS = {"contract_version", "parent_pid", "startup_deadline_ms"}
_INITIAL_RUNTIME_CONTROL_DRAIN_TIMEOUT_S = 0.05


class DesktopOverlayStartupError(Exception):
    def __init__(self, failure_reason: str, message: str) -> None:
        super().__init__(message)
        self.failure_reason = failure_reason


@dataclass(frozen=True, slots=True)
class _RuntimeOutcome:
    exit_code: int


class StdoutLifecycleSink:
    async def emit(self, event: dict[str, object]) -> None:
        safe_event = _redact_event(event)
        if safe_event.get("type") == "overlay_event":
            return
        stream = (
            sys.stderr
            if safe_event.get("type") in {"startup_error", "runtime_error"}
            else sys.stdout
        )
        print(json.dumps(safe_event, sort_keys=True), file=stream, flush=True)


def validate_desktop_bridge_url(bridge_url: str) -> str:
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(bridge_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("desktop overlay bridge_url is invalid") from exc

    if parsed.scheme != "ws":
        raise ValueError("desktop overlay bridge_url must use ws")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("desktop overlay bridge_url must not include credentials")
    if parsed.hostname not in _LOOPBACK_BRIDGE_HOSTS:
        raise ValueError("desktop overlay bridge_url must be loopback-only")
    if port is None or port <= 0:
        raise ValueError("desktop overlay bridge_url must include a positive port")
    return bridge_url


def load_renderer_manifest(config_path: Path) -> OverlayLaunchManifest:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        ) from exc
    if not isinstance(payload, dict):
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        )

    try:
        _validate_manifest_payload_shape(payload)
        manifest = OverlayLaunchManifest.from_dict(payload)
        _validate_runtime_manifest(manifest)
    except DesktopOverlayStartupError:
        raise
    except Exception as exc:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        ) from exc
    return manifest


class DesktopOverlayRenderer:
    def __init__(
        self,
        manifest: OverlayLaunchManifest,
        *,
        window: RendererWindow | None = None,
        lifecycle_sink: LifecycleSink | None = None,
        parent_monitor: ParentMonitor | None = None,
    ) -> None:
        self.manifest = manifest
        self.lifecycle_sink = lifecycle_sink or StdoutLifecycleSink()
        if window is None:
            from puripuly_heart.ui.desktop_overlay import FletDesktopRendererWindow
            window = FletDesktopRendererWindow(
                event_sink=self._emit_lifecycle,
                locale=manifest.locale,
                logging_mode=manifest.logging_mode,
            )
        self.window = window
        self.parent_monitor = parent_monitor or create_parent_monitor(manifest.parent_pid)
        self._shutdown_event = asyncio.Event()
        self._shutdown_lock = asyncio.Lock()
        self._shutdown_complete = False
        self._websocket: Any | None = None
        self._tasks: set[asyncio.Task[_RuntimeOutcome | None]] = set()
        self._ui_queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        self._startup_pending_messages: asyncio.Queue[object] = asyncio.Queue()

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown_complete

    async def run(self) -> int:
        unexpected_startup_failure_reason = "renderer_init_failed"
        try:
            _validate_runtime_manifest(self.manifest)
            unexpected_startup_failure_reason = "bridge_auth_failed"
            websocket = await self._connect_bridge()
            self._websocket = websocket
            await websocket.send(
                json.dumps({"type": "auth", "session_token": self.manifest.session_token})
            )
            unexpected_startup_failure_reason = "renderer_init_failed"
            initial_snapshot, initial_runtime_controls = (
                await self._receive_initial_snapshot_and_runtime_controls(websocket)
            )
            unexpected_startup_failure_reason = "window_configuration_failed"
            prime_startup_runtime_controls = getattr(
                self.window,
                "prime_startup_runtime_controls",
                None,
            )
            startup_runtime_controls_to_dispatch = initial_runtime_controls
            if callable(prime_startup_runtime_controls):
                startup_runtime_controls_to_dispatch = prime_startup_runtime_controls(
                    initial_runtime_controls
                )
            await self.window.start(initial_snapshot)
            for payload in startup_runtime_controls_to_dispatch:
                await self.window.dispatch_runtime_control(payload)
            unexpected_startup_failure_reason = "renderer_init_failed"
            self._start_runtime_tasks(websocket)
            await self._emit_lifecycle({"type": "overlay_ready"})
            outcome = await self._wait_for_runtime_outcome()
            return outcome.exit_code
        except DesktopOverlayStartupError as exc:
            await self._emit_lifecycle(
                {"type": "startup_error", "failure_reason": exc.failure_reason}
            )
            return _STARTUP_FAILURE_EXIT_CODE
        except Exception as exc:
            safe_exception_message = _redact_renderer_startup_exception_text(
                str(exc),
                self.manifest,
            )
            safe_exception_traceback = _redact_renderer_startup_exception_text(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                self.manifest,
            )
            logger.warning(
                "[DesktopOverlay] Renderer startup failed: "
                "exception_type=%s exception_message=%s exception_traceback=%s",
                type(exc).__name__,
                safe_exception_message,
                safe_exception_traceback,
            )
            await self._emit_lifecycle(
                {"type": "startup_error", "failure_reason": unexpected_startup_failure_reason}
            )
            return _STARTUP_FAILURE_EXIT_CODE
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        async with self._shutdown_lock:
            if self._shutdown_complete:
                return
            self._shutdown_event.set()

            websocket = self._websocket
            self._websocket = None
            if websocket is not None:
                with contextlib.suppress(Exception):
                    await websocket.close()

            with contextlib.suppress(Exception):
                await self.window.destroy()

            current_task = asyncio.current_task()
            pending_tasks = [
                task for task in self._tasks if task is not current_task and not task.done()
            ]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
            await _close_parent_monitor(self.parent_monitor)
            self._shutdown_complete = True

    async def _connect_bridge(self) -> Any:
        timeout_s = max(0.1, self.manifest.startup_deadline_ms / 1000.0)
        try:
            return await asyncio.wait_for(
                websockets.connect(self.manifest.bridge_url, ping_interval=None),
                timeout=timeout_s,
            )
        except Exception as exc:
            raise DesktopOverlayStartupError(
                "bridge_auth_failed",
                "desktop overlay bridge authentication failed",
            ) from exc

    async def _receive_initial_snapshot_and_runtime_controls(
        self,
        websocket: Any,
    ) -> tuple[OverlayPresentationSnapshot, tuple[dict[str, object], ...]]:
        snapshot = await self._receive_initial_snapshot(websocket)
        runtime_controls = await self._drain_startup_runtime_controls(websocket)
        return snapshot, runtime_controls

    async def _receive_initial_snapshot(self, websocket: Any) -> OverlayPresentationSnapshot:
        timeout_s = max(0.1, self.manifest.startup_deadline_ms / 1000.0)
        try:
            raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout_s)
            message = _load_bridge_message(raw_message)
        except DesktopOverlayStartupError:
            raise
        except Exception as exc:
            raise DesktopOverlayStartupError(
                "renderer_init_failed",
                "desktop overlay initial snapshot is invalid",
            ) from exc

        message_type = message.get("type")
        if message_type == "auth_error":
            raise DesktopOverlayStartupError(
                "bridge_auth_failed",
                "desktop overlay bridge authentication failed",
            )
        if message_type != "snapshot":
            raise DesktopOverlayStartupError(
                "renderer_init_failed",
                "desktop overlay initial snapshot is invalid",
            )
        try:
            return _parse_snapshot_message(message)
        except Exception as exc:
            raise DesktopOverlayStartupError(
                "renderer_init_failed",
                "desktop overlay initial snapshot is invalid",
            ) from exc

    async def _drain_startup_runtime_controls(
        self,
        websocket: Any,
    ) -> tuple[dict[str, object], ...]:
        controls: list[dict[str, object]] = []
        deadline = asyncio.get_running_loop().time() + _INITIAL_RUNTIME_CONTROL_DRAIN_TIMEOUT_S
        while True:
            timeout_s = max(0.0, deadline - asyncio.get_running_loop().time())
            if timeout_s <= 0:
                break
            try:
                raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout_s)
            except TimeoutError:
                break
            except Exception as exc:
                logger.warning(
                    "[DesktopOverlay] Error receiving initial runtime control: %s",
                    exc,
                )
                break
            try:
                message = _load_bridge_message(raw_message)
            except ValueError:
                await self._startup_pending_messages.put(raw_message)
                continue
            if message.get("type") != "runtime_control":
                await self._startup_pending_messages.put(raw_message)
                continue
            payload = _parse_runtime_control_payload(message)
            if payload is None:
                raise DesktopOverlayStartupError(
                    "runtime_control_invalid",
                    "desktop overlay initial runtime control is invalid",
                )
            controls.append(payload)
        return tuple(controls)

    def _start_runtime_tasks(self, websocket: Any) -> None:
        self._tasks = {
            asyncio.create_task(self._bridge_reader_loop(websocket)),
            asyncio.create_task(self._parent_monitor_loop()),
            asyncio.create_task(self._window_loop()),
            asyncio.create_task(self._ui_update_loop()),
            asyncio.create_task(self._heartbeat_loop()),
        }

    async def _wait_for_runtime_outcome(self) -> _RuntimeOutcome:
        while self._tasks:
            done, _pending = await asyncio.wait(
                self._tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                self._tasks.discard(task)
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as exc:
                    logger.warning(
                        "[DesktopOverlay] Runtime task failed: exception_type=%s",
                        type(exc).__name__,
                    )
                    await self._emit_runtime_error("runtime_crashed")
                    return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)
                if isinstance(result, _RuntimeOutcome):
                    return result
            if self._shutdown_event.is_set():
                return _RuntimeOutcome(_SUCCESS_EXIT_CODE)
        return _RuntimeOutcome(_SUCCESS_EXIT_CODE)

    async def _bridge_reader_loop(self, websocket: Any) -> _RuntimeOutcome:
        try:
            while not self._startup_pending_messages.empty():
                raw_message = await self._startup_pending_messages.get()
                outcome = await self._handle_bridge_message(raw_message)
                if outcome is not None:
                    return outcome
            async for raw_message in websocket:
                outcome = await self._handle_bridge_message(raw_message)
                if outcome is not None:
                    return outcome
        except ConnectionClosed:
            if self._shutdown_event.is_set():
                return _RuntimeOutcome(_SUCCESS_EXIT_CODE)
            await self._emit_runtime_error("runtime_disconnected")
            return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)
        if self._shutdown_event.is_set():
            return _RuntimeOutcome(_SUCCESS_EXIT_CODE)
        await self._emit_runtime_error("runtime_disconnected")
        return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)

    async def _handle_bridge_message(self, raw_message: object) -> _RuntimeOutcome | None:
        try:
            message = _load_bridge_message(raw_message)
        except ValueError:
            logger.warning("[DesktopOverlay] Ignoring malformed bridge message")
            return None

        message_type = message.get("type")
        if message_type == "heartbeat":
            return None
        if message_type == "shutdown":
            return _RuntimeOutcome(_SUCCESS_EXIT_CODE)
        if message_type == "snapshot":
            try:
                snapshot = _parse_snapshot_message(message)
            except Exception:
                logger.warning("[DesktopOverlay] Ignoring malformed snapshot update")
                return None
            await self._ui_queue.put(("snapshot", snapshot))
            return None
        if message_type == "runtime_control":
            payload = _parse_runtime_control_payload(message)
            if payload is None:
                await self._emit_runtime_error("runtime_control_invalid")
                return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)
            await self._ui_queue.put(("runtime_control", payload))
            return None
        logger.warning(
            "[DesktopOverlay] Ignoring unsupported bridge message type: %r", message_type
        )
        return None

    async def _ui_update_loop(self) -> _RuntimeOutcome | None:
        while not self._shutdown_event.is_set():
            queue_task = asyncio.create_task(self._ui_queue.get())
            stop_task = asyncio.create_task(self._shutdown_event.wait())
            try:
                done, _pending = await asyncio.wait(
                    {queue_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    return None
                kind, payload = queue_task.result()
            finally:
                for task in (queue_task, stop_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(queue_task, stop_task, return_exceptions=True)

            try:
                if kind == "snapshot" and isinstance(payload, OverlayPresentationSnapshot):
                    await self.window.dispatch_snapshot(payload)
                elif kind == "runtime_control" and isinstance(payload, dict):
                    await self.window.dispatch_runtime_control(payload)
            except Exception:
                await self._emit_runtime_error("window_configuration_failed")
                return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)
        return None

    async def _parent_monitor_loop(self) -> _RuntimeOutcome | None:
        try:
            await self.parent_monitor.wait_for_parent_exit(self._shutdown_event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[DesktopOverlay] Parent monitor failed: exception_type=%s",
                type(exc).__name__,
            )
            return None
        if self._shutdown_event.is_set():
            return None
        await self._emit_runtime_error("runtime_disconnected")
        return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)

    async def _window_loop(self) -> _RuntimeOutcome | None:
        try:
            await self.window.run_until_closed()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._emit_runtime_error("window_configuration_failed")
            return _RuntimeOutcome(_RUNTIME_FAILURE_EXIT_CODE)
        if self._shutdown_event.is_set():
            return None
        return _RuntimeOutcome(_SUCCESS_EXIT_CODE)

    async def _heartbeat_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=1.0)
            except TimeoutError:
                continue

    async def _emit_runtime_error(self, failure_reason: str) -> None:
        await self._emit_lifecycle({"type": "runtime_error", "failure_reason": failure_reason})

    async def _emit_lifecycle(self, event: dict[str, object]) -> None:
        safe_event = _redact_event(event)
        websocket = self._websocket
        if websocket is not None:
            with contextlib.suppress(Exception):
                await websocket.send(json.dumps(safe_event))
        await self.lifecycle_sink.emit(safe_event)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="puripuly-heart desktop-overlay")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--config",
        type=Path,
        help="Path to overlay launch manifest JSON",
    )
    mode.add_argument(
        "--preview",
        action="store_true",
        help="Run a local desktop overlay preview",
    )
    return parser


def run_renderer(config_path: Path) -> int:
    return asyncio.run(_run_renderer_async(config_path))


async def _run_renderer_async(config_path: Path) -> int:
    sink = StdoutLifecycleSink()
    try:
        manifest = load_renderer_manifest(config_path)
    except DesktopOverlayStartupError as exc:
        await sink.emit({"type": "startup_error", "failure_reason": exc.failure_reason})
        return _STARTUP_FAILURE_EXIT_CODE
    renderer = DesktopOverlayRenderer(manifest, lifecycle_sink=sink)
    return await renderer.run()


def _validate_runtime_manifest(manifest: OverlayLaunchManifest) -> None:
    if manifest.contract_version != OVERLAY_CONTRACT_VERSION:
        raise DesktopOverlayStartupError(
            "contract_mismatch",
            "desktop overlay contract version is not supported",
        )
    try:
        validate_desktop_bridge_url(manifest.bridge_url)
    except ValueError as exc:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        ) from exc
    if not manifest.session_token:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        )
    if manifest.parent_pid <= 0 or manifest.startup_deadline_ms <= 0:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        )
    if not manifest.log_dir or not manifest.log_level or not manifest.locale:
        raise DesktopOverlayStartupError(
            "manifest_invalid",
            "desktop overlay launch manifest is invalid",
        )


def _validate_manifest_payload_shape(payload: dict[object, object]) -> None:
    for field_name in _REQUIRED_MANIFEST_STRING_FIELDS:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value:
            raise DesktopOverlayStartupError(
                "manifest_invalid",
                "desktop overlay launch manifest is invalid",
            )
    for field_name in _REQUIRED_MANIFEST_INT_FIELDS:
        value = payload.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool):
            raise DesktopOverlayStartupError(
                "manifest_invalid",
                "desktop overlay launch manifest is invalid",
            )


def _load_bridge_message(raw_message: object) -> dict[str, object]:
    if not isinstance(raw_message, str):
        raise ValueError("desktop overlay bridge message must be text JSON")
    payload = json.loads(raw_message)
    if not isinstance(payload, dict):
        raise ValueError("desktop overlay bridge message must decode to an object")
    return payload


def _parse_snapshot_message(message: dict[str, object]) -> OverlayPresentationSnapshot:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("desktop overlay snapshot payload must be an object")
    return OverlayPresentationSnapshot.from_dict(payload)


def _parse_runtime_control_payload(message: dict[str, object]) -> dict[str, object] | None:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None
    if "logging_mode" in payload:
        if set(payload) != {"logging_mode"} or not isinstance(payload.get("logging_mode"), str):
            return None
        return dict(payload)
    command = payload.get("command")
    if not isinstance(command, str) or not command:
        return None
    return dict(payload)


def _redact_renderer_startup_exception_text(
    text: str,
    manifest: OverlayLaunchManifest,
) -> str:
    redacted = text
    if manifest.session_token:
        redacted = redacted.replace(manifest.session_token, "<redacted>")
    for _, pattern in _DESKTOP_PREVIEW_SECRET_PATTERNS:
        redacted = pattern.sub("<redacted>", redacted)
    return redacted


def _redact_event(event: dict[str, object]) -> dict[str, object]:
    redacted = _redact_value(event)
    if isinstance(redacted, dict):
        return redacted
    return {"type": "runtime_error", "failure_reason": "unknown"}


def _redact_value(value: object) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_event_key(key_text):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _redact_value(item)
        return result
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _is_sensitive_event_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in _SENSITIVE_EVENT_KEYS


async def _close_parent_monitor(parent_monitor: ParentMonitor) -> None:
    close = getattr(parent_monitor, "close", None)
    if not callable(close):
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result
