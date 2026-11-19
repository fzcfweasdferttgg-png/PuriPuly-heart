"""agent-4 repro: stale model-discovery results must not commit into staged state.

Race under test: "Fetch models" is async (up to 10s). While it is in flight the
user can switch the preset / URL of the same form. When the fetch resolves with
exactly one model it is auto-picked via ``_pick_openai_model`` which commits it
into the staged draft — a model discovered from the *old* endpoint lands under
the *new* preset/URL, invisible to the user.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.ports.settings_view import OpenAICompatibleModelEdit
from puripuly_heart.config import providers_registry
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from tests.helpers.provider_settings import (
    attach_provider_windows,
    create_settings_view,
)


def _install_two_presets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    documents = {
        "openai": {
            "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1"},
            "other": {"label": "Other", "base_url": "https://other.example.com/v1"},
        },
        "anthropic": {
            "anthropic": {"label": "Anthropic", "base_url": "https://api.anthropic.com"},
        },
        "local": {
            "llamacpp": {
                "label": "llama.cpp",
                "base_url": "http://127.0.0.1:8080/v1",
                "backend": "llamacpp",
            },
        },
    }
    paths = {protocol: tmp_path / f"{protocol}_providers.json" for protocol in documents}
    for protocol, providers in documents.items():
        paths[protocol].write_text(
            json.dumps({"version": "1.0", "providers": providers}), encoding="utf-8"
        )
    monkeypatch.setattr(
        providers_registry,
        "ensure_providers_file",
        lambda protocol="openai": paths[protocol],
    )
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)


@pytest.mark.asyncio
async def test_stale_single_model_fetch_does_not_commit_after_preset_switch(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    _install_two_presets(monkeypatch, tmp_path)
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    attach_provider_windows(view)
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._on_openai_provider_selected("deepseek")

    started = asyncio.Event()
    release = asyncio.Event()
    fetched_urls: list[str] = []

    async def slow_fetch(base_url: str, api_key: str = "") -> list[str]:
        fetched_urls.append(base_url)
        started.set()
        await release.wait()
        return ["stale-model"]

    view.on_fetch_models = slow_fetch

    task = asyncio.create_task(view._on_fetch_openai_models(None))
    await started.wait()
    assert fetched_urls == ["https://api.deepseek.com/v1"]

    # The user moves on while the fetch is still in flight.
    view._on_openai_provider_selected("other")
    assert (view._provider_draft.openai_compatible_model) == ""
    assert view._openai_model.value == ""

    release.set()
    await task

    # The stale result (from deepseek) must not survive into the form state
    # of the "other" preset.
    assert (view._provider_draft.openai_compatible_model) == ""
    assert view._openai_model.value == ""
    assert not any(
        isinstance(edit, OpenAICompatibleModelEdit) for edit in view._provider_edits.values()
    )


@pytest.mark.asyncio
async def test_stale_single_model_fetch_does_not_commit_after_url_edit(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    _install_two_presets(monkeypatch, tmp_path)
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    attach_provider_windows(view)
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._on_openai_provider_selected("deepseek")

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_fetch(base_url: str, api_key: str = "") -> list[str]:
        started.set()
        await release.wait()
        return ["stale-model"]

    view.on_fetch_models = slow_fetch

    task = asyncio.create_task(view._on_fetch_openai_models(None))
    await started.wait()

    # The user replaces the endpoint by hand while the fetch is in flight.
    view._openai_base_url.value = "https://custom.example.com/v1"
    view._on_openai_base_url_change_end(None)

    release.set()
    await task

    assert (view._provider_draft.openai_compatible_model) == ""
    assert view._openai_model.value == ""
