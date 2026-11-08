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
            found.append(str(node.value))
        for name in ("content", "controls"):
            child = getattr(node, name, None)
            if isinstance(child, list):
                for item in child:
                    walk(item)
            elif child is not None and hasattr(child, "__dict__"):
                walk(child)

    walk(control)
    return found


def _view(app: Application, tasks: list, **kwargs) -> ModelAssetsView:
    return ModelAssetsView(Page(), app, tasks.append, **kwargs)


async def _run(tasks: list) -> None:
    while tasks:
        task = tasks.pop(0)
        if task.__name__ == "_refresh_and_watch":
            continue
        await task()


@pytest.mark.asyncio
async def test_table_lists_models_with_state_and_source() -> None:
    app = Application()
    view = _view(app, [])
    view.open()

    header = _texts(view._header)
    assert header == [
        t("model_assets.column.name"),
        t("model_assets.column.size"),
        t("model_assets.column.status"),
        t("model_assets.column.source"),
    ]
    joined = " ".join(_texts(view._list))
    assert "Orukeet" in joined and "My GGUF" in joined
    assert "Gemma E4B" not in joined
    assert "2.0 GB" in joined
    assert t("model_assets.status.missing") in joined
    assert t("model_assets.status.installed") in joined
    assert t("model_assets.origin.catalog") in joined
    assert t("model_assets.origin.local") in joined
    assert set(view._rows) == {"asr", "custom"}


@pytest.mark.asyncio
async def test_selection_drives_the_action_buttons() -> None:
    app = Application()
    tasks: list = []
    view = _view(app, tasks)
    view.open()

    assert view._install_button.disabled is True
    assert view._delete_button.disabled is True

    view._rows["asr"].on_click(None)
    assert view._install_button.disabled is False
    assert view._delete_button.disabled is True

    view._rows["custom"].on_click(None)
    assert view._install_button.disabled is True
    assert view._delete_button.disabled is False

    view._rows["asr"].on_click(None)
    view._install_button.on_click(None)
    await _run(tasks)
    assert ("download", "asr", None) in app.calls


@pytest.mark.asyncio
async def test_two_step_delete_calls_the_application() -> None:
    app = Application()
    tasks: list = []
    view = _view(app, tasks)
    view.open()

    view._rows["custom"].on_click(None)
    view._delete_button.on_click(None)
    await _run(tasks)
    assert not any(call[0] == "delete" for call in app.calls)
    assert view._delete_button.content == t("model_assets.action.delete_confirm")

    view._delete_button.on_click(None)
    await _run(tasks)
    assert ("delete", "custom") in app.calls


@pytest.mark.asyncio
async def test_busy_state_shows_cancel_and_progress() -> None:
    app = Application()
    app.set("asr", status="downloading", progress_percent=42)
    app.operation_progress = ("asr", "downloading", 42)
    tasks: list = []
    view = _view(app, tasks)
    view.open()

    assert t("model_assets.status.downloading", percent=42) in " ".join(_texts(view._rows["asr"]))
    assert view._cancel_button.disabled is False
    assert view._progress_text.value == "42%"
    assert view._install_button.disabled is True

    view._cancel_button.on_click(None)
    await _run(tasks)
    assert ("cancel", "asr") in app.calls


@pytest.mark.asyncio
async def test_search_populates_the_repo_dropdown_and_installs_files() -> None:
    app = Application()
    app.repos = (
        ModelRepo(repo_id="user/qwen3-asr", source="hf", name="qwen3-asr", downloads=10),
        ModelRepo(repo_id="user/other", source="hf", name="other"),
    )
    app.files = (RepoFile(path="qwen3-asr-Q6_K.gguf", size_bytes=5 * 1024**3),)
    tasks: list = []
    view = _view(app, tasks)
    view.open()

    view._search_field.value = "qwen3"
    view._find_button.on_click(None)
    await _run(tasks)
    assert ("search", "qwen3") in app.calls
    assert [option.key for option in view._repo_dropdown.options] == [
        "user/qwen3-asr",
        "user/other",
    ]
    assert view._repo_dropdown.value == "user/qwen3-asr"
    assert ("files", "user/qwen3-asr") in app.calls

    view._rows["qwen3-asr-Q6_K.gguf"].on_click(None)
    assert view._install_button.disabled is False
    view._install_button.on_click(None)
    await _run(tasks)
    assert ("install", "user/qwen3-asr", "qwen3-asr-Q6_K.gguf") in app.calls
    assert view._mode == "library"


@pytest.mark.asyncio
async def test_repo_dropdown_switch_loads_that_repo_files() -> None:
    app = Application()
    app.repos = (
        ModelRepo(repo_id="user/a", source="hf", name="a"),
        ModelRepo(repo_id="user/b", source="hf", name="b"),
    )
    tasks: list = []
    view = _view(app, tasks)
    view.open()

    view._search_field.value = "x"
    view._find_button.on_click(None)
    await _run(tasks)

    view._on_repo_selected(SimpleNamespace(control=SimpleNamespace(value="user/b")))
    await _run(tasks)
    assert ("files", "user/b") in app.calls


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
    view._find_button.on_click(None)
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

    view._add_button.on_click(None)
    await _run(tasks)

    assert ("add_local", Path("C:/models/own.gguf")) in app.calls
    assert view._mode == "library"


@pytest.mark.asyncio
async def test_manager_offers_no_use_actions() -> None:
    app = Application()
    view = _view(app, [])
    view.open()
    view._rows["custom"].on_click(None)

    assert t("model_assets.action.use") not in _texts(view)
    assert t("model_assets.action.in_use") not in _texts(view)
