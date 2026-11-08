from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from puripuly_heart.app.services.model_assets import ModelAssetsError
from puripuly_heart.app.services.model_hub import RepoFile, ModelRepo
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.views.model_assets import ModelAssetsView


@dataclass(frozen=True)
class Entry:
    model_id: str
    name: str
    description_key: str
    purpose: str
    size_bytes: int
    status: str = "missing"
    progress_percent: int | None = None
    failure_code: str | None = None
    importable: bool = False
    required_files: tuple[str, ...] = ()
    origin: str = "catalog"


class Page:
    def __init__(self) -> None:
        self.overlay: list[object] = []
        self.update_calls = 0

    def update(self) -> None:
        self.update_calls += 1


class Picker:
    picked: tuple = ()

    async def pick_files(self, **_kwargs):
        return list(self.picked)


class Application:
    def __init__(self) -> None:
        self.entries = [
            Entry("asr", "Orukeet", "d.asr", "asr", 2 * 1024**3),
            Entry("gemma", "Gemma E4B", "d.gemma", "translation", 4 * 1024**3),
            Entry(
                "custom",
                "My GGUF",
                "d.custom",
                "asr",
                1024**3,
                status="installed",
                origin="local",
            ),
        ]
        self.calls: list[tuple] = []
        self.operation_progress = None
        self.repos: tuple = ()
        self.files: tuple = ()

    def model_asset_entries(self):
        return tuple(self.entries)

    async def refresh_model_assets(self):
        self.calls.append(("refresh",))
        return tuple(self.entries)

    async def download(self, model_id, *, source=None):
        self.calls.append(("download", model_id, source))

    async def delete(self, model_id):
        self.calls.append(("delete", model_id))

    async def cancel(self, model_id):
        self.calls.append(("cancel", model_id))
        return True

    async def search_hub(self, query):
        self.calls.append(("search", query))
        return self.repos

    async def hub_files(self, repo):
        self.calls.append(("files", repo.repo_id))
        return self.files

    async def install_hub_file(self, repo, file):
        self.calls.append(("install", repo.repo_id, file.path))

    async def add_local_file(self, path):
        self.calls.append(("add_local", Path(path)))

    def set(self, model_id: str, **changes) -> None:
        self.entries = [
            replace(entry, **changes) if entry.model_id == model_id else entry
            for entry in self.entries
        ]


def _texts(control) -> list[str]:
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, ft.Text) and node.value and getattr(node, "visible", True):
            found.append(node.value)
        for name in ("content", "controls", "actions"):
            child = getattr(node, name, None)
            if isinstance(child, list):
                for item in child:
                    walk(item)
            elif child is not None and hasattr(child, "__dict__"):
                walk(child)

    walk(control)
    return found


def _chips(control) -> list:
    found: list = []
    seen: set[int] = set()

    def walk(node) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if hasattr(node, "label_text"):
            found.append(node)
        for name in ("content", "controls", "actions"):
            child = getattr(node, name, None)
            if isinstance(child, list):
                for item in child:
                    walk(item)
            elif child is not None and hasattr(child, "__dict__"):
                walk(child)

    walk(control)
    return found


def _chip(control, label: str):
    for chip in _chips(control):
        if chip.label_text.value == label:
            return chip
    raise AssertionError(
        f"missing chip {label}: {[chip.label_text.value for chip in _chips(control)]}"
    )


def _labels(control) -> list[str]:
    return [chip.label_text.value for chip in _chips(control)]


def _view(app: Application, tasks: list, **kwargs) -> ModelAssetsView:
    return ModelAssetsView(Page(), app, tasks.append, **kwargs)


async def _run(tasks: list) -> None:
    while tasks:
        task = tasks.pop(0)
        if task.__name__ == "_refresh_and_watch":
            continue
        await task()


@pytest.mark.asyncio
async def test_library_lists_models_with_state_and_actions() -> None:
    app = Application()
    view = _view(app, [], on_use_model=lambda _model_id: None)
    view.open()

    texts = _texts(view._list)
    joined = " ".join(texts)
    assert "Orukeet" in texts and "My GGUF" in texts
    assert "Gemma E4B" not in texts
    assert t("model_assets.status.missing") in joined
    assert t("model_assets.status.installed") in joined
    assert t("model_assets.origin.catalog") in joined
    assert t("model_assets.origin.local") in joined
    assert _labels(view._rows["asr"]) == [t("model_assets.action.download")]
    assert _labels(view._rows["custom"]) == [
        t("model_assets.action.use"),
        t("model_assets.action.delete"),
    ]


