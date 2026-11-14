"""Regression tests (agent-5): the settings surface must survive an unreadable model registry.

Contract elsewhere in the tree (tests/ui/test_model_assets_registry_corrupt.py):
ModelRegistry.load() raises ModelRegistryError(failure_code="registry_corrupt")
on unreadable data, and the model manager window degrades to a localized
notice. The settings view reads the same registry for the STT model card and
picker; it must degrade to catalog-only options and id-based labels instead of
raising.

Run against a puripuly_heart tree via PYTHONPATH (patched sandbox or pristine
sources). The suite is self-contained: it redirects the app root to a temp dir
so registry reads/writes never touch the real user data.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.services.model_assets import ModelAssetsApplication
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext, STTIntent
from puripuly_heart.core.local_asr.local_stt_assets import LOCAL_STT_MODEL_IDS
from puripuly_heart.core.local_asr.model_registry import (
    ModelRegistry,
    ModelRegistryError,
    RegistryModelFile,
    RegistryModelRecord,
)
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import SettingsIntentRecorder

CATALOG_MODEL_ID = "qwen3-asr-0.6b-gguf-q8_0"
CUSTOM_MODEL_ID = "own-model"


@pytest.fixture(autouse=True)
def app_root(tmp_path_factory, monkeypatch) -> Path:
    """Redirect the app root (and thus the model registry) to a writable temp dir."""
    root = tmp_path_factory.mktemp("app-root")
    monkeypatch.setattr("puripuly_heart.config.paths.app_root", lambda: root)
    return root


@pytest.fixture
def model_root(app_root: Path) -> Path:
    root = app_root / "var" / "data_user" / "models" / "transcribe"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _corrupt_registry(root: Path) -> None:
    (root / "registry.json").write_text("{ not json", encoding="utf-8")


def _add_custom_model(root: Path) -> None:
    ModelRegistry(root).add(
        RegistryModelRecord(
            model_id=CUSTOM_MODEL_ID,
            name="My Own GGUF",
            files=(RegistryModelFile(name="own.gguf", size_bytes=10),),
            origin="local",
        )
    )


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


# --- corrupt registry: settings must stay usable --------------------------------


def test_stt_model_options_survive_corrupt_registry(monkeypatch, model_root):
    _corrupt_registry(model_root)
    view = _view(monkeypatch)

    options = view._stt_model_options()

    assert [option.value for option in options] == ["", *LOCAL_STT_MODEL_IDS]
    assert options[0].label == t("settings.not_selected")


def test_stt_model_label_survives_corrupt_registry(model_root):
    _corrupt_registry(model_root)

    label = settings_view.SettingsView._stt_model_label

    assert label(CATALOG_MODEL_ID) == "qwen3-asr-0.6b · Q8_0"
    assert label(CUSTOM_MODEL_ID) == CUSTOM_MODEL_ID


def test_load_from_settings_survives_corrupt_registry_with_selected_model(
    monkeypatch, tmp_path, model_root
):
    _corrupt_registry(model_root)
    view = _view(monkeypatch)
    recorder = SettingsIntentRecorder()
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(settings.intent, stt=STTIntent(model=CATALOG_MODEL_ID)),
    )

    recorder.load(view, settings, config_path=tmp_path / "settings.json")

    assert view._stt_model_text.content.value == "qwen3-asr-0.6b · Q8_0"
    view._sync_gpu_device_card()


def test_on_stt_model_selected_survives_corrupt_registry(monkeypatch, tmp_path, model_root):
    from puripuly_heart.app.ports.settings_view import SttLocalModelEdit

    _corrupt_registry(model_root)
    view = _view(monkeypatch)
    recorder = SettingsIntentRecorder()
    recorder.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_stt_model_selected(CATALOG_MODEL_ID)

    assert view._provider_draft is not None
    assert view._provider_draft.stt_local_model == CATALOG_MODEL_ID
    assert view._stt_model_text.content.value == "qwen3-asr-0.6b · Q8_0"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert any(isinstance(edit, SttLocalModelEdit) for edit in pending.edits)


# --- healthy registry: previous behavior is preserved ---------------------------


def test_stt_model_options_list_custom_models_when_registry_is_healthy(
    monkeypatch, model_root
):
    _add_custom_model(model_root)
    view = _view(monkeypatch)

    options = view._stt_model_options()

    assert [option.value for option in options] == ["", *LOCAL_STT_MODEL_IDS, CUSTOM_MODEL_ID]
    assert options[-1].label == "My Own GGUF"


def test_stt_model_label_uses_registry_names_when_healthy(model_root):
    _add_custom_model(model_root)

    label = settings_view.SettingsView._stt_model_label

    assert label(CUSTOM_MODEL_ID) == "My Own GGUF"
    assert label(CATALOG_MODEL_ID) == "qwen3-asr-0.6b · Q8_0"


# --- cross-consumer contract: the model manager error contract is unchanged ------


def test_model_manager_registry_corrupt_contract_is_unchanged(model_root):
    _corrupt_registry(model_root)
    application = ModelAssetsApplication(
        provisioning=lambda: None,
        gemma=lambda: None,
        locale=lambda: "ru",
        model_root=model_root,
        choices=(),
    )

    with pytest.raises(ModelRegistryError) as error:
        application.entries()

    assert error.value.failure_code == "registry_corrupt"
