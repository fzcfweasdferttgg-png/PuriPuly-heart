"""Translation enable wiring.

Composes the translation enable/mutation owner. The managed-account SaaS hooks
that used to feed this owner are gone; the managed Gemma (local author-model
runtime) keeps its warmup/teardown paths.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.app.services.canonical_settings_persistence import SettingsOwner
from puripuly_heart.app.services.managed_gemma_translation import (
    ManagedGemmaTranslationOwner,
)
from puripuly_heart.app.services.settings.settings_transaction_result import (
    SettingsTransactionResultOwner,
)
from puripuly_heart.app.services.translation_enable import (
    ManagedTranslationPreparation,
    TranslationEnableOwner,
    TranslationEnableState,
)
from puripuly_heart.config.translation_values import (
    MANAGED_GEMMA_MODELS,
    provider_llm_for_translation,
)
from puripuly_heart.core.orchestrator.configuration import (
    TranslationRuntimeConfigurationPort,
)

from .wiring_managed_gemma import managed_gemma_selection
from .wiring_translation_runtime_configuration import (
    replace_translation_runtime_enabled,
)


class ManagedTranslationContextPort(Protocol):
    def clear_context(self) -> None: ...


class ManagedLlmRuntimePort(Protocol):
    provider: object | None


@dataclass(slots=True)
class ManagedTranslationRuntimeAccess:
    llm_runtime_provider: Callable[[], ManagedLlmRuntimePort | None]
    context_provider: Callable[[], ManagedTranslationContextPort | None]
    translation_runtime_configuration_provider: Callable[
        [],
        TranslationRuntimeConfigurationPort | None,
    ]
    rebuild_llm: Callable[[], Awaitable[object]]

    def presence(self) -> tuple[bool, bool]:
        runtime = self.llm_runtime_provider()
        return runtime is not None, runtime is not None and runtime.provider is not None

    def snapshot(self) -> tuple[bool, bool, object | None]:
        runtime = self.llm_runtime_provider()
        config_owner = self.translation_runtime_configuration_provider()
        translation_enabled = (
            config_owner.snapshot().value.translation_enabled if config_owner is not None else False
        )
        return (
            runtime is not None,
            translation_enabled,
            runtime.provider if runtime is not None else None,
        )

    async def ensure(self, mode: str) -> bool:
        runtime = self.llm_runtime_provider()
        if runtime is None:
            return False
        if mode == "always" or (mode == "if_missing" and runtime.provider is None):
            await self.rebuild_llm()
        return runtime.provider is not None

    def set_enabled(self, enabled: bool) -> None:
        config_owner = self.translation_runtime_configuration_provider()
        if config_owner is not None:
            replace_translation_runtime_enabled(config_owner, enabled)

    def clear_context(self) -> None:
        context = self.context_provider()
        if context is not None:
            context.clear_context()


def _set_runtime_enabled(
    runtime: ManagedTranslationRuntimeAccess,
    enabled: bool,
    state_changed: Callable[[], None] | None,
) -> None:
    runtime.set_enabled(enabled)
    if state_changed is not None:
        state_changed()


def compose_translation_enable(
    *,
    settings: SettingsOwner,
    runtime: ManagedTranslationRuntimeAccess,
    ingress_provider: Callable[[], bool],
    dashboard_sink: Callable[[bool], None],
    message_sink: Callable[[str, Mapping[str, object]], None],
    results: SettingsTransactionResultOwner,
    log_basic: Callable[[str], None],
    log_diagnostic: Callable[[str], None],
    log_error: Callable[[str], None],
    starting_sink: Callable[[bool], None] | None = None,
    runtime_state_changed: Callable[[], None] | None = None,
    managed_gemma: ManagedGemmaTranslationOwner | None = None,
    sync_local_translation_demand: Callable[[], Awaitable[None]] | None = None,
) -> TranslationEnableOwner:
    def translation_state() -> TranslationEnableState:
        llm_present, translation_enabled, provider = runtime.snapshot()
        current = settings.canonical
        model = current.intent.translation.model if current is not None else ""
        connection = current.intent.translation.connection if current is not None else ""
        return TranslationEnableState(
            runtime_available=llm_present,
            translation_enabled=translation_enabled,
            llm_available=provider is not None,
            settings_available=current is not None,
            provider_name=provider_llm_for_translation(model, connection),
            qwen_region=(
                current.intent.translation.qwen.region or None
                if current is not None
                else None
            ),
            managed_selected=False,
            managed_china=False,
            managed_local_key_available=False,
            managed_release_service_available=False,
            ingress_frozen=bool(ingress_provider()),
        )

    async def translation_prepare(
        _allow_authorization: bool,
    ) -> ManagedTranslationPreparation:
        return ManagedTranslationPreparation(
            ready=False,
            message_key="settings.managed_translation_unavailable",
        )

    async def warmup_translation() -> None:
        current = settings.canonical
        if (
            current is not None
            and current.intent.translation.model in MANAGED_GEMMA_MODELS
            and managed_gemma is not None
        ):
            if sync_local_translation_demand is not None:
                await sync_local_translation_demand()
            else:
                await managed_gemma.prepare(managed_gemma_selection(current))
            return
        await runtime.ensure("if_missing")

    async def teardown_translation() -> None:
        if sync_local_translation_demand is not None:
            await sync_local_translation_demand()
            return
        if managed_gemma is not None:
            await managed_gemma.deactivate(linger=True)

    async def founder_route() -> bool:
        return False

    async def usage_refresh_now() -> None:
        return None

    return TranslationEnableOwner(
        state_provider=translation_state,
        managed_prepare=translation_prepare,
        founder_route=founder_route,
        pending_sink=lambda _pending: None,
        runtime_ensurer=runtime.ensure,
        usage_refresh_sink=lambda: None,
        usage_refresh_now=usage_refresh_now,
        runtime_sink=lambda enabled: _set_runtime_enabled(
            runtime,
            enabled,
            runtime_state_changed,
        ),
        dashboard_sink=dashboard_sink,
        starting_sink=starting_sink,
        mutation_settled=runtime_state_changed,
        clear_context=runtime.clear_context,
        warmup=warmup_translation,
        message_sink=message_sink,
        qq_dialog_sink=lambda: None,
        result_sink=results.set,
        log_basic=log_basic,
        log_diagnostic=log_diagnostic,
        log_error=log_error,
        founder_letter_sink=lambda: None,
        teardown=teardown_translation,
    )


__all__ = [
    "ManagedLlmRuntimePort",
    "ManagedTranslationContextPort",
    "ManagedTranslationRuntimeAccess",
    "compose_translation_enable",
]
