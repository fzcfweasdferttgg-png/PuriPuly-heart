from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from puripuly_heart.ports.ui import ParentMonitor

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PollingParentMonitor:
    parent_pid: int
    poll_interval_s: float = 1.0

    async def wait_for_parent_exit(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            if not self._pid_exists(self.parent_pid):
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.poll_interval_s)
            except TimeoutError:
                continue

    @staticmethod
    def _pid_exists(parent_pid: int) -> bool:
        if parent_pid <= 0:
            return False
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        return True


@dataclass(slots=True)
class BridgeDisconnectParentMonitor:
    """Windows-safe fallback when no parent handle can be opened.

    The bridge connection is owned by the parent process; if the parent exits, the
    bridge reader reports the disconnect. This monitor intentionally performs no
    PID probing so Windows fallback cannot signal or terminate the parent.
    """

    parent_pid: int

    async def wait_for_parent_exit(self, stop_event: asyncio.Event) -> None:
        _ = self.parent_pid
        await stop_event.wait()


@dataclass(slots=True)
class WindowsParentHandleMonitor:
    handle: object
    poll_interval_s: float = 0.25
    wait_handle_signaled: Callable[[object], bool] | None = None
    close_handle: Callable[[object], None] | None = None
    _closed: bool = field(init=False, default=False)

    async def wait_for_parent_exit(self, stop_event: asyncio.Event) -> None:
        wait_handle_signaled = self.wait_handle_signaled or _default_windows_handle_signaled
        try:
            while not stop_event.is_set():
                if await asyncio.to_thread(wait_handle_signaled, self.handle):
                    return
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self.poll_interval_s)
                except TimeoutError:
                    continue
        finally:
            await asyncio.to_thread(self.close)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close_handle = self.close_handle or _default_close_windows_handle
        close_handle(self.handle)


def _default_open_windows_parent_handle(parent_pid: int) -> object | None:
    if os.name != "nt" or parent_pid <= 0:
        return None
    try:
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(parent_pid))
    except Exception:
        return None
    if not handle:
        return None
    return int(handle)


def _default_windows_handle_signaled(handle: object) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        wait_object_0 = 0x00000000
        result = ctypes.windll.kernel32.WaitForSingleObject(int(handle), 0)
    except Exception:
        return False
    return result == wait_object_0


def _default_close_windows_handle(handle: object) -> None:
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        import ctypes

        ctypes.windll.kernel32.CloseHandle(int(handle))


def create_parent_monitor(
    parent_pid: int,
    *,
    is_windows: bool | None = None,
    open_windows_handle: Callable[[int], object | None] | None = None,
) -> ParentMonitor:
    windows = os.name == "nt" if is_windows is None else is_windows
    if windows:
        opener = open_windows_handle or _default_open_windows_parent_handle
        handle = opener(parent_pid)
        if handle is not None:
            return WindowsParentHandleMonitor(handle=handle)
        logger.warning(
            "[DesktopOverlay] Unable to open parent process handle; "
            "relying on bridge disconnect for parent-loss detection"
        )
        return BridgeDisconnectParentMonitor(parent_pid=parent_pid)
    return PollingParentMonitor(parent_pid=parent_pid)
