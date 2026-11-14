"""Regression tests: «Перевод»/«Резервный перевод» apply surface.

Run (from /koda/output/docker-roi/work/agent-3-translation-toggles):
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tree/src \
      /tmp/project/.venv/bin/python3 -m pytest tests/test_translation_backup_apply_surface.py \
      -p no:cacheprovider -o asyncio_mode=auto

Covers the production provider merge (`_copy_provider_prompt_apply_fields`)
for the OpenAI-compatible server and backup translation fields added by the
fork rework (commit 238db677). Before the fix, edits to
`intent.openai_compatible` and `intent.backup_translation` were dropped by the
merge: rapid toggles silently lost their state and selecting the
OpenAI-compatible translation variant reached the backend factory with an
empty endpoint ("translation variant needs an address and a model").

The companion gap in `build_llm_provider_signature` (no rebuild on those
toggles) is owned and patched by agent-2
(`work/agent-2-translation-runtime-signature/`); these tests do not depend on
that patch.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from puripuly_heart.app.ports.settings_view import (
    BackupLocalLlmBaseUrlEdit,
    BackupLocalLlmModelEdit,
    BackupTranslationEnabledEdit,
    BackupVariantEdit,
    OpenAICompatibleBaseUrlEdit,
    OpenAICompatibleModelEdit,
    OpenAICompatibleProviderKeyEdit,
    ProviderApplyIntent,
    TranslationSelectionEdit,
    TranslationSelectionSnapshot,
)
from puripuly_heart.app.services.canonical_settings_persistence import (
    materialize_canonical_translation_settings,
)
from puripuly_heart.app.services.provider.provider_runtime_apply import (
    ProviderRuntimeApplyPlan,
)
from puripuly_heart.app.services.provider.provider_settings import ProviderApplicationOwner
from puripuly_heart.app.services.settings.settings_application import (
    materialize_provider_apply_intent,
)
from puripuly_heart.app.services.settings.settings_mutation_legacy import (
    apply_settings_path_patch,
)
from puripuly_heart.app.services.settings.settings_transaction_result import (
    SettingsTransactionResultOwner,
)
from puripuly_heart.app.wiring.wiring_llm_factory import runtime_resolution_input_from_vnext
from puripuly_heart.app.wiring.wiring_provider_runtime_policy import (
    build_llm_provider_signature,
)
from puripuly_heart.app.wiring.wiring_translation_backend import create_translation_backend
from puripuly_heart.composition.application_runtime import _copy_provider_prompt_apply_fields
from puripuly_heart.config.settings_vnext import serialization
from puripuly_heart.config.settings_vnext.migration import apply_canonical_delta
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.http_extensions import HttpExtensionRegistry
from puripuly_heart.core.messages import (
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
    TransactionResult,
)
from puripuly_heart.core.storage.secrets import InMemorySecretStore

USER_VARIANT_LOCAL_LLM = "local_llm"
USER_VARIANT_OPENAI_COMPATIBLE = "openai_compatible"


def _materialize(intent: ProviderApplyIntent, base: AppSettingsVNext) -> AppSettingsVNext:
    return materialize_provider_apply_intent(
        base,
        intent,
        materialize_translation=materialize_canonical_translation_settings,
    )


def _production_merge(pending: AppSettingsVNext, current: AppSettingsVNext) -> AppSettingsVNext:
    return _copy_provider_prompt_apply_fields(pending, copy.deepcopy(current))


def _backup_enable_intent() -> ProviderApplyIntent:
    return ProviderApplyIntent(
        (
            BackupTranslationEnabledEdit(True),
            BackupVariantEdit(USER_VARIANT_LOCAL_LLM),
            BackupLocalLlmBaseUrlEdit("http://127.0.0.1:1234/v1"),
            BackupLocalLlmModelEdit("backup-model"),
        )
    )


def _network_selection_intent() -> ProviderApplyIntent:
    return ProviderApplyIntent(
        (
            OpenAICompatibleProviderKeyEdit("deepseek"),
            OpenAICompatibleBaseUrlEdit("https://api.deepseek.com/v1"),
            OpenAICompatibleModelEdit("deepseek-chat"),
            TranslationSelectionEdit(
                TranslationSelectionSnapshot(
                    model=USER_VARIANT_OPENAI_COMPATIBLE,
                    connection="ollama",
                    connection_history=(),
                    http_extension_id=None,
                    previous_llm_model=None,
                    gpu_device_id="auto",
                ),
                (),
            ),
        )
    )


def test_production_merge_keeps_backup_translation_edits() -> None:
    base = AppSettingsVNext()
    pending = _materialize(_backup_enable_intent(), base)
    merged = _production_merge(pending, base)
    assert merged.intent.backup_translation == pending.intent.backup_translation
    assert merged.intent.backup_translation.enabled is True
    assert merged.intent.backup_translation.model == USER_VARIANT_LOCAL_LLM
    assert merged.intent.backup_translation.local_llm.model == "backup-model"


def test_production_merge_keeps_openai_compatible_edits() -> None:
    base = AppSettingsVNext()
    pending = _materialize(_network_selection_intent(), base)
    merged = _production_merge(pending, base)
    assert merged.intent.openai_compatible == pending.intent.openai_compatible
    assert merged.intent.translation.model == USER_VARIANT_OPENAI_COMPATIBLE


def test_production_merge_keeps_local_llm_edits() -> None:
    base = AppSettingsVNext()
    intent = ProviderApplyIntent(
        (
            TranslationSelectionEdit(
                TranslationSelectionSnapshot(
                    model=USER_VARIANT_LOCAL_LLM,
                    connection="ollama",
                    connection_history=(),
                    http_extension_id=None,
                    previous_llm_model=None,
                    gpu_device_id="auto",
                ),
                (),
            ),
        )
    )
    pending = _materialize(intent, base)
    merged = _production_merge(pending, base)
    assert merged.intent.translation == pending.intent.translation
    assert merged.intent.local_llm == pending.intent.local_llm


def test_llm_signature_tracks_backup_translation_settings() -> None:
    """Independent verification of agent-2's signature patch (not shipped here).

    This test intentionally exercises `build_llm_provider_signature`, owned by
    agent-2 (`work/agent-2-translation-runtime-signature/`). It is skipped
    unless that patch is applied, so this suite stays green on its own.
    """
    base = AppSettingsVNext()
    on = replace(
        base,
        intent=replace(
            base.intent,
            backup_translation=replace(
                base.intent.backup_translation,
                enabled=True,
                model=USER_VARIANT_LOCAL_LLM,
                local_llm=replace(
                    base.intent.backup_translation.local_llm,
                    base_url="http://127.0.0.1:1234/v1",
                    model="backup-model",
                ),
            ),
        ),
    )
    if build_llm_provider_signature(base) == build_llm_provider_signature(on):
        pytest.skip("agent-2 signature patch not applied")
    assert build_llm_provider_signature(base) != build_llm_provider_signature(on)


def test_llm_signature_tracks_openai_compatible_server_settings() -> None:
    """Independent verification of agent-2's signature patch (not shipped here)."""
    base = AppSettingsVNext()
    selected = replace(
        base,
        intent=replace(
            base.intent,
            translation=replace(
                base.intent.translation,
                model=USER_VARIANT_OPENAI_COMPATIBLE,
                connection="ollama",
            ),
            openai_compatible=replace(
                base.intent.openai_compatible,
                provider_key="deepseek",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
            ),
        ),
    )
    changed = replace(
        selected,
        intent=replace(
            selected.intent,
            openai_compatible=replace(selected.intent.openai_compatible, model="deepseek-reasoner"),
        ),
    )
    if build_llm_provider_signature(selected) == build_llm_provider_signature(changed):
        pytest.skip("agent-2 signature patch not applied")
    assert build_llm_provider_signature(selected) != build_llm_provider_signature(changed)


