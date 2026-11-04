from __future__ import annotations

from dataclasses import dataclass

from puripuly_heart.core.local_asr.local_asr_provisioning import LocalASRProvisioningSnapshot


@dataclass(frozen=True, slots=True)
class LocalASRSelectionDecision:
    requested_provider: str
    effective_provider: str
    source_language: str
    model_id: str | None
    supported: bool


def required_local_asr_model_ids(provider: str) -> tuple[str, ...]:
    _ = provider
    return ()


def local_asr_status_for_provider(
    snapshot: LocalASRProvisioningSnapshot,
    provider: str,
) -> str:
    model_ids = required_local_asr_model_ids(provider)
    return snapshot.status_for(model_ids) if model_ids else "ready"


def resolve_local_asr_selection(
    provider: str,
    source_language: str,
) -> LocalASRSelectionDecision:
    return LocalASRSelectionDecision(
        requested_provider=provider,
        effective_provider=provider,
        source_language=source_language,
        model_id=None,
        supported=True,
    )