@pytest.mark.asyncio
async def test_install_use_and_two_step_delete_call_the_application() -> None:
    app = Application()
    tasks: list = []
    used: list[str] = []
    view = _view(app, tasks, on_use_model=used.append)
    view.open()

    _chip(view._rows["asr"], t("model_assets.action.download"))._handle_click(None)
    await _run(tasks)
    assert ("download", "asr", None) in app.calls

    _chip(view._rows["custom"], t("model_assets.action.use"))._handle_click(None)
    assert used == ["custom"]

    _chip(view._rows["custom"], t("model_assets.action.delete"))._handle_click(None)
    await _run(tasks)
    assert not any(call[0] == "delete" for call in app.calls)
    _chip(view._rows["custom"], t("model_assets.action.delete_confirm"))._handle_click(None)
    await _run(tasks)
    assert ("delete", "custom") in app.calls


@pytest.mark.asyncio
async def test_active_model_shows_in_use_without_delete() -> None:
    app = Application()
    view = _view(app, [], active_model_id=lambda: "custom")
    view.open()

    assert _labels(view._rows["custom"]) == [t("model_assets.action.in_use")]
    assert _labels(view._rows["asr"]) == [t("model_assets.action.download")]


@pytest.mark.asyncio
async def test_progress_replaces_actions_while_a_model_is_installing() -> None:
    app = Application()
    app.set("asr", status="downloading", progress_percent=42)
    view = _view(app, [])
    view.open()

    texts = " ".join(_texts(view._rows["asr"]))
    assert t("model_assets.status.downloading", percent=42) in texts
    assert _labels(view._rows["asr"]) == [t("model_assets.action.cancel")]


@pytest.mark.asyncio
async def test_search_walks_repos_then_files_and_installs() -> None:
    app = Application()
    app.repos = (ModelRepo(repo_id="user/qwen3-asr", source="hf", name="qwen3-asr", downloads=10),)
    app.files = (RepoFile(path="qwen3-asr-Q6_K.gguf", size_bytes=5 * 1024**3),)
    tasks: list = []
    view = _view(app, tasks)
    view.open()

    view._search_field.value = "qwen3"
    _chip(view, t("model_assets.search_action"))._handle_click(None)
    await _run(tasks)
    assert ("search", "qwen3") in app.calls
    assert "user/qwen3-asr" in _texts(view._list)

    _chip(view._rows["user/qwen3-asr"], t("model_assets.action.open"))._handle_click(None)
    await _run(tasks)
    assert ("files", "user/qwen3-asr") in app.calls
    assert "qwen3-asr-Q6_K.gguf" in _texts(view._list)

    _chip(view._rows["qwen3-asr-Q6_K.gguf"], t("model_assets.action.download"))._handle_click(None)
    await _run(tasks)
    assert ("install", "user/qwen3-asr", "qwen3-asr-Q6_K.gguf") in app.calls
    assert view._mode == "library"


@pytest.mark.asyncio
async def test_search_failure_shows_the_notice() -> None:
    app = Application()

    async def fail(_query):
        raise ModelAssetsError("nope", failure_code="search_failed")

    app.search_hub = fail
    tasks: list = []
    view = _view(app, tasks)
    view.open()

    view._search_field.value = "x"
    _chip(view, t("model_assets.search_action"))._handle_click(None)
    await _run(tasks)

    assert t("model_assets.error.search_failed") in _texts(view)


@pytest.mark.asyncio
async def test_add_file_picks_a_gguf_and_registers_it() -> None:
    app = Application()
    tasks: list = []

    class Picker:
        async def pick_files(self, **_kwargs):
            return [SimpleNamespace(path="C:/models/own.gguf")]

    view = _view(app, tasks, picker_factory=Picker)
    view.open()

    _chip(view, t("model_assets.action.add_file"))._handle_click(None)
    await _run(tasks)

    assert ("add_local", Path("C:/models/own.gguf")) in app.calls
    assert view._mode == "library"
