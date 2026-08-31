"""Theme constants for the Tkinter/CTk GUI.

Color palette — blue / material with light and dark themes.
Provides hex colors, CTk appearance settings, font references,
and layout constants consumed by every Tkinter view and component.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Color palettes — light and dark
# ---------------------------------------------------------------------------

_LIGHT: dict[str, str] = {
    "background": "#FFFFFF",
    "surface": "#F5F5F5",
    "primary": "#2196F3",
    "on_primary": "#FFFFFF",  # Text on primary background
    "error": "#F44336",
    "success": "#4CAF50",
    "warning": "#FF9800",
    "divider": "#E0E0E0",
    "text": "#1A1A1A",
    "text_secondary": "#666666",
    "primary_container": "#BBDEFB",
    "on_primary_container": "#0D47A1",
    "on_surface_variant": "#666666",
    "surface_tonal": "#E3F2FD",
    "toast_warning": "#FF9800",
    "toast_error": "#F44336",
    "toast_info": "#4CAF50",
    "entry_bg": "#FFFFFF",
}

_DARK: dict[str, str] = {
    "background": "#121212",
    "surface": "#1E1E1E",
    "primary": "#64B5F6",
    "on_primary": "#1A1A1A",  # Text on primary background
    "error": "#EF5350",
    "success": "#66BB6A",
    "warning": "#FFA726",
    "divider": "#333333",
    "text": "#FFFFFF",
    "text_secondary": "#B0B0B0",
    "primary_container": "#0D47A1",
    "on_primary_container": "#BBDEFB",
    "on_surface_variant": "#B0B0B0",
    "surface_tonal": "#252525",
    "toast_warning": "#FFA726",
    "toast_error": "#EF5350",
    "toast_info": "#66BB6A",
    "entry_bg": "#2C2C2C",
}

# Current theme — mutable module-level dict, starts with light
_theme: dict[str, str] = dict(_LIGHT)
_is_dark: bool = False


def toggle_theme() -> bool:
    """Switch between light and dark. Returns True if now dark."""
    global _theme, _is_dark
    _is_dark = not _is_dark
    _theme = dict(_DARK if _is_dark else _LIGHT)
    return _is_dark


def is_dark() -> bool:
    return _is_dark


def get_theme() -> dict[str, str]:
    return _theme


# ---------------------------------------------------------------------------
# Active color aliases — read by all views at import time
# ---------------------------------------------------------------------------

COLOR_BACKGROUND: str = _theme["background"]
COLOR_SURFACE: str = _theme["surface"]
COLOR_PRIMARY: str = _theme["primary"]
COLOR_ON_PRIMARY: str = _theme["on_primary"]
COLOR_ERROR: str = _theme["error"]
COLOR_SUCCESS: str = _theme["success"]
COLOR_WARNING: str = _theme["warning"]
COLOR_DIVIDER: str = _theme["divider"]
COLOR_TEXT: str = _theme["text"]
COLOR_TEXT_SECONDARY: str = _theme["text_secondary"]
COLOR_PRIMARY_CONTAINER: str = _theme["primary_container"]
COLOR_ON_PRIMARY_CONTAINER: str = _theme["on_primary_container"]
COLOR_ON_SURFACE_VARIANT: str = _theme["on_surface_variant"]
COLOR_SURFACE_TONAL: str = _theme["surface_tonal"]
COLOR_TOAST_WARNING: str = _theme["toast_warning"]
COLOR_TOAST_ERROR: str = _theme["toast_error"]
COLOR_TOAST_INFO: str = _theme["toast_info"]


def refresh_colors() -> None:
    """Update module-level color constants from current theme dict."""
    global COLOR_BACKGROUND, COLOR_SURFACE, COLOR_PRIMARY, COLOR_ON_PRIMARY
    global COLOR_ERROR, COLOR_SUCCESS, COLOR_WARNING, COLOR_DIVIDER
    global COLOR_TEXT, COLOR_TEXT_SECONDARY
    global COLOR_PRIMARY_CONTAINER, COLOR_ON_PRIMARY_CONTAINER
    global COLOR_ON_SURFACE_VARIANT, COLOR_SURFACE_TONAL
    global COLOR_TOAST_WARNING, COLOR_TOAST_ERROR, COLOR_TOAST_INFO

    COLOR_BACKGROUND = _theme["background"]
    COLOR_SURFACE = _theme["surface"]
    COLOR_PRIMARY = _theme["primary"]
    COLOR_ON_PRIMARY = _theme["on_primary"]
    COLOR_ERROR = _theme["error"]
    COLOR_SUCCESS = _theme["success"]
    COLOR_WARNING = _theme["warning"]
    COLOR_DIVIDER = _theme["divider"]
    COLOR_TEXT = _theme["text"]
    COLOR_TEXT_SECONDARY = _theme["text_secondary"]
    COLOR_PRIMARY_CONTAINER = _theme["primary_container"]
    COLOR_ON_PRIMARY_CONTAINER = _theme["on_primary_container"]
    COLOR_ON_SURFACE_VARIANT = _theme["on_surface_variant"]
    COLOR_SURFACE_TONAL = _theme["surface_tonal"]
    COLOR_TOAST_WARNING = _theme["toast_warning"]
    COLOR_TOAST_ERROR = _theme["toast_error"]
    COLOR_TOAST_INFO = _theme["toast_info"]


# ---------------------------------------------------------------------------
# CTk appearance
# ---------------------------------------------------------------------------

CTK_APPEARANCE_MODE: str = "light"

CTK_COLOR_THEME: dict[str, str] = {
    "CTk": COLOR_BACKGROUND,
    "CTkToplevel": COLOR_BACKGROUND,
    "CTkFrame": COLOR_SURFACE,
    "CTkButton": COLOR_PRIMARY,
    "CTkEntry": "#FFFFFF",
    "CTkLabel": COLOR_TEXT,
}

# ---------------------------------------------------------------------------
# Font defaults
# ---------------------------------------------------------------------------

FONT_FAMILY_DEFAULT: str = "NanumSquareRound"
FONT_FAMILY_FALLBACK: str = "Segoe UI"

FONT_SIZE_TITLE: int = 22
FONT_SIZE_HEADING: int = 18
FONT_SIZE_BODY: int = 15
FONT_SIZE_SMALL: int = 13
FONT_SIZE_CAPTION: int = 12

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

CONTENT_PAD_X: int = 3
CONTENT_PAD_Y: int = 20

STATUS_BAR_HEIGHT: int = 32
NAV_BUTTON_CORNER_RADIUS: int = 8

CARD_CORNER_RADIUS: int = 12
CARD_PAD_X: int = 16
CARD_PAD_Y: int = 12

TOAST_WIDTH: int = 320
TOAST_HEIGHT: int = 48
TOAST_DURATION_MS: int = 3000

# Window geometry
WINDOW_MIN_WIDTH: int = 535
WINDOW_MIN_HEIGHT: int = 555
WINDOW_DEFAULT_WIDTH: int = 535
WINDOW_DEFAULT_HEIGHT: int = 555
