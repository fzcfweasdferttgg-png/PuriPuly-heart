from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.ports.settings_view import (
    PeerSttGpuDeviceEdit,
    SttGpuBackendEdit,
    SttGpuDeviceEdit,
    SttSharedEngineEdit,
)
from puripuly_heart.app.ports.ui_models import GpuDeviceOption
from puripuly_heart.config.provider_values import STTProviderName
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.settings_view import apply_provider_intent

_ENGINE_LABEL = "transcribe.cpp"


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


def test_engine_label_is_the_honest_engine_name() -> None:
    assert (
        settings_view.provider_label(STTProviderName.LOCAL_QWEN_GPU.value)
        == _ENGINE_LABEL
    )


def test_stt_engine_selected_selects_engine_and_backend(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_stt_engine_selected("cuda")

    assert view._provider_draft.stt_provider == STTProviderName.LOCAL_QWEN_GPU
    assert view._provider_draft.stt_gpu_backend == "cuda"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert any(isinstance(edit, SttGpuBackendEdit) for edit in pending.edits)
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.stt.provider == STTProviderName.LOCAL_QWEN_GPU.value
    assert applied.intent.stt.gpu_backend == "cuda"


def test_stt_engine_value_shows_engine_and_toggle_shows_backend(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_stt_engine_selected("vulkan")

    assert view._stt_text.content.value == _ENGINE_LABEL
    assert view._stt_backend_toggle_host.visible is True


def test_backend_change_resets_saved_device(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_gpu_device_selected("vulkan-index-0")

    view._on_stt_engine_selected("cpu")

    assert view._provider_draft.stt_gpu_backend == "cpu"
    assert view._provider_draft.stt_gpu_device_id == "auto"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert any(isinstance(edit, SttGpuDeviceEdit) for edit in pending.edits)
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.stt.gpu_device_id == "auto"


def test_cpu_backend_hides_self_device_card(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_stt_engine_selected("vulkan")
    assert view._gpu_device_card.visible is True

    view._on_stt_engine_selected("cpu")
    assert view._gpu_device_card.visible is False


def test_stt_device_options_filter_by_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view(monkeypatch)
    view.set_gpu_devices(
        devices=(
            GpuDeviceOption("vulkan-index-0", "Vulkan GPU", "Vulkan0", "vulkan"),
            GpuDeviceOption("cuda-index-0", "CUDA GPU", "CUDA0", "cuda"),
        )
    )

    cuda_options = view._stt_device_options("auto", view._gpu_devices, "cuda")
    vulkan_options = view._stt_device_options("auto", view._gpu_devices, "vulkan")

    assert [option.value for option in cuda_options] == ["auto", "cuda-index-0"]
    assert [option.value for option in vulkan_options] == ["auto", "vulkan-index-0"]


def test_backend_availability_greys_missing_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view(monkeypatch)
    view.set_gpu_devices(
        devices=(GpuDeviceOption("vulkan-index-0", "Vulkan GPU", "Vulkan0", "vulkan"),)
    )

    assert view._engine_backend_availability() == {
        "cpu": True,
        "vulkan": True,
        "cuda": False,
    }


def test_peer_engine_picker_options_are_kind_filtered(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view.set_gpu_devices(
        devices=(GpuDeviceOption("cuda-index-0", "CUDA GPU", "CUDA0", "cuda"),)
    )

    view._on_peer_stt_engine_selected("cuda")
    view._on_peer_gpu_device_selected("cuda-index-0")

    assert view._provider_draft.peer_stt_gpu_backend == "cuda"
    assert view._provider_draft.peer_stt_gpu_device_id == "cuda-index-0"
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert any(isinstance(edit, PeerSttGpuDeviceEdit) for edit in pending.edits)
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.peer_stt.gpu_device_id == "cuda-index-0"


def test_shared_engine_toggle_greys_peer_cards_and_mirrors_label(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    view._on_stt_engine_selected("cuda")
    view._on_peer_stt_engine_selected("vulkan")
    assert view._peer_backend_toggle_host.visible is True

    view._on_shared_engine_selected("on")

    assert view._provider_draft.stt_shared_engine is True
    assert view._peer_stt_card.opacity == 0.5
    assert view._peer_gpu_device_card.visible is False
    assert view._peer_stt_text.content.value == view._stt_text.content.value
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert any(isinstance(edit, SttSharedEngineEdit) for edit in pending.edits)
    applied = apply_provider_intent(settings_events.settings, pending)
    assert applied.intent.stt.shared_engine is True

    view._on_shared_engine_selected("off")
    assert view._peer_stt_card.opacity == 1.0


def test_backend_toggle_lives_on_the_surface(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    assert view._stt_backend_toggle_host.visible is False
    assert view._peer_backend_toggle_host.visible is False

    view._on_stt_engine_selected("cuda")
    assert view._stt_backend_toggle_host.visible is True

    view._on_peer_stt_engine_selected("vulkan")
    assert view._peer_backend_toggle_host.visible is True

    view._on_shared_engine_selected("on")
    assert view._peer_backend_toggle_host.visible is False
    assert view._stt_backend_toggle_host.visible is True
