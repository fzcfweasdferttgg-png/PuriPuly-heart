"""Repro/regression test: model manager "save folder" label vs actual install dir.

Defect (HEAD 66f9809): the Model Management window shows a concrete target
path for the selected row (`ModelAssetsView._target_path_label`,
ui/views/model_assets.py:825-844) computed as
``sanitize_model_id(Path(name).stem)``.

The install actions do NOT use that id: `ModelAssetsApplication.install_hub_file`
and `add_local_file` (app/services/model_assets.py:297-401) call
``ModelRegistry.allocate_model_id(stem, ...)`` which appends ``-2``, ``-3``...
whenever the stem-derived id is taken (registry record or existing directory).
So the displayed "concrete folder the selection would be saved into" is wrong
whenever a collision suffix is added.

Scenario: an earlier install already owns the stem-derived id ``my-model``.
Selecting a different file that sanitizes to the same id (``my-model.gguf``)
shows folder ``.../my-model`` in the UI, but the install lands in
``.../my-model-2`` and the registry records ``my-model-2``.

Expected (asserted below): the label names the folder the install actually
uses. Fails at HEAD — see logs/repro-model-install-path-label.log.

Run (from the read-only tree, sources unmodified):
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:<python-shim> python -m pytest \
    -p no:cacheprovider -o asyncio_mode=auto -q <this file>
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.services.model_assets import ModelAssetsApplication
from puripuly_heart.app.services.model_hub import ModelRepo, RepoFile
from puripuly_heart.core.local_asr.model_registry import (
    ModelRegistry,
    RegistryModelFile,
    RegistryModelRecord,
    sanitize_model_id,
)
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


class Page:
    def __init__(self) -> None:
        self.overlay: list[object] = []

    def update(self) -> None:
        pass


class Application:
    """View-facing fake; planned_model_id mirrors the real service wiring."""

    def __init__(self, files, service=None) -> None:
        self.entries = []
        self.model_operation_progress = None
        self.files = files
        self._service = service

    def model_asset_entries(self):
        return tuple(self.entries)

    async def refresh_model_assets(self):
        return tuple(self.entries)

    async def model_repo_files(self, repo):
        return self.files

    def planned_model_id(self, file_name: str) -> str:
        return self._service.planned_model_id(file_name)


def _view(app: Application, tasks: list) -> ModelAssetsView:
    return ModelAssetsView(Page(), app, tasks.append, catalog=())


async def test_target_path_label_matches_actual_install_dir(tmp_path, monkeypatch) -> None:
    root = tmp_path / "transcribe"
    registry = ModelRegistry(root)
    # An earlier install already owns the stem-derived id "my-model".
    (root / "my-model").mkdir(parents=True)
    (root / "my-model" / "My Model.gguf").write_bytes(b"old")
    registry.add(
        RegistryModelRecord(
            model_id="my-model",
            name="My Model",
            files=(RegistryModelFile(name="My Model.gguf", size_bytes=3),),
            origin="local",
        )
    )

    repo = ModelRepo(repo_id="org/other", source="hf", name="other")
    new_file = RepoFile(path="my-model.gguf", size_bytes=4)
    assert sanitize_model_id(Path(new_file.name).stem) == "my-model"

    application = ModelAssetsApplication(
        provisioning=lambda: None,
        gemma=lambda: None,
        locale=lambda: "en",
        model_root=root,
        choices=(),
    )

    # --- what the UI shows for the selected repo file ---
    tasks: list = []
    view = _view(Application((new_file,), service=application), tasks)
    view._tab = "search"  # TAB_SEARCH
    view._selected_repo = repo
    view._repo_files = (new_file,)
    view._selected["search"] = str(new_file.path)
    label = view._target_path_label()

    # --- where the install actually lands (same service instance) ---

    async def fake_download(repo_arg, file_arg, *, target_dir, cancel_event=None, on_progress=None):
        target_dir.mkdir(parents=True, exist_ok=True)
        out = target_dir / file_arg.name
        out.write_bytes(b"new!")
        return out

    monkeypatch.setattr("puripuly_heart.app.services.model_assets.hub_download_file", fake_download)
    entry = await application.install_hub_file(repo, new_file)
    assert (root / entry.model_id / "my-model.gguf").is_file()

    # Expected: the label names the folder the install really uses.
    assert Path(label).name == entry.model_id, (
        f"UI shows save folder {Path(label).name!r} " f"but install used {entry.model_id!r}"
    )
