from __future__ import annotations

from pathlib import Path

import pytest

from puripuly_heart.app.services.model_assets import ModelAssetsApplication, ModelAssetsError
from puripuly_heart.app.services.model_hub import RepoFile, ModelRepo
from puripuly_heart.core.local_asr.model_registry import ModelRegistry


def _service(tmp_path, *, active=frozenset()):
    return ModelAssetsApplication(
        provisioning=lambda: None,
        gemma=lambda: None,
        locale=lambda: "en",
        active_model_ids=lambda: active,
        model_root=tmp_path,
        choices=(),
    )


async def test_add_local_file_copies_and_registers_the_model(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    model_file = source / "My Own Model.gguf"
    model_file.write_bytes(b"gguf-data")

    entry = await service.add_local_file(model_file)

    assert entry.origin == "local"
    assert entry.status == "installed"
    installed = tmp_path / entry.model_id / "My Own Model.gguf"
    assert installed.read_bytes() == b"gguf-data"
    assert model_file.exists()
    record = ModelRegistry(tmp_path).get(entry.model_id)
    assert record is not None and record.origin == "local"
    assert record.source == str(model_file)


async def test_add_local_file_rejects_a_non_gguf_file(tmp_path) -> None:
    service = _service(tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("hello", encoding="utf-8")

    with pytest.raises(ModelAssetsError) as caught:
        await service.add_local_file(source)

    assert caught.value.failure_code == "invalid_file"
    assert ModelRegistry(tmp_path).load() == ()


async def test_registry_model_delete_removes_files_and_record(tmp_path) -> None:
    service = _service(tmp_path)
    source = tmp_path / "own.gguf"
    source.write_bytes(b"gguf-data")
    entry = await service.add_local_file(source)

    deleted = await service.delete(entry.model_id)

    assert deleted.status == "missing"
    assert not (tmp_path / entry.model_id).exists()
    assert ModelRegistry(tmp_path).load() == ()
    with pytest.raises(ModelAssetsError) as unknown:
        await service.delete(entry.model_id)
    assert unknown.value.failure_code == "unknown_model"


async def test_registry_model_delete_refuses_the_active_model(tmp_path) -> None:
    service = _service(tmp_path)
    source = tmp_path / "own.gguf"
    source.write_bytes(b"gguf-data")
    entry = await service.add_local_file(source)

    active_service = _service(tmp_path, active=frozenset({entry.model_id}))
    with pytest.raises(ModelAssetsError) as caught:
        await active_service.delete(entry.model_id)

    assert caught.value.failure_code == "model_in_use"
    assert (tmp_path / entry.model_id / "own.gguf").exists()


async def test_install_hub_file_downloads_and_registers_the_model(tmp_path, monkeypatch) -> None:
    import puripuly_heart.app.services.model_assets as module

    service = _service(tmp_path)
    calls = []

    async def fake_download(repo, file, *, target_dir, cancel_event=None, on_progress=None):
        calls.append((repo.repo_id, file.path))
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / file.name
        target.write_bytes(b"12345678")
        if on_progress is not None:
            on_progress(8, 8)
        return target

    monkeypatch.setattr(module, "hub_download_file", fake_download)

    entry = await service.install_hub_file(
        ModelRepo(repo_id="user/repo", source="hf", name="repo"),
        RepoFile(path="files/model-Q4.gguf", size_bytes=8),
    )

    assert calls == [("user/repo", "files/model-Q4.gguf")]
    assert entry.origin == "search"
    assert entry.status == "installed"
    assert entry.name == "model-Q4"
    record = ModelRegistry(tmp_path).get(entry.model_id)
    assert record is not None and record.source == "hf:user/repo"
    assert service.operation_progress is None


async def test_operation_progress_reflects_the_running_task(tmp_path) -> None:
    service = _service(tmp_path)
    assert service.operation_progress is None

    source = tmp_path / "own.gguf"
    source.write_bytes(b"gguf")
    entry = await service.add_local_file(source)
    assert service.operation_progress is None
    assert entry.size_bytes == 4
