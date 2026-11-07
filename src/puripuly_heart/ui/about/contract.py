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
    info_card: ft.Container
    licenses_card: ft.Container
    components_scroll: ft.Column
    text_scroll: ft.Column


__all__ = [
    "AboutStateSink",
    "AboutSurfaceRegions",
    "AboutSurfaceSlots",
]
