from __future__ import annotations

from pathlib import Path

import pytest

from puripuly_heart.config import providers_registry
from puripuly_heart.ui.components.provider_preset_modal import ProviderPresetModal


class FakePage:
    def __init__(self) -> None:
        self.dialog = None
        self.pop_calls = 0

    def show_dialog(self, dialog) -> None:
        self.dialog = dialog

    def pop_dialog(self) -> None:
        self.dialog = None
        self.pop_calls += 1


def _action_labels(modal: ProviderPresetModal) -> list[str]:
    body = modal.dialog.content.content.controls[-1]
    actions = body.controls[-1]
    return [button.content.value for button in actions.controls]


def test_provider_preset_modal_add_edit_and_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "providers.json"
    monkeypatch.setattr(providers_registry, "ensure_providers_file", lambda protocol="openai": path)
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    translations = {
        "settings.provider_presets.cancel": "Cancel",
        "settings.provider_presets.save": "Save",
        "settings.provider_presets.delete": "Delete server",
    }
    monkeypatch.setattr(
        "puripuly_heart.ui.components.provider_preset_modal.t",
        lambda key, **kwargs: translations.get(key, key),
    )
    page = FakePage()
    changes = []
    modal = ProviderPresetModal(page, lambda action, key: changes.append((action, key)))

    modal.open_add()
    assert page.dialog is not None
    assert _action_labels(modal) == ["Cancel", "Save"]
    modal.name_field.value = "Custom server"
    modal.url_field.value = "http://127.0.0.1:5000/"
    modal._save(None)

    assert changes == [("add", "custom-server")]
    assert page.pop_calls == 1

    modal.open_edit("custom-server")
    assert _action_labels(modal) == ["Cancel", "Save"]
    modal.name_field.value = "Renamed"
    modal.url_field.value = "https://example.com/v1"
    modal._save(None)

    assert changes[-1] == ("edit", "custom-server")
    assert providers_registry.load_provider_presets()["custom-server"].label == "Renamed"

    modal.open_delete("custom-server")
    assert _action_labels(modal) == ["Cancel", "Delete server"]
    modal._delete(None)

    assert changes[-1] == ("delete", "custom-server")
    assert providers_registry.load_provider_presets() == {}


def test_provider_preset_modal_shows_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "providers.json"
    monkeypatch.setattr(providers_registry, "ensure_providers_file", lambda protocol="openai": path)
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    page = FakePage()
    changes = []
    modal = ProviderPresetModal(page, lambda action, key: changes.append((action, key)))

    modal.open_add()
    modal.name_field.value = ""
    modal.url_field.value = "http://127.0.0.1:5000"
    modal._save(None)

    assert changes == []
    assert page.pop_calls == 0
    assert modal.dialog is not None


def test_provider_preset_modal_uses_its_explicit_protocol(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = {
        "openai": tmp_path / "providers.json",
        "anthropic": tmp_path / "anthropic_providers.json",
        "local": tmp_path / "local_providers.json",
    }
    monkeypatch.setattr(
        providers_registry, "ensure_providers_file", lambda protocol="openai": paths[protocol]
    )
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    page = FakePage()
    changes = []
    modal = ProviderPresetModal(
        page, lambda action, key: changes.append((action, key)), protocol="local"
    )

    modal.open_add()
    modal.name_field.value = "Local custom"
    modal.url_field.value = "http://127.0.0.1:11434"
    modal.backend_field.value = "ollama"
    modal._save(None)
    modal.open_edit("local-custom")
    modal.name_field.value = "Local renamed"
    modal._save(None)
    assert providers_registry.load_provider_presets("local")["local-custom"].backend == "ollama"
    modal.open_delete("local-custom")
    modal._delete(None)

    assert changes == [
        ("add", "local-custom"),
        ("edit", "local-custom"),
        ("delete", "local-custom"),
    ]
    assert providers_registry.load_provider_presets("local") == {}
    assert not paths["openai"].exists()
    assert not paths["anthropic"].exists()


def test_provider_preset_modal_delete_failure_shows_error_and_stays_open(monkeypatch) -> None:
    preset = providers_registry.ProviderPreset(
        key="custom-server", label="Custom server", base_url="http://localhost/v1"
    )
    monkeypatch.setattr(
        "puripuly_heart.ui.components.provider_preset_modal.load_provider_presets",
        lambda _protocol: {"custom-server": preset},
    )

    def fail_delete(*_args, **_kwargs):
        raise providers_registry.ProviderPresetError("write_failed")

    monkeypatch.setattr(
        "puripuly_heart.ui.components.provider_preset_modal.delete_provider_preset", fail_delete
    )
    monkeypatch.setattr(
        "puripuly_heart.ui.components.provider_preset_modal.t",
        lambda key, **kwargs: (
            "Could not save providers.json"
            if key == "settings.provider_presets.error.write_failed"
            else key
        ),
    )
    page = FakePage()
    changes = []
    modal = ProviderPresetModal(page, lambda action, key: changes.append((action, key)))
    modal.open_delete("custom-server")
    dialog = modal.dialog
    body = dialog.content.content.controls[-1]
    delete_button = body.controls[-1].controls[-1]

    delete_button.on_click(None)

    assert changes == []
    assert page.pop_calls == 0
    assert page.dialog is dialog
    assert modal._error_text is not None
    assert modal._error_text in body.controls
    assert modal._error_text.value == "Could not save providers.json"
    assert modal._error_text.visible is True
