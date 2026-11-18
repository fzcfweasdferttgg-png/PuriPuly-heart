"""Regression tests: unconfigured «Перевод» user variants are an expected state.

Reproduction for a residual of the user-reported symptom (errors when frequently
switching "Translation" / "Backup translation" settings): selecting the
OpenAI-compatible (or local server) translation variant while its endpoint form
is still empty made `create_translation_backend` raise a plain ValueError that
escaped `LlmProviderRebuildOwner.rebuild_with_context(..., propagate_failure=True)`
(the production wiring in `compose_provider_runtime`). The settings mutation
layer mapped it to `settings.mutation.runtime_apply_failed`, the active provider
was replaced with None, and the same error repeated on every apply and on every
restart (`apply_providers(force_rebuild_llm=True)`) until the form was filled.
The backup translation path already degraded gracefully for the same state.

After the fix the state follows the established "provider unavailable" handling
(same as a missing API key): no exception, provider contract unchanged
(`test_llm_creation_failure_replaces_previous_provider_with_none`), the
needs-key/configuration notice is raised, and a later apply with a configured
form rebuilds the chain.

Run (from work/agent-4-variant-apply):
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tree/src \
      /tmp/project/.venv/bin/python3 -m pytest tests/test_unconfigured_variant_apply.py \
      -p no:cacheprovider -o asyncio_mode=auto
"""

from __future__ import annotations

from pathlib import Path

import pytest

from puripuly_heart.app.ports.settings_view import (
    BackupTranslationEnabledEdit,
    BackupVariantEdit,
    LocalLlmBaseUrlEdit,
    LocalLlmModelEdit,
    OpenAICompatibleBaseUrlEdit,
    OpenAICompatibleModelEdit,
    ProviderApplyIntent,
    TranslationSelectionEdit,
    TranslationSelectionSnapshot,
)
from puripuly_heart.app.services.provider.provider_runtime_apply import (
    LlmProviderRebuildContext,
    LlmProviderRebuildOwner,
    ProviderRuntimeRebuildService,
)
from puripuly_heart.app.services.settings.settings_application import (
    materialize_provider_apply_intent,
)
from puripuly_heart.app.wiring.wiring_llm_factory import (
    llm_factory_extras_from_vnext,
    runtime_resolution_input_from_vnext,
)
from puripuly_heart.app.wiring.wiring_translation_backend import create_translation_backend
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.http_extensions import HttpExtensionRegistry
from puripuly_heart.core.runtime.provider_handle import ProviderRuntimeHandle
from puripuly_heart.core.storage.secrets import InMemorySecretStore

try:
    from puripuly_heart.core.translation_backend import TranslationVariantConfigurationError

    HAS_CONFIG_ERROR = True
except ImportError:  # pre-fix tree: plain ValueError is raised
    TranslationVariantConfigurationError = ValueError  # type: ignore[assignment]
    HAS_CONFIG_ERROR = False


def _selection_edits(model: str) -> tuple:
    return (
        TranslationSelectionEdit(
            TranslationSelectionSnapshot(
                model=model,
                connection="ollama",
                connection_history=(),
                http_extension_id=None,
                previous_llm_model=None,
                gpu_device_id="auto",
            ),
            (),
        ),
    )


def _materialize(base: AppSettingsVNext, edits: tuple) -> AppSettingsVNext:
    return materialize_provider_apply_intent(
        base,
        ProviderApplyIntent(edits),
        materialize_translation=lambda settings: settings,
    )


def _factory(secrets: InMemorySecretStore, registry: HttpExtensionRegistry):
    def create(settings: AppSettingsVNext) -> object:
        return create_translation_backend(
            translation_model=settings.intent.translation.model,
            secrets=secrets,
            http_extensions=registry,
            runtime_input=runtime_resolution_input_from_vnext(settings),
            extras=llm_factory_extras_from_vnext(settings),
            http_extension_id=settings.intent.translation.http_extension_id,
            concurrency_limit=settings.intent.translation.concurrency_limit,
        )

    return create


async def _noop_async() -> None:
    return None


class _Sinks:
    def __init__(self) -> None:
        self.availability: list[bool] = []
        self.failure: list[str] = []
        self.success: list[str] = []
        self.retry: list[int] = []
        self.signature: list[object] = []

    def owner(self, *, factory, handle: ProviderRuntimeHandle) -> LlmProviderRebuildOwner:
        async def replace_provider(provider: object | None) -> object | None:
            return await handle.replace_provider(provider, start=False)

        return LlmProviderRebuildOwner(
            context_provider=lambda: None,
            provider_factory=factory,
            availability_sink=self.availability.append,
            usage_refresh=_noop_async,
            failure_sink=self.failure.append,
            success_sink=self.success.append,
            signature_sink=self.signature.append,
            retry_sink=lambda: self.retry.append(1),
            runtime=ProviderRuntimeRebuildService(),
        )


def _context(settings: AppSettingsVNext, handle: ProviderRuntimeHandle):
    async def replace_provider(provider: object | None) -> object | None:
        return await handle.replace_provider(provider, start=False)

    return LlmProviderRebuildContext(
        settings=settings,
        replace_provider=replace_provider,
        requires_secret=False,
    )


