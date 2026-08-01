from __future__ import annotations

from typing import Protocol

from puripuly_heart.domain.peer_types import PeerChannelRuntimeState, PeerRuntimeConfig


class SpeechChannelRuntime(Protocol):
    @property
    def state(self) -> PeerChannelRuntimeState: ...

    @property
    def current_signature(self) -> object | None: ...

    async def apply_policy(self, *, config: PeerRuntimeConfig, desired_active: bool) -> None: ...
    async def warmup(self) -> None: ...
    async def close(self) -> None: ...


__all__ = [
    "PeerChannelRuntimeState",
    "PeerRuntimeConfig",
    "SpeechChannelRuntime",
]