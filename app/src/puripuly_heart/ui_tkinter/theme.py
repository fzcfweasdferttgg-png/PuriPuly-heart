"""Theme constants for the Tkinter/CTk GUI.

Color palette derived from the Flet Material Design 3 seed (#FF6B6B).
Provides hex colors, CTk appearance settings, font references,
and layout constants consumed by every Tkinter view and component.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Color palette — warm coral / rose
# ---------------------------------------------------------------------------

COLOR_BACKGROUND: str = "#FFF8F6"  # Window surface
COLOR_SURFACE: str = "#FFF0EE"  # Panel / card surface
COLOR_PRIMARY: str = "#FF6B6B"  # Primary accent (coral)
COLOR_ERROR: str = "#FF5449"  # Error states
COLOR_SUCCESS: str = "#66BB6A"  # Success states
COLOR_WARNING: str = "#FF8A65"  # Warning states (coral orange)
COLOR_DIVIDER: str = "#E8D4D2"  # Dividers / borders

COLOR_TEXT: str = "#5C4D4C"  # Primary text (neutral dark)
COLOR_TEXT_SECONDARY: str = "#B78481"  # Secondary / muted text

COLOR_SIDEBAR: str = "#FFF0EE"  # Sidebar background
COLOR_SIDEBAR_ACTIVE: str = "#FFDAD8"  # Sidebar active button bg
COLOR_NAV_ACTIVE: str = "#FF6B6B"  # Active nav text/icon
COLOR_NAV_INACTIVE: str = "#998E8D"  # Inactive nav text/icon

COLOR_PRIMARY_CONTAINER: str = "#FFDAD8"
COLOR_ON_PRIMARY_CONTAINER: str = "#733332"
COLOR_ON_SURFACE_VARIANT: str = "#534341"
COLOR_SURFACE_TONAL: str = "#FCEBE9"  # Hover / alternative surface

# Notification toast colors
COLOR_TOAST_WARNING: str = "#FF8A65"
COLOR_TOAST_ERROR: str = "#FF5449"
COLOR_TOAST_INFO: str = "#66BB6A"

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

FONT_SIZE_TITLE: int = 20
FONT_SIZE_HEADING: int = 16
FONT_SIZE_BODY: int = 13
FONT_SIZE_SMALL: int = 11
FONT_SIZE_CAPTION: int = 10

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

SIDEBAR_WIDTH: int = 220
SIDEBAR_PAD_X: int = 12
SIDEBAR_PAD_Y: int = 8
SIDEBAR_BUTTON_HEIGHT: int = 40
SIDEBAR_BUTTON_CORNER: int = 10

CONTENT_PAD_X: int = 24
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
WINDOW_MIN_WIDTH: int = 1024
WINDOW_MIN_HEIGHT: int = 760
WINDOW_DEFAULT_WIDTH: int = 1136
WINDOW_DEFAULT_HEIGHT: int = 850
