from __future__ import annotations

from pathlib import Path

import pytest

from puripuly_heart.app.services.model_assets import ModelAssetsApplication
from puripuly_heart.core.local_asr.model_registry import ModelRegistryError
from puripuly_heart.core.transcribe_catalog import CatalogDownload, CatalogModel
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.views.model_assets import ModelAssetsView


def _model(model_id: str) -> CatalogModel:
    return CatalogModel(
        model_id=model_id,
        family="test",
        role="asr",
        name=model_id,
        params=1_000_000_000,
        license="Apache-2.0",
        languages=("ru",),
        repo=f"handy-computer/{model_id}-gguf",
        capabilities=("transcribe",),
        downloads=(CatalogDownload("Q6_K", f"{model_id}-Q6_K.gguf", 1_024**3),),
        accuracy={"ru": {"Q6_K": {"metric": "wer", "err_pct": 6.2}}},
    )


CATALOG = (_model("qwen3-test"),)


class Entry:
    def __init__(self, model_id: str, name: str, *, status: str = "installed") -> None:
        self.model_id = model_id
        self.name = name
        self.description_key = ""
        self.purpose = "asr"
        self.size_bytes = 1024**3
        self.status = status
        self.progress_percent = None
        self.failure_code = None
        self.importable = False
        self.required_files: tuple[str, ...] = ()
        self.origin = "local"
        self.files: tuple[str, ...] = (f"{model_id}.gguf",)
        self.source = None


class Page:
    def __init__(self) -> None:
        self.overlay: list[object] = []

    def update(self) -> None:
        pass


class BrokenApplication:
    def __init__(self) -> None:
        self.broken = True
        self.entries = [Entry("own", "My GGUF")]
        self.model_operation_progress = None

    def model_asset_entries(self):
        if self.broken:
            raise ModelRegistryError(
                "Model registry is unreadable or corrupt", failure_code="registry_corrupt"
            )
        return tuple(self.entries)

    async def refresh_model_assets(self):
        return self.model_asset_entries()


def _view(app: BrokenApplication) -> ModelAssetsView:
    return ModelAssetsView(Page(), app, lambda task: task, catalog=CATALOG)


def test_corrupt_registry_shows_notice_and_does_not_break_render() -> None:
    app = BrokenApplication()
    view = _view(app)

    view.open()
    view.render()

    assert view._notice_text.visible
    assert view._notice_text.value == t("model_assets.error.registry_corrupt")


def test_corrupt_registry_keeps_the_last_rendered_rows() -> None:
    app = BrokenApplication()
    view = _view(app)
    app.broken = False
    view.open()
    assert set(view._rows) == {"own"}

    app.broken = True
    view.render()

    assert set(view._rows) == {"own"}
    assert view._notice_text.value == t("model_assets.error.registry_corrupt")


def test_manager_recovers_when_the_registry_reads_again() -> None:
    app = BrokenApplication()
    view = _view(app)
    view.open()
    assert view._notice_text.visible

    app.broken = False
    view.render()

    assert not view._notice_text.visible
    assert set(view._rows) == {"own"}


def test_entries_fail_with_registry_corrupt_on_a_bad_registry_file(tmp_path: Path) -> None:
    (tmp_path / "registry.json").write_text("{ not json", encoding="utf-8")
    application = ModelAssetsApplication(
        provisioning=lambda: None,
        gemma=lambda: None,
        locale=lambda: "ru",
        model_root=tmp_path,
    )

    with pytest.raises(ModelRegistryError) as error:
        application.entries()
    assert error.value.failure_code == "registry_corrupt"
