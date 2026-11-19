"""agent-5 repro: stale-fetch URL guard must honor trailing-slash address matching.

The guard in ``SettingsView._do_fetch_models`` drops in-flight discovery results
when the form URL changed while the fetch ran. Since 6bd18b30 the rest of the
form treats addresses that differ only by a trailing slash as the *same*
address (``base_url_addresses_match``), so an edit that only adds/strips a
trailing slash must not discard a still-valid fetch result.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config import providers_registry
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from tests.helpers.provider_settings import (
    attach_provider_windows,
    create_settings_view,
)


def _install_deepseek_preset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    documents = {
        "openai": {
            "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1"},
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
async def test_trailing_slash_url_edit_keeps_single_model_fetch_result(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    _install_deepseek_preset(monkeypatch, tmp_path)
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
        return ["only-model"]

    view.on_fetch_models = slow_fetch

    task = asyncio.create_task(view._on_fetch_openai_models(None))
    await started.wait()

    # The user adds a trailing slash to the same address mid-fetch. This must
    # not be treated as a different endpoint (base_url_addresses_match).
    view._openai_base_url.value = "https://api.deepseek.com/v1/"
    view._on_openai_base_url_change_end(None)

    release.set()
    await task

    current = view._provider_draft or view._provider_snapshot
    assert current.openai_compatible_model == "only-model"
    assert view._openai_model.value == "only-model"


@pytest.mark.asyncio
async def test_different_url_still_drops_single_model_fetch_result(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    _install_deepseek_preset(monkeypatch, tmp_path)
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
        return ["only-model"]

    view.on_fetch_models = slow_fetch

    task = asyncio.create_task(view._on_fetch_openai_models(None))
    await started.wait()

    view._openai_base_url.value = "https://custom.example.com/v1"
    view._on_openai_base_url_change_end(None)

    release.set()
    await task

    current = view._provider_draft or view._provider_snapshot
    assert current.openai_compatible_model == ""
    assert view._openai_model.value == ""
