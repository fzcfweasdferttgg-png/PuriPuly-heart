from __future__ import annotations

import asyncio
from typing import Protocol

from puripuly_heart.domain.overlay_types import OverlayPresentationSnapshot


class ClipboardWatcherRuntime(Protocol):
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class LifecycleSink(Protocol):
    async def emit(self, event: dict[str, object]) -> None: ...


class RendererWindow(Protocol):
    async def start(self, initial_snapshot: OverlayPresentationSnapshot) -> None: ...
    async def run_until_closed(self) -> None: ...
    async def close(self) -> None: ...
    async def dispatch_snapshot(self, snapshot: OverlayPresentationSnapshot) -> None: ...
    async def dispatch_runtime_control(self, payload: dict[str, object]) -> None: ...


class ParentMonitor(Protocol):
    async def wait_for_parent_exit(self, stop_event: asyncio.Event) -> None: ...
