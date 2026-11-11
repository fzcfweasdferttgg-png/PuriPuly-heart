from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from puripuly_heart.app.services.model_assets import ModelAssetsError
from puripuly_heart.app.services.model_hub import ModelRepo, RepoFile
from puripuly_heart.core.transcribe_catalog import CatalogDownload, CatalogModel
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
    files: tuple[str, ...] = ()
    source: str | None = None


def _model(
    model_id: str, *, role: str = "asr", repo: str | None = None, languages=("ru", "en")
) -> CatalogModel:
    return CatalogModel(
        model_id=model_id,
        family="test",
        role=role,
        name=model_id,
        params=2_038_078_608,
        license="Apache-2.0",
        languages=tuple(languages),
        repo=repo or f"handy-computer/{model_id}-gguf",
        capabilities=("transcribe",),
        downloads=(
            CatalogDownload("Q6_K", f"{model_id}-Q6_K.gguf", 1_692_554_208),
            CatalogDownload("Q8_0", f"{model_id}-Q8_0.gguf", 2_185_030_624),
        ),
        accuracy={"ru": {"Q6_K": {"metric": "wer", "err_pct": 6.2}}},
    )


CATALOG = (
    _model("qwen3-test"),
    _model("whisper-tiny", languages=("en",)),
    _model("diar-test", role="diarize", languages=()),
    _model("vad-test", role="vad", languages=()),
)


class Page:
    def __init__(self) -> None:
        self.overlay: list[object] = []
        self.update_calls = 0

    def update(self) -> None:
        self.update_calls += 1


class Picker:
    async def pick_files(self, **_kwargs):
        return [SimpleNamespace(path="C:/models/own.gguf")]


class Application:
    def __init__(self) -> None:
        self.entries = [
            Entry(
                "asr",
                "Orukeet",
                "d",
                "asr",
                2 * 1024**3,
                status="installed",
                files=("orukeet-q6.gguf",),
                source="hf:handy-computer/qwen3-test-gguf",
            ),
            Entry("catalog-missing", "GigaAM", "d", "asr", 1024**3),
            Entry("own", "My GGUF", "d", "asr", 1024**3, status="missing", origin="local"),
            Entry(
                "busy",
                "Downloading",
                "d",
                "asr",
                1024**3,
                status="downloading",
                progress_percent=42,
            ),
        ]
        self.calls: list[tuple] = []
        self.model_operation_progress = None
        self.repos: tuple = ()
        self.files: tuple = ()

    def model_asset_entries(self):
        return tuple(self.entries)

    async def refresh_model_assets(self):
        self.calls.append(("refresh",))
        return tuple(self.entries)

    async def delete_model_asset(self, model_id):
        self.calls.append(("delete", model_id))

    async def cancel_model_asset(self, model_id):
        self.calls.append(("cancel", model_id))
        return True

    async def search_model_repos(self, query, source):
        self.calls.append(("search", query, source))
        return self.repos

    async def list_model_repos(self, owner, source):
        self.calls.append(("list", owner, source))
        return self.repos

    async def model_repo_files(self, repo):
        self.calls.append(("files", repo.repo_id))
        return self.files

    async def install_model_file(self, repo, file):
        self.calls.append(("install_file", repo.repo_id, file.path))

    async def install_catalog_model(self, model_id, quant):
        self.calls.append(("install_catalog", model_id, quant))

    async def add_model_file(self, path):
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
    kwargs.setdefault("catalog", CATALOG)
    return ModelAssetsView(Page(), app, tasks.append, **kwargs)


async def _run(tasks: list) -> None:
    while tasks:
        task = tasks.pop(0)
        if task.__name__ == "_refresh_and_watch":
            continue
        await task()


@pytest.mark.asyncio
async def test_library_tab_shows_only_current_and_downloading_models() -> None:
    app = Application()
    view = _view(app, [])
    view.open()

    assert set(view._rows) == {"asr", "own", "busy"}
    joined = " ".join(_texts(view._list))
    assert "Orukeet" in joined and "My GGUF" in joined and "Downloading" in joined
    assert "GigaAM" not in joined
    assert t("model_assets.status.installed") in joined
    assert t("model_assets.origin.local") in joined
    assert t("model_assets.origin.official") in joined


