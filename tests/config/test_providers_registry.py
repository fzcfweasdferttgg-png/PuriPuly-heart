from __future__ import annotations

import json
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
    monkeypatch.setattr(providers_registry, "ensure_providers_file", lambda: path)
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
