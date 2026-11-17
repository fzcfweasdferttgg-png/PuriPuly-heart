"""agent-5 round 2 (bb133336): masked local-LLM defaults must round-trip.

bb133336 masks the translation-local endpoint fields to look empty when the
stored settings hold the schema defaults ("http://127.0.0.1:11434",
"llama3.1:8b"); the merged test test_local_form_defaults_are_empty locks that
display in. But the consumers of those very fields still treat empty as
"invalid / not set":

- fetch-models and test-connection silently do nothing for the default local
  server (the primary workflow for the local/OpenAI-compatible server concept);
- commit-on-apply/accept treats the masked default as a validation error and
  sets field errors the user never triggered, and clearing a custom value can
  never restore the default.

Runtime contract: empty base_url falls back to the default endpoint
(wiring_llm_factory: `target.base_url or "http://127.0.0.1:11434"`), so
"empty == default" is exactly the semantic the masking encodes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import SettingsIntentRecorder, apply_provider_intent

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.1:8b"


@pytest.fixture
def settings_events() -> SettingsIntentRecorder:
    return SettingsIntentRecorder()


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


def _load(view, settings_events, tmp_path: Path) -> SettingsIntentRecorder:
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    return settings_events


async def _recording_fetch(*_args, **_kwargs):
    return ["only-model"]


# ---------------------------------------------------------------------------
# D1: fetch / test connection must work for the (masked) default endpoint.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_local_models_with_defaults_calls_discovery(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    _load(view, settings_events, tmp_path)
    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    assert view._local_llm_base_url.value == ""  # masked default

    calls: list[tuple[str, str, str]] = []

    async def fetch(backend: str, base_url: str, api_key: str = "") -> list[str]:
        calls.append((backend, base_url, api_key))
        return ["only-model"]

    monkeypatch.setattr(settings_view, "_fetch_llm_lib_models_default", fetch)
    await view._on_fetch_local_llm_models(None)

    assert calls == [("ollama", DEFAULT_BASE_URL, "")]


@pytest.mark.asyncio
async def test_test_local_connection_with_defaults_calls_probe(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    _load(view, settings_events, tmp_path)
    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)

    calls: list[tuple[str, str, str]] = []

    async def test_connection(backend: str, base_url: str, api_key: str = "") -> tuple[int, str]:
        calls.append((backend, base_url, api_key))
        return 200, "ok"

    monkeypatch.setattr(settings_view, "_test_llm_lib_connection_default", test_connection)
    await view._on_test_local_llm_connection(None)

    assert calls == [("ollama", DEFAULT_BASE_URL, "")]


# ---------------------------------------------------------------------------
# D2: masked defaults must not raise validation errors on commit paths.
# ---------------------------------------------------------------------------


def test_apply_with_default_local_fields_sets_no_field_errors(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    _load(view, settings_events, tmp_path)
    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)

    pending = view.build_provider_apply_settings()
    assert pending is not None

    assert view._local_llm_base_url.error is None
    assert view._local_llm_model.error is None
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.local_llm.base_url == DEFAULT_BASE_URL
    assert applied.intent.local_llm.model == DEFAULT_MODEL


def test_clearing_custom_local_fields_restores_defaults(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    _load(view, settings_events, tmp_path)
    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)

    # configure a custom endpoint
    view._local_llm_base_url.value = "http://127.0.0.1:2222/v1"
    view._on_local_llm_base_url_change_end(None)
    view._local_llm_model.value = "my-model"
    view._on_local_llm_model_change_end(None)
    assert view._provider_draft.local_llm_base_url == "http://127.0.0.1:2222/v1"

    # clear the fields: empty is the masked default again
    view._local_llm_base_url.value = ""
    view._on_local_llm_base_url_change_end(None)
    view._local_llm_model.value = ""
    view._on_local_llm_model_change_end(None)

    assert view._local_llm_base_url.error is None
    assert view._local_llm_model.error is None
    assert view._provider_draft.local_llm_base_url == DEFAULT_BASE_URL
    assert view._provider_draft.local_llm_model == DEFAULT_MODEL
    # display stays masked
    assert view._local_llm_base_url.value == ""
    assert view._local_llm_model.value == ""


# ---------------------------------------------------------------------------
# Guards: behavior that must NOT change.
# ---------------------------------------------------------------------------


def test_invalid_local_base_url_still_errors(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    _load(view, settings_events, tmp_path)
    view._local_llm_base_url.value = "not-a-url"
    view._on_local_llm_base_url_change_end(None)
    assert view._local_llm_base_url.error == settings_view.t("settings.local_llm.base_url.invalid")


def test_custom_local_values_still_commit_and_display(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    _load(view, settings_events, tmp_path)
    view._local_llm_base_url.value = "http://127.0.0.1:2222/v1"
    view._on_local_llm_base_url_change_end(None)
    view._local_llm_model.value = "my-model"
    view._on_local_llm_model_change_end(None)
    assert view._local_llm_base_url.value == "http://127.0.0.1:2222/v1"
    assert view._local_llm_model.value == "my-model"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.local_llm.base_url == "http://127.0.0.1:2222/v1"
    assert applied.intent.local_llm.model == "my-model"


def test_backup_local_empty_fields_keep_required_validation(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    """Backup local fields are intentionally NOT masked: empty means
    'not configured' (wiring_translation_variants refuses to build a backup
    provider from empty fields), so its validation must keep erroring."""
    view = _view(monkeypatch)
    _load(view, settings_events, tmp_path)
    view._on_backup_variant_selected("local_llm")
    assert view._backup_local_base_url.value == ""

    view._on_backup_local_base_url_change_end(None)
    view._on_backup_local_model_change_end(None)

    assert view._backup_local_base_url.error == settings_view.t(
        "settings.local_llm.base_url.invalid"
    )
    assert view._backup_local_model.error == settings_view.t("settings.local_llm.model.required")
