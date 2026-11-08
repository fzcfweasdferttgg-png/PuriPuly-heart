from __future__ import annotations

import httpx
import pytest

from puripuly_heart.app.services.model_hub import (
    RepoFile,
    ModelRepo,
    ModelRepoError,
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
        ms_files = await list_gguf_files(
            ModelRepo(repo_id="x/y", source="ms", name="y"), client=client
        )

    assert hf_files == (RepoFile(path="model-Q4.gguf", size_bytes=7),)
    assert ms_files == (RepoFile(path="a/model.gguf", size_bytes=9),)


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
