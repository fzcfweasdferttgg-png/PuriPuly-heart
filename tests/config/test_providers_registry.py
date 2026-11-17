from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from puripuly_heart.config import providers_registry


def test_provider_presets_cache_reloads_only_after_file_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "providers.json"

    def write_provider(key: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "providers": {key: {"label": key, "base_url": "http://127.0.0.1:8000/v1"}},
                }
            ),
            encoding="utf-8",
        )

    write_provider("first")
    monkeypatch.setattr(providers_registry, "ensure_providers_file", lambda protocol="openai": path)
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    reads = []
    original_read_text = Path.read_text

    def counted_read_text(self: Path, *args, **kwargs):
        if self == path:
            reads.append(self)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    assert set(providers_registry.load_provider_presets()) == {"first"}
    assert set(providers_registry.load_provider_presets()) == {"first"}
    assert len(reads) == 1

    write_provider("second-longer-key")

    assert set(providers_registry.load_provider_presets()) == {"second-longer-key"}
    assert len(reads) == 2


def test_provider_preset_add_edit_delete_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "providers.json"
    monkeypatch.setattr(providers_registry, "ensure_providers_file", lambda protocol="openai": path)
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)

    added = providers_registry.add_provider_preset("Мой сервер", "http://127.0.0.1:5000/")

    assert added.label == "Мой сервер"
    assert added.base_url == "http://127.0.0.1:5000"
    assert added.key == "custom"

    updated = providers_registry.update_provider_preset(
        added.key, "Custom name", "https://example.com/v1/"
    )

    assert updated.label == "Custom name"
    assert updated.base_url == "https://example.com/v1"
    assert providers_registry.load_provider_presets()[added.key] == updated

    deleted = providers_registry.delete_provider_preset(added.key)

    assert deleted == updated
    assert providers_registry.load_provider_presets() == {}


def test_provider_preset_validation_and_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "providers.json"
    monkeypatch.setattr(providers_registry, "ensure_providers_file", lambda protocol="openai": path)
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    added = providers_registry.add_provider_preset("Unique", "http://127.0.0.1:5000")

    with pytest.raises(providers_registry.ProviderPresetError, match="duplicate_name"):
        providers_registry.add_provider_preset("unique", "http://127.0.0.1:5001")
    with pytest.raises(providers_registry.ProviderPresetError, match="invalid_url"):
        providers_registry.add_provider_preset("Bad URL", "file:///tmp/model")
    with pytest.raises(providers_registry.ProviderPresetError, match="empty_name"):
        providers_registry.add_provider_preset("", "http://127.0.0.1:5001")

    for index in range(1, providers_registry.MAX_PROVIDER_PRESETS):
        providers_registry.add_provider_preset(f"Server {index}", "http://127.0.0.1:5000")

    with pytest.raises(providers_registry.ProviderPresetError, match="limit"):
        providers_registry.add_provider_preset("Overflow", "http://127.0.0.1:5000")

    updated = providers_registry.update_provider_preset(
        added.key, "Unique edited", "http://127.0.0.1:5002"
    )
    assert updated.base_url == "http://127.0.0.1:5002"
    providers_registry.delete_provider_preset(added.key)
    assert len(providers_registry.load_provider_presets()) == 49


def test_bundled_provider_list_contains_eight_defaults() -> None:
    path = providers_registry._bundled_providers_path()
    data = json.loads(path.read_text(encoding="utf-8"))

    assert set(data["providers"]) == {
        "openrouter",
        "deepseek",
        "google",
        "openai",
        "qwen",
        "groq",
        "mistral",
        "together",
    }


def test_anthropic_provider_presets_have_six_defaults() -> None:
    path = providers_registry._bundled_providers_path("anthropic")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert set(data["providers"]) == {
        "anthropic",
        "openrouter",
        "deepseek",
        "moonshot",
        "zhipu",
        "minimax",
    }


