from __future__ import annotations

from typing import Protocol

from puripuly_heart.ports.vad import VadEvent


class STTProvider(Protocol):
    async def handle_vad_event(self, event: VadEvent) -> None: ...
    async def close(self) -> None: ...
    def events(self): ...
