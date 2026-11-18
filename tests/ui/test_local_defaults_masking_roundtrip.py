"""Default local-LLM values must survive discovery and commit round-trips.

The base URL remains visible and is resolved from the selected preset when the
stored value is empty. The model is empty by default and is not masked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import SettingsIntentRecorder, apply_provider_intent

DEFAULT_BASE_URL = "http://127.0.0.1:5001/v1"
DEFAULT_MODEL = ""


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
# D1: fetch / test connection must work for the visible default endpoint.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_local_models_with_defaults_calls_discovery(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    _load(view, settings_events, tmp_path)
    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    assert view._local_llm_base_url.value == DEFAULT_BASE_URL

    calls: list[tuple[str, str, str]] = []

    async def fetch(backend: str, base_url: str, api_key: str = "") -> list[str]:
        calls.append((backend, base_url, api_key))
        return ["only-model"]

    monkeypatch.setattr(settings_view, "_fetch_llm_lib_models_default", fetch)
    await view._on_fetch_local_llm_models(None)

    assert calls == [("koboldcpp", DEFAULT_BASE_URL, "")]


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

    assert calls == [("koboldcpp", DEFAULT_BASE_URL, "")]


# ---------------------------------------------------------------------------
# D2: default values must not raise validation errors on commit paths.
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

    view._local_llm_base_url.value = ""
    view._on_local_llm_base_url_change_end(None)
    view._local_llm_model.value = ""
    view._on_local_llm_model_change_end(None)

    assert view._local_llm_base_url.error is None
    assert view._local_llm_model.error is None
    assert view._provider_draft.local_llm_base_url == DEFAULT_BASE_URL
    assert view._provider_draft.local_llm_model == DEFAULT_MODEL
    assert view._local_llm_base_url.value == DEFAULT_BASE_URL
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
    """Explicitly empty backup fields remain invalid after preset resolution."""
    view = _view(monkeypatch)
    _load(view, settings_events, tmp_path)
    view._on_backup_variant_selected("local_llm")
    assert view._backup_local_base_url.value == DEFAULT_BASE_URL

    view._backup_local_base_url.value = ""
    view._on_backup_local_base_url_change_end(None)
    view._on_backup_local_model_change_end(None)

    assert view._backup_local_base_url.error == settings_view.t(
        "settings.local_llm.base_url.invalid"
    )
    assert view._backup_local_model.error == settings_view.t("settings.local_llm.model.required")
