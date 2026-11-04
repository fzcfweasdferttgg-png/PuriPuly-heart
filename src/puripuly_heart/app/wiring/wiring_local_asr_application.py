from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from puripuly_heart.app.adapters.local_asr_application import (
    LocalASRApplicationAdapters,
    LocalASRApplicationEffectsAdapter,
    LocalASRApplicationSettings,
    LocalASRApplicationStateAdapter,
    LocalASRNoticeProjector,
    LocalASRNoticeSink,
)
from puripuly_heart.app.services.local_asr.local_asr_readiness import LocalASRReadinessOwner
from puripuly_heart.app.services.peer_application import PeerApplicationOwner
from puripuly_heart.config.provider_values import STTProviderName
from puripuly_heart.core.local_asr.local_asr_provider_runtime import (
    LocalASRProviderRuntimePort,
)
from puripuly_heart.core.local_asr.local_asr_provisioning import LocalASRProvisioningPort
from puripuly_heart.core.runtime.self_capture import SelfCaptureSessionOwner

from .wiring_composition import (
    create_local_asr_readiness_owner,
)

LocalASRChannel = Literal["self", "peer"]


@dataclass(frozen=True, slots=True)
class LocalASRApplicationRuntime:
    adapters: LocalASRApplicationAdapters
    readiness: LocalASRReadinessOwner
    provisioning_provider: Callable[[], LocalASRProvisioningPort]

    async def ensure_self_ready(
        self,
        *,
        activation_generation: int | None = None,
    ) -> bool:
        return await self.readiness.ensure_self_ready(
            activation_generation=activation_generation,
        )

    async def ensure_peer_ready(
        self,
        *,
        activation_generation: int | None = None,
    ) -> bool:
        return await self.readiness.ensure_peer_ready(
            activation_generation=activation_generation,
            gpu_provider_id=STTProviderName.LOCAL_QWEN_GPU.value,
        )

    async def close(self) -> None:
        await self.provisioning_provider().close()


def compose_local_asr_application(
    *,
    settings_provider: Callable[[], LocalASRApplicationSettings | None],
    runtime_provider: Callable[[], LocalASRProviderRuntimePort | None],
    self_capture_provider: Callable[[], SelfCaptureSessionOwner | None],
    peer_provider: Callable[[], PeerApplicationOwner],
    provisioning_provider: Callable[[], LocalASRProvisioningPort],
    gpu_state_provider: Callable[[], str],
    retain_gpu_pending: Callable[[LocalASRChannel], None],
    validate_gpu_activation: Callable[[], Awaitable[bool]],
    notice_sink: LocalASRNoticeSink,
) -> LocalASRApplicationRuntime:
    state = LocalASRApplicationStateAdapter(
        settings_provider=settings_provider,
        runtime_available=lambda: runtime_provider() is not None,
        self_capture_provider=self_capture_provider,
        peer=peer_provider,
        gpu_state_provider=gpu_state_provider,
        provisioning_snapshot=lambda: provisioning_provider().snapshot,
    )
    notice = LocalASRNoticeProjector(
        settings_provider=settings_provider,
        self_capture_provider=self_capture_provider,
        peer=peer_provider,
        provisioning_snapshot=lambda: provisioning_provider().snapshot,
        sink=notice_sink,
    )
    effects = LocalASRApplicationEffectsAdapter(
        retain_gpu_pending=retain_gpu_pending,
    )
    readiness = create_local_asr_readiness_owner(
        state_provider=state.readiness,
        validate_gpu_activation=validate_gpu_activation,
        gpu_state_provider=gpu_state_provider,
        retain_gpu_pending=retain_gpu_pending,
    )
    return LocalASRApplicationRuntime(
        adapters=LocalASRApplicationAdapters(
            state=state,
            effects=effects,
            notice=notice,
        ),
        readiness=readiness,
        provisioning_provider=provisioning_provider,
    )


__all__ = [
    "LocalASRApplicationRuntime",
    "compose_local_asr_application",
]