def test_anthropic_registry_is_independent_from_openai_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    openai_path = tmp_path / "providers.json"
    anthropic_path = tmp_path / "anthropic_providers.json"
    paths = {"openai": openai_path, "anthropic": anthropic_path}
    monkeypatch.setattr(
        providers_registry, "ensure_providers_file", lambda protocol="openai": paths[protocol]
    )
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)

    added = providers_registry.add_provider_preset(
        "Anthropic custom", "http://127.0.0.1:5000", protocol="anthropic"
    )
    providers_registry.add_provider_preset("OpenAI custom", "http://127.0.0.1:5001")

    assert added.key == "anthropic-custom"
    assert set(providers_registry.load_provider_presets("anthropic")) == {"anthropic-custom"}
    assert set(providers_registry.load_provider_presets()) == {"openai-custom"}

    providers_registry.delete_provider_preset(added.key, protocol="anthropic")

    assert providers_registry.load_provider_presets("anthropic") == {}
    assert set(providers_registry.load_provider_presets()) == {"openai-custom"}


def test_local_provider_list_contains_twelve_llm_lib_addresses() -> None:
    path = providers_registry._bundled_providers_path("local")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert {key: info["base_url"] for key, info in data["providers"].items()} == {
        "colibri": "http://127.0.0.1:8000/v1",
        "lmstudio": "http://127.0.0.1:1234/v1",
        "llamacpp": "http://127.0.0.1:8080/v1",
        "vllm": "http://127.0.0.1:8000/v1",
        "sglang": "http://127.0.0.1:30000/v1",
        "mlx": "http://127.0.0.1:8080/v1",
        "koboldcpp": "http://127.0.0.1:5001/v1",
        "textgen": "http://127.0.0.1:5000/v1",
        "tabbyapi": "http://127.0.0.1:5000/v1",
        "localai": "http://127.0.0.1:8080/v1",
        "jan": "http://127.0.0.1:1337/v1",
        "ollama": "http://127.0.0.1:11434",
    }
    assert all(info["backend"] == key for key, info in data["providers"].items())


def test_local_registry_crud_does_not_touch_other_registries(
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

    added = providers_registry.add_provider_preset(
        "My llama.cpp", "http://127.0.0.1:8081/v1", protocol="local", backend="llamacpp"
    )
    providers_registry.add_provider_preset("OpenAI custom", "http://127.0.0.1:5001")
    providers_registry.add_provider_preset(
        "Anthropic custom", "http://127.0.0.1:5002", protocol="anthropic"
    )

    assert set(providers_registry.load_provider_presets("local")) == {added.key}
    assert set(providers_registry.load_provider_presets()) == {"openai-custom"}
    assert set(providers_registry.load_provider_presets("anthropic")) == {"anthropic-custom"}

    updated = providers_registry.update_provider_preset(
        added.key, "Renamed llama.cpp", "http://127.0.0.1:8082/v1", protocol="local"
    )
    assert updated.base_url == "http://127.0.0.1:8082/v1"
    assert updated.backend == "llamacpp"
    providers_registry.delete_provider_preset(added.key, protocol="local")

    assert providers_registry.load_provider_presets("local") == {}
    assert set(providers_registry.load_provider_presets()) == {"openai-custom"}
    assert set(providers_registry.load_provider_presets("anthropic")) == {"anthropic-custom"}


def test_concurrent_registry_adds_lose_no_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "providers.json"
    monkeypatch.setattr(providers_registry, "ensure_providers_file", lambda protocol="openai": path)
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
    errors: list[str] = []
    started = threading.Barrier(8, timeout=10)

    def add(index: int) -> None:
        try:
            started.wait()
            providers_registry.add_provider_preset(
                f"Server {index}", f"http://127.0.0.1:50{index:02d}"
            )
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=add, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    providers_registry._PROVIDER_PRESETS_CACHE = None
    presets = providers_registry.load_provider_presets()

    assert not errors, f"concurrent adds raised: {errors}"
    assert len(presets) == 8, f"lost updates: only {len(presets)}/8 entries saved"
