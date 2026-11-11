from __future__ import annotations

import pytest

from puripuly_heart.app.services.model_assets import ModelAssetsApplication, ModelAssetsError
from puripuly_heart.app.services.model_hub import ModelRepo, RepoFile
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


def _local_source(tmp_path, payload=b"gguf-data"):
    source = tmp_path / "own.gguf"
    if not source.exists():
        source.write_bytes(payload)
    return source


async def test_add_local_file_refuses_a_second_copy_of_an_installed_file(tmp_path) -> None:
    service = _service(tmp_path)
    source = _local_source(tmp_path)

    first = await service.add_local_file(source)
    with pytest.raises(ModelAssetsError) as caught:
        await service.add_local_file(source)

    assert caught.value.failure_code == "already_installed"
    records = ModelRegistry(tmp_path).load()
    assert [record.model_id for record in records] == [first.model_id]
    copies = [path for path in tmp_path.rglob("own.gguf") if path != source]
    assert len(copies) == 1
    assert source.read_bytes() == b"gguf-data"


async def test_add_local_file_allows_readding_after_delete(tmp_path) -> None:
    service = _service(tmp_path)
    source = _local_source(tmp_path)

    first = await service.add_local_file(source)
    await service.delete(first.model_id)
    second = await service.add_local_file(source)

    assert second.status == "installed"
    assert ModelRegistry(tmp_path).get(second.model_id) is not None


async def test_missing_record_does_not_block_readding_the_file(tmp_path) -> None:
    service = _service(tmp_path)
    source = _local_source(tmp_path)

    first = await service.add_local_file(source)
    (tmp_path / first.model_id / "own.gguf").unlink()
    second = await service.add_local_file(source)

    assert second.model_id != first.model_id
    assert second.status == "installed"


async def test_invalid_record_does_not_block_readding_the_file(tmp_path) -> None:
    service = _service(tmp_path)
    source = _local_source(tmp_path)

    first = await service.add_local_file(source)
    installed = tmp_path / first.model_id / "own.gguf"
    installed.write_bytes(b"x")
    await service.refresh()
    assert service.entry(first.model_id).status == "invalid"

    second = await service.add_local_file(source)

    assert second.model_id != first.model_id
    assert second.status == "installed"


async def test_install_hub_file_refuses_a_second_copy_of_an_installed_file(
    tmp_path, monkeypatch
) -> None:
    import puripuly_heart.app.services.model_assets as module

    service = _service(tmp_path)

    async def fake_download(repo, file, *, target_dir, cancel_event=None, on_progress=None):
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / file.name
        target.write_bytes(b"12345678")
        return target

    monkeypatch.setattr(module, "hub_download_file", fake_download)
    repo = ModelRepo(repo_id="user/repo", source="hf", name="repo")
    remote = RepoFile(path="files/model-Q4.gguf", size_bytes=8)

    first = await service.install_hub_file(repo, remote)
    with pytest.raises(ModelAssetsError) as caught:
        await service.install_hub_file(repo, remote)

    assert caught.value.failure_code == "already_installed"
    records = ModelRegistry(tmp_path).load()
    assert [record.model_id for record in records] == [first.model_id]

    (tmp_path / first.model_id / "model-Q4.gguf").write_bytes(b"bad")
    second = await service.install_hub_file(repo, remote)
    assert second.model_id != first.model_id
    assert second.status == "installed"


def test_already_installed_message_is_localized_and_mapped() -> None:
    from puripuly_heart.ui.i18n import t
    from puripuly_heart.ui.views.model_assets import _ERROR_KEYS, _failure_label

    assert "already_installed" in _ERROR_KEYS
    assert _failure_label("already_installed") == t("model_assets.error.already_installed")
    assert _failure_label("already_installed") != t("model_assets.error.download_failed")
