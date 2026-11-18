from __future__ import annotations

from pathlib import Path

import pytest

from puripuly_heart.config.providers_registry import is_placeholder_base_url
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from tests.helpers.provider_settings import (
    attach_provider_windows,
    create_settings_view,
    install_provider_presets,
)


def _view(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    install_provider_presets(monkeypatch, tmp_path)
    view = create_settings_view(monkeypatch)
    return view


def test_is_placeholder_matches_preset_address_with_trailing_slash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _view(monkeypatch, tmp_path)
    assert is_placeholder_base_url("https://api.deepseek.com/v1", "openai") is True
    assert is_placeholder_base_url("https://api.deepseek.com/v1/", "openai") is True
    assert is_placeholder_base_url("https://api.deepseek.com/v2", "openai") is False


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_trailing_slash_keeps_cloud_provider_key(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
    protocol: str,
) -> None:
    view = _view(monkeypatch, tmp_path)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    attach_provider_windows(view)
    if protocol == "anthropic":
        preset_key = "anthropic"
        preset_url = "https://api.anthropic.com"
        touched_url = "https://api.anthropic.com/"
        view._on_anthropic_provider_selected(preset_key)
        view._anthropic_base_url.value = touched_url
        view._on_anthropic_base_url_change_end(
            type("Event", (), {"control": view._anthropic_base_url})()
        )
        actual = (
            view._provider_draft.anthropic_provider_key,
            view._anthropic_provider.value,
        )
    else:
        preset_key = "deepseek"
        preset_url = "https://api.deepseek.com/v1"
        touched_url = "https://api.deepseek.com/v1/"
        view._on_openai_provider_selected(preset_key)
        view._openai_base_url.value = touched_url
        view._on_openai_base_url_change_end(type("Event", (), {"control": view._openai_base_url})())
        actual = (
            view._provider_draft.openai_compatible_provider_key,
            view._openai_provider.value,
        )
    # Same address as the selected preset (modulo trailing slash): keep the key.
    assert actual == (preset_key, preset_key)
    assert is_placeholder_base_url(preset_url + "/", protocol) is True


def test_trailing_slash_keeps_local_provider_key_control(
    monkeypatch: pytest.MonkeyPatch,
    settings_events,
    tmp_path: Path,
) -> None:
    """Control case: the local handler already normalizes; behaviour must match."""
    view = _view(monkeypatch, tmp_path)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    attach_provider_windows(view)
    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    view._on_local_llm_provider_selected("llamacpp")
    view._local_llm_base_url.value = "http://127.0.0.1:8080/v1/"
    view._on_local_llm_base_url_change_end(None)
    assert view._provider_draft.local_llm_provider_key == "llamacpp"
    assert view._local_llm_provider.value == "llamacpp"
