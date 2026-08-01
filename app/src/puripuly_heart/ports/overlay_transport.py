from __future__ import annotations

import logging
from typing import Protocol

from puripuly_heart.domain.overlay_types import OverlayPresentationSnapshot


class OverlayPresentationTransport(Protocol):
    async def replace_snapshot(self, snapshot: OverlayPresentationSnapshot) -> None: ...

    async def broadcast_shutdown(self) -> None: ...


class RuntimeDetailedLogger(Protocol):
    def __call__(self, message: str, *, level: int = logging.INFO) -> bool: ...
