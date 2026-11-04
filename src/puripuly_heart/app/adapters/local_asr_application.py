from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.app.ports.self_capture_admission import (
    SelfCaptureAdmissionEffect,
    SelfCaptureAdmissionEffectType,
    SelfCaptureAdmissionState,
)
from puripuly_heart.app.services.local_asr.local_asr_readiness import (
    LocalASRReadinessState,
)
from puripuly_heart.app.services.local_asr.local_asr_selection import (
    local_asr_status_for_provider,
    required_local_asr_model_ids,
)
from puripuly_heart.app.services.peer_application import PeerApplicationOwner
from puripuly_heart.core.local_asr.local_asr_provisioning import LocalASRProvisioningSnapshot
from puripuly_heart.core.runtime.self_capture import SelfCaptureSessionOwner
from puripuly_heart.core.self_capture import (
    SelfCaptureSessionConfig,
    SelfCaptureSessionSnapshot,
    SelfCaptureSessionState,
)


class LocalASRNoticeSink(Protocol):
    def __call__(
        self,
        *,
        status: str | None,
        model_id: str | None,
        percent: int | None,
        starting: bool,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class LocalASRApplicationSettings:
    locale: str
    self_provider: str
    peer_provider: str
    self_source_language: str
    peer_source_language: str
    self_gpu_provider: bool
    peer_gpu_provider: bool
    peer_requested: bool
    peer_activation_requested: bool


@dataclass(frozen=True, slots=True)
class LocalASRApplicationStateAdapter:
    settings_provider: Callable[[], LocalASRApplicationSettings | None]
    runtime_available: Callable[[], bool]
    self_capture_provider: Callable[[], SelfCaptureSessionOwner | None]
    peer: Callable[[], PeerApplicationOwner]
    gpu_state_provider: Callable[[], str]
    provisioning_snapshot: Callable[[], LocalASRProvisioningSnapshot]

    def readiness(self) -> LocalASRReadinessState:
        settings = self.settings_provider()
        snapshot = self._self_snapshot()
        return LocalASRReadinessState(
            settings_available=settings is not None,
            runtime_available=self.runtime_available(),
            self_provider=settings.self_provider if settings is not None else None,
            peer_provider=settings.peer_provider if settings is not None else None,
            self_source_language=settings.self_source_language if settings is not None else "",
            peer_source_language=settings.peer_source_language if settings is not None else "",
            self_desired=bool(snapshot is not None and snapshot.desired_active),
            peer_requested=bool(settings is not None and settings.peer_requested),
            self_activation_generation=snapshot.generation if snapshot is not None else 0,
            peer_activation_generation=self.peer().activation_generation,
        )

    def self_admission(
        self,
        config: SelfCaptureSessionConfig,
    ) -> SelfCaptureAdmissionState:
        _ = config
        settings = self.settings_provider()
        snapshot = self._self_snapshot()
        return SelfCaptureAdmissionState(
            settings_available=settings is not None,
            runtime_available=self.runtime_available(),
            gpu_status=self.gpu_state_provider(),
            activation_generation=snapshot.generation if snapshot is not None else 0,
        )

    def _self_snapshot(self) -> SelfCaptureSessionSnapshot | None:
        owner = self.self_capture_provider()
        return owner.snapshot if owner is not None else None


@dataclass(frozen=True, slots=True)
class LocalASRApplicationEffectsAdapter:
    retain_gpu_pending: Callable[[str], None]

    def apply_self_admission(self, effect: SelfCaptureAdmissionEffect) -> None:
        if effect.type is SelfCaptureAdmissionEffectType.RETAIN_GPU_PENDING_INTENT:
            self.retain_gpu_pending("self")
            return
        raise ValueError(f"Unsupported Self capture admission effect: {effect.type}")


@dataclass(frozen=True, slots=True)
class LocalASRNoticeProjector:
    settings_provider: Callable[[], LocalASRApplicationSettings | None]
    self_capture_provider: Callable[[], SelfCaptureSessionOwner | None]
    peer: Callable[[], PeerApplicationOwner]
    provisioning_snapshot: Callable[[], LocalASRProvisioningSnapshot]
    sink: LocalASRNoticeSink

    def sync(self) -> None:
        settings = self.settings_provider()
        if settings is None:
            return
        owner = self.self_capture_provider()
        self_snapshot = owner.snapshot if owner is not None else None
        peer_provider = settings.peer_provider
        self_local_asr = settings.self_gpu_provider
        peer_local_asr = settings.peer_gpu_provider and settings.peer_activation_requested
        provisioning = self.provisioning_snapshot()
        status = local_asr_status_for_provider(
            provisioning,
            peer_provider,
        )
        visible_model_ids = required_local_asr_model_ids(peer_provider)
        activity = provisioning.activity_for("gpu")
        notice_model_id = (
            activity.model_id
            if activity is not None and activity.model_id in visible_model_ids
            else next(
                (
                    model_id
                    for model_id in visible_model_ids
                    if provisioning.state_for(model_id).status != "ready"
                ),
                None,
            )
        )
        self_starting = bool(
            self_snapshot is not None
            and self_snapshot.desired_active
            and self_snapshot.state
            in {
                SelfCaptureSessionState.STARTING,
                SelfCaptureSessionState.ADMISSION_PENDING,
            }
        )
        if self_starting and self_local_asr:
            status = "self_loading"
        elif (self.peer().activation_starting or self.peer().model_loading) and peer_local_asr:
            status = "peer_loading"
        elif (
            self_snapshot is not None
            and self_snapshot.state is SelfCaptureSessionState.FAULTED
            and self_local_asr
        ):
            status = "start_failed"
        should_show = status in {"self_loading", "peer_loading", "downloading"} or (
            (self_local_asr or peer_local_asr) and status != "ready"
        )
        with contextlib.suppress(Exception):
            self.sink(
                status=status if should_show else None,
                model_id=notice_model_id if should_show else None,
                percent=(
                    activity.progress_percent
                    if status == "downloading" and activity is not None
                    else None
                ),
                starting=self_starting,
            )


@dataclass(frozen=True, slots=True)
class LocalASRApplicationAdapters:
    state: LocalASRApplicationStateAdapter
    effects: LocalASRApplicationEffectsAdapter
    notice: LocalASRNoticeProjector


__all__ = [
    "LocalASRApplicationAdapters",
    "LocalASRApplicationEffectsAdapter",
    "LocalASRApplicationSettings",
    "LocalASRApplicationStateAdapter",
    "LocalASRNoticeProjector",
]
