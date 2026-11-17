"""agent-4 repro (HEAD 6c26bcd1): re-selecting the same provider preset in a
cloud form clears the visible model field, and Accept then commits an empty
model — silently wiping the configured model.

Chain (ui/views/settings.py):
- _on_openai_provider_selected (same for backup/anthropic/backup_anthropic):
  when the picked preset's URL equals the current URL it correctly skips
  committing an empty model, but still runs `self._openai_model.value = ""`
  unconditionally (control-only clear).
- _accept_provider_settings_dialog -> _commit_openai_fields_from_controls
  commits whatever the controls show, so the wiped control overwrites the
  staged model with "".

The local form guards the control clear with
`if selection_changed or backend_changed or url_changed` (see
_on_local_llm_provider_selected); the cloud forms lack that guard.

Expected: re-picking the currently selected preset is a no-op (model kept).
Actual: model becomes "" in the applied settings after Accept.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.providers_registry import ProviderPreset
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from tests.helpers.provider_settings import attach_provider_windows, create_settings_view

OPENROUTER = ProviderPreset(
    key="openrouter",
    label="OpenRouter",
    base_url="https://openrouter.ai/api/v1",
    backend="",
)


def _fake_load_presets(protocol: str = "openai") -> dict[str, ProviderPreset]:
    if protocol in ("", "openai", None):
        return {"openrouter": OPENROUTER}
    return {}


def test_reselect_same_preset_accept_keeps_model(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "puripuly_heart.config.providers_registry.load_provider_presets",
        _fake_load_presets,
    )
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    attach_provider_windows(view)

    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._on_openai_gear_click(None)

    class _E:
        pass

    e = _E()

    # Configure provider first, then the model.
    view._on_openai_provider_selected("openrouter")
    view._openai_model.value = "gpt-x"
    e.control = view._openai_model
    view._on_openai_model_change_end(e)

    # User re-picks the very same preset in the dropdown.
    view._on_openai_provider_selected("openrouter")

    assert (
        view._openai_model.value == "gpt-x"
    ), "re-selecting the same preset must not clear the model field"

    view._accept_provider_settings_dialog("translation")

    assert (
        view._provider_draft.openai_compatible_model == "gpt-x"
    ), "Accept must keep the model; empty model would be applied to settings"
