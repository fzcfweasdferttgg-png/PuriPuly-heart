from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class LocalASRReadinessState:
    settings_available: bool
    runtime_available: bool
    self_provider: str | None
    peer_provider: str | None
    self_source_language: str
    peer_source_language: str
    self_desired: bool
    peer_requested: bool
    self_activation_generation: int
    peer_activation_generation: int


LocalASRReadinessStateProvider = Callable[[], LocalASRReadinessState]
LocalASRReadinessGpuValidator = Callable[[], Awaitable[bool]]
LocalASRReadinessGpuStateProvider = Callable[[], str | None]
LocalASRReadinessChannel = Literal["self", "peer"]
LocalASRReadinessGpuPendingSink = Callable[[LocalASRReadinessChannel], None]

_GPU_PENDING_STATUSES = frozenset(
    {
        "not_installed",
        "invalid",
        "install_failed",
        "installing",
    }
)


@dataclass(slots=True)
class LocalASRReadinessOwner:
    state_provider: LocalASRReadinessStateProvider = field(repr=False)
    validate_gpu_activation: LocalASRReadinessGpuValidator = field(repr=False)
    gpu_state_provider: LocalASRReadinessGpuStateProvider = field(repr=False)
    retain_gpu_pending: LocalASRReadinessGpuPendingSink = field(repr=False)

    @property
    def owner_name(self) -> str:
        return "LocalASRReadinessOwner"

    async def ensure_self_ready(
        self,
        *,
        activation_generation: int | None = None,
    ) -> bool:
        return self._self_activation_is_current(activation_generation)

    async def ensure_peer_ready(
        self,
        *,
        activation_generation: int | None = None,
        gpu_provider_id: str,
    ) -> bool:
        if not self._peer_activation_is_current(activation_generation):
            return False
        state = self.state_provider()
        if state.peer_provider == gpu_provider_id:
            ready = await self.validate_gpu_activation()
            if not ready and self.gpu_state_provider() in _GPU_PENDING_STATUSES:
                self.retain_gpu_pending("peer")
            return ready
        if not state.settings_available or not state.peer_requested:
            return True
        return state.peer_provider is not None

    def _self_activation_is_current(self, generation: int | None) -> bool:
        state = self.state_provider()
        return generation is None or (
            generation == state.self_activation_generation and state.self_desired
        )

    def _peer_activation_is_current(self, generation: int | None) -> bool:
        state = self.state_provider()
        return generation is None or (
            generation == state.peer_activation_generation and state.peer_requested
        )


__all__ = [
    "LocalASRReadinessChannel",
    "LocalASRReadinessGpuPendingSink",
    "LocalASRReadinessGpuStateProvider",
    "LocalASRReadinessGpuValidator",
    "LocalASRReadinessOwner",
    "LocalASRReadinessState",
    "LocalASRReadinessStateProvider",
]
