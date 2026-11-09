from __future__ import annotations

import hashlib

import httpx
import pytest

from puripuly_heart.app.services.model_hub import (
    ModelRepo,
    ModelRepoError,
    RepoFile,
    download_file,
    list_gguf_files,
    search_repos,
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_search_repos_parses_and_ranks_by_downloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["filter"] == "gguf"
        return httpx.Response(
            200,
            json=[
                {"modelId": "a/small", "downloads": 5},
                {"modelId": "b/popular", "downloads": 500},
                {"id": "c/other"},
            ],
        )

    async with _client(handler) as client:
        repos = await search_repos("asr", client=client)

    assert [repo.repo_id for repo in repos] == ["b/popular", "a/small", "c/other"]
    assert repos[0].source == "hf"
    assert repos[0].name == "popular"


async def test_search_repos_of_blank_query_is_empty() -> None:
    assert await search_repos("   ") == ()


async def test_list_gguf_files_filters_by_extension() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "huggingface.co" in request.url.host:
            return httpx.Response(
                200,
                json=[
                    {"type": "file", "path": "model-Q4.gguf", "size": 7},
                    {"type": "file", "path": "README.md", "size": 1},
                    {"type": "directory", "path": "sub", "size": 0},
                ],
            )
        return httpx.Response(
            200,
            json={
                "Code": 200,
                "Data": {
                    "Files": [
                        {"Path": "a/model.gguf", "Size": 9, "Type": "blob"},
                        {"Path": "a", "Size": 0, "Type": "tree"},
                        {"Path": "a/tokens.txt", "Size": 2, "Type": "blob"},
                    ]
                },
            },
        )

    async with _client(handler) as client:
        hf_files = await list_gguf_files(
            ModelRepo(repo_id="x/y", source="hf", name="y"), client=client
        )

    assert hf_files == (RepoFile(path="model-Q4.gguf", size_bytes=7),)


async def test_download_file_streams_to_the_target_and_reports_progress(tmp_path) -> None:
    seen: list[tuple[int, int | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"123456")

    async with _client(handler) as client:
        path = await download_file(
            ModelRepo(repo_id="x/y", source="hf", name="y"),
            RepoFile(path="model.gguf", size_bytes=6),
            target_dir=tmp_path,
            on_progress=lambda done, total: seen.append((done, total)),
            client=client,
        )

    assert path == tmp_path / "model.gguf"
    assert path.read_bytes() == b"123456"
    assert seen[-1] == (6, 6)


async def test_download_file_rejects_a_size_mismatch(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"123")

    async with _client(handler) as client:
        with pytest.raises(ModelRepoError) as caught:
            await download_file(
                ModelRepo(repo_id="x/y", source="hf", name="y"),
                RepoFile(path="model.gguf", size_bytes=6),
                target_dir=tmp_path,
                client=client,
            )

    assert caught.value.failure_code == "file_size_mismatch"
    assert not (tmp_path / "model.gguf").exists()


async def test_download_file_honours_the_cancel_event(tmp_path) -> None:
    import asyncio

    cancel_event = asyncio.Event()
    cancel_event.set()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"123456")

    async with _client(handler) as client:
        with pytest.raises(ModelRepoError) as caught:
            await download_file(
                ModelRepo(repo_id="x/y", source="hf", name="y"),
                RepoFile(path="model.gguf", size_bytes=6),
                target_dir=tmp_path,
                cancel_event=cancel_event,
                client=client,
            )

    assert caught.value.failure_code == "cancelled"
    assert not (tmp_path / "model.gguf").exists()


async def test_ms_search_lists_owner_models_and_ranks_by_downloads(monkeypatch) -> None:
    import puripuly_heart.app.services.model_hub as model_hub

    calls = []

    def fake_list(owner, *, limit):
        calls.append((owner, limit))
        return [
            {"Id": "u/small", "Name": "small", "Downloads": 2},
            {"Id": "u/big", "Name": "big", "Downloads": 9},
            {"Id": "broken", "Name": "broken"},
        ]

    monkeypatch.setattr(model_hub, "_ms_list_models", fake_list)
    repos = await search_repos("someone", source="ms")

    assert calls == [("someone", model_hub.SEARCH_LIMIT)]
    assert [repo.repo_id for repo in repos] == ["u/big", "u/small"]
    assert repos[0].source == "ms"
    assert repos[0].name == "big"


async def test_ms_search_accepts_repo_id_and_link_without_listing(monkeypatch) -> None:
    import puripuly_heart.app.services.model_hub as model_hub

    def fail(*_args, **_kwargs):
        raise AssertionError("owner listing must not run")

    monkeypatch.setattr(model_hub, "_ms_list_models", fail)

    by_id = await search_repos("user/repo", source="ms")
    assert [repo.repo_id for repo in by_id] == ["user/repo"]
    assert by_id[0].source == "ms"

    by_link = await search_repos("https://modelscope.cn/models/user/other", source="ms")
    assert [repo.repo_id for repo in by_link] == ["user/other"]


async def test_ms_files_and_download_go_through_the_sdk(monkeypatch, tmp_path) -> None:
    import puripuly_heart.app.services.model_hub as model_hub

    monkeypatch.setattr(
        model_hub,
        "_ms_get_model_files",
        lambda repo_id: [
            {"Path": "sub/model-Q4.gguf", "Size": 8},
            {"Path": "sub", "Size": 0},
            {"Path": "sub/notes.txt", "Size": 2},
        ],
    )
    files = await list_gguf_files(ModelRepo(repo_id="u/r", source="ms", name="r"))
    assert files == (RepoFile(path="sub/model-Q4.gguf", size_bytes=8),)

    def fake_download(
        repo_id, file_path, *, file_name, file_size, local_dir, on_update, cancel_event
    ):
        assert (repo_id, file_path) == ("u/r", "sub/model-Q4.gguf")
        (local_dir / file_name).write_bytes(b"12345678")
        on_update(8, 8)

    monkeypatch.setattr(model_hub, "_ms_download_file", fake_download)
    path = await download_file(
        ModelRepo(repo_id="u/r", source="ms", name="r"),
        files[0],
        target_dir=tmp_path,
    )
    assert path.read_bytes() == b"12345678"


async def test_ms_download_cancel_is_reported(monkeypatch, tmp_path) -> None:
    import puripuly_heart.app.services.model_hub as model_hub

    def fake_download(
        repo_id, file_path, *, file_name, file_size, local_dir, on_update, cancel_event
    ):
        raise model_hub._CancelDownload()

    monkeypatch.setattr(model_hub, "_ms_download_file", fake_download)
    with pytest.raises(ModelRepoError) as caught:
        await download_file(
            ModelRepo(repo_id="u/r", source="ms", name="r"),
            RepoFile(path="m.gguf", size_bytes=4),
            target_dir=tmp_path,
        )

    assert caught.value.failure_code == "cancelled"
    assert not (tmp_path / "m.gguf").exists()


async def test_download_file_verifies_sha256(tmp_path) -> None:
    payload = b"123456"

    async with _client(lambda request: httpx.Response(200, content=payload)) as client:
        path = await download_file(
            ModelRepo(repo_id="x/y", source="hf", name="y"),
            RepoFile(
                path="model.gguf",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            ),
            target_dir=tmp_path,
            client=client,
        )

    assert path.read_bytes() == payload


async def test_download_file_rejects_a_sha256_mismatch(tmp_path) -> None:
    async with _client(lambda request: httpx.Response(200, content=b"123456")) as client:
        with pytest.raises(ModelRepoError) as caught:
            await download_file(
                ModelRepo(repo_id="x/y", source="hf", name="y"),
                RepoFile(path="model.gguf", size_bytes=6, sha256="0" * 64),
                target_dir=tmp_path,
                client=client,
            )

    assert caught.value.failure_code == "checksum_failed"
    assert not (tmp_path / "model.gguf").exists()
    assert not (tmp_path / "model.gguf.part").exists()


async def test_download_file_quotes_the_complete_repo_path(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "sub%20folder/a%23b.gguf" in request.url.raw_path.decode()
        return httpx.Response(200, content=b"x")

    async with _client(handler) as client:
        await download_file(
            ModelRepo(repo_id="x/y", source="hf", name="y"),
            RepoFile(path="sub folder/a#b.gguf", size_bytes=1),
            target_dir=tmp_path,
            client=client,
        )


async def test_download_file_rejects_unsafe_names(tmp_path) -> None:
    with pytest.raises(ModelRepoError) as caught:
        await download_file(
            ModelRepo(repo_id="x/y", source="hf", name="y"),
            RepoFile(path="..\\outside.gguf", size_bytes=1),
            target_dir=tmp_path,
        )

    assert caught.value.failure_code == "invalid_file"