@pytest.mark.asyncio
async def test_unconfigured_openai_compatible_selection_is_graceful(
    tmp_path: Path,
) -> None:
    registry = HttpExtensionRegistry(tmp_path)
    registry.reload()
    handle = ProviderRuntimeHandle(name="llm-unconfigured")
    factory = _factory(InMemorySecretStore(), registry)
    sinks = _Sinks()
    owner = sinks.owner(factory=factory, handle=handle)
    settings = _materialize(AppSettingsVNext(), _selection_edits("openai_compatible"))

    # production wiring: propagate_failure=True (compose_provider_runtime)
    result = await owner.rebuild_with_context(
        _context(settings, handle), propagate_failure=True
    )

    assert result is False
    # pinned contract kept: failed creation replaces the provider with None
    assert handle.provider is None
    # expected state: announced as unavailable (needs key/configuration notice)
    assert sinks.availability == [True]
    assert sinks.failure and "not available" in sinks.failure[0]
    assert sinks.retry
    # success must not be claimed, so the llm signature cache stays stale and
    # a later apply retries the rebuild
    assert sinks.signature == []
    await handle.close()


@pytest.mark.asyncio
async def test_empty_local_llm_model_uses_default_server_and_auto_resolution(
    tmp_path: Path,
) -> None:
    registry = HttpExtensionRegistry(tmp_path)
    registry.reload()
    handle = ProviderRuntimeHandle(name="llm-unconfigured-local")
    factory = _factory(InMemorySecretStore(), registry)
    sinks = _Sinks()
    owner = sinks.owner(factory=factory, handle=handle)
    empty_local = _materialize(
        AppSettingsVNext(),
        _selection_edits("local_llm")
        + (
            LocalLlmBaseUrlEdit(""),
            LocalLlmModelEdit(""),
        ),
    )

    result = await owner.rebuild_with_context(
        _context(empty_local, handle), propagate_failure=True
    )

    assert result is True
    assert handle.provider is not None
    assert sinks.availability == [False]
    await handle.close()


@pytest.mark.asyncio
async def test_configured_variant_rebuilds_after_recovery(tmp_path: Path) -> None:
    registry = HttpExtensionRegistry(tmp_path)
    registry.reload()
    handle = ProviderRuntimeHandle(name="llm-recovery")
    factory = _factory(InMemorySecretStore(), registry)
    sinks = _Sinks()
    owner = sinks.owner(factory=factory, handle=handle)
    unconfigured = _materialize(AppSettingsVNext(), _selection_edits("openai_compatible"))
    configured = _materialize(
        unconfigured,
        (
            OpenAICompatibleBaseUrlEdit("http://127.0.0.1:18081/v1"),
            OpenAICompatibleModelEdit("model-a"),
        ),
    )

    await owner.rebuild_with_context(_context(unconfigured, handle), propagate_failure=True)
    assert handle.provider is None

    result = await owner.rebuild_with_context(
        _context(configured, handle), propagate_failure=True
    )

    assert result is True
    assert handle.provider is not None
    assert sinks.availability[-1] is False
    assert sinks.signature
    await handle.close()


@pytest.mark.asyncio
async def test_generic_factory_failure_still_propagates(tmp_path: Path) -> None:
    handle = ProviderRuntimeHandle(name="llm-generic-failure")
    sinks = _Sinks()

    def broken_factory(_settings: AppSettingsVNext) -> object:
        raise RuntimeError("controlled provider construction failure")

    owner = sinks.owner(factory=broken_factory, handle=handle)
    settings = _materialize(AppSettingsVNext(), _selection_edits("openai_compatible"))

    with pytest.raises(RuntimeError, match="controlled provider construction failure"):
        await owner.rebuild_with_context(_context(settings, handle), propagate_failure=True)

    await handle.close()


@pytest.mark.asyncio
async def test_unconfigured_backup_translation_remains_graceful(tmp_path: Path) -> None:
    registry = HttpExtensionRegistry(tmp_path)
    registry.reload()
    handle = ProviderRuntimeHandle(name="llm-backup-control")
    factory = _factory(InMemorySecretStore(), registry)
    sinks = _Sinks()
    owner = sinks.owner(factory=factory, handle=handle)
    settings = _materialize(
        AppSettingsVNext(),
        _selection_edits("openai_compatible")
        + (
            OpenAICompatibleBaseUrlEdit("http://127.0.0.1:18081/v1"),
            OpenAICompatibleModelEdit("model-a"),
            BackupTranslationEnabledEdit(True),
            BackupVariantEdit("openai_compatible"),
        ),
    )

    result = await owner.rebuild_with_context(
        _context(settings, handle), propagate_failure=True
    )

    assert result is True
    assert handle.provider is not None
    await handle.close()


def test_configuration_error_is_a_value_error_subclass() -> None:
    assert HAS_CONFIG_ERROR, "TranslationVariantConfigurationError must exist"
    assert issubclass(TranslationVariantConfigurationError, ValueError)


@pytest.mark.asyncio
async def test_create_translation_backend_raises_configuration_error(tmp_path: Path) -> None:
    registry = HttpExtensionRegistry(tmp_path)
    registry.reload()
    settings = _materialize(AppSettingsVNext(), _selection_edits("openai_compatible"))

    with pytest.raises(ValueError, match="translation variant needs an address and a model"):
        create_translation_backend(
            translation_model=settings.intent.translation.model,
            secrets=InMemorySecretStore(),
            http_extensions=registry,
            runtime_input=runtime_resolution_input_from_vnext(settings),
            extras=llm_factory_extras_from_vnext(settings),
            http_extension_id=settings.intent.translation.http_extension_id,
        )
