from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config import providers_registry
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from tests.helpers.provider_settings import create_settings_view
from tests.helpers.settings_view import apply_provider_intent


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("card", ["translation", "backup"])
def test_same_url_preset_switch_clears_staged_model_for_auto_apply(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
    card: str,
    protocol: str,
) -> None:
    base_url = (
        "https://shared.example/v1" if protocol == "openai" else "https://shared.example/anthropic"
    )
    path = tmp_path / f"{protocol}_providers.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "providers": {
                    "first": {"label": "First", "base_url": base_url},
                    "second": {"label": "Second", "base_url": base_url},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        providers_registry,
        "ensure_providers_file",
        lambda requested="openai": path,
    )
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    variant = (
        TranslationModel.OPENAI_COMPATIBLE if protocol == "openai" else TranslationModel.ANTHROPIC
    )
    if card == "translation":
        view._on_llm_selected(variant.value)
        select = (
            view._on_openai_provider_selected
            if protocol == "openai"
            else view._on_anthropic_provider_selected
        )
        pick_model = view._pick_openai_model if protocol == "openai" else view._pick_anthropic_model
        model_control = view._openai_model if protocol == "openai" else view._anthropic_model
        draft_attr = "openai_compatible_model" if protocol == "openai" else "anthropic_model"
    else:
        view._on_backup_variant_selected(variant.value)
        select = (
            view._on_backup_provider_selected
            if protocol == "openai"
            else view._on_backup_anthropic_provider_selected
        )
        pick_model = (
            view._pick_backup_model if protocol == "openai" else view._pick_backup_anthropic_model
        )
        model_control = view._backup_model if protocol == "openai" else view._backup_anthropic_model
        draft_attr = "backup_model" if protocol == "openai" else "backup_anthropic_model"

    select("first")
    pick_model("old-model")
    select("second")

    assert model_control.value == ""
    current = view._provider_draft or view._provider_snapshot
    assert getattr(current, draft_attr) == ""

    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    if card == "translation":
        intent = (
            applied.intent.openai_compatible if protocol == "openai" else applied.intent.anthropic
        )
    else:
        backup = applied.intent.backup_translation
        intent = backup.openai_compatible if protocol == "openai" else backup.anthropic
    assert intent.model == ""
