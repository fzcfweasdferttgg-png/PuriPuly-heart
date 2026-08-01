from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from puripuly_heart.ui.overlay_calibration import OverlayCalibration

from .constants import (
    DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
    DESKTOP_FLET_DEFAULT_HEIGHT,
    DESKTOP_FLET_DEFAULT_SIZE_PRESET,
    DESKTOP_FLET_DEFAULT_TEXT_SCALE,
    DESKTOP_FLET_DEFAULT_WIDTH,
    DESKTOP_FLET_MAX_BACKGROUND_ALPHA,
    DESKTOP_FLET_MAX_OUTLINE_WIDTH,
    DESKTOP_FLET_MAX_TEXT_SCALE,
    DESKTOP_FLET_MIN_BACKGROUND_ALPHA,
    DESKTOP_FLET_MIN_HEIGHT,
    DESKTOP_FLET_MIN_OUTLINE_WIDTH,
    DESKTOP_FLET_MIN_TEXT_SCALE,
    DESKTOP_FLET_MIN_WIDTH,
    DESKTOP_FLET_SIZE_PRESET_ORDER,
    DESKTOP_FLET_SIZE_PRESETS,
    OVERLAY_TARGET_STEAMVR,
    OVERLAY_TARGET_VALUES,
)


def _parse_overlay_target(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in OVERLAY_TARGET_VALUES:
            return normalized
    return OVERLAY_TARGET_STEAMVR


def _finite_non_bool_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number):
        return None
    return value


