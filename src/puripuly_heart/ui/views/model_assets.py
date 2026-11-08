"""Model library body: built-in catalog, online search and local GGUF files."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import flet as ft

from puripuly_heart.ui.components.modal_kit import CARD_SPACING
from puripuly_heart.ui.flet_runtime import is_hover_active, update_control_if_mounted
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
_NOTICE_TEXT_SIZE = 14
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


class _ActionChip(ft.Container):
    """Small action button used on model rows."""

    def __init__(
        self,
        label: str,
        *,
        on_click: Callable[[], None],
        primary: bool = False,
        enabled: bool = True,
    ) -> None:
        self._on_click = on_click
        self._enabled = enabled
        self._hovered = False
        self._primary = primary
        self.label_text = ft.Text(
            label,
            size=15,
            weight=ft.FontWeight.BOLD,
            text_align=ft.TextAlign.CENTER,
            max_lines=1,
        )
        super().__init__(
            content=self.label_text,
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            border_radius=10,
            border=ft.Border.all(1, COLOR_DIVIDER),
            alignment=ft.Alignment.CENTER,
            on_click=self._handle_click,
            on_hover=self._handle_hover,
        )
        self._paint()

    def _handle_click(self, _event) -> None:
        if self._enabled:
            self._on_click()

    def _handle_hover(self, event) -> None:
        self._hovered = is_hover_active(event)
        self._paint()
        with contextlib.suppress(Exception):
            self.update()

    def _paint(self) -> None:
        self.disabled = not self._enabled
        if self._primary and self._enabled:
            self.bgcolor = COLOR_PRIMARY
            self.label_text.color = ft.Colors.WHITE
            return
        self.bgcolor = COLOR_SURFACE
        if not self._enabled:
            self.label_text.color = ft.Colors.with_opacity(0.35, COLOR_NEUTRAL_DARK)
            return
        self.label_text.color = COLOR_PRIMARY if self._hovered else COLOR_NEUTRAL_DARK


class _ModelRow(ft.Container):
    """One model (or one source file): name, detail line and action chips."""

    def __init__(self, name: str) -> None:
        self.title_text = ft.Text(
            name,
            size=18,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL_DARK,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.detail_text = ft.Text(
            "",
            size=14,
            color=COLOR_SECONDARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.actions = ft.Row(spacing=8, alignment=ft.MainAxisAlignment.END)
        super().__init__(
            content=ft.Row(
                [
                    ft.Column([self.title_text, self.detail_text], spacing=4, expand=True),
                    self.actions,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            padding=ft.Padding.symmetric(horizontal=20, vertical=12),
            border_radius=16,
            bgcolor=COLOR_SURFACE,
            border=ft.Border.all(1, COLOR_DIVIDER),
        )
        self._actions_signature: tuple | None = None

    def refresh(
        self,
        *,
        detail: str,
        actions_signature: tuple,
        build_actions: Callable[[], list],
    ) -> None:
        self.detail_text.value = detail
        if actions_signature != self._actions_signature:
            self._actions_signature = actions_signature
            self.actions.controls = build_actions()


class ModelAssetsView(ft.Container):
    """STT model library: catalog, online search and local GGUF files."""

    def __init__(
        self,
        page: ft.Page,
        application,
        run_task: ModelAssetsRunTask,
        *,
        on_use_model: Callable[[str], None] | None = None,
        active_model_id: Callable[[], str] | None = None,
        picker_factory: Callable[[], object] = ft.FilePicker,
    ) -> None:
        self._page = page
        self._application = application
        self._run_task = run_task
        self._on_use_model = on_use_model
        self._active_model_id = active_model_id
        self._picker_factory = picker_factory
        self._picker = None
        self._is_open = False
        self._mode = "library"
        self._loading = False
        self._notice: str | None = None
        self._repos: tuple = ()
        self._selected_repo = None
        self._repo_files: tuple = ()
        self._confirm_delete: str | None = None
        self._install_key: str | None = None
        self._rows: dict[str, _ModelRow] = {}
        self._rows_signature: tuple | None = None

        self._search_field = ft.TextField(
            hint_text=t("model_assets.search_placeholder"),
            expand=True,
            on_submit=lambda _e: self._on_search_click(),
        )
        self._search_button = _ActionChip(
            t("model_assets.search_action"), on_click=self._on_search_click, primary=True
        )
        self._add_button = _ActionChip(
            t("model_assets.action.add_file"), on_click=self._on_add_file_click
        )
        self._back_button = _ActionChip(
            t("model_assets.search_back"), on_click=self._on_back_click
        )
        self._back_button.visible = False
        self._notice_text = ft.Text(
            "",
            size=_NOTICE_TEXT_SIZE,
            color=COLOR_WARNING,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._list = ft.Column(
            spacing=CARD_SPACING,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        super().__init__(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            self._search_field,
                            self._search_button,
                            self._add_button,
                            self._back_button,
                        ],
                        spacing=12,
                    ),
                    self._notice_text,
                    self._list,
                ],
                spacing=16,
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
        self._search_button.label_text.value = t("model_assets.search_action")
        self._add_button.label_text.value = t("model_assets.action.add_file")
        self._back_button.label_text.value = t("model_assets.search_back")
        self._rows_signature = None
        self._rows = {}
        if self._is_open:
            self.render()

    def render(self) -> None:
        self._back_button.visible = self._mode != "library"
        self._notice_text.value = self._notice or ""
        self._notice_text.visible = bool(self._notice)
        if self._mode == "library":
            self._render_library()
        else:
            self._render_browse()
        update_control_if_mounted(self)

    def _render_library(self) -> None:
        entries = [
            entry
            for entry in self._application.model_asset_entries()
            if entry.purpose == "asr"
        ]
        signature = tuple(
            (
                entry.model_id,
                entry.status,
                entry.origin,
                self._confirm_delete == entry.model_id,
                self._is_active(entry.model_id),
            )
            for entry in entries
        )
        if signature != self._rows_signature:
            self._rows_signature = signature
            self._rows = {}
            controls: list[ft.Control] = []
            for entry in entries:
                row = _ModelRow(entry.name)
                self._rows[entry.model_id] = row
                controls.append(row)
            self._list.controls = controls
        for entry in entries:
            row = self._rows.get(entry.model_id)
            if row is None:
                continue
            row.refresh(
                detail=self._library_detail(entry),
                actions_signature=signature[self._index_of(entries, entry.model_id)],
                build_actions=lambda entry=entry: self._library_actions(entry),
            )

    @staticmethod
    def _index_of(entries, model_id: str) -> int:
        for index, entry in enumerate(entries):
            if entry.model_id == model_id:
                return index
        return -1

    def _library_detail(self, entry: object) -> str:
        parts = [
            _size_label(int(entry.size_bytes)),
            _status_label(entry),
            t(_ORIGIN_KEYS.get(entry.origin, "model_assets.origin.catalog")),
        ]
        failure = _failure_label(getattr(entry, "failure_code", None))
        if failure and entry.status not in _BUSY_STATUSES:
            parts.append(failure)
        return " · ".join(parts)

    def _library_actions(self, entry: object) -> list:
        chips: list[_ActionChip] = []
        status = entry.status
        if status in _BUSY_STATUSES:
            if status != "deleting":
                chips.append(
                    _ActionChip(
                        t("model_assets.action.cancel"),
                        on_click=lambda: self._on_cancel(entry.model_id),
                    )
                )
            return chips
        active = self._is_active(entry.model_id)
        if status != "installed" and entry.origin == "catalog":
            chips.append(
                _ActionChip(
                    t("model_assets.action.download"),
                    on_click=lambda: self._on_install(entry.model_id),
                )
            )
        if status == "installed":
            if active:
                chips.append(
                    _ActionChip(
                        t("model_assets.action.in_use"),
                        on_click=lambda: None,
                        primary=True,
                    )
                )
            elif self._on_use_model is not None:
                chips.append(
                    _ActionChip(
                        t("model_assets.action.use"),
                        on_click=lambda: self._on_use(entry.model_id),
                        primary=True,
                    )
                )
        removable = status in {"installed", "invalid"} or (
            status == "missing" and entry.origin != "catalog"
        )
        if removable and not active:
            confirming = self._confirm_delete == entry.model_id
            chips.append(
                _ActionChip(
                    t(
                        "model_assets.action.delete_confirm"
                        if confirming
                        else "model_assets.action.delete"
                    ),
                    on_click=lambda: self._on_delete(entry.model_id),
                )
            )
        return chips

    def _render_browse(self) -> None:
        operation = self._application.operation_progress
        if self._install_key is not None and operation is None:
            self._install_key = None
        if self._mode == "results":
            signature = (
                "results",
                self._loading,
                tuple((repo.repo_id, repo.source) for repo in self._repos),
            )
        else:
            signature = (
                "files",
                self._loading,
                getattr(self._selected_repo, "repo_id", ""),
                tuple(file.path for file in self._repo_files),
                self._install_key,
            )
        if signature != self._rows_signature:
            self._rows_signature = signature
            self._rows = {}
            controls = []
            if self._mode == "results":
                for repo in self._repos:
                    row = _ModelRow(repo.repo_id)
                    self._rows[repo.repo_id] = row
                    controls.append(row)
            else:
                for file in self._repo_files:
                    row = _ModelRow(file.name)
                    self._rows[file.path] = row
                    controls.append(row)
            self._list.controls = controls
        if self._mode == "results":
            for repo in self._repos:
                row = self._rows.get(repo.repo_id)
                if row is None:
                    continue
                row.refresh(
                    detail=_SOURCE_LABELS.get(repo.source, repo.source),
                    actions_signature=(self._loading,),
                    build_actions=lambda repo=repo: [
                        _ActionChip(
                            t("model_assets.action.open"),
                            on_click=lambda repo=repo: self._on_open_repo(repo),
                            enabled=not self._loading,
                        )
                    ],
                )
            return
        for file in self._repo_files:
            row = self._rows.get(file.path)
            if row is None:
                continue
            installing = self._install_key == file.path
            percent = operation[2] if (installing and operation is not None) else None
            detail = _size_label(file.size_bytes)
            if installing:
                detail += " · " + (
                    t("model_assets.status.downloading", percent=percent or 0)
                )
            row.refresh(
                detail=detail,
                actions_signature=(installing, self._loading),
                build_actions=lambda file=file, installing=installing: self._file_actions(
                    file, installing
                ),
            )

    def _file_actions(self, file, installing: bool) -> list:
        if installing:
            operation = self._application.operation_progress
            target = operation[0] if operation is not None else None
            return [
                _ActionChip(
                    t("model_assets.action.cancel"),
                    on_click=lambda: self._on_cancel(target),
                    enabled=target is not None,
                )
            ]
        return [
            _ActionChip(
                t("model_assets.action.download"),
                on_click=lambda: self._on_install_file(file),
                enabled=not self._loading,
            )
        ]

    def _on_search_click(self) -> None:
        query = (self._search_field.value or "").strip()
        if not query or self._loading:
            return
        self._mode = "results"
        self._loading = True
        self._notice = None
        self._rows_signature = None

        async def task() -> None:
            try:
                self._repos = await self._application.search_hub(query)
            except Exception as error:
                self._notice = self._error_notice(error)
                self._repos = ()
            else:
                if not self._repos:
                    self._notice = t("model_assets.search_empty")
            finally:
                self._loading = False
                self.render()

        self._run_task(task)
        self.render()

    def _on_back_click(self) -> None:
        if self._mode == "files":
            self._mode = "results"
        else:
            self._mode = "library"
            self._notice = None
        self._rows_signature = None
        self.render()

    def _on_open_repo(self, repo) -> None:
        if self._loading:
            return
        self._loading = True
        self._notice = None

        async def task() -> None:
            try:
                files = await self._application.hub_files(repo)
            except Exception as error:
                self._notice = self._error_notice(error)
            else:
                self._selected_repo = repo
                self._repo_files = files
                self._mode = "files"
                self._rows_signature = None
                if not files:
                    self._notice = t("model_assets.search_empty")
            finally:
                self._loading = False
                self.render()

        self._run_task(task)
        self.render()

    def _on_install_file(self, file) -> None:
        if self._loading or self._selected_repo is None:
            return
        repo = self._selected_repo
        self._install_key = file.path
        self._notice = None

        async def task() -> None:
            try:
                await self._application.install_hub_file(repo, file)
            except Exception as error:
                self._notice = self._error_notice(error)
                self.render()
            else:
                self._mode = "library"
                self._notice = None
                self._rows_signature = None
                self.render()

        self._run_task(task)
        self.render()

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
            self._rows_signature = None
            try:
                await self._application.add_local_file(Path(path))
            except Exception as error:
                self._notice = self._error_notice(error)
            finally:
                self._loading = False
                self.render()

        self._run_task(task)
        self.render()

    def _on_install(self, model_id: str) -> None:
        self._confirm_delete = None
        self._run(lambda: self._application.download(model_id))

    def _on_use(self, model_id: str) -> None:
        if self._on_use_model is not None:
            self._on_use_model(model_id)
        self.render()

    def _on_delete(self, model_id: str) -> None:
        if self._confirm_delete != model_id:
            self._confirm_delete = model_id
            self.render()
            return
        self._confirm_delete = None
        self._run(lambda: self._application.delete(model_id))

    def _on_cancel(self, model_id: str | None) -> None:
        if model_id is not None:
            self._run(lambda: self._application.cancel(model_id))

    def _is_active(self, model_id: str) -> bool:
        if self._active_model_id is None:
            return False
        with contextlib.suppress(Exception):
            return self._active_model_id() == model_id
        return False

    def _error_notice(self, error: BaseException) -> str:
        code = getattr(error, "failure_code", None)
        return _failure_label(code if isinstance(code, str) else None) or _failure_label(
            "download_failed"
        )

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