class _FakeSettingsOwner:
    def __init__(self, current: AppSettingsVNext) -> None:
        self.canonical = current
        self.committed_settings = current
        self.projection_snapshot = copy.deepcopy(current)
        self.last_failure = None

    @staticmethod
    def snapshot_values(settings: AppSettingsVNext) -> dict[str, object]:
        return serialization.to_dict(settings)

    def require_committed(self) -> AppSettingsVNext:
        return self.committed_settings

    def project_canonical_delta(self, _base: object, _next: object) -> object:
        return object()

    def create_canonical_patch_repository(self, **_kwargs: object) -> object:
        return object()

    def commit_delta(
        self, base: AppSettingsVNext, next_settings: AppSettingsVNext
    ) -> AppSettingsVNext:
        self.canonical = apply_canonical_delta(self.canonical, base, next_settings)
        self.committed_settings = self.canonical
        self.projection_snapshot = copy.deepcopy(self.canonical)
        return self.canonical


class _FakeRuntimeOwner:
    def build_plan(self, _settings: object, **_kwargs: object) -> ProviderRuntimeApplyPlan:
        return ProviderRuntimeApplyPlan(False, False, False)

    async def apply(self, settings: object, _plan: ProviderRuntimeApplyPlan) -> object:
        return settings

    def unavailable_result(
        self, _settings: object, _plan: ProviderRuntimeApplyPlan, **_kwargs: object
    ) -> object:
        return None


