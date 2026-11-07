from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import flet as ft


class AboutStateSink(Protocol):
    def apply_locale(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AboutSurfaceSlots:
    info_content: ft.Control
    licenses_title: ft.Control
    license_components: ft.Control
    license_text: ft.Control


@dataclass(frozen=True, slots=True)
class AboutSurfaceRegions:
    controls: tuple[ft.Control, ...]
    root_scroll: ft.Column
    info_card: ft.Container
    licenses_card: ft.Container


__all__ = [
    "AboutStateSink",
    "AboutSurfaceRegions",
    "AboutSurfaceSlots",
]
