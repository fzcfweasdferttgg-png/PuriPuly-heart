"""agent-4 repro (HEAD 6c26bcd1): Accept silently discards a filled-in provider
form when the persisted *active slot* holds a different variant — even when the
user never touched any slot button.

Contract (ProviderSettingsWindow docstring +
SettingsView._accept_provider_settings_dialog): "Accept button: save the slot,
commit staged form values and apply them."

The 6c26bcd1 cross-variant guard ("Accept with an activated slot of a different
variant: switch to that slot atomically") was written for the flow where the
user *explicitly picks* a foreign-variant slot inside the form
(test_provider_registry_consistency.
test_cross_variant_form_slot_accept_does_not_overwrite_slot). But it also
fires when the active slot merely *happens* to hold another variant.

Reachable through the real UI (gear cards are visible per active variant,
ui/views/settings.py _update_api_visibility):
1. Earlier, the user accepted a Local-dialog form -> slot 1 saved as
   local_llm and stays the active slot.
2. The user switches the translation card to the OpenAI variant (variant
   selector), opens the visible OpenAI gear, fills in base_url/model.
3. Presses Accept.
4. Expected: the OpenAI form is committed and applied (the dialog variant,
   which matches the card variant). Actual: the guard triggers (active slot 1
   is local_llm), the typed OpenAI form is discarded and the old local slot
   is re-applied — the variant flips back to local_llm.

Fully-typed-but-not-yet-blurred fields are lost outright (the cross-variant
path never runs the *_from_controls commits).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.provider_slots import (
    ProviderCardSlots,
    ProviderSlotConfig,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from tests.helpers.provider_settings import attach_provider_windows, create_settings_view


def test_accept_openai_form_with_stale_local_active_slot_commits_the_form(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = create_settings_view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    monkeypatch.setattr(view, "on_providers_changed", lambda: None)
    attach_provider_windows(view)

    # Slot 1 was saved by an earlier Local-dialog Accept and remains active.
    view._provider_slots_state["translation"] = ProviderCardSlots(
        active=1,
        slots=(
            ProviderSlotConfig(
                variant=TranslationModel.LOCAL_LLM.value,
                provider_key="llamacpp",
                backend="llamacpp",
                base_url="http://127.0.0.1:8080/v1",
                model="old-slot-model",
                extra_body_json="{}",
            ),
            None,
            None,
        ),
    )

    # The user switches the translation card to OpenAI and configures it.
    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._on_openai_gear_click(None)

    class _E:
        pass

    e = _E()
    view._openai_base_url.value = "https://new.example/v1"
    e.control = view._openai_base_url
    view._on_openai_base_url_change_end(e)
    view._openai_model.value = "new-openai-model"
    e.control = view._openai_model
    view._on_openai_model_change_end(e)

    view._accept_provider_settings_dialog("translation")

    display = view._provider_draft or view._provider_snapshot
    assert display.translation.model == TranslationModel.OPENAI_COMPATIBLE, (
        "Accept must commit the dialog's own variant; a stale active slot of "
        "another variant must not hijack Accept (user never touched slots)"
    )
    assert view._provider_draft.openai_compatible_model == "new-openai-model"
    assert view._provider_draft.openai_compatible_base_url == "https://new.example/v1"
    # The old local slot must not be re-applied over the form.
    assert (
        view._provider_draft.local_llm_model != "old-slot-model"
        or display.translation.model == TranslationModel.OPENAI_COMPATIBLE
    )