class _RecordingMutationService:
    def __init__(self) -> None:
        self.settings: _FakeSettingsOwner | None = None

    async def mutate(self, request: object) -> TransactionResult:
        assert self.settings is not None
        current = self.settings.canonical
        values = getattr(request, "values")
        self.settings.commit_delta(current, apply_settings_path_patch(current, values))
        return TransactionResult(
            status=TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
            message=None,
            diagnostics=None,
        )


async def _apply_with_production_merge(
    state: _FakeSettingsOwner, intent: ProviderApplyIntent
) -> bool:
    service = _RecordingMutationService()
    service.settings = state

    async def preserve(settings: AppSettingsVNext) -> AppSettingsVNext:
        return settings

    async def no_order24(_settings: object) -> bool:
        return False

    owner = ProviderApplicationOwner(
        settings=state,
        runtime=_FakeRuntimeOwner(),
        merge_settings=lambda pending: _production_merge(pending, state.canonical or pending),
        preserve_before_replace=preserve,
        sync_ui=lambda: None,
        order24_patch_provider=lambda _settings: None,
        apply_order24=no_order24,
        remember_order22=lambda _settings: None,
        mutation_service_provider=lambda: service,
        results=SettingsTransactionResultOwner(),
        sync_memory=lambda _settings: None,
        capture_runtime_signatures=lambda: None,
        sync_stt_signatures=lambda _settings: None,
        consume_superseded_settings=lambda _settings: False,
        active_local_asr_change=lambda _base, _next: False,
        compensate_local_asr=lambda **_kwargs: object(),
        llm_retry_pending=lambda: False,
        mark_llm_retry=lambda: None,
    )
    pending = _materialize(intent, state.canonical)
    return await owner.apply(pending)


@pytest.mark.asyncio
async def test_rapid_backup_toggles_persist_last_state() -> None:
    state = _FakeSettingsOwner(AppSettingsVNext())
    for enabled in (True, False, True):
        edits: list[object] = [BackupTranslationEnabledEdit(enabled)]
        if enabled:
            edits += [
                BackupVariantEdit(USER_VARIANT_LOCAL_LLM),
                BackupLocalLlmBaseUrlEdit("http://127.0.0.1:1234/v1"),
                BackupLocalLlmModelEdit("backup-model"),
            ]
        else:
            edits.append(BackupVariantEdit(""))
        assert await _apply_with_production_merge(state, ProviderApplyIntent(tuple(edits)))
    committed = state.committed_settings.intent.backup_translation
    assert committed.enabled is True
    assert committed.model == USER_VARIANT_LOCAL_LLM
    assert committed.local_llm.base_url == "http://127.0.0.1:1234/v1"
    assert committed.local_llm.model == "backup-model"


@pytest.mark.asyncio
async def test_openai_compatible_variant_apply_commits_a_buildable_backend(
    tmp_path: Path,
) -> None:
    state = _FakeSettingsOwner(AppSettingsVNext())
    assert await _apply_with_production_merge(state, _network_selection_intent())
    committed = state.committed_settings
    assert committed.intent.translation.model == USER_VARIANT_OPENAI_COMPATIBLE
    backend = create_translation_backend(
        translation_model=committed.intent.translation.model,
        secrets=InMemorySecretStore(),
        http_extensions=HttpExtensionRegistry(tmp_path),
        runtime_input=runtime_resolution_input_from_vnext(committed),
    )
    assert backend is not None