@pytest.mark.asyncio
async def test_official_tab_lists_models_including_whisper_and_filters() -> None:
    app = Application()
    view = _view(app, [])
    view.open()
    view._on_tab_click("official")

    assert set(view._rows) == {"qwen3-test", "whisper-tiny", "diar-test"}
    assert all(model.model_id != "vad-test" for model in view._catalog)
    joined = " ".join(_texts(view._list))
    assert "whisper-tiny" in joined, "whisper GGUF models stay listed"
    assert t("model_assets.role.asr") in joined
    assert "6.2%" in joined

    view._role_dropdown.value = "diarize"
    view._on_filter_changed(None)
    assert set(view._rows) == {"diar-test"}

    view._role_dropdown.value = "all"
    view._lang_dropdown.value = "en"
    view._on_filter_changed(None)
    assert set(view._rows) == {"qwen3-test", "whisper-tiny"}


def test_official_name_filter_clears_hidden_selection() -> None:
    app = Application()
    view = _view(app, [])
    view._on_tab_click("official")
    view._rows["qwen3-test"].on_click(None)
    assert view._install_target() == ("catalog", "qwen3-test", "Q6_K")

    view._name_field.value = "whisper"
    view._name_field.on_change(None)

    assert set(view._rows) == {"whisper-tiny"}
    assert view._selected_model() is None
    assert view._install_target() is None
    assert view._install_button.disabled
    view._install_button.on_click(None)
    assert not any(call[0] == "install_catalog" for call in app.calls)


@pytest.mark.asyncio
async def test_official_tab_quant_choice_and_install() -> None:
    app = Application()
    tasks: list = []
    view = _view(app, tasks)
    view.open()
    view._on_tab_click("official")

    view._rows["qwen3-test"].on_click(None)
    assert view._side_panel.visible
    assert view._details_name.value == "qwen3-test"
    assert view._details_languages.value == f"{t('model_assets.column.languages')}: ru, en"
    assert view._details_parameters.value == f"{t('model_assets.column.params')}: 2.0B"
    assert view._quant_dropdown in view._side_panel.content.controls
    assert [option.key for option in view._quant_dropdown.options] == ["Q6_K", "Q8_0"]
    assert view._quant_dropdown.value == "Q6_K"

    view._quant_dropdown.value = "Q8_0"
    view._on_quant_selected(SimpleNamespace(control=SimpleNamespace(value="Q8_0")))
    view._install_button.on_click(None)
    await _run(tasks)
    assert ("install_catalog", "qwen3-test", "Q8_0") in app.calls

    app.set("asr", files=("qwen3-test-Q6_K.gguf",))
    view._rows["qwen3-test"].on_click(None)
    joined = " ".join(_texts(view._rows["qwen3-test"]))
    assert f"{t('model_assets.status.installed')} · Q6_K" in joined


@pytest.mark.asyncio
async def test_user_repos_tab_uses_fixed_repository_and_installs_files() -> None:
    app = Application()
    app.repos = (
        ModelRepo(repo_id="fzcfweasdferttgg/asr-gguf-onnx", source="hf", name="asr-gguf-onnx"),
    )
    app.files = (
        RepoFile(path="z/z-last.gguf", size_bytes=1),
        RepoFile(path="gigaam/gigaam-Q8_0.gguf", size_bytes=1024**3),
    )
    tasks: list = []
    view = _view(app, tasks)
    view.open()
    view._on_tab_click("user_repos")

    await _run(tasks)
    assert view._user_repo_dropdown.disabled
    assert not any(call[0] == "list" for call in app.calls)
    assert ("files", "fzcfweasdferttgg/asr-gguf-onnx") in app.calls
    assert list(view._rows) == ["gigaam/gigaam-Q8_0.gguf", "z/z-last.gguf"]

    view._rows["gigaam/gigaam-Q8_0.gguf"].on_click(None)
    assert view._install_button.disabled is False
    view._install_button.on_click(None)
    await _run(tasks)
    assert (
        "install_file",
        "fzcfweasdferttgg/asr-gguf-onnx",
        "gigaam/gigaam-Q8_0.gguf",
    ) in app.calls


