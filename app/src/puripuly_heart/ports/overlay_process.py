from __future__ import annotations

from pathlib import Path
from typing import Protocol

from puripuly_heart.domain.overlay_types import OverlayLaunchManifest


class OverlayManagedProcess(Protocol):
    async def next_event(self) -> dict[str, object]: ...
    async def wait(self) -> int | None: ...
    async def terminate(self) -> None: ...
    def set_logging_mode(self, mode: str) -> None: ...


class OverlayProcessRunner(Protocol):
    def prepare(self, manifest: OverlayLaunchManifest) -> Path: ...
    async def spawn(
        self,
        executable_path: Path,
        manifest_path: Path,
    ) -> OverlayManagedProcess: ...
