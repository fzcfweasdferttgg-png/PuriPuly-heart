"""Regression: rapid «Перевод»/«Резервный перевод» toggles across E2E seams.

Pins behavior verified on portable-core-2.10.0 (bb133336):
  1. rapid toggles survive the production merge -> real persistence -> reload
     (save/reload round-trip) and the restart rebuild (signature + backend
     factory) sees the final state;
  2. the UI queue (SettingsQueue + TranslatorApp debounce) coalesces rapid
     provider toggles to one merged apply; disabling backup keeps the
     configured endpoint fields (re-enable restores them);
  3. live chain swap (backup toggle rebuild) with parallel in-flight
     translations cancels them contained (request level) and closes the old
     chain exactly once.

Before the round-1 merge fix (`_copy_provider_prompt_apply_fields`) tests 1-2
fail on the unpatched base (backup/openai_compatible edits dropped on apply).
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from uuid import uuid4

from puripuly_heart.app.ports.settings_view import (
    BackupLocalLlmBaseUrlEdit,
    BackupLocalLlmModelEdit,
    BackupOpenAICompatibleBaseUrlEdit,
    BackupOpenAICompatibleModelEdit,
    BackupTranslationEnabledEdit,
    BackupVariantEdit,
    OpenAICompatibleBaseUrlEdit,
    OpenAICompatibleModelEdit,
    OpenAICompatibleProviderKeyEdit,
    ProviderApplyIntent,
    SettingsApplyOutcome,
    TranslationSelectionEdit,
    TranslationSelectionSnapshot,
)
from puripuly_heart.app.services.canonical_settings_persistence import (
    compose_settings_owner,
    materialize_canonical_translation_settings,
)
from puripuly_heart.app.services.settings.settings_application import (
    materialize_provider_apply_intent,
)
from puripuly_heart.app.wiring.wiring_llm_factory import (
    runtime_resolution_input_from_vnext,
)
from puripuly_heart.app.wiring.wiring_provider_runtime_policy import (
    build_llm_provider_signature,
)
from puripuly_heart.app.wiring.wiring_translation_backend import (
    create_translation_backend,
)
from puripuly_heart.app.wiring.wiring_translation_variants import (
    USER_VARIANT_LOCAL_LLM,
    USER_VARIANT_OPENAI_COMPATIBLE,
    SequentialFallbackLLMProvider,
)
from puripuly_heart.composition.application_runtime import (
    _copy_provider_prompt_apply_fields,
)
from puripuly_heart.config.settings_vnext.facade import load_vnext_settings
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.http_extensions import HttpExtensionRegistry
from puripuly_heart.core.llm.provider import SemaphoreLLMProvider
from puripuly_heart.core.runtime.provider_handle import ProviderRuntimeHandle
from puripuly_heart.core.storage.secrets import InMemorySecretStore


def _materialize(
    intent: ProviderApplyIntent, base: AppSettingsVNext
) -> AppSettingsVNext:
    return materialize_provider_apply_intent(
        base,
        intent,
        materialize_translation=materialize_canonical_translation_settings,
    )


def _production_merge(
    pending: AppSettingsVNext, current: AppSettingsVNext
) -> AppSettingsVNext:
    return _copy_provider_prompt_apply_fields(pending, copy.deepcopy(current))


def _backup_snapshot(settings: AppSettingsVNext) -> tuple:
    b = settings.intent.backup_translation
    return (
        b.enabled,
        b.model,
        b.local_llm.base_url,
        b.local_llm.model,
        b.openai_compatible.base_url,
        b.openai_compatible.model,
    )


def _openai_snapshot(settings: AppSettingsVNext) -> tuple:
    o = settings.intent.openai_compatible
    return (o.provider_key, o.base_url, o.model)


def _selection(model: str) -> TranslationSelectionEdit:
    return TranslationSelectionEdit(
        TranslationSelectionSnapshot(
            model=model,
            connection="ollama",
            connection_history=(),
            http_extension_id=None,
            previous_llm_model=None,
            gpu_device_id="auto",
        ),
        (),
    )


_TOGGLE_SEQUENCE = (
    ProviderApplyIntent(
        (
            BackupTranslationEnabledEdit(True),
            BackupVariantEdit(USER_VARIANT_LOCAL_LLM),
            BackupLocalLlmBaseUrlEdit("http://127.0.0.1:1234/v1"),
            BackupLocalLlmModelEdit("backup-model"),
        )
    ),
    ProviderApplyIntent((BackupTranslationEnabledEdit(False), BackupVariantEdit(""))),
    ProviderApplyIntent(
        (
            BackupTranslationEnabledEdit(True),
            BackupVariantEdit(USER_VARIANT_OPENAI_COMPATIBLE),
            BackupOpenAICompatibleBaseUrlEdit("https://api.example.com/v1"),
            BackupOpenAICompatibleModelEdit("backup-net-model"),
        )
    ),
    ProviderApplyIntent((BackupTranslationEnabledEdit(False), BackupVariantEdit(""))),
)


def test_rapid_backup_toggles_survive_save_reload_roundtrip(tmp_path: Path) -> None:
    settings = compose_settings_owner(tmp_path / "settings.json")
    settings.persistence.persist(settings.path, AppSettingsVNext())
    settings.start()
    expected = AppSettingsVNext()
    # hand-written expectations per step: the production merge must keep the
    # toggled values (not just reload whatever it produced)
    step_expectations = (
        (True, USER_VARIANT_LOCAL_LLM, "http://127.0.0.1:1234/v1", "backup-model"),
        (False, "", "http://127.0.0.1:1234/v1", "backup-model"),
        (
            True,
            USER_VARIANT_OPENAI_COMPATIBLE,
            "https://api.example.com/v1",
            "backup-net-model",
        ),
        (False, "", "https://api.example.com/v1", "backup-net-model"),
    )
    for intent, want in zip(_TOGGLE_SEQUENCE, step_expectations, strict=True):
        expected = _production_merge(_materialize(intent, expected), expected)
        settings.commit_delta(settings.canonical, expected)
        reloaded = load_vnext_settings(settings.path).settings
        backup = reloaded.intent.backup_translation
        assert (backup.enabled, backup.model) == want[:2]
        if backup.model == USER_VARIANT_LOCAL_LLM:
            assert (backup.local_llm.base_url, backup.local_llm.model) == want[2:]
        elif backup.model == USER_VARIANT_OPENAI_COMPATIBLE:
            assert (
                backup.openai_compatible.base_url,
                backup.openai_compatible.model,
            ) == want[2:]
        assert _backup_snapshot(reloaded) == _backup_snapshot(expected)
        assert _openai_snapshot(reloaded) == _openai_snapshot(expected)
        assert reloaded.intent.translation.model == expected.intent.translation.model


def test_restart_rebuild_sees_final_toggle_state(tmp_path: Path) -> None:
    settings = compose_settings_owner(tmp_path / "settings.json")
    settings.persistence.persist(settings.path, AppSettingsVNext())
    settings.start()
    final = AppSettingsVNext()
    for intent in _TOGGLE_SEQUENCE + (
        ProviderApplyIntent(
            (
                _selection(USER_VARIANT_OPENAI_COMPATIBLE),
                OpenAICompatibleProviderKeyEdit("deepseek"),
                OpenAICompatibleBaseUrlEdit("https://api.deepseek.com/v1"),
                OpenAICompatibleModelEdit("deepseek-chat"),
            )
        ),
        ProviderApplyIntent(
            (
                BackupTranslationEnabledEdit(True),
                BackupVariantEdit(USER_VARIANT_LOCAL_LLM),
                BackupLocalLlmBaseUrlEdit("http://127.0.0.1:1234/v1"),
                BackupLocalLlmModelEdit("backup-model"),
            )
        ),
    ):
        final = _production_merge(_materialize(intent, final), final)
        settings.commit_delta(settings.canonical, final)

    reloaded = load_vnext_settings(settings.path).settings
    assert build_llm_provider_signature(reloaded) != build_llm_provider_signature(
        AppSettingsVNext()
    )
    backend = create_translation_backend(
        translation_model=reloaded.intent.translation.model,
        secrets=InMemorySecretStore(),
        http_extensions=HttpExtensionRegistry(tmp_path),
        runtime_input=runtime_resolution_input_from_vnext(reloaded),
    )
    try:
        chain = backend.provider
        while isinstance(chain, SemaphoreLLMProvider):
            chain = chain.inner
        assert isinstance(chain, SequentialFallbackLLMProvider)
        assert reloaded.intent.backup_translation.enabled is True
    finally:
        asyncio.run(backend.close())


async def test_queue_coalesces_rapid_toggles_and_keeps_fields_on_disable(
    tmp_path: Path,
) -> None:
    from puripuly_heart.ui.app import TranslatorApp
    from puripuly_heart.ui.settings_queue import SettingsQueue

    settings = compose_settings_owner(tmp_path / "settings.json")
    settings.persistence.persist(settings.path, AppSettingsVNext())
    settings.start()
    applied: list[AppSettingsVNext] = []

    async def apply(intent: object) -> SettingsApplyOutcome:
        if isinstance(intent, ProviderApplyIntent):
            merged = _production_merge(
                _materialize(intent, settings.canonical), settings.canonical
            )
            settings.commit_delta(settings.canonical, merged)
            applied.append(merged)
        return SettingsApplyOutcome(saved=True, applied=True)

    ui = TranslatorApp.__new__(TranslatorApp)
    workers: list = []
    ui._queue_settings_mutation_task = workers.append
    ui._settings_queue = SettingsQueue(apply, lambda _intent: None)
    for intent in _TOGGLE_SEQUENCE:
        ui._queue_settings_apply(intent)
    while workers:
        await workers.pop(0)()

    assert len(applied) == 1
    assert not ui._settings_queue.has_pending
    final = load_vnext_settings(settings.path).settings
    expected = AppSettingsVNext()
    for intent in _TOGGLE_SEQUENCE:
        expected = _production_merge(_materialize(intent, expected), expected)
    assert _backup_snapshot(final) == _backup_snapshot(expected)
    assert _openai_snapshot(final) == _openai_snapshot(AppSettingsVNext())
    # disabling keeps configured endpoint fields; re-enable restores them
    assert final.intent.backup_translation.enabled is False
    assert final.intent.backup_translation.local_llm.model == "backup-model"
    reenabled = _production_merge(
        _materialize(ProviderApplyIntent((BackupTranslationEnabledEdit(True),)), final),
        final,
    )
    assert reenabled.intent.backup_translation.enabled is True
    assert reenabled.intent.backup_translation.local_llm.model == "backup-model"


class _FakeLLM:
    """Mirrors HttpxLocalOpenAIClient close semantics: cancel active operations."""

    def __init__(self, name: str, delay: float = 0.0) -> None:
        self.name = name
        self.delay = delay
        self.close_calls = 0
        self._active: set = set()

    async def translate(self, **_kwargs):
        from puripuly_heart.domain.models import Translation

        operation = asyncio.current_task()
        self._active.add(operation)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self._active.discard(operation)
        return Translation(
            utterance_id=uuid4(),
            translated_text=f"ok-{self.name}",
            source_text="x",
            source_language="ja",
            target_language="en",
            channel="self",
            update_id="u",
            origin_wall_clock_ms=0,
            session_scope="s",
            source_text_hash="h",
            source_text_len=1,
            logical_turn_key="k",
        )

    async def close(self) -> None:
        self.close_calls += 1
        current = asyncio.current_task()
        operations = tuple(op for op in self._active if op is not current)
        for op in operations:
            op.cancel()
        if operations:
            await asyncio.gather(*operations, return_exceptions=True)


async def test_chain_swap_cancels_inflight_translations_and_closes_old_chain() -> None:
    primary = _FakeLLM("primary", delay=5.0)
    backup = _FakeLLM("backup")
    chain = SemaphoreLLMProvider(
        inner=SequentialFallbackLLMProvider(primary=primary, fallback=backup),
        semaphore=asyncio.Semaphore(10),
        provider_name="local_llm",
    )
    handle = ProviderRuntimeHandle(name="llm")
    handle.attach_provider_reference(chain)

    kwargs = {
        "utterance_id": uuid4(),
        "text": "hi",
        "system_prompt": "p",
        "source_language": "ja",
        "target_language": "en",
    }
    tasks = [
        asyncio.get_running_loop().create_task(chain.translate(**kwargs))
        for _ in range(3)
    ]
    await asyncio.sleep(0.05)
    assert not any(task.done() for task in tasks)

    new_primary = _FakeLLM("primary2")
    new_backup = _FakeLLM("backup2")
    new_chain = SemaphoreLLMProvider(
        inner=SequentialFallbackLLMProvider(primary=new_primary, fallback=new_backup),
        semaphore=asyncio.Semaphore(10),
        provider_name="local_llm",
    )
    old = await handle.replace_provider(new_chain, start=False)
    assert old is chain
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert len(results) == 3
    assert all(isinstance(result, BaseException) for result in results)
    assert primary.close_calls == 1
    assert backup.close_calls == 1
    fresh = await new_chain.translate(**kwargs)
    assert fresh.translated_text == "ok-primary2"
    await handle.close()
    assert new_primary.close_calls == 1
    assert new_backup.close_calls == 1
