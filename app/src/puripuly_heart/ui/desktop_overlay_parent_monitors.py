from __future__ import annotations

# AI-REFACTORING: Parent process exit detection — cascading fallback strategy.
# Used by DesktopOverlayRenderer to terminate the overlay when the parent dies.
#
# Selection order (create_parent_monitor):
#   Windows: OpenProcess(SYNCHRONIZE) → WaitForSingleObject (preferred)
#            ↓ handle open fails
#            BridgeDisconnectParentMonitor (passive, waits for websocket disconnect)
#   POSIX:   PollingParentMonitor (os.kill(pid, 0) every 1s)
#
# Why not just poll on Windows? os.kill(pid, 0) on Windows uses OpenProcess
# with PROCESS_TERMINATE — if the monitor lacks rights, it can accidentally
# signal or misreport. WaitForSingleObject with SYNCHRONIZE right is the
# correct kernel-level "is this process alive?" primitive.

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

    # AI-REFACTORING: Error handling policy — conservative (assume alive).
    # ProcessLookupError → PID gone → return False (correct)
    # PermissionError → process exists but we can't signal it → return True
    # OSError (catch-all) → unknown edge case → return True (safe default)
    # Note: on POSIX, os.kill(pid, 0) returns True for zombie processes.
    # Zombie detection would require /proc/<pid>/status parsing — not worth
    # the complexity for this use case.

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


# AI-REFACTORING: This monitor does NO PID probing — intentional.
# On Windows, os.kill(pid, 0) can trigger TerminateProcess if the caller
# lacks SYNCHRONIZE rights (edge case with restricted security tokens).
# Instead, this monitor relies on the bridge websocket disconnecting,
# which the renderer's _bridge_reader_loop detects and sets _shutdown_event.
# Trade-off: if the bridge never connects, this monitor hangs forever.
# This is acceptable because the overlay is non-functional without the bridge.

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


# AI-REFACTORING: WaitForSingleObject with SYNCHRONIZE right (0x00100000).
# This is the correct Win32 API for "wait until process exits".
# The handle remains valid after process exit (kernel transitions it to
# signaled state). Non-blocking poll (dwMilliseconds=0) checked every 250ms.
# CloseHandle in finally block ensures cleanup even under task cancellation.
# The _closed bool flag prevents double-close (safe under Python GIL).

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


# AI-REFACTORING: Injectable dependencies for testing.
# is_windows: override OS detection without monkey-patching os.name
# open_windows_handle: inject mock handle opener for unit tests
# Both have sensible defaults for production use.

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
