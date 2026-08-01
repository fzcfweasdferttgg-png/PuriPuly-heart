from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from puripuly_heart.domain.peer_types import ResolvedPeerSTTConfig


class PeerChannelRuntimeState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAULTED = "faulted"


@dataclass(frozen=True, slots=True)
class PeerRuntimeConfig:
    backend: ResolvedPeerSTTConfig
    output_device: str
    vad_threshold: float
    vad_hangover_ms: int
    vad_pre_roll_ms: int
    provider_signature: tuple[object, ...]
    runtime_signature: tuple[object, ...]


class SpeechChannelRuntime(Protocol):
    @property
    def state(self) -> PeerChannelRuntimeState: ...

    @property
    def current_signature(self) -> object | None: ...

    async def apply_policy(self, *, config: PeerRuntimeConfig, desired_active: bool) -> None: ...
    async def warmup(self) -> None: ...
    async def close(self) -> None: ...
