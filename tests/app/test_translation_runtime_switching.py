from __future__ import annotations

import json
from dataclasses import replace

import pytest

from puripuly_heart.app.services.provider.provider_runtime_apply import (
    ProviderRuntimeOwner,
    ProviderRuntimeState,
)
from puripuly_heart.app.wiring.wiring_provider_runtime_policy import (
    build_llm_provider_signature,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.settings_vnext.serialization import from_dict, to_json_text


def _with_intent(settings: AppSettingsVNext, **changes: object) -> AppSettingsVNext:
    return replace(settings, intent=replace(settings.intent, **changes))


def _openai_primary(model: str = "primary-a") -> AppSettingsVNext:
    settings = AppSettingsVNext()
    return _with_intent(
        settings,
        translation=replace(
            settings.intent.translation,
            model="openai_compatible",
            connection="ollama",
        ),
        openai_compatible=replace(
            settings.intent.openai_compatible,
            provider_key="openai",
            base_url="https://api.openai.com/v1",
            model=model,
        ),
    )


def _with_local_backup(settings: AppSettingsVNext, *, model: str = "backup-a") -> AppSettingsVNext:
    backup = settings.intent.backup_translation
    return _with_intent(
        settings,
        backup_translation=replace(
            backup,
            enabled=True,
            model="local_llm",
            local_llm=replace(
                backup.local_llm,
                base_url="http://127.0.0.1:11434/v1",
                model=model,
                extra_body={"temperature": 0.2},
            ),
        ),
    )


def _with_openai_backup(settings: AppSettingsVNext) -> AppSettingsVNext:
    backup = settings.intent.backup_translation
    return _with_intent(
        settings,
        backup_translation=replace(
            backup,
            enabled=True,
            model="openai_compatible",
            openai_compatible=replace(
                backup.openai_compatible,
                provider_key="deepseek",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
            ),
        ),
    )


def _signature(settings: AppSettingsVNext) -> tuple[object, ...]:
    return build_llm_provider_signature(settings)


@pytest.mark.parametrize("field", ["provider_key", "base_url", "model"])
def test_primary_openai_signature_tracks_active_runtime_fields(field: str) -> None:
    current = _openai_primary()
    replacement = {
        "provider_key": "deepseek",
        "base_url": "http://127.0.0.1:9000/v1",
        "model": "primary-b",
    }[field]
    changed = _with_intent(
        current,
        openai_compatible=replace(current.intent.openai_compatible, **{field: replacement}),
    )

    assert _signature(changed) != _signature(current)


def test_primary_openai_signature_ignores_fields_while_variant_is_inactive() -> None:
    current = AppSettingsVNext()
    changed = _with_intent(
        current,
        openai_compatible=replace(
            current.intent.openai_compatible,
            provider_key="openai",
            base_url="https://api.openai.com/v1",
            model="unused",
        ),
    )

    assert _signature(changed) == _signature(current)


def test_backup_signature_tracks_enable_disable_and_variant_switches() -> None:
    primary = _openai_primary()
    local = _with_local_backup(primary)
    network = _with_openai_backup(local)
    disabled = _with_intent(
        network,
        backup_translation=replace(network.intent.backup_translation, enabled=False, model=""),
    )

    assert _signature(primary) != _signature(local)
    assert _signature(local) != _signature(network)
    assert _signature(network) != _signature(disabled)


@pytest.mark.parametrize("field", ["base_url", "model", "extra_body"])
def test_local_backup_signature_tracks_active_runtime_fields(field: str) -> None:
    current = _with_local_backup(_openai_primary())
    replacement: object = {
        "base_url": "http://127.0.0.1:9000/v1",
        "model": "backup-b",
        "extra_body": {"temperature": 0.7},
    }[field]
    backup = current.intent.backup_translation
    changed = _with_intent(
        current,
        backup_translation=replace(
            backup,
            local_llm=replace(backup.local_llm, **{field: replacement}),
        ),
    )

    assert _signature(changed) != _signature(current)


@pytest.mark.parametrize("field", ["provider_key", "base_url", "model"])
def test_openai_backup_signature_tracks_active_runtime_fields(field: str) -> None:
    current = _with_openai_backup(_openai_primary())
    replacement = {
        "provider_key": "openai",
        "base_url": "http://127.0.0.1:9001/v1",
        "model": "backup-b",
    }[field]
    backup = current.intent.backup_translation
    changed = _with_intent(
        current,
        backup_translation=replace(
            backup,
            openai_compatible=replace(backup.openai_compatible, **{field: replacement}),
        ),
    )

    assert _signature(changed) != _signature(current)


def test_backup_signature_canonicalizes_json_and_ignores_inactive_configuration() -> None:
    current = _with_local_backup(_openai_primary())
    backup = current.intent.backup_translation
    reordered = _with_intent(
        current,
        backup_translation=replace(
            backup,
            local_llm=replace(
                backup.local_llm,
                extra_body={"temperature": 0.2, "reasoning_effort": "none"},
            ),
        ),
    )
    reordered_again = _with_intent(
        reordered,
        backup_translation=replace(
            reordered.intent.backup_translation,
            local_llm=replace(
                reordered.intent.backup_translation.local_llm,
                extra_body={"reasoning_effort": "none", "temperature": 0.2},
            ),
        ),
    )
    inactive_openai_change = _with_intent(
        current,
        backup_translation=replace(
            backup,
            openai_compatible=replace(backup.openai_compatible, model="inactive"),
        ),
    )
    disabled = _with_intent(current, backup_translation=replace(backup, enabled=False, model=""))
    disabled_change = _with_intent(
        disabled,
        backup_translation=replace(
            disabled.intent.backup_translation,
            local_llm=replace(disabled.intent.backup_translation.local_llm, model="inactive"),
        ),
    )

    assert _signature(reordered) != _signature(current)
    assert _signature(reordered_again) == _signature(reordered)
    assert _signature(inactive_openai_change) == _signature(current)
    assert _signature(disabled_change) == _signature(disabled)


def test_custom_http_signature_does_not_track_unused_backup() -> None:
    current = AppSettingsVNext()
    current = _with_intent(
        current,
        translation=replace(
            current.intent.translation,
            model="custom_http",
            connection="custom_http",
        ),
    )

    assert _signature(_with_local_backup(current)) == _signature(current)


def _runtime_owner(current: AppSettingsVNext) -> ProviderRuntimeOwner:
    state = ProviderRuntimeState(
        runtime_available=True,
        llm_available=True,
        self_stt_available=True,
        peer_stt_available=True,
        self_stt_desired=False,
        peer_stt_desired=False,
    )

    async def noop(*_args: object) -> None:
        return None

    return ProviderRuntimeOwner(
        state_provider=lambda _settings: state,
        common_effect=lambda _settings: None,
        rebuild_llm=noop,
        recover_gpu=noop,
        refresh_peer=noop,
        refresh_self_stt=noop,
        signature_sink=lambda _settings: None,
        current_settings_provider=lambda: current,
        signature_cache_provider=lambda: ("self", "peer", _signature(current)),
        self_signature_builder=lambda _settings: "self",
        peer_signature_builder=lambda _settings, _canonical: "peer",
        llm_signature_builder=_signature,
        gpu_restart_decision=lambda _current, _next: False,
    )


def test_every_saved_switch_in_a_rapid_sequence_requests_runtime_rebuild() -> None:
    primary = _openai_primary()
    local_backup = _with_local_backup(primary)
    openai_backup = _with_openai_backup(local_backup)
    backup_off = _with_intent(
        openai_backup,
        backup_translation=replace(
            openai_backup.intent.backup_translation,
            enabled=False,
            model="",
        ),
    )
    primary_changed = _with_intent(
        backup_off,
        openai_compatible=replace(backup_off.intent.openai_compatible, model="primary-b"),
    )
    local_primary = _with_intent(
        primary_changed,
        translation=replace(primary_changed.intent.translation, model="local_llm"),
        local_llm=replace(
            primary_changed.intent.local_llm,
            backend="ollama",
            base_url="http://127.0.0.1:11434/v1",
            model="local-primary",
        ),
    )

    sequence = [local_backup, openai_backup, backup_off, primary_changed, local_primary]
    current = primary
    for next_settings in sequence:
        reloaded = from_dict(json.loads(to_json_text(next_settings)))
        assert _signature(reloaded) == _signature(next_settings)
        plan = _runtime_owner(current).build_plan(reloaded, force_rebuild_llm=False)
        assert plan.should_rebuild_llm is True
        assert plan.should_refresh_self_stt is False
        assert plan.should_refresh_peer is False
        current = reloaded