def _clamp_float(value: float, *, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _normalize_desktop_flet_bounds_position(
    x_value: object,
    y_value: object,
) -> tuple[int | float | None, int | float | None]:
    return _normalize_desktop_flet_position(x_value, y_value)


def _normalize_desktop_flet_position(
    x_value: object,
    y_value: object,
) -> tuple[int | float | None, int | float | None]:
    x = _finite_non_bool_number(x_value)
    y = _finite_non_bool_number(y_value)
    if x is None or y is None:
        return None, None
    return x, y


def _normalize_desktop_flet_dimension(
    value: object,
    *,
    default: int,
    minimum: int,
) -> int | float:
    number = _finite_non_bool_number(value)
    if number is None:
        return default
    return max(number, minimum)


def _normalize_desktop_flet_range(
    value: object,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    number = _finite_non_bool_number(value)
    if number is None:
        return default
    return _clamp_float(number, minimum=minimum, maximum=maximum)


def _parse_desktop_flet_size_preset(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in DESKTOP_FLET_SIZE_PRESET_ORDER:
            return normalized
    return DESKTOP_FLET_DEFAULT_SIZE_PRESET


def _desktop_flet_dimensions_for_preset(preset: object) -> tuple[int, int]:
    return DESKTOP_FLET_SIZE_PRESETS[_parse_desktop_flet_size_preset(preset)]


def _valid_desktop_flet_legacy_dimension(value: object) -> int | float | None:
    number = _finite_non_bool_number(value)
    if number is None or number <= 0:
        return None
    return number


def _nearest_desktop_flet_size_preset(width_value: object, height_value: object) -> str:
    width = _valid_desktop_flet_legacy_dimension(width_value)
    height = _valid_desktop_flet_legacy_dimension(height_value)
    if width is None or height is None:
        return DESKTOP_FLET_DEFAULT_SIZE_PRESET

    scores: list[tuple[str, float]] = []
    for preset in DESKTOP_FLET_SIZE_PRESET_ORDER:
        preset_width, preset_height = DESKTOP_FLET_SIZE_PRESETS[preset]
        score = (
            abs(width - preset_width) / preset_width + abs(height - preset_height) / preset_height
        )
        scores.append((preset, score))

    lowest_score = min(score for _preset, score in scores)
    tied = [
        preset
        for preset, score in scores
        if math.isclose(score, lowest_score, rel_tol=0.0, abs_tol=1e-12)
    ]
    if DESKTOP_FLET_DEFAULT_SIZE_PRESET in tied:
        return DESKTOP_FLET_DEFAULT_SIZE_PRESET
    return tied[0]


def _parse_desktop_flet_bounds(value: object) -> DesktopFletOverlayBounds:
    if isinstance(value, DesktopFletOverlayBounds):
        bounds = copy.deepcopy(value)
        bounds.validate()
        return bounds
    data = value if isinstance(value, dict) else {}
    x, y = _normalize_desktop_flet_bounds_position(data.get("x"), data.get("y"))
    return DesktopFletOverlayBounds(
        x=x,
        y=y,
        width=_normalize_desktop_flet_dimension(
            data.get("width"),
            default=DESKTOP_FLET_DEFAULT_WIDTH,
            minimum=DESKTOP_FLET_MIN_WIDTH,
        ),
        height=_normalize_desktop_flet_dimension(
            data.get("height"),
            default=DESKTOP_FLET_DEFAULT_HEIGHT,
            minimum=DESKTOP_FLET_MIN_HEIGHT,
        ),
    )


def _parse_desktop_flet_position(value: object) -> DesktopFletOverlayPosition:
    if isinstance(value, DesktopFletOverlayPosition):
        position = copy.deepcopy(value)
        position.validate()
        return position
    data: dict[str, object]
    if isinstance(value, DesktopFletOverlayBounds):
        data = {"x": value.x, "y": value.y}
    else:
        data = value if isinstance(value, dict) else {}
    x, y = _normalize_desktop_flet_position(data.get("x"), data.get("y"))
    return DesktopFletOverlayPosition(x=x, y=y)


def _parse_desktop_flet_visual(value: object) -> DesktopFletOverlayVisualSettings:
    if isinstance(value, DesktopFletOverlayVisualSettings):
        visual = copy.deepcopy(value)
        visual.validate()
        return visual
    data = value if isinstance(value, dict) else {}
    return DesktopFletOverlayVisualSettings(
        background_alpha=_normalize_desktop_flet_range(
            data.get("background_alpha"),
            default=DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
            minimum=DESKTOP_FLET_MIN_BACKGROUND_ALPHA,
            maximum=DESKTOP_FLET_MAX_BACKGROUND_ALPHA,
        ),
    )


def _parse_desktop_flet_settings(value: object) -> DesktopFletOverlaySettings:
    if isinstance(value, DesktopFletOverlaySettings):
        settings = copy.deepcopy(value)
        settings.validate()
        return settings
    data = value if isinstance(value, dict) else {}
    bounds_data = data.get("bounds") if isinstance(data.get("bounds"), dict) else {}
    size_preset = (
        _parse_desktop_flet_size_preset(data.get("size_preset"))
        if "size_preset" in data
        else _nearest_desktop_flet_size_preset(
            bounds_data.get("width"),
            bounds_data.get("height"),
        )
    )
    position = (
        _parse_desktop_flet_position(data.get("position"))
        if "position" in data
        else _parse_desktop_flet_position(bounds_data)
    )
    return DesktopFletOverlaySettings(
        size_preset=size_preset,
        position=position,
        locked=False,
        visual=_parse_desktop_flet_visual(data.get("visual")),
    )


def _desktop_flet_visual_to_dict(
    visual: DesktopFletOverlayVisualSettings,
) -> dict[str, float]:
    if not isinstance(visual, DesktopFletOverlayVisualSettings):
        visual = DesktopFletOverlayVisualSettings()
    visual = copy.deepcopy(visual)
    visual.validate()
    return {"background_alpha": visual.background_alpha}


def _desktop_flet_settings_to_dict(settings: DesktopFletOverlaySettings) -> dict[str, object]:
    if not isinstance(settings, DesktopFletOverlaySettings):
        settings = DesktopFletOverlaySettings()
    settings = copy.deepcopy(settings)
    settings.validate()
    return {
        "size_preset": settings.size_preset,
        "position": {"x": settings.position.x, "y": settings.position.y},
        "visual": _desktop_flet_visual_to_dict(settings.visual),
    }


@dataclass(slots=True)
class DesktopFletOverlayBounds:
    x: int | float | None = None
    y: int | float | None = None
    width: int | float = DESKTOP_FLET_DEFAULT_WIDTH
    height: int | float = DESKTOP_FLET_DEFAULT_HEIGHT

    def validate(self) -> None:
        self.x, self.y = _normalize_desktop_flet_bounds_position(self.x, self.y)
        self.width = _normalize_desktop_flet_dimension(
            self.width,
            default=DESKTOP_FLET_DEFAULT_WIDTH,
            minimum=DESKTOP_FLET_MIN_WIDTH,
        )
        self.height = _normalize_desktop_flet_dimension(
            self.height,
            default=DESKTOP_FLET_DEFAULT_HEIGHT,
            minimum=DESKTOP_FLET_MIN_HEIGHT,
        )


@dataclass(slots=True)
class DesktopFletOverlayPosition:
    x: int | float | None = None
    y: int | float | None = None

    def validate(self) -> None:
        self.x, self.y = _normalize_desktop_flet_position(self.x, self.y)


@dataclass(slots=True, init=False)
class DesktopFletOverlayVisualSettings:
    background_alpha: float = DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA

    def __init__(
        self,
        text_scale: object = None,
        background_alpha: object = DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
        outline_width: object = None,
    ) -> None:
        _ = (text_scale, outline_width)
        self.background_alpha = background_alpha

    def validate(self) -> None:
        self.background_alpha = _normalize_desktop_flet_range(
            self.background_alpha,
            default=DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
            minimum=DESKTOP_FLET_MIN_BACKGROUND_ALPHA,
            maximum=DESKTOP_FLET_MAX_BACKGROUND_ALPHA,
        )

    @property
    def text_scale(self) -> float:
        return DESKTOP_FLET_DEFAULT_TEXT_SCALE

    @text_scale.setter
    def text_scale(self, _value: object) -> None:
        return

    @property
    def outline_width(self) -> None:
        return None

    @outline_width.setter
    def outline_width(self, _value: object) -> None:
        return


@dataclass(slots=True)
class DesktopFletOverlaySettings:
    size_preset: str = DESKTOP_FLET_DEFAULT_SIZE_PRESET
    position: DesktopFletOverlayPosition = field(default_factory=DesktopFletOverlayPosition)
    locked: bool = False
    visual: DesktopFletOverlayVisualSettings = field(
        default_factory=DesktopFletOverlayVisualSettings
    )

    def validate(self) -> None:
        self.size_preset = _parse_desktop_flet_size_preset(self.size_preset)
        if not isinstance(self.position, DesktopFletOverlayPosition):
            self.position = DesktopFletOverlayPosition()
        if not isinstance(self.locked, bool):
            self.locked = False
        if not isinstance(self.visual, DesktopFletOverlayVisualSettings):
            self.visual = DesktopFletOverlayVisualSettings()
        self.position.validate()
        self.visual.validate()

    @property
    def bounds(self) -> DesktopFletOverlayBounds:
        width, height = _desktop_flet_dimensions_for_preset(self.size_preset)
        return DesktopFletOverlayBounds(
            x=self.position.x,
            y=self.position.y,
            width=width,
            height=height,
        )

    @bounds.setter
    def bounds(self, value: object) -> None:
        bounds = _parse_desktop_flet_bounds(value)
        self.size_preset = _nearest_desktop_flet_size_preset(bounds.width, bounds.height)
        self.position = DesktopFletOverlayPosition(x=bounds.x, y=bounds.y)


@dataclass(slots=True)
class OverlaySettings:
    target: str = OVERLAY_TARGET_STEAMVR
    show_translation: bool = True
    show_peer_original: bool = True
    calibration: OverlayCalibration = field(default_factory=OverlayCalibration)
    desktop_flet: DesktopFletOverlaySettings = field(default_factory=DesktopFletOverlaySettings)

    def validate(self) -> None:
        self.target = _parse_overlay_target(self.target)
        if not isinstance(self.show_translation, bool):
            raise ValueError("overlay show_translation must be a bool")
        if not isinstance(self.show_peer_original, bool):
            raise ValueError("overlay show_peer_original must be a bool")
        self.calibration.validate()
        if not isinstance(self.desktop_flet, DesktopFletOverlaySettings):
            self.desktop_flet = DesktopFletOverlaySettings()
        self.desktop_flet.validate()
