"""Model manager body: four tabs around one model library."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import flet as ft

from puripuly_heart.app.services.model_hub import ModelRepo
from puripuly_heart.core.local_asr.local_stt_assets import default_local_stt_model_root
from puripuly_heart.core.local_asr.model_registry import sanitize_model_id
from puripuly_heart.core.transcribe_catalog import CatalogModel, load_transcribe_catalog
from puripuly_heart.ui.flet_runtime import update_control_if_mounted
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import COLOR_DIVIDER, COLOR_NEUTRAL_DARK, COLOR_PRIMARY

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
        "registry_corrupt",
        "already_installed",
        "unknown_model",
        "unsupported_source",
        "model_owner_unavailable",
    }
)
_BUSY_STATUSES = frozenset({"downloading", "importing", "deleting"})
_ORIGIN_KEYS = {
    "catalog": "model_assets.origin.catalog",
    "search": "model_assets.origin.search",
    "local": "model_assets.origin.local",
}
_SOURCE_LABELS = {"hf": "Hugging Face", "ms": "ModelScope"}
_ROLE_KEYS = {
    "asr": "model_assets.role.asr",
    "diarize": "model_assets.role.diarize",
    "langid": "model_assets.role.langid",
    "vad": "model_assets.role.vad",
}
_PREFERRED_QUANTS = ("Q6_K", "Q8_0", "Q5_K_M", "Q4_K_M", "F16", "BF16")

TAB_LIBRARY = "library"
TAB_OFFICIAL = "official"
TAB_USER_REPOS = "user_repos"
TAB_SEARCH = "search"

USER_REPO_IDS = {
    "hf": "fzcfweasdferttgg/asr-gguf-onnx",
    "ms": "fzcfweasdferttggpng/asr-gguf-onnx",
}

_CELLS_LIBRARY = (3, 1, 2, 2)
_CELLS_OFFICIAL = (3, 1, 1, 2, 2)
_CELLS_FILES = (4, 1, 2)

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


def _origin_label(entry: object, official_repos: set[str]) -> str:
    origin = str(getattr(entry, "origin", "catalog"))
    if origin == "local":
        return t("model_assets.origin.local")
    source = str(getattr(entry, "source", "") or "")
    repo = source.split(":", 1)[-1]
    if repo in official_repos:
        return t("model_assets.origin.official")
    if repo in set(USER_REPO_IDS.values()):
        return USER_REPO_IDS["hf"].split("/", 1)[0]
    return t(_ORIGIN_KEYS.get(origin, "model_assets.origin.search"))


class _TabButton(ft.Container):
    """One tab of the manager window."""

    def __init__(self, label: str, *, on_click: Callable[[str], None], key: str) -> None:
        self.label_text = ft.Text(label, size=TEXT_SIZE, weight=ft.FontWeight.BOLD)
        self._selected = False
        super().__init__(
            content=self.label_text,
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            border_radius=10,
            border=ft.Border.all(1, ft.Colors.TRANSPARENT),
            alignment=ft.Alignment.CENTER,
            on_click=lambda _event, key=key: on_click(key),
        )
        self._paint()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._paint()

    def _paint(self) -> None:
        self.label_text.color = ft.Colors.WHITE if self._selected else COLOR_NEUTRAL_DARK
        self.bgcolor = COLOR_PRIMARY if self._selected else ft.Colors.TRANSPARENT


class _TableRow(ft.Container):
    """One table row: N cells and a selection highlight."""

    def __init__(
        self,
        key: str,
        flexes: tuple[int, ...],
        *,
        on_click: Callable[[str], None] | None,
    ) -> None:
        self.cells = [
            ft.Text(
                "",
                size=TEXT_SIZE,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
            for _ in flexes
        ]
        cells = [
            ft.Container(content=cell, expand=flex, padding=ft.Padding.only(right=8))
            for cell, flex in zip(self.cells, flexes, strict=True)
        ]
        super().__init__(
            content=ft.Row(controls=cells, spacing=8),
            height=ROW_HEIGHT,
            padding=ft.Padding.symmetric(horizontal=8),
            alignment=ft.Alignment.CENTER_LEFT,
            border=ft.Border.only(bottom=ft.BorderSide(1, COLOR_DIVIDER)),
            on_click=(lambda _event, key=key: on_click(key)) if on_click else None,
        )
        self._selected = False

    def refresh(self, values: tuple[str, ...], *, selected: bool) -> None:
        for cell, value in zip(self.cells, values, strict=True):
            cell.value = value
        self._selected = selected
        self.bgcolor = (
            ft.Colors.with_opacity(0.12, COLOR_PRIMARY) if selected else ft.Colors.TRANSPARENT
        )


class ModelAssetsView(ft.Container):
    """Model manager: library, official catalog, owner repositories and search."""

    def __init__(
        self,
        page: ft.Page,
        application,
        run_task: ModelAssetsRunTask,
        *,
        catalog: tuple[CatalogModel, ...] | None = None,
        picker_factory: Callable[[], object] = ft.FilePicker,
    ) -> None:
        self._page = page
        self._application = application
        self._run_task = run_task
        available_catalog = catalog if catalog is not None else load_transcribe_catalog()
        self._catalog = tuple(model for model in available_catalog if model.role != "vad")
        self._official_repos = {model.repo for model in self._catalog}
        self._picker_factory = picker_factory
        self._picker = None
        self._is_open = False
        self._tab = TAB_LIBRARY
        self._loading = False
        self._notice: str | None = None
        self._entries: tuple = ()
        self._installed_files: set[str] = set()
        self._selected: dict[str, str | None] = {
            TAB_LIBRARY: None,
            TAB_OFFICIAL: None,
            TAB_USER_REPOS: None,
            TAB_SEARCH: None,
        }
        self._confirm_delete: str | None = None
        self._load_generation = 0
        self._rows: dict[str, _TableRow] = {}
        self._rows_key: tuple | None = None

        self._role_filter = "all"
        self._lang_filter = "all"
        self._quant: str | None = None

        self._user_provider = "hf"
        self._user_repos: tuple = ()
        self._user_repo = None
        self._user_files: tuple = ()

        self._search_provider = "hf"
        self._repos: tuple = ()
        self._selected_repo = None
        self._repo_files: tuple = ()

        self._tabs = {
            key: _TabButton(label, on_click=self._on_tab_click, key=key)
            for key, label in (
                (TAB_LIBRARY, t("model_assets.tab.library")),
                (TAB_OFFICIAL, t("model_assets.tab.official")),
                (TAB_USER_REPOS, f"fzcfweasdferttgg {t('model_assets.tab.user_repos')}"),
                (TAB_SEARCH, t("model_assets.tab.search")),
            )
        }

        self._role_dropdown = ft.Dropdown(
            label=t("model_assets.filter.role"),
            options=[
                ft.dropdown.Option(key="all", text=t("model_assets.filter.all")),
                *(ft.dropdown.Option(key=key, text=t(label)) for key, label in _ROLE_KEYS.items()),
            ],
            value="all",
            width=190,
            text_size=TEXT_SIZE,
            label_style=ft.TextStyle(size=HEADER_SIZE),
            on_select=self._on_filter_changed,
        )
        languages = sorted({code for model in self._catalog for code in model.languages})
        self._lang_dropdown = ft.Dropdown(
            label=t("model_assets.filter.language"),
            options=[
                ft.dropdown.Option(key="all", text=t("model_assets.filter.all")),
                *(ft.dropdown.Option(key=code, text=code) for code in languages),
            ],
            value="all",
            width=150,
            text_size=TEXT_SIZE,
            label_style=ft.TextStyle(size=HEADER_SIZE),
            on_select=self._on_filter_changed,
        )
        self._name_field = ft.TextField(
            hint_text=t("model_assets.filter.name"),
            text_size=TEXT_SIZE,
            expand=True,
            on_change=self._on_filter_changed,
        )
        self._add_button = ft.Button(
            content=t("model_assets.action.add_file"),
            on_click=lambda _event: self._on_add_file_click(),
        )
        self._refresh_button = ft.Button(
            content=t("model_assets.action.refresh"),
            on_click=lambda _event: self._on_refresh_click(),
        )
        self._user_provider_dropdown = ft.Dropdown(
            label=t("model_assets.search_provider"),
            options=[
                ft.dropdown.Option(key="hf", text="Hugging Face"),
                ft.dropdown.Option(key="ms", text="ModelScope"),
            ],
            value="hf",
            width=190,
            text_size=TEXT_SIZE,
            label_style=ft.TextStyle(size=HEADER_SIZE),
            on_select=self._on_user_provider_selected,
        )
        self._user_repo_dropdown = ft.Dropdown(
            label=t("model_assets.search_repo"),
            options=[],
            text_size=TEXT_SIZE,
            label_style=ft.TextStyle(size=HEADER_SIZE),
            expand=True,
            disabled=True,
            on_select=self._on_user_repo_selected,
        )
        self._user_refresh_button = ft.Button(
            content=t("model_assets.action.refresh"),
            on_click=lambda _event: self._on_user_refresh_click(),
        )
        self._search_provider_dropdown = ft.Dropdown(
            label=t("model_assets.search_provider"),
            options=[
                ft.dropdown.Option(key="hf", text="Hugging Face"),
                ft.dropdown.Option(key="ms", text="ModelScope"),
            ],
            value="hf",
            width=190,
            text_size=TEXT_SIZE,
            label_style=ft.TextStyle(size=HEADER_SIZE),
            on_select=self._on_provider_selected,
        )
        self._search_field = ft.TextField(
            hint_text=t("model_assets.search_placeholder"),
            text_size=TEXT_SIZE,
            expand=True,
            on_submit=lambda _event: self._on_search_click(),
        )
        self._find_button = ft.Button(
            content=t("model_assets.search_action"),
            on_click=lambda _event: self._on_search_click(),
        )
        self._search_repo_dropdown = ft.Dropdown(
            label=t("model_assets.search_repo"),
            options=[],
            text_size=TEXT_SIZE,
            label_style=ft.TextStyle(size=HEADER_SIZE),
            expand=True,
            on_select=self._on_repo_selected,
        )

        self._toolbars = {
            TAB_LIBRARY: ft.Row(
                controls=[self._add_button, self._refresh_button, ft.Container(expand=True)],
                spacing=8,
            ),
            TAB_OFFICIAL: ft.Row(controls=[], height=0),
            TAB_USER_REPOS: ft.Row(
                controls=[
                    self._user_provider_dropdown,
                    self._user_repo_dropdown,
                    self._user_refresh_button,
                ],
                spacing=8,
            ),
            TAB_SEARCH: ft.Row(
                controls=[
                    self._search_provider_dropdown,
                    self._search_field,
                    self._find_button,
                    self._search_repo_dropdown,
                ],
                spacing=8,
            ),
        }

        self._notice_text = ft.Text("", size=HEADER_SIZE, max_lines=2)
        self._header = ft.Container(
            content=ft.Row(controls=[], spacing=8),
            height=ROW_HEIGHT,
            padding=ft.Padding.symmetric(horizontal=8),
            alignment=ft.Alignment.CENTER_LEFT,
            border=ft.Border.only(bottom=ft.BorderSide(1, COLOR_DIVIDER)),
        )
        self._list = ft.ListView(expand=True, spacing=0)
        self._empty_text = ft.Container(
            content=ft.Text(
                "",
                size=TEXT_SIZE,
                color=COLOR_NEUTRAL_DARK,
                max_lines=3,
                text_align=ft.TextAlign.CENTER,
            ),
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.symmetric(horizontal=24, vertical=16),
            visible=False,
        )

        self._quant_dropdown = ft.Dropdown(
            label=t("model_assets.details.quantization"),
            options=[],
            text_size=TEXT_SIZE,
            label_style=ft.TextStyle(size=HEADER_SIZE),
            width=300,
            visible=False,
            on_select=self._on_quant_selected,
        )
        self._install_button = ft.Button(
            content=t("model_assets.action.install"),
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
        self._path_text = ft.Text(
            "",
            size=HEADER_SIZE,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._progress_bar = ft.ProgressBar(value=0, expand=2, height=6)
        self._progress_text = ft.Text("", size=TEXT_SIZE, width=48)
        self._details_heading = ft.Text(
            t("model_assets.details.title"),
            size=TEXT_SIZE,
            weight=ft.FontWeight.BOLD,
        )
        self._details_name = ft.Text("", size=TEXT_SIZE, weight=ft.FontWeight.BOLD)
        self._details_parameters = ft.Text("", size=HEADER_SIZE)
        self._details_languages = ft.Text("", size=HEADER_SIZE)
        self._details_accuracy = ft.Text("", size=HEADER_SIZE)
        self._side_panel = ft.Container(
            content=ft.Column(
                controls=[
                    self._details_heading,
                    self._name_field,
                    self._role_dropdown,
                    self._lang_dropdown,
                    self._quant_dropdown,
                    ft.Divider(height=12, color=COLOR_DIVIDER),
                    self._details_name,
                    self._details_parameters,
                    self._details_languages,
                    self._details_accuracy,
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
            width=330,
            visible=False,
            padding=ft.Padding.all(12),
            border=ft.Border.only(left=ft.BorderSide(1, COLOR_DIVIDER)),
        )
        self._table_frame = ft.Container(
            content=self._list,
            expand=True,
            border=ft.Border.all(1, COLOR_DIVIDER),
            border_radius=10,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

        super().__init__(
            content=ft.Column(
                controls=[
                    ft.Row(controls=list(self._tabs.values()), spacing=8),
                    *[
                        self._toolbars[key]
                        for key in (TAB_LIBRARY, TAB_OFFICIAL, TAB_USER_REPOS, TAB_SEARCH)
                    ],
                    self._notice_text,
                    ft.Row(
                        controls=[
                            ft.Column(
                                controls=[self._header, self._table_frame],
                                expand=True,
                                spacing=8,
                            ),
                            self._side_panel,
                        ],
                        expand=True,
                        spacing=0,
                    ),
                    self._empty_text,
                    ft.Row(
                        controls=[
                            self._install_button,
                            self._delete_button,
                            self._cancel_button,
                            self._path_text,
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
        self._tabs[TAB_LIBRARY].label_text.value = t("model_assets.tab.library")
        self._tabs[TAB_OFFICIAL].label_text.value = t("model_assets.tab.official")
        self._tabs[TAB_USER_REPOS].label_text.value = (
            f"fzcfweasdferttgg {t('model_assets.tab.user_repos')}"
        )
        self._tabs[TAB_SEARCH].label_text.value = t("model_assets.tab.search")
        self._role_dropdown.label = t("model_assets.filter.role")
        self._lang_dropdown.label = t("model_assets.filter.language")
        self._name_field.hint_text = t("model_assets.filter.name")
        self._add_button.content = t("model_assets.action.add_file")
        self._refresh_button.content = t("model_assets.action.refresh")
        self._user_refresh_button.content = t("model_assets.action.refresh")
        self._search_provider_dropdown.label = t("model_assets.search_provider")
        self._user_provider_dropdown.label = t("model_assets.search_provider")
        self._search_repo_dropdown.label = t("model_assets.search_repo")
        self._user_repo_dropdown.label = t("model_assets.search_repo")
        self._search_field.hint_text = self._search_hint()
        self._find_button.content = t("model_assets.search_action")
        self._install_button.content = t("model_assets.action.install")
        self._delete_button.content = t("model_assets.action.delete")
        self._cancel_button.content = t("model_assets.action.cancel")
        self._quant_dropdown.label = t("model_assets.details.quantization")
        self._details_heading.value = t("model_assets.details.title")
        self._path_text.value = self._target_path_label()
        self._rows_key = None
        self._rows = {}
        if self._is_open:
            self.render()

    def render(self) -> None:
        for key, button in self._tabs.items():
            button.set_selected(key == self._tab)
        for key, toolbar in self._toolbars.items():
            toolbar.visible = key == self._tab
        self._notice_text.value = self._notice or ""
        self._notice_text.visible = bool(self._notice)
        self._refresh_state()
        if self._tab == TAB_LIBRARY:
            self._render_library()
        elif self._tab == TAB_OFFICIAL:
            self._render_official()
        elif self._tab == TAB_USER_REPOS:
            self._render_files(self._user_files, cells=_CELLS_FILES)
        else:
            self._render_files(self._repo_files, cells=_CELLS_FILES)
        self._render_details()
        self._render_empty_state()
        self._render_actions()
        update_control_if_mounted(self)

    def _render_details(self) -> None:
        self._side_panel.visible = self._tab == TAB_OFFICIAL
        model = self._selected_model()
        self._quant_dropdown.visible = model is not None
        if model is None:
            self._details_name.value = t("model_assets.details.select")
            self._details_parameters.value = ""
            self._details_languages.value = ""
            self._details_accuracy.value = ""
            return
        self._details_name.value = model.name
        self._details_parameters.value = f"{t('model_assets.column.params')}: {model.params_label}"
        languages = ", ".join(model.languages) if model.languages else "—"
        self._details_languages.value = f"{t('model_assets.column.languages')}: {languages}"
        language = None if self._lang_filter == "all" else self._lang_filter
        accuracy = model.accuracy_for(language, self._quant) or "—"
        self._details_accuracy.value = f"{t('model_assets.column.accuracy')}: {accuracy}"

    def _render_empty_state(self) -> None:
        message_keys = {
            TAB_LIBRARY: "model_assets.empty.library",
            TAB_OFFICIAL: "model_assets.empty.official",
            TAB_USER_REPOS: "model_assets.empty.repositories",
            TAB_SEARCH: "model_assets.empty.search",
        }
        self._empty_text.content.value = t(message_keys[self._tab])
        self._empty_text.visible = not self._rows and not self._loading

    def _refresh_state(self) -> None:
        self._entries = tuple(self._application.model_asset_entries())
        self._installed_files = {
            name
            for entry in self._entries
            if getattr(entry, "status", "missing") == "installed"
            for name in (getattr(entry, "files", ()) or ())
        }

    # ---------- rows ----------

    def _set_header(self, labels: tuple[str, ...], flexes: tuple[int, ...]) -> None:
        row = self._header.content
        row.controls = [
            ft.Container(
                content=ft.Text(
                    label,
                    size=HEADER_SIZE,
                    weight=ft.FontWeight.BOLD,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                expand=flex,
                padding=ft.Padding.only(right=8),
            )
            for label, flex in zip(labels, flexes, strict=True)
        ]

    def _rebuild_rows(self, keys: tuple[str, ...], flexes: tuple[int, ...]) -> None:
        self._rows = {}
        controls: list[ft.Control] = []
        for key in keys:
            row = _TableRow(key, flexes, on_click=self._on_row_click)
            self._rows[key] = row
            controls.append(row)
        self._list.controls = controls

    def _render_library(self) -> None:
        self._set_header(
            (
                t("model_assets.column.name"),
                t("model_assets.column.size"),
                t("model_assets.column.status"),
                t("model_assets.column.source"),
            ),
            _CELLS_LIBRARY,
        )
        entries = [
            entry
            for entry in self._entries
            if getattr(entry, "purpose", "asr") == "asr"
            and (
                getattr(entry, "status", "missing") != "missing"
                or getattr(entry, "origin", "catalog") != "catalog"
            )
        ]
        rows_key = (TAB_LIBRARY, tuple(entry.model_id for entry in entries))
        if rows_key != self._rows_key:
            self._rows_key = rows_key
            self._rebuild_rows(tuple(entry.model_id for entry in entries), _CELLS_LIBRARY)
        for entry in entries:
            row = self._rows.get(entry.model_id)
            if row is None:
                continue
            row.refresh(
                (
                    str(entry.name),
                    _size_label(int(entry.size_bytes)),
                    _status_label(entry),
                    _origin_label(entry, self._official_repos),
                ),
                selected=self._selected[TAB_LIBRARY] == entry.model_id,
            )

    def _render_official(self) -> None:
        self._set_header(
            (
                t("model_assets.column.name"),
                t("model_assets.column.role"),
                t("model_assets.column.languages"),
                t("model_assets.column.accuracy"),
                t("model_assets.column.status"),
            ),
            _CELLS_OFFICIAL,
        )
        query = (self._name_field.value or "").strip().lower()
        models = [
            model
            for model in self._catalog
            if (self._role_filter == "all" or model.role == self._role_filter)
            and (self._lang_filter == "all" or self._lang_filter in model.languages)
            and (not query or query in model.name.lower())
        ]
        rows_key = (
            TAB_OFFICIAL,
            tuple(model.model_id for model in models),
            self._lang_filter,
            self._installed_files,
        )
        if rows_key != self._rows_key:
            self._rows_key = rows_key
            self._rebuild_rows(tuple(model.model_id for model in models), _CELLS_OFFICIAL)
        for model in models:
            row = self._rows.get(model.model_id)
            if row is None:
                continue
            installed = self._installed_quant(model)
            row.refresh(
                (
                    model.name,
                    t(_ROLE_KEYS.get(model.role, "model_assets.role.asr")),
                    self._languages_label(model.languages),
                    model.accuracy_for(
                        None if self._lang_filter == "all" else self._lang_filter,
                        None,
                    ),
                    (f"{t('model_assets.status.installed')} · {installed}" if installed else "—"),
                ),
                selected=self._selected[TAB_OFFICIAL] == model.model_id,
            )

    def _render_files(self, files: tuple, *, cells: tuple[int, ...]) -> None:
        self._set_header(
            (
                t("model_assets.column.file"),
                t("model_assets.column.size"),
                t("model_assets.column.status"),
            ),
            cells,
        )
        key_prefix = self._tab
        rows_key = (key_prefix, tuple(getattr(file, "path", "") for file in files))
        if rows_key != self._rows_key:
            self._rows_key = rows_key
            self._rebuild_rows(tuple(getattr(file, "path", "") for file in files), cells)
        for file in files:
            key = str(getattr(file, "path", ""))
            row = self._rows.get(key)
            if row is None:
                continue
            installed = getattr(file, "name", key) in self._installed_files
            row.refresh(
                (
                    str(getattr(file, "name", key)),
                    _size_label(int(getattr(file, "size_bytes", 0) or 0)),
                    t("model_assets.status.installed") if installed else "—",
                ),
                selected=self._selected[self._tab] == key,
            )

    def _installed_quant(self, model: CatalogModel) -> str | None:
        for item in model.downloads:
            if item.filename in self._installed_files:
                return item.quant
        return None

    @staticmethod
    def _languages_label(languages: tuple[str, ...]) -> str:
        return str(len(languages)) if languages else "—"

    # ---------- actions ----------

    def _render_actions(self) -> None:
        operation = self._application.model_operation_progress
        busy = operation is not None
        self._cancel_button.disabled = not busy
        install_target = self._install_target()
        self._install_button.visible = self._tab != TAB_LIBRARY
        self._install_button.disabled = busy or install_target is None
        self._path_text.value = self._target_path_label()
        self._path_text.visible = bool(self._path_text.value)
        entry = self._delete_target()
        confirming = entry is not None and self._confirm_delete == getattr(entry, "model_id", None)
        self._delete_button.content = t(
            "model_assets.action.delete_confirm" if confirming else "model_assets.action.delete"
        )
        self._delete_button.disabled = busy or entry is None
        self._progress_bar.visible = busy
        self._progress_text.visible = busy
        if operation is not None and operation[2] is not None:
            self._progress_bar.value = max(0.0, min(1.0, operation[2] / 100))
            self._progress_text.value = f"{operation[2]}%"
        else:
            self._progress_bar.value = None if busy else 0
            self._progress_text.value = ""

    def _install_target(self) -> tuple[str, object | None, object | None] | None:
        """(kind, repo, file) or (kind, model_id, quant) for the selected row."""
        if self._tab == TAB_OFFICIAL:
            model = self._selected_model()
            if model is None:
                return None
            quant = self._quant or self._default_quant(model)
            download = model.download(quant)
            if download is None:
                return None
            if download.filename in self._installed_files:
                return None
            return ("catalog", model.model_id, quant)
        file = self._selected_file()
        if file is None or getattr(file, "name", "") in self._installed_files:
            return None
        repo = self._user_repo if self._tab == TAB_USER_REPOS else self._selected_repo
        if repo is None:
            return None
        return ("file", repo, file)

    def _delete_target(self):
        if self._tab == TAB_LIBRARY:
            entry = self._selected_entry()
            if entry is None:
                return None
            status = getattr(entry, "status", "missing")
            if status in _BUSY_STATUSES:
                return None
            return entry
        file = self._selected_file()
        name = getattr(file, "name", "") if file is not None else ""
        if not name:
            model = self._selected_model()
            if model is not None and self._tab == TAB_OFFICIAL:
                quant = self._quant or self._default_quant(model)
                download = model.download(quant)
                name = download.filename if download else ""
        for entry in self._entries:
            if name and name in (getattr(entry, "files", ()) or ()):
                if getattr(entry, "status", "missing") not in _BUSY_STATUSES:
                    return entry
        return None

    def _target_path_label(self) -> str:
        """Concrete folder the selection would be saved into."""
        model_id = None
        if self._tab == TAB_LIBRARY:
            entry = self._selected_entry()
            model_id = getattr(entry, "model_id", None)
        elif self._tab == TAB_OFFICIAL:
            model = self._selected_model()
            if model is not None:
                quant = self._quant or self._default_quant(model)
                download = model.download(quant) if quant else None
                if download is not None:
                    model_id = sanitize_model_id(Path(download.filename).stem)
        else:
            file = self._selected_file()
            if file is not None:
                model_id = sanitize_model_id(Path(getattr(file, "name", "")).stem)
        if not model_id:
            return ""
        return str(default_local_stt_model_root() / model_id)

    def _selected_entry(self):
        model_id = self._selected.get(TAB_LIBRARY)
        if model_id is None:
            return None
        for entry in self._entries:
            if entry.model_id == model_id:
                return entry
        return None

    def _selected_model(self) -> CatalogModel | None:
        model_id = self._selected.get(TAB_OFFICIAL)
        if model_id is None:
            return None
        for model in self._catalog:
            if model.model_id == model_id:
                return model
        return None

    def _selected_file(self):
        key = self._selected.get(self._tab)
        if key is None:
            return None
        files = self._user_files if self._tab == TAB_USER_REPOS else self._repo_files
        for file in files:
            if str(getattr(file, "path", "")) == key:
                return file
        return None

    @staticmethod
    def _default_quant(model: CatalogModel) -> str | None:
        for preferred in _PREFERRED_QUANTS:
            if model.download(preferred) is not None:
                return preferred
        return model.downloads[0].quant if model.downloads else None

    # ---------- handlers ----------

    def _on_tab_click(self, key: str) -> None:
        self._tab = key
        self._confirm_delete = None
        self._rows_key = None
        if key == TAB_OFFICIAL:
            self._fill_quant_dropdown(self._selected_model())
        if key == TAB_USER_REPOS and not self._user_repos:
            self._load_user_repos()
        self.render()

    def _on_row_click(self, key: str) -> None:
        self._selected[self._tab] = key
        self._confirm_delete = None
        if self._tab == TAB_OFFICIAL:
            model = self._selected_model()
            self._quant = self._default_quant(model) if model is not None else None
            self._fill_quant_dropdown(model)
        self.render()

    def _fill_quant_dropdown(self, model: CatalogModel | None) -> None:
        if model is None:
            self._quant_dropdown.options = []
            return
        self._quant_dropdown.options = [
            ft.dropdown.Option(
                key=item.quant,
                text=f"{item.quant} · {_size_label(item.size_bytes)}",
            )
            for item in model.downloads
        ]
        self._quant_dropdown.value = self._quant

    def _on_quant_selected(self, event) -> None:
        self._quant = getattr(getattr(event, "control", None), "value", None)
        self._confirm_delete = None
        self.render()

    def _on_filter_changed(self, _event) -> None:
        self._role_filter = self._role_dropdown.value or "all"
        self._lang_filter = self._lang_dropdown.value or "all"
        self._selected[TAB_OFFICIAL] = None
        self._rows_key = None
        self.render()

    def _on_install_click(self) -> None:
        target = self._install_target()
        if target is None:
            return
        self._confirm_delete = None
        if target[0] == "catalog":
            self._run(lambda: self._application.install_catalog_model(target[1], target[2]))
        else:
            self._run(lambda: self._application.install_model_file(target[1], target[2]))

    def _on_delete_click(self) -> None:
        entry = self._delete_target()
        if entry is None:
            return
        model_id = entry.model_id
        if self._confirm_delete != model_id:
            self._confirm_delete = model_id
            self.render()
            return
        self._confirm_delete = None
        self._run(lambda: self._application.delete_model_asset(model_id))

    def _on_cancel_click(self) -> None:
        operation = self._application.model_operation_progress
        if operation is not None:
            self._run(lambda: self._application.cancel_model_asset(operation[0]))

    def _on_refresh_click(self) -> None:
        self._run(lambda: self._application.refresh_model_assets())

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
            try:
                await self._application.add_model_file(Path(path))
            except Exception as error:
                self._notice = self._error_notice(error)
            finally:
                self._loading = False
                self.render()

        self._run_task(task)

    # user repositories tab

    def _on_user_provider_selected(self, event) -> None:
        self._user_provider = getattr(getattr(event, "control", None), "value", None) or "hf"
        self._user_repo = None
        self._user_files = ()
        self._selected[TAB_USER_REPOS] = None
        self._rows_key = None
        self._load_user_repos()

    def _on_user_refresh_click(self) -> None:
        self._load_user_repos()

    def _load_user_repos(self) -> None:
        provider = self._user_provider_dropdown.value or "hf"
        repo_id = USER_REPO_IDS.get(provider, USER_REPO_IDS["hf"])
        self._user_repos = (ModelRepo(repo_id=repo_id, source=provider, name="asr-gguf-onnx"),)
        self._loading = False
        self._notice = None
        self._fill_user_repo_dropdown()
        self.render()

    def _fill_user_repo_dropdown(self) -> None:
        self._user_repo_dropdown.options = [
            ft.dropdown.Option(key=repo.repo_id, text=repo.repo_id) for repo in self._user_repos
        ]
        if self._user_repos:
            self._user_repo_dropdown.value = self._user_repos[0].repo_id
            self._load_repo_files(self._user_repos[0], tab=TAB_USER_REPOS)
        else:
            self._user_repo_dropdown.value = None

    def _on_user_repo_selected(self, event) -> None:
        key = getattr(getattr(event, "control", None), "value", None)
        repo = next((item for item in self._user_repos if item.repo_id == key), None)
        if repo is None:
            return
        self._load_repo_files(repo, tab=TAB_USER_REPOS)

    # search tab

    def _search_hint(self) -> str:
        source = self._search_provider_dropdown.value or "hf"
        return (
            t("model_assets.search_placeholder_ms")
            if source == "ms"
            else t("model_assets.search_placeholder")
        )

    def _on_provider_selected(self, event) -> None:
        self._search_provider = getattr(getattr(event, "control", None), "value", None) or "hf"
        self._search_field.hint_text = self._search_hint()
        with contextlib.suppress(Exception):
            self._search_field.update()

    def _on_search_click(self) -> None:
        query = (self._search_field.value or "").strip()
        if not query:
            return
        self._load_generation += 1
        generation = self._load_generation
        self._loading = True
        self._notice = None

        async def task() -> None:
            try:
                repos = await self._application.search_model_repos(
                    query, self._search_provider_dropdown.value or "hf"
                )
            except Exception as error:
                if generation == self._load_generation:
                    self._repos = ()
                    self._notice = self._error_notice(error)
            else:
                if generation != self._load_generation:
                    return
                self._repos = repos
            if generation != self._load_generation:
                return
            self._selected_repo = None
            self._repo_files = ()
            self._selected[TAB_SEARCH] = None
            self._rows_key = None
            self._loading = False
            self._fill_search_repo_dropdown()
            if not self._repos and self._notice is None:
                self._notice = t("model_assets.search_empty")
            self.render()

        self._run_task(task)
        self.render()

    def _fill_search_repo_dropdown(self) -> None:
        self._search_repo_dropdown.options = [
            ft.dropdown.Option(
                key=repo.repo_id,
                text=f"{repo.repo_id} · {_SOURCE_LABELS.get(repo.source, repo.source)}",
            )
            for repo in self._repos
        ]
        if self._repos:
            self._search_repo_dropdown.value = self._repos[0].repo_id
            self._load_repo_files(self._repos[0], tab=TAB_SEARCH)
        else:
            self._search_repo_dropdown.value = None

    def _on_repo_selected(self, event) -> None:
        key = getattr(getattr(event, "control", None), "value", None)
        repo = next((item for item in self._repos if item.repo_id == key), None)
        if repo is None:
            return
        self._load_repo_files(repo, tab=TAB_SEARCH)

    def _load_repo_files(self, repo, *, tab: str) -> None:
        self._load_generation += 1
        generation = self._load_generation
        if tab == TAB_USER_REPOS:
            self._user_repo = repo
        else:
            self._selected_repo = repo
        self._selected[tab] = None
        self._loading = True
        self._notice = None

        async def task() -> None:
            try:
                files = await self._application.model_repo_files(repo)
            except Exception as error:
                files = ()
                if generation == self._load_generation:
                    self._notice = self._error_notice(error)
            if generation != self._load_generation:
                return
            files = tuple(
                sorted(
                    files,
                    key=lambda file: (
                        str(getattr(file, "name", "")).casefold(),
                        str(getattr(file, "path", "")).casefold(),
                    ),
                )
            )
            if tab == TAB_USER_REPOS:
                self._user_files = files
            else:
                self._repo_files = files
            self._rows_key = None
            self._loading = False
            self.render()

        self._run_task(task)

    # shared

    def _error_notice(self, error: BaseException) -> str | None:
        code = getattr(error, "failure_code", None)
        if code == "cancelled":
            return None
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
