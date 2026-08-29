"""Font management for the Tkinter/CTk GUI.

Detects CJK font files from data/fonts/ and provides helpers that return
CTk-compatible font tuples.  Falls back gracefully when custom fonts
are unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from puripuly_heart.domain.language import get_language_info

# ---------------------------------------------------------------------------
# Font family constants (mirrors ui/fonts.py)
# ---------------------------------------------------------------------------

FONT_FAMILY_NANUM: str = "NanumSquareRound"
FONT_FAMILY_MPLUS: str = "MPLUSRounded1c"
FONT_FAMILY_RESOURCE_HAN_CN: str = "ResourceHanRoundedCN"

_FONT_FILE_CANDIDATES: dict[str, tuple[str, ...]] = {
    FONT_FAMILY_NANUM: (
        "NanumSquareRoundEB.ttf",
        "NanumSquareRoundB.ttf",
        "NanumSquareRound-Bold.ttf",
    ),
    FONT_FAMILY_MPLUS: (
        "MPLUSRounded1c-Bold.ttf",
        "MPLUSRounded1c-Bold.otf",
    ),
    FONT_FAMILY_RESOURCE_HAN_CN: ("ResourceHanRoundedCN-Bold.ttf",),
}

# Map font family → list of resolved absolute paths (populated lazily)
_font_paths_cache: dict[str, list[str]] = {}
_cache_populated: bool = False


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def assets_dir() -> Path:
    """Return the data/ directory alongside the package."""
    from puripuly_heart.ui_tkinter import __file__ as _pkg_file

    return Path(_pkg_file).resolve().parents[1] / "data"


def fonts_dir() -> Path:
    """Return the data/fonts/ directory."""
    return assets_dir() / "fonts"


def _populate_cache() -> None:
    """Scan fonts_dir() once and populate _font_paths_cache."""
    global _cache_populated
    if _cache_populated:
        return
    _cache_populated = True

    root = fonts_dir()
    if not root.is_dir():
        return
    for family, candidates in _FONT_FILE_CANDIDATES.items():
        found: list[str] = []
        for name in candidates:
            path = root / name
            if path.is_file():
                found.append(str(path))
        if found:
            _font_paths_cache[family] = found


def _font_available(family: str) -> bool:
    _populate_cache()
    return family in _font_paths_cache


def _first_font_path(family: str) -> str | None:
    _populate_cache()
    paths = _font_paths_cache.get(family)
    return paths[0] if paths else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def default_font_family() -> str:
    """Return the preferred default font family, with fallback."""
    if _font_available(FONT_FAMILY_NANUM):
        return FONT_FAMILY_NANUM
    return "Segoe UI"


def font_for_language(code: str | None) -> str:
    """Return the appropriate font family for *code* (e.g. 'ko', 'ja', 'zh-CN').

    Falls back to NanumSquareRound → system default.
    """
    if not code:
        return default_font_family()

    info = get_language_info(code)
    lang_code = info.code if info else code

    if lang_code == "zh-CN":
        return _resolve_family(FONT_FAMILY_RESOURCE_HAN_CN)

    base_code = lang_code.split("-")[0].lower()
    if base_code == "ja":
        return _resolve_family(FONT_FAMILY_MPLUS)
    if base_code in ("ko", "en"):
        return _resolve_family(FONT_FAMILY_NANUM)

    return default_font_family()


def _resolve_family(family: str) -> str:
    if _font_available(family):
        return family
    return default_font_family()


def register_tk_font(family: str, *, root: Any = None) -> bool:
    """Register a custom TTF font with Tk via ``root.call("font", ...)``.

    Uses ``tkinter.font.Font`` to register if a root window is available.
    Returns True on success.
    """
    path = _first_font_path(family)
    if not path:
        return False

    try:
        import tkinter as tk
        import tkinter.font as tkfont

        if root is None:
            root = tk._default_root  # type: ignore[attr-defined]
        if root is None:
            return False

        root.call("font", "create", family, "-file", path)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CTk font-tuple helpers
# ---------------------------------------------------------------------------

def get_font(size: int = 13, weight: str = "normal") -> tuple[str, int, str]:
    """Return a CTk-compatible (family, size, weight) tuple.

    *weight* is ``"normal"`` or ``"bold"``.
    """
    family = default_font_family()
    return (family, size, weight)


def font_for_ctk(language_code: str | None, size: int = 13, weight: str = "normal") -> tuple[str, int, str]:
    """Return a CTk font tuple selected for *language_code*."""
    family = font_for_language(language_code)
    return (family, size, weight)
