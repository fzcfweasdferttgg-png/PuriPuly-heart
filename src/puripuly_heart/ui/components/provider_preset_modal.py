from __future__ import annotations

from collections.abc import Callable

import flet as ft

from puripuly_heart.config.providers_registry import (
    DEFAULT_LOCAL_PROVIDER_BACKEND,
    LOCAL_PROVIDER_BACKENDS,
    ProviderPresetError,
    add_provider_preset,
    delete_provider_preset,
    load_provider_presets,
    update_provider_preset,
)
from puripuly_heart.ui.components.modal_kit import build_modal_dialog
from puripuly_heart.ui.flet_runtime import update_control_if_mounted
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_ERROR,
    COLOR_NEUTRAL_DARK,
    COLOR_PRIMARY,
    COLOR_SECONDARY,
    COLOR_SURFACE,
)


class ProviderPresetModal:
    def __init__(
        self,
        page: ft.Page,
        on_changed: Callable[[str, str], None],
        *,
        protocol: str = "openai",
    ) -> None:
        self._page = page
        self._on_changed = on_changed
        self._protocol = protocol
        self._dialog: ft.AlertDialog | None = None
        self._name_field: ft.TextField | None = None
        self._url_field: ft.TextField | None = None
        self._backend_field: ft.Dropdown | None = None
        self._error_text: ft.Text | None = None
        self._key = ""

    @property
    def name_field(self) -> ft.TextField | None:
        return self._name_field

    @property
    def url_field(self) -> ft.TextField | None:
        return self._url_field

    @property
    def backend_field(self) -> ft.Dropdown | None:
        return self._backend_field

    @property
    def dialog(self) -> ft.AlertDialog | None:
        return self._dialog

    def open_add(self) -> None:
        self._open_editor("", "", "", DEFAULT_LOCAL_PROVIDER_BACKEND)

    def open_edit(self, key: str) -> None:
        preset = load_provider_presets(self._protocol).get(key)
        if preset is not None:
            self._open_editor(key, preset.label, preset.base_url, preset.backend)

    def open_delete(self, key: str) -> None:
        preset = load_provider_presets(self._protocol).get(key)
        if preset is None:
            return
        self._key = key
        content = self._surface(
            title=t("settings.provider_presets.delete"),
            body=ft.Column(
                [
                    ft.Text(preset.label, size=22, weight=ft.FontWeight.BOLD),
                    ft.Text(preset.base_url, size=16, color=COLOR_SECONDARY),
                    ft.Container(height=12),
                    ft.Row(
                        [
                            self._button(t("settings.cancel"), self._close),
                            self._button(t("settings.delete"), self._delete),
                        ],
                        alignment=ft.MainAxisAlignment.END,
                    ),
                ],
                spacing=12,
            ),
            height=250,
        )
        self._show(content)

    def _open_editor(self, key: str, label: str, base_url: str, backend: str) -> None:
        self._key = key
        self._name_field = self._field(t("settings.provider_presets.name"), label)
        self._url_field = self._field(t("settings.provider_presets.base_url"), base_url)
        self._backend_field = ft.Dropdown(
            label=t("settings.local_llm.backend"),
            options=[ft.dropdown.Option(key=name, text=name) for name in LOCAL_PROVIDER_BACKENDS],
            value=backend or DEFAULT_LOCAL_PROVIDER_BACKEND,
            visible=self._protocol == "local",
            border_radius=12,
            text_size=20,
            color=COLOR_NEUTRAL_DARK,
            focused_border_color=COLOR_PRIMARY,
        )
        self._error_text = ft.Text("", size=14, color=COLOR_ERROR, visible=False)
        content = self._surface(
            title=t(
                "settings.provider_presets.add" if not key else "settings.provider_presets.edit"
            ),
            body=ft.Column(
                [
                    self._name_field,
                    self._url_field,
                    self._backend_field,
                    self._error_text,
                    ft.Container(height=8),
                    ft.Row(
                        [
                            self._button(t("settings.cancel"), self._close),
                            self._button(t("settings.save"), self._save),
                        ],
                        alignment=ft.MainAxisAlignment.END,
                    ),
                ],
                spacing=12,
            ),
            height=480 if self._protocol == "local" else 390,
        )
        self._show(content)

    def _field(self, label: str, value: str) -> ft.TextField:
        return ft.TextField(
            label=label,
            value=value,
            expand=True,
            text_size=20,
            color=COLOR_NEUTRAL_DARK,
            border_radius=12,
            focused_border_color=COLOR_PRIMARY,
        )

    def _button(self, label: str, on_click: Callable[[object], None]) -> ft.TextButton:
        return ft.TextButton(content=ft.Text(label), on_click=on_click)

    def _surface(self, *, title: str, body: ft.Control, height: int) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, size=24, weight=ft.FontWeight.BOLD, color=COLOR_SECONDARY),
                    ft.Container(height=12),
                    body,
                ]
            ),
            width=560,
            height=height,
            padding=24,
            bgcolor=COLOR_SURFACE,
            border_radius=20,
        )

    def _show(self, content: ft.Control) -> None:
        self._dialog = build_modal_dialog(content)
        self._page.show_dialog(self._dialog)

    def _save(self, _event: object) -> None:
        if self._name_field is None or self._url_field is None or self._backend_field is None:
            return
        backend = self._backend_field.value or DEFAULT_LOCAL_PROVIDER_BACKEND
        try:
            preset = (
                add_provider_preset(
                    self._name_field.value or "",
                    self._url_field.value or "",
                    protocol=self._protocol,
                    backend=backend,
                )
                if not self._key
                else update_provider_preset(
                    self._key,
                    self._name_field.value or "",
                    self._url_field.value or "",
                    protocol=self._protocol,
                    backend=backend,
                )
            )
        except ProviderPresetError as exc:
            if self._error_text is not None:
                self._error_text.value = t(
                    f"settings.provider_presets.error.{exc.code}", default=exc.code
                )
                self._error_text.visible = True
                update_control_if_mounted(self._error_text)
            return
        action = "edit" if self._key else "add"
        self._on_changed(action, preset.key)
        self._close()

    def _delete(self, _event: object) -> None:
        try:
            delete_provider_preset(self._key, protocol=self._protocol)
        except ProviderPresetError:
            return
        self._on_changed("delete", self._key)
        self._close()

    def _close(self, _event: object | None = None) -> None:
        self._page.pop_dialog()


__all__ = ["ProviderPresetModal"]
