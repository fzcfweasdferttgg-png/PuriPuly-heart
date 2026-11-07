from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.ports.settings_view import SttGpuDeviceEdit, SttLocalModelEdit
from puripuly_heart.config.provider_values import STTProviderName
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationConnection, TranslationModel
from puripuly_heart.core.local_asr.local_stt_assets import LOCAL_STT_MODEL_IDS
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import apply_provider_intent


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


def test_load_from_settings_defaults_every_choice_to_not_selected(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    not_selected = settings_view.t("settings.not_selected")
    assert view._stt_text.content.value == not_selected
    assert view._llm_text.content.value == not_selected
    assert view._stt_model_text.content.value == not_selected
    assert view._gpu_device_row.visible is True
    assert view._gpu_device_card.visible is False


def test_stt_model_picker_offers_not_selected_and_the_full_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view(monkeypatch)

    options = view._stt_model_options()

    assert [option.value for option in options] == ["", *LOCAL_STT_MODEL_IDS]
    assert options[0].label == settings_view.t("settings.not_selected")
    assert len(LOCAL_STT_MODEL_IDS) == 12


def test_stt_model_label_splits_family_and_quantization() -> None:
    label = settings_view.SettingsView._stt_model_label

    assert label("qwen3-asr-1.7b-gguf-q6_k") == "qwen3-asr-1.7b · Q6_K"
    assert label("gigaam-v3-gguf-q8_0") == "gigaam-v3 · Q8_0"
    assert label("custom-id") == "custom-id"


def test_on_stt_model_selected_stages_edit_updates_card_and_applies(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_stt_model_selected("qwen3-asr-0.6b-gguf-q8_0")

    assert view._provider_draft is not None
    assert view._provider_draft.stt_local_model == "qwen3-asr-0.6b-gguf-q8_0"
    assert view.has_provider_changes is True
    assert view._stt_model_text.content.value == "qwen3-asr-0.6b · Q8_0"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert any(isinstance(edit, SttLocalModelEdit) for edit in pending.edits)
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.stt.model == "qwen3-asr-0.6b-gguf-q8_0"


def test_on_stt_model_selected_accepts_not_selected(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_stt_model_selected("")

    assert view._provider_draft.stt_local_model == ""
    assert view._stt_model_text.content.value == settings_view.t("settings.not_selected")


def test_gpu_device_modal_offers_cpu_and_labels_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view(monkeypatch)

    options = view._gpu_column_options("auto", ())

    assert [option.value for option in options] == ["auto", "cpu"]
    assert view._gpu_column_label("cpu", ()) == settings_view.t("settings.gpu_device.cpu")
    assert view._gpu_column_label("auto", ()) == settings_view.t("settings.gpu_device.auto")


def test_on_gpu_device_selected_accepts_cpu_and_applies(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_gpu_device_selected("cpu")

    assert view._provider_draft is not None
    assert view._provider_draft.stt_gpu_device_id == "cpu"
    assert view.has_provider_changes is True
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert any(isinstance(edit, SttGpuDeviceEdit) for edit in pending.edits)
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.stt.gpu_device_id == "cpu"


def test_gpu_device_cards_follow_local_provider_selection(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    assert view._gpu_device_row.visible is True
    assert view._gpu_device_card.visible is False
    assert view._stt_model_card.visible is False
    assert view._gpu_llm_card.visible is False

    view._on_stt_selected(STTProviderName.LOCAL_QWEN_GPU.value)
    assert view._gpu_device_card.visible is True
    assert view._stt_model_card.visible is True
    assert view._gpu_llm_card.visible is True

    view._on_stt_selected(STTProviderName.NONE.value)
    assert view._gpu_device_card.visible is False
    assert view._stt_model_card.visible is False
    assert view._gpu_llm_card.visible is False


def test_on_llm_selected_local_llm_uses_ollama_connection(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)

    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.translation.model == TranslationModel.LOCAL_LLM.value
    assert applied.intent.translation.connection == TranslationConnection.OLLAMA.value


def test_on_llm_selected_custom_http_uses_custom_http_connection(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_llm_selected(TranslationModel.CUSTOM_HTTP.value)

    pending = view.build_provider_apply_settings()
    assert pending is not None
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.translation.model == TranslationModel.CUSTOM_HTTP.value
    assert applied.intent.translation.connection == TranslationConnection.CUSTOM_HTTP.value


def test_provider_selection_stages_edit_without_immediate_apply(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_stt_selected(STTProviderName.LOCAL_QWEN_GPU.value)

    assert view.has_provider_changes is True
    assert view._provider_draft is not None
    assert view._provider_draft.stt_provider == STTProviderName.LOCAL_QWEN_GPU
    assert settings_events.immediate_intents == []
    applied = apply_provider_intent(
        settings_events.settings, view.build_provider_apply_settings()
    )
    assert applied.intent.stt.provider == STTProviderName.LOCAL_QWEN_GPU.value