@pytest.mark.asyncio
async def test_files_of_uninstalled_models_are_not_marked_installed() -> None:
    app = Application()
    app.entries = [
        Entry(
            "gone",
            "Gone",
            "d",
            "asr",
            1024**3,
            status="missing",
            origin="search",
            files=("Qwen3-ASR-1.7B-Q6_K.gguf",),
            source="hf:user/repo",
        ),
        Entry(
            "ready",
            "Ready",
            "d",
            "asr",
            1024**3,
            status="installed",
            origin="search",
            files=("ready.gguf",),
            source="hf:user/repo",
        ),
    ]
    app.files = (
        RepoFile(path="Qwen3-ASR-1.7B-Q6_K.gguf", size_bytes=1),
        RepoFile(path="ready.gguf", size_bytes=1),
    )
    tasks: list = []
    view = _view(app, tasks)
    view.open()
    view._on_tab_click("user_repos")
    await _run(tasks)

    view._rows["Qwen3-ASR-1.7B-Q6_K.gguf"].on_click(None)
    assert t("model_assets.status.installed") not in _texts(view._rows["Qwen3-ASR-1.7B-Q6_K.gguf"])
    assert view._install_button.disabled is False

    view._rows["ready.gguf"].on_click(None)
    assert t("model_assets.status.installed") in _texts(view._rows["ready.gguf"])
    assert view._install_button.disabled is True


@pytest.mark.asyncio
async def test_official_tab_quant_badge_requires_installed_files() -> None:
    app = Application()
    app.entries = [
        Entry(
            "gone",
            "Gone",
            "d",
            "asr",
            1024**3,
            status="missing",
            files=("qwen3-test-Q6_K.gguf",),
        ),
    ]
    tasks: list = []
    view = _view(app, tasks)
    view.open()
    view._on_tab_click("official")
    await _run(tasks)

    view._rows["qwen3-test"].on_click(None)
    joined = " ".join(_texts(view._rows["qwen3-test"]))
    assert f"{t('model_assets.status.installed')} · Q6_K" not in joined

    app.set("gone", status="installed")
    view.render()
    joined = " ".join(_texts(view._rows["qwen3-test"]))
    assert f"{t('model_assets.status.installed')} · Q6_K" in joined


@pytest.mark.asyncio
async def test_search_tab_passes_the_selected_provider() -> None:
    app = Application()
    tasks: list = []
    view = _view(app, tasks)
    view.open()
    view._on_tab_click("search")

    view._search_provider_dropdown.value = "ms"
    view._search_field.value = "user/repo"
    view._find_button.on_click(None)
    await _run(tasks)

    assert ("search", "user/repo", "ms") in app.calls


@pytest.mark.asyncio
async def test_library_delete_is_two_step_and_cancel_targets_the_operation() -> None:
    app = Application()
    app.model_operation_progress = ("busy", "downloading", 42)
    tasks: list = []
    view = _view(app, tasks)
    view.open()
    view._rows["asr"].on_click(None)

    view._delete_button.on_click(None)
    await _run(tasks)
    assert not any(call[0] == "delete" for call in app.calls)
    assert view._delete_button.content == t("model_assets.action.delete_confirm")

    view._delete_button.on_click(None)
    await _run(tasks)
    assert ("delete", "asr") in app.calls

    assert view._cancel_button.disabled is False
    view._cancel_button.on_click(None)
    await _run(tasks)
    assert ("cancel", "busy") in app.calls


@pytest.mark.asyncio
async def test_add_file_picks_a_gguf_and_registers_it() -> None:
    app = Application()
    tasks: list = []
    view = _view(app, tasks, picker_factory=Picker)
    view.open()

    view._add_button.on_click(None)
    await _run(tasks)

    assert ("add_local", Path("C:/models/own.gguf")) in app.calls


@pytest.mark.asyncio
async def test_manager_offers_no_use_actions() -> None:
    app = Application()
    view = _view(app, [])
    view.open()
    view._rows["asr"].on_click(None)

    assert t("model_assets.action.use") not in _texts(view)
    assert t("model_assets.action.in_use") not in _texts(view)


def test_cancelled_operation_does_not_show_download_failure() -> None:
    view = _view(Application(), [])

    assert view._error_notice(ModelAssetsError("cancelled", failure_code="cancelled")) is None


@pytest.mark.asyncio
async def test_empty_states_offer_localized_guidance_and_hide_idle_progress() -> None:
    app = Application()
    app.entries = []
    view = _view(app, [])
    view.open()

    assert view._empty_text.visible
    assert view._empty_text.content.value == t("model_assets.empty.library")
    assert not view._progress_bar.visible
    assert not view._progress_text.visible

    view._on_tab_click("official")
    view._name_field.value = "no-such-model"
    view.render()
    assert view._empty_text.visible
    assert view._empty_text.content.value == t("model_assets.empty.official")

    view._on_tab_click("search")
    assert view._empty_text.content.value == t("model_assets.empty.search")


def test_registry_corruption_has_a_specific_error_message() -> None:
    view = _view(Application(), [])
    error = ModelAssetsError("corrupt", failure_code="registry_corrupt")

    assert view._error_notice(error) == t("model_assets.error.registry_corrupt")
