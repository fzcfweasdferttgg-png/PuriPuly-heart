from __future__ import annotations

import asyncio
import threading

import pytest

from puripuly_heart.app.services.model_assets import ModelAssetsApplication, ModelAssetsError
from puripuly_heart.core.local_asr.model_registry import ModelRegistry
from puripuly_heart.core.model_install_removal import remove_interrupted_install_residue


def _service(tmp_path, *, choices=()):
    return ModelAssetsApplication(
        provisioning=lambda: None,
        gemma=lambda: None,
        locale=lambda: "en",
        model_root=tmp_path,
        choices=choices,
    )


def test_removes_part_files_and_empty_unregistered_dirs(tmp_path) -> None:
    half = tmp_path / "half"
    half.mkdir()
    (half / "model.gguf.part").write_bytes(b"xx")
    empty = tmp_path / "empty"
    empty.mkdir()

    removed = remove_interrupted_install_residue(tmp_path)

    assert not half.exists() and not empty.exists()
    assert {path.name for path in removed} == {"model.gguf.part", "half", "empty"}


def test_keeps_registered_models_and_their_contents(tmp_path) -> None:
    known = tmp_path / "known"
    known.mkdir()
    (known / "model.gguf").write_bytes(b"data")
    (known / "model.gguf.part").write_bytes(b"xx")

    remove_interrupted_install_residue(tmp_path, keep={"known"})

    assert (known / "model.gguf").read_bytes() == b"data"
    assert (known / "model.gguf.part").exists()


def test_keeps_staging_deleting_and_backup_directories(tmp_path) -> None:
    for name in ("gigaam.staging-1", "gigaam.deleting-2", "gigaam.backup"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "model.gguf.part").write_bytes(b"xx")

    remove_interrupted_install_residue(tmp_path)

    for name in ("gigaam.staging-1", "gigaam.deleting-2", "gigaam.backup"):
        assert (tmp_path / name / "model.gguf.part").exists()


def test_directory_with_unknown_files_is_kept(tmp_path) -> None:
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    (mixed / "notes.txt").write_text("mine", encoding="utf-8")
    (mixed / "model.gguf.part").write_bytes(b"xx")

    remove_interrupted_install_residue(tmp_path)

    assert (mixed / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert not (mixed / "model.gguf.part").exists()


def test_removes_only_registry_temp_files(tmp_path) -> None:
    (tmp_path / ".registry.json.deadbeef.tmp").write_text("{}", encoding="utf-8")
    (tmp_path / "registry.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".other.json.tmp").write_text("{}", encoding="utf-8")

    remove_interrupted_install_residue(tmp_path)

    assert not (tmp_path / ".registry.json.deadbeef.tmp").exists()
    assert (tmp_path / "registry.json").is_file()
    assert (tmp_path / ".other.json.tmp").is_file()


async def test_refresh_cleans_residue_and_frees_the_model_id(tmp_path) -> None:
    half = tmp_path / "qwen3-test-q6_k"
    half.mkdir()
    (half / "qwen3-test-Q6_K.gguf.part").write_bytes(b"xx")
    (tmp_path / ".registry.json.deadbeef.tmp").write_text("{}", encoding="utf-8")

    service = _service(tmp_path)
    await service.refresh()

    assert list(tmp_path.iterdir()) == []
    assert ModelRegistry(tmp_path).allocate_model_id("qwen3-test-q6_k") == "qwen3-test-q6_k"
    assert service.entries() == ()


async def test_refresh_keeps_registered_models_installed(tmp_path) -> None:
    service = _service(tmp_path)
    source = tmp_path / "own.gguf"
    source.write_bytes(b"gguf-data")
    entry = await service.add_local_file(source)
    (tmp_path / entry.model_id / "leftover.gguf.part").write_bytes(b"stale")

    entries = await service.refresh()

    refreshed = next(item for item in entries if item.model_id == entry.model_id)
    assert refreshed.status == "installed"
    assert (tmp_path / entry.model_id / "own.gguf").read_bytes() == b"gguf-data"


async def test_refresh_does_not_sweep_while_an_operation_runs(tmp_path, monkeypatch) -> None:
    import puripuly_heart.app.services.model_assets as module

    service = _service(tmp_path)
    residue = tmp_path / "half"
    residue.mkdir()
    (residue / "model.gguf.part").write_bytes(b"xx")

    sweeping = threading.Event()
    release = threading.Event()
    real_sweep = module.remove_interrupted_install_residue

    def gated_sweep(root, *, keep=()):
        sweeping.set()
        release.wait(timeout=10)
        return real_sweep(root, keep=keep)

    monkeypatch.setattr(module, "remove_interrupted_install_residue", gated_sweep)

    refresh_task = asyncio.create_task(service.refresh())
    assert await asyncio.to_thread(sweeping.wait, 10)

    source = tmp_path / "own.gguf"
    source.write_bytes(b"gguf-data")
    with pytest.raises(ModelAssetsError) as caught:
        await service.add_local_file(source)
    assert caught.value.failure_code == "task_busy"

    release.set()
    await refresh_task
    assert not residue.exists()


async def test_operation_does_not_start_while_residue_sweep_runs(tmp_path, monkeypatch) -> None:
    import puripuly_heart.app.services.model_assets as module

    service = _service(tmp_path)
    source = tmp_path / "own.gguf"
    source.write_bytes(b"gguf-data")
    entry = await service.add_local_file(source)

    sweeping = threading.Event()
    release = threading.Event()
    real_sweep = module.remove_interrupted_install_residue

    def gated_sweep(root, *, keep=()):
        sweeping.set()
        release.wait(timeout=10)
        return real_sweep(root, keep=keep)

    monkeypatch.setattr(module, "remove_interrupted_install_residue", gated_sweep)

    refresh_task = asyncio.create_task(service.refresh())
    assert await asyncio.to_thread(sweeping.wait, 10)

    other_source = tmp_path / "other.gguf"
    other_source.write_bytes(b"other")
    with pytest.raises(ModelAssetsError) as caught:
        await service.add_local_file(other_source)
    assert caught.value.failure_code == "task_busy"

    release.set()
    await refresh_task
    refreshed = next(item for item in service.entries() if item.model_id == entry.model_id)
    assert refreshed.status == "installed"
