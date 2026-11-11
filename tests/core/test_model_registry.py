from __future__ import annotations

import pytest

from puripuly_heart.core.local_asr.model_registry import (
    REGISTRY_STATUS_INSTALLED,
    REGISTRY_STATUS_INVALID,
    REGISTRY_STATUS_MISSING,
    ModelRegistry,
    ModelRegistryError,
    RegistryModelFile,
    RegistryModelRecord,
    sanitize_model_id,
)


def _record(model_id: str = "my-model", name: str = "My Model") -> RegistryModelRecord:
    return RegistryModelRecord(
        model_id=model_id,
        name=name,
        files=(RegistryModelFile(name="model.gguf", size_bytes=4),),
        origin="search",
        source="hf:user/repo",
    )


def test_registry_roundtrip_add_get_remove(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    assert registry.load() == ()

    registry.add(_record())
    loaded = registry.load()
    assert [record.model_id for record in loaded] == ["my-model"]
    assert loaded[0].name == "My Model"
    assert loaded[0].files == (RegistryModelFile(name="model.gguf", size_bytes=4),)
    assert loaded[0].origin == "search"
    assert registry.get("my-model") == loaded[0]

    registry.remove("my-model")
    assert registry.load() == ()


def test_allocate_model_id_avoids_collisions(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    registry.add(_record(model_id="my-model"))
    (tmp_path / "my-model-2").mkdir()

    assert registry.allocate_model_id("My Model") == "my-model-3"
    assert sanitize_model_id("Qwen3-ASR 1.7B!") == "qwen3-asr-1.7b"


def test_status_tracks_files_on_disk(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    record = _record()
    registry.add(record)

    assert registry.status(record) == REGISTRY_STATUS_MISSING

    model_dir = tmp_path / "my-model"
    model_dir.mkdir()
    (model_dir / "model.gguf").write_bytes(b"1234")
    assert registry.status(record) == REGISTRY_STATUS_INSTALLED

    (model_dir / "model.gguf").write_bytes(b"12")
    assert registry.status(record) == REGISTRY_STATUS_INVALID


def test_status_flags_zero_byte_file_without_recorded_size(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    record = RegistryModelRecord(
        model_id="zero-sized",
        name="zero-sized",
        files=(RegistryModelFile(name="model.gguf", size_bytes=0),),
        origin="search",
        source="hf:user/repo",
    )
    registry.add(record)

    model_dir = tmp_path / "zero-sized"
    model_dir.mkdir()
    (model_dir / "model.gguf").write_bytes(b"")
    assert registry.status(record) == REGISTRY_STATUS_INVALID

    (model_dir / "model.gguf").write_bytes(b"123456")
    assert registry.status(record) == REGISTRY_STATUS_INSTALLED


def test_model_file_path_points_at_the_first_file(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    record = _record()
    assert registry.model_file_path(record) == tmp_path / "my-model" / "model.gguf"


def test_corrupt_registry_is_not_silently_overwritten(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    registry.path.write_text("{truncated", encoding="utf-8")

    with pytest.raises(ModelRegistryError) as caught:
        registry.add(_record())

    assert caught.value.failure_code == "registry_corrupt"
    assert registry.path.read_text(encoding="utf-8") == "{truncated"


def test_registry_rejects_paths_outside_the_model_root(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    registry.path.write_text(
        '{"version": 1, "models": [{"model_id": "..", "files": [{"name": "model.gguf"}]}]}',
        encoding="utf-8",
    )

    with pytest.raises(ModelRegistryError):
        registry.load()


def test_registry_roundtrip_preserves_sha256(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    digest = "a" * 64
    record = RegistryModelRecord(
        model_id="hashed",
        name="Hashed",
        files=(RegistryModelFile("model.gguf", 4, digest),),
    )

    registry.add(record)

    assert registry.get("hashed").files[0].sha256 == digest


def test_allocate_model_id_avoids_reserved_catalog_ids(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)

    assert registry.allocate_model_id("qwen3-asr-1.7b-q6_k") == "qwen3-asr-1.7b-q6_k"
    assert (
        registry.allocate_model_id("qwen3-asr-1.7b-q6_k", reserved={"qwen3-asr-1.7b-q6_k"})
        == "qwen3-asr-1.7b-q6_k-2"
    )
