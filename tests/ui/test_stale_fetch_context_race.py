"""agent-4 repro: stale model-discovery results vs. non-URL context switches.

The stale-fetch URL guard (6bd18b30) drops a ``Fetch models`` result when the
form's base URL changed while the fetch was in flight. Two context changes keep
the URL but still invalidate the result:

1. Local card: the user switches to another preset with the *same* base URL but
   a different llm-lib backend (e.g. llama.cpp and an ollama endpoint behind one
   proxy address). The model list of the old backend is auto-picked into the
   new backend's form — an invalid model id for that backend.
2. OpenAI card: the user switches to another preset with the *same* base URL.
   The preset handler deliberately clears the model field on selection change;
   the stale result silently resurrects it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.ports.settings_view import (
    BackupLocalLlmModelEdit,
    LocalLlmModelEdit,
    OpenAICompatibleModelEdit,
)
from puripuly_heart.config import providers_registry
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.provider_settings import (
    attach_provider_windows,
    create_settings_view,
)


def _install_presets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, documents: dict) -> None:
    protocols = ("openai", "anthropic", "local")
    paths = {protocol: tmp_path / f"{protocol}_providers.json" for protocol in protocols}
    for protocol in protocols:
        providers = documents.get(protocol, {})
        paths[protocol].write_text(
            json.dumps({"version": "1.0", "providers": providers}), encoding="utf-8"
        )
    monkeypatch.setattr(
        providers_registry,
        "ensure_providers_file",
        lambda protocol="openai": paths[protocol],
    )
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)


@pytest.mark.parametrize("card", ["translation", "backup"])
@pytest.mark.asyncio
async def test_stale_local_fetch_does_not_commit_after_backend_swap_same_url(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path, card: str
) -> None:
    _install_presets(
        monkeypatch,
        tmp_path,
        {
            "local": {
                "llamacpp": {
                    "label": "llama.cpp",
                    "base_url": "http://127.0.0.1:8080/v1",
                    "backend": "llamacpp",
                },
                "ollama-proxy": {
                    "label": "Ollama behind the same proxy",
                    "base_url": "http://127.0.0.1:8080/v1",
                    "backend": "ollama",
                },
            }
        },
    )
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    attach_provider_windows(view)
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    if card == "backup":
        view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
        view._on_backup_local_provider_selected("llamacpp")
    else:
        view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
        view._on_local_llm_provider_selected("llamacpp")

    started = asyncio.Event()
    release = asyncio.Event()
    fetched_backends: list[str] = []

    async def slow_fetch(backend: str, base_url: str, api_key: str = "") -> list[str]:
        fetched_backends.append(backend)
        started.set()
        await release.wait()
        return ["stale-model"]

    monkeypatch.setattr(settings_view, "_fetch_llm_lib_models_default", slow_fetch)

    if card == "backup":
        handler = view._on_fetch_backup_local_models
        select_other = lambda: view._on_backup_local_provider_selected("ollama-proxy")  # noqa: E731
        model_control = view._backup_local_model
        model_attr = "backup_local_model"
        backend_attr = "backup_local_backend"
        edit_type = BackupLocalLlmModelEdit
    else:
        handler = view._on_fetch_local_llm_models
        select_other = lambda: view._on_local_llm_provider_selected("ollama-proxy")  # noqa: E731
        model_control = view._local_llm_model
        model_attr = "local_llm_model"
        backend_attr = "local_llm_backend"
        edit_type = LocalLlmModelEdit

    task = asyncio.create_task(handler(None))
    await started.wait()
    assert fetched_backends == ["llamacpp"]

    # The user moves to another preset with the same address but a different
    # backend while the fetch is still in flight. The preset handler clears the
    # model field (selection changed).
    select_other()
    assert model_control.value == ""

    release.set()
    await task

    # The model list fetched for llamacpp must not land in the ollama form.
    assert model_control.value == ""
    current = view._provider_draft or view._provider_snapshot
    assert current is not None
    assert getattr(current, model_attr) == ""
    assert getattr(current, backend_attr) == "ollama"
    assert not any(isinstance(edit, edit_type) for edit in view._provider_edits.values())


@pytest.mark.asyncio
async def test_stale_openai_fetch_does_not_commit_after_same_url_preset_switch(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    _install_presets(
        monkeypatch,
        tmp_path,
        {
            "openai": {
                "first": {
                    "label": "First",
                    "base_url": "https://shared.example.com/v1",
                },
                "second": {
                    "label": "Second",
                    "base_url": "https://shared.example.com/v1",
                },
            }
        },
    )
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    attach_provider_windows(view)
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._on_openai_provider_selected("first")

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_fetch(base_url: str, api_key: str = "") -> list[str]:
        started.set()
        await release.wait()
        return ["stale-model"]

    view.on_fetch_models = slow_fetch

    task = asyncio.create_task(view._on_fetch_openai_models(None))
    await started.wait()

    # Selection change clears the model field even though the address is the
    # same; the stale fetch result must not resurrect it.
    view._on_openai_provider_selected("second")
    assert view._openai_model.value == ""

    release.set()
    await task

    assert view._openai_model.value == ""
    current = view._provider_draft or view._provider_snapshot
    assert current is not None
    assert current.openai_compatible_model == ""
    assert current.openai_compatible_provider_key == "second"
    assert not any(
        isinstance(edit, OpenAICompatibleModelEdit) for edit in view._provider_edits.values()
    )
