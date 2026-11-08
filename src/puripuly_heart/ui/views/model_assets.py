"""Model manager body: download, file management and registration of STT models."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import flet as ft

from puripuly_heart.ui.flet_runtime import update_control_if_mounted
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_DIVIDER,
    COLOR_NEUTRAL_DARK,
    COLOR_PRIMARY,
    COLOR_SECONDARY,
    COLOR_SURFACE,
    COLOR_WARNING,
)

logger = logging.getLogger(__name__)

RENDER_INTERVAL_S = 0.25
TEXT_SIZE = 13
HEADER_SIZE = 12
ROW_HEIGHT = 32
_ERROR_KEYS = frozenset(
    {
        "connection_failed",
        "proxy_failed",
        "download_timeout",
        "disk_full",
        "checksum_failed",
        "file_size_mismatch",
        "missing_file",
        "invalid_file",
        "model_in_use",
        "task_busy",
        "search_failed",
        "download_failed",
    }
)
_BUSY_STATUSES = frozenset({"downloading", "importing", "deleting"})
_ORIGIN_KEYS = {
    "catalog": "model_assets.origin.catalog",
    "search": "model_assets.origin.search",
    "local": "model_assets.origin.local",
}
_SOURCE_LABELS = {"hf": "Hugging Face", "ms": "ModelScope"}
_COLUMN_FLEX = (3, 1, 2, 2)

ModelAssetsRunTask = Callable[[Callable[[], Awaitable[None]]], object]


def _status_label(entry: object) -> str:
    status = str(getattr(entry, "status", "missing"))
    percent = getattr(entry, "progress_percent", None) or 0
    if status in {"downloading", "importing"}:
        return t(f"model_assets.status.{status}", percent=percent)
    return t(f"model_assets.status.{status}")


def _size_label(size_bytes: int) -> str:
    return f"{size_bytes / 1024**3:.1f} GB"


def _failure_label(code: str | None) -> str | None:
    if not code:
        return None
    return t("model_assets.error." + (code if code in _ERROR_KEYS else "download_failed"))


class _TableRow(ft.Container):
    """One table row: four cells and a selection highlight."""

    def __init__(self, key: str, *, on_click: Callable[[str], None]) -> None:
        self.cells = [
            ft.Text(
                "",
                size=TEXT_SIZE,
                color=COLOR_NEUTRAL_DARK,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
            for _ in range(4)
        ]
        cells = [
            ft.Container(content=cell, expand=flex, padding=ft.Padding.only(right=8))
            for cell, flex in zip(self.cells, _COLUMN_FLEX, strict=True)
        ]
        super().__init__(
            content=ft.Row(controls=cells, spacing=8),
            height=ROW_HEIGHT,
            padding=ft.Padding.symmetric(horizontal=8),
            alignment=ft.Alignment.CENTER_LEFT,
            border=ft.Border.only(bottom=ft.BorderSide(1, COLOR_DIVIDER)),
            bgcolor=COLOR_SURFACE,
            on_click=lambda _event, key=key: on_click(key),
        )

    def refresh(self, values: tuple[str, str, str, str], *, selected: bool) -> None:
        for cell, value in zip(self.cells, values, strict=True):
            cell.value = value
        self.bgcolor = (
            ft.Colors.with_opacity(0.12, COLOR_PRIMARY) if selected else COLOR_SURFACE
        )


class ModelAssetsView(ft.Container):
    """Model manager: table of models, search with a repo dropdown, file import."""

    def __init__(
        self,
        page: ft.Page,
        application,
        run_task: ModelAssetsRunTask,
        *,
        picker_factory: Callable[[], object] = ft.FilePicker,
    ) -> None:
        self._page = page
        self._application = application
        self._run_task = run_task
        self._picker_factory = picker_factory
        self._picker = None
        self._is_open = False
        self._mode = "library"
        self._loading = False
        self._notice: str | None = None
        self._repos: tuple = ()
        self._selected_repo = None
        self._repo_files: tuple = ()
        self._selected: str | None = None
        self._confirm_delete: str | None = None
        self._rows: dict[str, _TableRow] = {}
        self._rows_key: tuple | None = None

        self._search_field = ft.TextField(
            hint_text=t("model_assets.search_placeholder"),
            text_size=TEXT_SIZE,
            expand=True,
            on_submit=lambda _event: self._on_search_click(),
        )
        self._find_button = ft.Button(
            content=t("model_assets.search_action"), on_click=lambda _event: self._on_search_click()
        )
        self._add_button = ft.Button(
            content=t("model_assets.action.add_file"),
            on_click=lambda _event: self._on_add_file_click(),
        )
        self._refresh_button = ft.Button(
            content=t("model_assets.action.refresh"),
            on_click=lambda _event: self._on_refresh_click(),
        )
        self._back_button = ft.Button(
            content=t("model_assets.search_back"),
            on_click=lambda _event: self._on_back_click(),
        )
        self._back_button.visible = False
        self._repo_dropdown = ft.Dropdown(
            label=t("model_assets.search_repo"),
            options=[],
            text_size=TEXT_SIZE,
            label_style=ft.TextStyle(size=HEADER_SIZE, color=COLOR_SECONDARY),
            expand=True,
            on_select=self._on_repo_selected,
        )
        self._repo_row = ft.Row(controls=[self._repo_dropdown], visible=False)
        self._notice_text = ft.Text("", size=HEADER_SIZE, color=COLOR_WARNING, max_lines=2)
        self._notice_text.visible = False

        self._install_button = ft.Button(
            content=t("model_assets.action.download"),
            on_click=lambda _event: self._on_install_click(),
            disabled=True,
        )
        self._delete_button = ft.Button(
            content=t("model_assets.action.delete"),
            on_click=lambda _event: self._on_delete_click(),
            disabled=True,
        )
        self._cancel_button = ft.Button(
            content=t("model_assets.action.cancel"),
            on_click=lambda _event: self._on_cancel_click(),
            disabled=True,
        )
        self._progress_bar = ft.ProgressBar(value=0, expand=2, height=6)
        self._progress_text = ft.Text("", size=TEXT_SIZE, color=COLOR_SECONDARY, width=48)

        header = _TableRow("__header__", on_click=lambda _key: None)
        header.refresh(
            (
                t("model_assets.column.name"),
                t("model_assets.column.size"),
                t("model_assets.column.status"),
                t("model_assets.column.source"),
            ),
            selected=False,
        )
        for cell in header.cells:
            cell.size = HEADER_SIZE
            cell.weight = ft.FontWeight.BOLD
            cell.color = COLOR_SECONDARY
        self._header = header

        self._list = ft.ListView(expand=True, spacing=0)
        super().__init__(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            self._search_field,
                            self._find_button,
                            self._add_button,
                            self._refresh_button,
                            self._back_button,
                        ],
                        spacing=8,
                    ),
                    self._repo_row,
                    self._notice_text,
                    self._header,
                    self._list,
                    ft.Row(
                        controls=[
                            self._install_button,
                            self._delete_button,
                            self._cancel_button,
                            ft.Container(expand=True),
                            self._progress_bar,
                            self._progress_text,
                        ],
                        spacing=8,
                    ),
                ],
                spacing=8,
                expand=True,
            ),
            expand=True,
        )

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self) -> None:
        if self._is_open:
            return
        self._is_open = True
        self.render()
        self._run_task(self._refresh_and_watch)

    def close(self) -> None:
        self._is_open = False

    def apply_locale(self) -> None:
        """Refresh localized labels while the window stays open."""
        self._search_field.hint_text = t("model_assets.search_placeholder")
        self._find_button.content = t("model_assets.search_action")
        self._add_button.content = t("model_assets.action.add_file")
        self._refresh_button.content = t("model_assets.action.refresh")
        self._back_button.content = t("model_assets.search_back")
        self._repo_dropdown.label = t("model_assets.search_repo")
        self._rows_key = None
        self._rows = {}
        if self._is_open:
            self.render()

    def render(self) -> None:
        self._back_button.visible = self._mode != "library"
        self._repo_row.visible = self._mode == "browse"
        self._notice_text.value = self._notice or ""
        self._notice_text.visible = bool(self._notice)
        if self._mode == "library":
            self._render_library()
        else:
            self._render_files()
        self._render_actions()
        update_control_if_mounted(self)

    def _render_library(self) -> None:
        entries = [
            entry
            for entry in self._application.model_asset_entries()
            if entry.purpose == "asr"
        ]
        rows_key = tuple(entry.model_id for entry in entries)
        if rows_key != self._rows_key:
            self._rows_key = rows_key
            self._rebuild_rows(rows_key)
        for entry in entries:
            row = self._rows.get(entry.model_id)
            if row is None:
                continue
            row.refresh(
                (
                    entry.name,
                    _size_label(int(entry.size_bytes)),
                    _status_label(entry),
                    t(_ORIGIN_KEYS.get(entry.origin, "model_assets.origin.catalog")),
                ),
                selected=self._selected == entry.model_id,
            )

    def _render_files(self) -> None:
        rows_key = tuple(file.path for file in self._repo_files)
        if rows_key != self._rows_key:
            self._rows_key = rows_key
            self._rebuild_rows(rows_key)
        repo_label = getattr(self._selected_repo, "repo_id", "")
        for file in self._repo_files:
            row = self._rows.get(file.path)
            if row is None:
                continue
            row.refresh(
                (file.name, _size_label(file.size_bytes), "—", repo_label),
                selected=self._selected == file.path,
            )

    def _rebuild_rows(self, keys: tuple[str, ...]) -> None:
        self._rows = {}
        controls: list[ft.Control] = []
        for key in keys:
            row = _TableRow(key, on_click=self._on_row_click)
            self._rows[key] = row
            controls.append(row)
        self._list.controls = controls

    def _render_actions(self) -> None:
        operation = self._application.operation_progress
        busy = operation is not None
        if operation is not None and self._selected is not None:
            if operation[1] != "delete":
                self._confirm_delete = None
        entry = self._selected_entry()
        file = self._selected_file()

        self._cancel_button.disabled = not busy
        if self._mode == "browse":
            self._install_button.content = t("model_assets.action.download")
            self._install_button.disabled = busy or file is None
            self._delete_button.disabled = True
        else:
            confirming = entry is not None and self._confirm_delete == entry.model_id
            self._install_button.content = t("model_assets.action.download")
            self._install_button.disabled = (
                busy
                or entry is None
                or entry.status in _BUSY_STATUSES
                or entry.status == "installed"
                or entry.origin != "catalog"
            )
            self._delete_button.content = t(
                "model_assets.action.delete_confirm" if confirming else "model_assets.action.delete"
            )
            self._delete_button.disabled = busy or entry is None or not self._removable(entry)

        if operation is not None and operation[2] is not None:
            self._progress_bar.value = max(0.0, min(1.0, operation[2] / 100))
            self._progress_text.value = f"{operation[2]}%"
        else:
            self._progress_bar.value = None if busy else 0
            self._progress_text.value = ""

    def _selected_entry(self):
        if self._mode != "library" or self._selected is None:
            return None
        for entry in self._application.model_asset_entries():
            if entry.model_id == self._selected:
                return entry
        return None

    def _selected_file(self):
        if self._mode != "browse" or self._selected is None:
            return None
        for file in self._repo_files:
            if file.path == self._selected:
                return file
        return None

    @staticmethod
    def _removable(entry: object) -> bool:
        if entry.status in {"installed", "invalid"}:
            return True
        return entry.status == "missing" and entry.origin != "catalog"

    def _on_row_click(self, key: str) -> None:
        self._selected = key
        self._confirm_delete = None
        self.render()

    def _on_search_click(self) -> None:
        query = (self._search_field.value or "").strip()
        if not query or self._loading:
            return
        self._loading = True
        self._notice = None

        async def task() -> None:
            try:
                repos = await self._application.search_hub(query)
            except Exception as error:
                self._notice = self._error_notice(error)
                self._repos = ()
            else:
                self._repos = repos
                self._mode = "browse"
                self._selected = None
                self._selected_repo = None
                self._repo_files = ()
                self._rows_key = None
                self._fill_repo_dropdown()
                if not repos:
                    self._notice = t("model_assets.search_empty")
            finally:
                self._loading = False
                self.render()

        self._run_task(task)
        self.render()

    def _fill_repo_dropdown(self) -> None:
        self._repo_dropdown.options = [
            ft.dropdown.Option(
                key=repo.repo_id,
                text=f"{repo.repo_id} · {_SOURCE_LABELS.get(repo.source, repo.source)}",
            )
            for repo in self._repos
        ]
        if self._repos:
            self._repo_dropdown.value = self._repos[0].repo_id
            self._load_repo_files(self._repos[0])
        else:
            self._repo_dropdown.value = None

    def _on_repo_selected(self, event) -> None:
        key = getattr(getattr(event, "control", None), "value", None)
        repo = next((item for item in self._repos if item.repo_id == key), None)
        if repo is None or self._loading:
            return
        self._load_repo_files(repo)

    def _load_repo_files(self, repo) -> None:
        self._selected_repo = repo
        self._selected = None
        self._loading = True
        self._notice = None

        async def task() -> None:
            try:
                self._repo_files = await self._application.hub_files(repo)
            except Exception as error:
                self._notice = self._error_notice(error)
                self._repo_files = ()
            finally:
                self._rows_key = None
                self._loading = False
                self.render()

        self._run_task(task)

    def _on_back_click(self) -> None:
        self._mode = "library"
        self._notice = None
        self._selected = None
        self._rows_key = None
        self.render()

    def _on_refresh_click(self) -> None:
        if self._loading:
            return
        self._run(lambda: self._application.refresh_model_assets())

    def _on_install_click(self) -> None:
        if self._mode == "browse":
            file = self._selected_file()
            repo = self._selected_repo
            if file is None or repo is None:
                return
            self._notice = None

            async def task() -> None:
                try:
                    await self._application.install_hub_file(repo, file)
                except Exception as error:
                    self._notice = self._error_notice(error)
                    self.render()
                else:
                    self._mode = "library"
                    self._selected = None
                    self._rows_key = None
                    self.render()

            self._run_task(task)
            self.render()
            return
        entry = self._selected_entry()
        if entry is None:
            return
        self._confirm_delete = None
        self._run(lambda: self._application.download(entry.model_id))

    def _on_delete_click(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if self._confirm_delete != entry.model_id:
            self._confirm_delete = entry.model_id
            self.render()
            return
        self._confirm_delete = None
        self._run(lambda: self._application.delete(entry.model_id))

    def _on_cancel_click(self) -> None:
        operation = self._application.operation_progress
        if operation is not None:
            self._run(lambda: self._application.cancel(operation[0]))

    def _on_add_file_click(self) -> None:
        if self._loading:
            return

        async def task() -> None:
            picker = self._ensure_picker()
            try:
                picked = await picker.pick_files(
                    dialog_title=t("model_assets.action.add_file"),
                    allowed_extensions=["gguf"],
                )
            except Exception:
                logger.debug("Model file picker failed", exc_info=True)
                return
            if not picked:
                return
            path = getattr(picked[0], "path", None) or picked[0].name
            if not path:
                return
            self._loading = True
            self._notice = None
            self._mode = "library"
            self._rows_key = None
            try:
                await self._application.add_local_file(Path(path))
            except Exception as error:
                self._notice = self._error_notice(error)
            finally:
                self._loading = False
                self.render()

        self._run_task(task)

    def _error_notice(self, error: BaseException) -> str:
        code = getattr(error, "failure_code", None)
        label = _failure_label(code if isinstance(code, str) else None)
        return label or _failure_label("download_failed")

    def _ensure_picker(self) -> object:
        if self._picker is None:
            self._picker = self._picker_factory()
            overlay = getattr(self._page, "overlay", None)
            if isinstance(overlay, list):
                overlay.append(self._picker)
                with contextlib.suppress(Exception):
                    self._page.update()
        return self._picker

    def _run(self, operation: Callable[[], Awaitable[object]]) -> None:
        async def task() -> None:
            try:
                await operation()
            except Exception as error:
                logger.debug("Model asset operation ended with a failure", exc_info=True)
                self._notice = self._error_notice(error)
            finally:
                if self._is_open:
                    self.render()

        self._run_task(task)
        self.render()

    async def _refresh_and_watch(self) -> None:
        try:
            await self._application.refresh_model_assets()
        except Exception:
            logger.debug("Model asset refresh failed", exc_info=True)
        while self._is_open:
            self.render()
            await asyncio.sleep(RENDER_INTERVAL_S)


__all__ = ["ModelAssetsRunTask", "ModelAssetsView"]
