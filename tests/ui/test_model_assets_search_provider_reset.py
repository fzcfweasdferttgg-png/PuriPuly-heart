from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from puripuly_heart.app.services.model_hub import ModelRepo, RepoFile
from puripuly_heart.core.transcribe_catalog import CatalogDownload, CatalogModel
from puripuly_heart.ui.views.model_assets import ModelAssetsView


@dataclass(frozen=True)
class Entry:
    model_id: str
    name: str = ""
    purpose: str = "asr"
    size_bytes: int = 1024
    status: str = "missing"
    progress_percent: int | None = None
    failure_code: str | None = None
    importable: bool = False
    required_files: tuple[str, ...] = ()
    origin: str = "catalog"
    files: tuple[str, ...] = ()
    source: str | None = None
    description_key: str = "d"


CATALOG = (
    CatalogModel(
        model_id="qwen3-test",
        family="test",
        role="asr",
        name="qwen3-test",
        params=2_038_078_608,
        license="Apache-2.0",
        languages=("ru", "en"),
        repo="handy-computer/qwen3-test-gguf",
        capabilities=("transcribe",),
        downloads=(CatalogDownload("Q6_K", "qwen3-test-Q6_K.gguf", 1_692_554_208),),
        accuracy={},
    ),
)


class Page:
    def update(self) -> None:
        pass


class Application:
    def __init__(self) -> None:
        self.model_operation_progress = None
        self.calls: list[tuple] = []

    def model_asset_entries(self):
        return (Entry("qwen3-test"),)

    async def search_model_repos(self, query, source):
        self.calls.append(("search", query, source))
        if source == "hf":
            return (ModelRepo(repo_id="hf-owner/hf-repo", source="hf", name="hf-repo"),)
        return (ModelRepo(repo_id="ms-owner/ms-repo", source="ms", name="ms-repo"),)

    async def model_repo_files(self, repo):
        self.calls.append(("files", repo.repo_id))
        return (RepoFile(path=f"{repo.name}-q4.gguf", size_bytes=1024),)

    async def install_model_file(self, repo, file):
        self.calls.append(("install_file", repo.source, repo.repo_id, file.path))


def _view(app: Application, tasks: list) -> ModelAssetsView:
    return ModelAssetsView(Page(), app, tasks.append, catalog=CATALOG)


async def _drain(tasks: list) -> None:
    while tasks:
        task = tasks.pop(0)
        if task.__name__ == "_refresh_and_watch":
            continue
        await task()


def _event(control):
    return SimpleNamespace(control=control)


def _select_model_scope(view: ModelAssetsView) -> None:
    view._search_provider_dropdown.value = "ms"
    view._on_provider_selected(_event(view._search_provider_dropdown))


@pytest.mark.asyncio
async def test_search_provider_switch_resets_results() -> None:
    app = Application()
    tasks: list = []
    view = _view(app, tasks)
    view.open()
    view._on_tab_click("search")
    view._search_field.value = "qwen"
    view._on_search_click()
    await _drain(tasks)

    assert view._repos and view._repos[0].source == "hf"
    assert view._selected_repo.repo_id == "hf-owner/hf-repo"
    assert view._repo_files and view._repo_files[0].name == "hf-repo-q4.gguf"

    _select_model_scope(view)
    await _drain(tasks)

    assert view._repos == ()
    assert view._selected_repo is None
    assert view._repo_files == ()
    assert view._search_repo_dropdown.options == []
    assert view._search_repo_dropdown.value is None
    assert view._selected["search"] is None
    assert set(view._rows) == set()
    assert view._install_target() is None
    assert view._search_provider == "ms"

    view._search_field.value = "qwen"
    view._on_search_click()
    await _drain(tasks)
    assert app.calls[-2] == ("search", "qwen", "ms")
    assert view._selected_repo.source == "ms"
    view._rows[view._repo_files[0].path].on_click(None)
    view._install_button.on_click(None)
    await _drain(tasks)
    assert ("install_file", "ms", "ms-owner/ms-repo", "ms-repo-q4.gguf") in app.calls


@pytest.mark.asyncio
async def test_provider_switch_cancels_pending_search_and_clears_loading() -> None:
    app = Application()
    tasks: list = []
    view = _view(app, tasks)
    started = asyncio.Event()
    release = asyncio.Event()

    async def pending_search(query, source):
        started.set()
        await release.wait()
        return (ModelRepo(repo_id="hf-owner/hf-repo", source="hf", name="hf-repo"),)

    app.search_model_repos = pending_search
    view.open()
    view._on_tab_click("search")
    view._search_field.value = "qwen"
    view._on_search_click()
    search_task = asyncio.create_task(tasks.pop()())
    await asyncio.wait_for(started.wait(), timeout=1)
    assert view._loading

    _select_model_scope(view)

    assert not view._loading
    assert view._repos == ()
    release.set()
    await search_task
    assert view._repos == ()
    assert view._selected_repo is None
    assert not view._loading
