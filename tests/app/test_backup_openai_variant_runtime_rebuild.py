"""Regression tests: runtime rebuild must track OpenAI-compatible and backup settings.

Reproduction for the user-reported symptom (errors when frequently switching
"Translation" / "Backup translation" settings): the LLM provider chain is only
rebuilt when `build_llm_provider_signature` changes, and the translation-provider
mutation surface (order21) only diffs its registered paths. Before the fix both
omitted `intent.openai_compatible.*` and `intent.backup_translation.*`, so those
settings were persisted but never applied to the running chain (UI/runtime
divergence: requests keep hitting the old endpoint, the backup chain stays
armed/disabled as before).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from puripuly_heart.app.services.provider.provider_runtime_apply import (
    LlmProviderRebuildContext,
    LlmProviderRebuildOwner,
    ProviderRuntimeApplyPlan,
    ProviderRuntimeOwner,
    ProviderRuntimeState,
)
from puripuly_heart.app.services.settings.settings_mutation_legacy import (
    build_translation_provider_settings_path_patch,
)
from puripuly_heart.app.wiring.wiring_llm_factory import (
    llm_factory_extras_from_vnext,
    runtime_resolution_input_from_vnext,
)
from puripuly_heart.app.wiring.wiring_provider_runtime_policy import (
    build_llm_provider_signature,
)
from puripuly_heart.app.wiring.wiring_translation_backend import create_translation_backend
from puripuly_heart.config.settings_vnext.schema import (
    AppSettingsVNext,
    BackupTranslationIntent,
    LocalLLMIntent,
    OpenAICompatibleIntent,
)
from puripuly_heart.core.http_extensions import HttpExtensionRegistry
from puripuly_heart.core.llm.fallback_chain import SequentialFallbackLLMProvider
from puripuly_heart.core.llm.provider import SemaphoreLLMProvider
from puripuly_heart.core.runtime.provider_handle import ProviderRuntimeHandle
from puripuly_heart.core.storage.secrets import InMemorySecretStore


def _settings(**intent_changes: object) -> AppSettingsVNext:
    settings = AppSettingsVNext()
    return replace(settings, intent=replace(settings.intent, **intent_changes))


def _openai_compatible_settings(
    *,
    base_url: str = "http://127.0.0.1:18081/v1",
    model: str = "model-a",
    provider_key: str = "",
) -> AppSettingsVNext:
    return _settings(
        translation=replace(
            AppSettingsVNext().intent.translation,
            model="openai_compatible",
            connection="openai_compatible",
        ),
        openai_compatible=OpenAICompatibleIntent(
            provider_key=provider_key,
            base_url=base_url,
            model=model,
        ),
    )


def _with_backup(settings: AppSettingsVNext, *, enabled: bool) -> AppSettingsVNext:
    return replace(
        settings,
        intent=replace(
            settings.intent,
            backup_translation=BackupTranslationIntent(
                enabled=enabled,
                model="openai_compatible",
                openai_compatible=OpenAICompatibleIntent(
                    base_url="http://127.0.0.1:18082/v1",
                    model="model-b",
                ),
                local_llm=LocalLLMIntent(base_url="", model=""),
            ),
        ),
    )


def _signature(settings: AppSettingsVNext) -> tuple[object, ...]:
    return build_llm_provider_signature(settings)


def test_llm_signature_tracks_openai_compatible_variant_endpoint() -> None:
    baseline = _openai_compatible_settings()

    changed_url = _openai_compatible_settings(base_url="http://127.0.0.1:19090/v1")
    changed_model = _openai_compatible_settings(model="model-next")
    changed_key = _openai_compatible_settings(provider_key="preset-x")

    assert _signature(baseline) != _signature(changed_url)
    assert _signature(baseline) != _signature(changed_model)
    assert _signature(baseline) != _signature(changed_key)
    assert _signature(changed_url) != _signature(changed_model)


def test_llm_signature_tracks_backup_translation_configuration() -> None:
    baseline = _openai_compatible_settings()
    off = _with_backup(baseline, enabled=False)
    on = _with_backup(baseline, enabled=True)

    assert _signature(off) != _signature(on)

    on_other_endpoint = replace(
        on,
        intent=replace(
            on.intent,
            backup_translation=replace(
                on.intent.backup_translation,
                openai_compatible=replace(
                    on.intent.backup_translation.openai_compatible,
                    base_url="http://127.0.0.1:19191/v1",
                ),
            ),
        ),
    )
    on_other_variant = replace(
        on,
        intent=replace(
            on.intent,
            backup_translation=replace(on.intent.backup_translation, model="local_llm"),
        ),
    )
    assert _signature(on) != _signature(on_other_endpoint)
    assert _signature(on) != _signature(on_other_variant)


def _runtime_owner(
    *,
    signature_cache: list[object | None],
    rebuild_calls: list[object],
    handle: ProviderRuntimeHandle,
    provider_factory,
) -> ProviderRuntimeOwner:
    async def rebuild_llm(current: object) -> bool:
        rebuild_calls.append(current)

        async def replace_provider(provider: object | None) -> object | None:
            return await handle.replace_provider(provider, start=False)

        context = LlmProviderRebuildContext(
            settings=current,
            replace_provider=replace_provider,
            requires_secret=False,
        )
        owner = LlmProviderRebuildOwner(
            context_provider=lambda: context,
            provider_factory=provider_factory,
            availability_sink=lambda _missing: None,
            usage_refresh=_noop_async,
            failure_sink=lambda _message: None,
            success_sink=lambda _message: None,
            signature_sink=lambda _settings: None,
            retry_sink=lambda: None,
        )
        return await owner.rebuild_with_context(context, propagate_failure=True)

    async def _refresh(_settings: object) -> None:
        return None

    def state_provider(_settings: object) -> ProviderRuntimeState:
        return ProviderRuntimeState(
            runtime_available=True,
            llm_available=True,
            self_stt_available=False,
            peer_stt_available=False,
            self_stt_desired=False,
            peer_stt_desired=False,
        )

    def sync_llm_signature(settings: object) -> None:
        signature_cache[2] = build_llm_provider_signature(settings)

    return ProviderRuntimeOwner(
        state_provider=state_provider,
        common_effect=lambda _settings: None,
        rebuild_llm=rebuild_llm,
        recover_gpu=lambda _s, _p: _refresh(_s),
        refresh_peer=_refresh,
        refresh_self_stt=_refresh,
        signature_sink=sync_llm_signature,
        current_settings_provider=lambda: None,
        signature_cache_provider=lambda: tuple(signature_cache),
        self_signature_builder=lambda _settings: ("self",),
        peer_signature_builder=lambda _settings, _canonical: ("peer",),
        llm_signature_builder=build_llm_provider_signature,
        gpu_restart_decision=lambda _current, _next: False,
    )


async def _noop_async() -> None:
    return None


def test_provider_runtime_plan_rebuilds_llm_when_backup_configuration_changes() -> None:
    handle = ProviderRuntimeHandle(name="llm-test")
    cache: list[object | None] = [None, None, None]
    rebuild_calls: list[object] = []
    off = _with_backup(_openai_compatible_settings(), enabled=False)
    on = _with_backup(_openai_compatible_settings(), enabled=True)

    owner = _runtime_owner(
        signature_cache=cache,
        rebuild_calls=rebuild_calls,
        handle=handle,
        provider_factory=lambda _settings: None,
    )
    cache[2] = build_llm_provider_signature(off)

    plan = owner.build_plan(on, force_rebuild_llm=False)

    assert plan.should_rebuild_llm is True


def test_provider_runtime_plan_rebuilds_llm_when_openai_compatible_endpoint_changes() -> None:
    handle = ProviderRuntimeHandle(name="llm-test")
    cache: list[object | None] = [None, None, None]
    before = _openai_compatible_settings(base_url="http://127.0.0.1:18081/v1")
    after = _openai_compatible_settings(base_url="http://127.0.0.1:19090/v1")

    owner = _runtime_owner(
        signature_cache=cache,
        rebuild_calls=[],
        handle=handle,
        provider_factory=lambda _settings: None,
    )
    cache[2] = build_llm_provider_signature(before)

    plan = owner.build_plan(after, force_rebuild_llm=False)

    assert plan.should_rebuild_llm is True


def test_translation_provider_patch_covers_backup_and_openai_compatible_paths() -> None:
    base = _with_backup(_openai_compatible_settings(), enabled=False)
    toggled = _with_backup(_openai_compatible_settings(), enabled=True)

    patch = build_translation_provider_settings_path_patch(base, toggled)

    assert patch.get("intent.backup_translation.enabled") is True

    endpoint_changed = _openai_compatible_settings(base_url="http://127.0.0.1:19090/v1")
    endpoint_patch = build_translation_provider_settings_path_patch(
        _openai_compatible_settings(), endpoint_changed
    )

    assert endpoint_patch.get("intent.openai_compatible.base_url") == (
        "http://127.0.0.1:19090/v1"
    )


@pytest.mark.asyncio
async def test_backup_toggle_apply_replaces_the_running_chain(tmp_path: Path) -> None:
    registry = HttpExtensionRegistry(tmp_path)
    registry.reload()
    handle = ProviderRuntimeHandle(name="llm-test")
    rebuild_calls: list[object] = []
    secrets = InMemorySecretStore()

    def provider_factory(settings: AppSettingsVNext):
        return create_translation_backend(
            translation_model=settings.intent.translation.model,
            secrets=secrets,
            http_extensions=registry,
            runtime_input=runtime_resolution_input_from_vnext(settings),
            extras=llm_factory_extras_from_vnext(settings),
            http_extension_id=settings.intent.translation.http_extension_id,
            concurrency_limit=settings.intent.translation.concurrency_limit,
        )

    off = _with_backup(_openai_compatible_settings(), enabled=False)
    on = _with_backup(_openai_compatible_settings(), enabled=True)

    owner = _runtime_owner(
        signature_cache=[None, None, None],
        rebuild_calls=rebuild_calls,
        handle=handle,
        provider_factory=provider_factory,
    )

    await owner.apply(off, ProviderRuntimeApplyPlan(True, False, False))
    chain_off = handle.provider
    assert isinstance(chain_off, object)
    provider_off = chain_off.provider
    assert isinstance(provider_off, SemaphoreLLMProvider)
    assert not isinstance(provider_off.inner, SequentialFallbackLLMProvider)

    plan = owner.build_plan(on, force_rebuild_llm=False)
    assert plan.should_rebuild_llm is True

    await owner.apply(on, plan)
    chain_on = handle.provider
    provider_on = chain_on.provider
    assert isinstance(provider_on, SemaphoreLLMProvider)
    assert isinstance(provider_on.inner, SequentialFallbackLLMProvider)

    await handle.close()
