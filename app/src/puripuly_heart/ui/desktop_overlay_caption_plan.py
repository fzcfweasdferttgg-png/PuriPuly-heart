from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from puripuly_heart.config.settings import (
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
    DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER,
    DESKTOP_FLET_SIZE_PRESET_ORDER,
    DESKTOP_FLET_SIZE_PRESETS,
    DesktopFletOverlayVisualSettings,
)
from puripuly_heart.domain.overlay_types import OverlayPresentationBlock, OverlayPresentationSnapshot
from puripuly_heart.domain.i18n import t_for_locale

# ---------------------------------------------------------------------------
# Section A — Constants
# ---------------------------------------------------------------------------

_DESKTOP_CAPTION_WHITE = "#FFFFFF"
_DESKTOP_CAPTION_GOLD = "#FFD700"
_DESKTOP_CAPTION_LATIN_FONT_FAMILY = "Noto Sans"
_DESKTOP_CAPTION_CJK_FONT_FAMILY = "Noto Sans CJK JP"
_DESKTOP_CAPTION_CJK_LANGUAGE_PRIMARY_SUBTAGS = frozenset(
    {"ko", "kor", "ja", "jpn", "zh", "zho", "chi", "cmn", "yue"}
)
_DESKTOP_CAPTION_BACKGROUND_RGB = "000000"
_DESKTOP_CAPTION_TRANSPARENT = "transparent"
_DESKTOP_CAPTION_MAX_VISIBLE_SLOTS = 2
_DESKTOP_CAPTION_MAX_VISIBLE_LINES = 6
_DESKTOP_CAPTION_PRIMARY_MAX_LINES = 2
_DESKTOP_CAPTION_SECONDARY_MAX_LINES = 1
_DESKTOP_CAPTION_LINE_HEIGHT = 1.24
_DESKTOP_CAPTION_PRIMARY_REGION_ALIGNMENT_Y = -0.5
_DESKTOP_CAPTION_TEXT_STACK_ALIGNMENT_Y = -0.08
_DESKTOP_CAPTION_MIN_DYNAMIC_CARD_WIDTH = 320.0
_DESKTOP_CAPTION_DYNAMIC_WIDTH_SAFETY = 24.0
_DESKTOP_CAPTION_CJK_WIDTH_EM = 1.0
_DESKTOP_CAPTION_LATIN_WIDE_WIDTH_EM = 0.62
_DESKTOP_CAPTION_LATIN_NARROW_WIDTH_EM = 0.42
_DESKTOP_CAPTION_SPACE_WIDTH_EM = 0.32
_DESKTOP_CAPTION_PUNCT_WIDTH_EM = 0.38
_DESKTOP_CAPTION_EMOJI_WIDTH_EM = 1.15
_DESKTOP_CAPTION_CONTACT_SHADOW_COLOR = "#C0000000"
_DESKTOP_CAPTION_CONTACT_SHADOW_OFFSET = (0, 1)
_DESKTOP_CAPTION_CONTACT_SHADOW_BLUR = 1.0
_DESKTOP_CAPTION_AMBIENT_SHADOW_COLOR = "#66000000"
_DESKTOP_CAPTION_AMBIENT_SHADOW_OFFSET = (0, 0)
_DESKTOP_CAPTION_AMBIENT_SHADOW_BLUR = 3.0
_DESKTOP_CAPTION_OVERFLOW_STRATEGY = (
    "two-turn-slots:presenter-selected-blocks,primary-two-lines,secondary-one-line"
)
_DESKTOP_INTERACTION_MODE_EDIT = "edit"
_DESKTOP_INTERACTION_MODE_PASS_THROUGH = "pass_through"
_DESKTOP_INTERACTION_MODES = {
    _DESKTOP_INTERACTION_MODE_EDIT,
    _DESKTOP_INTERACTION_MODE_PASS_THROUGH,
}
_DESKTOP_PREVIEW_BACKGROUND_ALPHA_PRESETS = (0.35, 0.5, 0.6, 0.8)
_DESKTOP_PREVIEW_DEFAULT_BACKGROUND_ALPHA = DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
_DESKTOP_PREVIEW_DEFAULT_BACKGROUND_SURFACE_ID = "bright"
_DESKTOP_PREVIEW_BACKGROUND_SURFACE_DATA = (
    ("bright", "flet.settings.overlay.desktop.preview.background_surface.bright", "#FFFFFF"),
    ("dark", "flet.settings.overlay.desktop.preview.background_surface.dark", "#111827"),
    ("busy", "flet.settings.overlay.desktop.preview.background_surface.busy", "#1F2937"),
)
_DESKTOP_EMPTY_LOCK_ACTION_I18N_KEY = "flet.settings.overlay.desktop.empty_state.action.lock"
_DESKTOP_EMPTY_LOCK_ACTION_DEFAULT_LABEL = "Lock"
_DESKTOP_EMPTY_LOCK_ACTION_DEFAULT_COLOR = "#FFF8F4"
_DESKTOP_EMPTY_LOCK_ACTION_FOCUS_COLOR = "#FF6B6B"
_DESKTOP_EMPTY_LOCK_ACTION_MIN_HIT_TARGET = 44
_DESKTOP_EMPTY_LOCK_ACTION_HORIZONTAL_PADDING = 28
_DESKTOP_EMPTY_LOCK_ACTION_VERTICAL_PADDING = 12
_DESKTOP_EMPTY_LOCK_ACTION_TEXT_WIDTH_SAFETY = 24

# ---------------------------------------------------------------------------
# Section B — Helper function
# ---------------------------------------------------------------------------


def _desktop_caption_color_for_channel(channel: str) -> str:
    if channel == "peer":
        return _DESKTOP_CAPTION_GOLD
    return _DESKTOP_CAPTION_WHITE


# ---------------------------------------------------------------------------
# Section C — DesktopCaptionMappingRule + mapping table
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DesktopCaptionMappingRule:
    snapshot_field: str
    block_type: str
    role: str
    slot: str
    promoted: bool
    color: str
    priority: str
    truncation: str


DESKTOP_CAPTION_MAPPING_TABLE: tuple[DesktopCaptionMappingRule, ...] = (
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="active_self/self",
        role="active_self_source",
        slot="primary",
        promoted=False,
        color=_DESKTOP_CAPTION_WHITE,
        priority="100 newest active/interim source",
        truncation="max 2 lines; retained before secondary and finalized lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="active_self/self",
        role="active_self_translation",
        slot="secondary",
        promoted=False,
        color=_desktop_caption_color_for_channel("self"),
        priority="85 active/interim secondary",
        truncation="max 1 line; drops before active primary",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="active_peer/peer",
        role="active_peer_source",
        slot="primary",
        promoted=True,
        color=_desktop_caption_color_for_channel("peer"),
        priority="95 newest active/interim peer source",
        truncation="max 2 lines; retained before finalized secondary lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/peer translated",
        role="peer_translation",
        slot="primary",
        promoted=False,
        color=_DESKTOP_CAPTION_GOLD,
        priority="90 peer translated primary; newer appearance wins ties",
        truncation="max 2 lines; outranks older finalized source/self lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/peer translated",
        role="peer_source_original",
        slot="secondary",
        promoted=False,
        color=_desktop_caption_color_for_channel("peer"),
        priority="70 peer original/source secondary",
        truncation="max 1 line; drops before peer translated primary",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/peer source-only",
        role="peer_source_original",
        slot="primary",
        promoted=True,
        color=_desktop_caption_color_for_channel("peer"),
        priority="60 peer source-only finalized",
        truncation="max 2 lines; drops before active and translated primary lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/self",
        role="self_source",
        slot="primary",
        promoted=False,
        color=_DESKTOP_CAPTION_WHITE,
        priority="65 self/source finalized; newer appearance wins ties",
        truncation="max 2 lines; older finalized drops first",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/self",
        role="self_translation",
        slot="secondary",
        promoted=False,
        color=_desktop_caption_color_for_channel("self"),
        priority="50 self translation secondary",
        truncation="max 1 line; drops before finalized primary lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="finalized/self secondary-only",
        role="self_translation",
        slot="primary",
        promoted=True,
        color=_desktop_caption_color_for_channel("self"),
        priority="55 self translation secondary-only promoted primary",
        truncation="max 2 lines; drops before active and peer translated primary lines",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="calibration",
        block_type="all",
        role="desktop_visual_ignored",
        slot="none",
        promoted=False,
        color="none",
        priority="not rendered",
        truncation="desktop caption visual state comes from repaired desktop visual config",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="none/edit",
        role="edit_no_caption_empty_card",
        slot="none",
        promoted=False,
        color="none",
        priority="0 edit-mode empty caption surface",
        truncation="renders empty caption card with centered lock text action",
    ),
    DesktopCaptionMappingRule(
        snapshot_field="blocks[]",
        block_type="none/pass_through",
        role="pass_through_no_caption",
        slot="none",
        promoted=False,
        color="none",
        priority="not rendered",
        truncation="renders no text and no background",
    ),
)

# ---------------------------------------------------------------------------
# Section D — Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DesktopCaptionSizePreset:
    id: str
    window_width: int
    window_height: int
    primary_font_size: int
    secondary_font_size: int
    padding_horizontal: int
    padding_vertical: int
    border_radius: int
    slot_gap: int


_DESKTOP_CAPTION_SIZE_PRESETS: dict[str, DesktopCaptionSizePreset] = {
    "tiny": DesktopCaptionSizePreset("tiny", 640, 160, 20, 12, 10, 2, 10, 4),
    "xsmall": DesktopCaptionSizePreset("xsmall", 960, 240, 29, 18, 14, 6, 12, 6),
    "small": DesktopCaptionSizePreset("small", 1152, 288, 35, 21, 18, 8, 14, 8),
    "medium": DesktopCaptionSizePreset("medium", 1344, 336, 41, 25, 22, 10, 16, 10),
    "large": DesktopCaptionSizePreset("large", 1600, 400, 50, 30, 26, 12, 18, 12),
    "xlarge": DesktopCaptionSizePreset("xlarge", 1792, 448, 56, 34, 30, 14, 20, 14),
}


@dataclass(frozen=True, slots=True)
class DesktopCaptionVisualState:
    text_scale: float = DESKTOP_FLET_DEFAULT_TEXT_SCALE
    background_alpha: float = DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
    outline_width: float | None = None


@dataclass(frozen=True, slots=True)
class DesktopCaptionLine:
    text: str
    role: str
    slot: str
    color: str
    priority: int
    block_id: str
    channel: str
    block_variant: str
    appearance_seq: int
    max_lines: int
    font_size: int
    font_family: str | None
    line_height: float = _DESKTOP_CAPTION_LINE_HEIGHT
    weight: str = "semibold"
    promoted: bool = False
    active: bool = False


@dataclass(frozen=True, slots=True)
class DesktopCaptionSlot:
    block_id: str
    occupant_key: str
    channel: str
    block_variant: str
    appearance_seq: int
    lines: tuple[DesktopCaptionLine, ...]
    secondary_enabled: bool
    card_width: float = 0.0
    card_text_width: float = 0.0
    active: bool = False


@dataclass(frozen=True, slots=True)
class DesktopCaptionPlan:
    slots: tuple[DesktopCaptionSlot, ...]
    lines: tuple[DesktopCaptionLine, ...]
    size_preset: str
    window_width: int
    window_height: int
    text_width: int
    primary_font_size: int
    secondary_font_size: int
    outline_width: float
    padding_horizontal: int
    padding_vertical: int
    slot_gap: int
    slot_height: float
    primary_region_height: float
    secondary_region_height: float
    border_radius: int
    background_alpha: float
    background_color: str
    surface_visible: bool
    full_window_background_visible: bool
    no_scrollbars: bool = True
    max_visible_lines: int = _DESKTOP_CAPTION_MAX_VISIBLE_LINES
    max_visible_slots: int = _DESKTOP_CAPTION_MAX_VISIBLE_SLOTS
    secondary_line_max_lines: int = _DESKTOP_CAPTION_SECONDARY_MAX_LINES
    overflow_strategy: str = _DESKTOP_CAPTION_OVERFLOW_STRATEGY


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewFixture:
    id: str
    label: str
    i18n_key: str
    snapshot: OverlayPresentationSnapshot
    coverage_tags: frozenset[str]


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewSizePreset:
    id: str
    label: str
    i18n_key: str
    window_width: int
    window_height: int
    primary_font_size: int
    secondary_font_size: int
    padding_horizontal: int
    padding_vertical: int
    border_radius: int


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewBackgroundSurface:
    id: str
    label: str
    i18n_key: str
    bgcolor: str


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewLabels:
    fixture: str
    size_preset: str
    background_alpha: str
    background_surface: str


@dataclass(frozen=True, slots=True)
class DesktopOverlayPreviewCatalog:
    fixtures: tuple[DesktopOverlayPreviewFixture, ...]
    background_surfaces: tuple[DesktopOverlayPreviewBackgroundSurface, ...]
    size_presets: tuple[DesktopOverlayPreviewSizePreset, ...]
    background_alpha_presets: tuple[float, ...]
    labels: DesktopOverlayPreviewLabels


_DESKTOP_PREVIEW_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b", re.IGNORECASE),
    ),
    (
        "api_key",
        re.compile(r"\b(?:sk|rk|pk)-(?:live|prod|test)?-?[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    ),
)

# ---------------------------------------------------------------------------
# Section E — build_desktop_caption_plan
# ---------------------------------------------------------------------------


def build_desktop_caption_plan(
    snapshot: OverlayPresentationSnapshot,
    *,
    window_width: int | float = DESKTOP_FLET_DEFAULT_WIDTH,
    window_height: int | float = DESKTOP_FLET_DEFAULT_HEIGHT,
    visual_state: DesktopCaptionVisualState | None = None,
    interaction_mode: str = "pass_through",
    locale: str | None = None,
) -> DesktopCaptionPlan:
    """Map the current overlay snapshot contract into a deterministic caption plan."""

    width = _positive_int_or_default(window_width, DESKTOP_FLET_DEFAULT_WIDTH)
    height = _positive_int_or_default(window_height, DESKTOP_FLET_DEFAULT_HEIGHT)
    visual = _validated_visual_state(visual_state)
    preset = _desktop_caption_size_preset_for_dimensions(width, height)
    primary_font_size = preset.primary_font_size
    secondary_font_size = preset.secondary_font_size
    outline_width = 0.0
    _ = locale

    candidate_slots = _caption_slots_for_snapshot(
        snapshot,
        primary_font_size=primary_font_size,
        secondary_font_size=secondary_font_size,
    )
    slots = tuple(
        _caption_slot_with_dynamic_width(
            slot,
            padding_horizontal=preset.padding_horizontal,
            max_card_width=width,
        )
        for slot in candidate_slots[:_DESKTOP_CAPTION_MAX_VISIBLE_SLOTS]
    )
    lines = tuple(line for slot in slots for line in slot.lines)

    full_window_background_visible = interaction_mode == _DESKTOP_INTERACTION_MODE_EDIT
    surface_visible = bool(slots) or full_window_background_visible
    background_alpha = 0.0
    if surface_visible:
        background_alpha = visual.background_alpha
    slot_height = max(
        1.0,
        (float(height) - preset.slot_gap) / _DESKTOP_CAPTION_MAX_VISIBLE_SLOTS,
    )
    primary_region_height = (
        primary_font_size * _DESKTOP_CAPTION_LINE_HEIGHT * _DESKTOP_CAPTION_PRIMARY_MAX_LINES
    )
    secondary_region_height = (
        secondary_font_size * _DESKTOP_CAPTION_LINE_HEIGHT * _DESKTOP_CAPTION_SECONDARY_MAX_LINES
    )
    return DesktopCaptionPlan(
        slots=slots,
        lines=lines,
        size_preset=preset.id,
        window_width=width,
        window_height=height,
        text_width=max(1, width - (preset.padding_horizontal * 2)),
        primary_font_size=primary_font_size,
        secondary_font_size=secondary_font_size,
        outline_width=outline_width,
        padding_horizontal=preset.padding_horizontal,
        padding_vertical=preset.padding_vertical,
        slot_gap=preset.slot_gap,
        slot_height=slot_height,
        primary_region_height=primary_region_height,
        secondary_region_height=secondary_region_height,
        border_radius=preset.border_radius,
        background_alpha=background_alpha,
        background_color=_caption_background_color(background_alpha),
        surface_visible=surface_visible,
        full_window_background_visible=full_window_background_visible,
    )


# ---------------------------------------------------------------------------
# Section F — Pure helper
# ---------------------------------------------------------------------------


def desktop_empty_lock_action_label(locale: str | None) -> str:
    return t_for_locale(
        locale,
        _DESKTOP_EMPTY_LOCK_ACTION_I18N_KEY,
        default=_DESKTOP_EMPTY_LOCK_ACTION_DEFAULT_LABEL,
    )


# ---------------------------------------------------------------------------
# Section G — Preview catalog builder + fixtures
# ---------------------------------------------------------------------------


def build_desktop_overlay_preview_catalog(
    *,
    locale: str | None = None,
) -> DesktopOverlayPreviewCatalog:
    """Return local-only desktop overlay preview fixtures and visual presets."""

    def text(key: str) -> str:
        return t_for_locale(locale, key)

    fixtures = tuple(
        DesktopOverlayPreviewFixture(
            id=fixture_id,
            i18n_key=i18n_key,
            label=text(i18n_key),
            snapshot=snapshot,
            coverage_tags=frozenset(coverage_tags),
        )
        for fixture_id, i18n_key, snapshot, coverage_tags in _desktop_preview_fixture_data()
    )
    size_presets = tuple(
        _preview_size_preset(preset_id, locale=locale)
        for preset_id in DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER
    )
    background_surfaces = tuple(
        DesktopOverlayPreviewBackgroundSurface(
            id=surface_id,
            i18n_key=i18n_key,
            label=text(i18n_key),
            bgcolor=bgcolor,
        )
        for surface_id, i18n_key, bgcolor in _DESKTOP_PREVIEW_BACKGROUND_SURFACE_DATA
    )
    labels = DesktopOverlayPreviewLabels(
        fixture=text("flet.settings.overlay.desktop.preview.fixture"),
        size_preset=text("flet.settings.overlay.desktop.size.title"),
        background_alpha=text("flet.settings.overlay.desktop.preview.background_alpha"),
        background_surface=text("flet.settings.overlay.desktop.preview.background_surface"),
    )
    return DesktopOverlayPreviewCatalog(
        fixtures=fixtures,
        background_surfaces=background_surfaces,
        size_presets=size_presets,
        background_alpha_presets=_DESKTOP_PREVIEW_BACKGROUND_ALPHA_PRESETS,
        labels=labels,
    )


def _preview_size_preset(
    preset_id: str,
    *,
    locale: str | None,
) -> DesktopOverlayPreviewSizePreset:
    preset = _DESKTOP_CAPTION_SIZE_PRESETS[preset_id]
    i18n_key = f"flet.settings.overlay.desktop.size.option.{preset_id}"
    return DesktopOverlayPreviewSizePreset(
        id=preset.id,
        label=t_for_locale(locale, i18n_key),
        i18n_key=i18n_key,
        window_width=preset.window_width,
        window_height=preset.window_height,
        primary_font_size=preset.primary_font_size,
        secondary_font_size=preset.secondary_font_size,
        padding_horizontal=preset.padding_horizontal,
        padding_vertical=preset.padding_vertical,
        border_radius=preset.border_radius,
    )


def preview_fixture_secret_findings(
    catalog: DesktopOverlayPreviewCatalog | None = None,
) -> tuple[str, ...]:
    """Return redacted diagnostics for credential-like preview fixture content."""

    catalog = catalog or build_desktop_overlay_preview_catalog(locale="en")
    findings: list[str] = []
    for fixture in catalog.fixtures:
        fixture_identifier = _safe_preview_fixture_identifier(fixture.id)
        for field_path, value in _iter_preview_guard_strings(
            _preview_fixture_guard_payload(fixture)
        ):
            for pattern_name, pattern in _DESKTOP_PREVIEW_SECRET_PATTERNS:
                if pattern.search(value):
                    findings.append(
                        f"fixture {fixture_identifier} field {field_path} matched {pattern_name}"
                    )
    for field_path, value in _iter_preview_guard_strings(
        _preview_catalog_control_guard_payload(catalog)
    ):
        for pattern_name, pattern in _DESKTOP_PREVIEW_SECRET_PATTERNS:
            if pattern.search(value):
                findings.append(f"preview catalog field {field_path} matched {pattern_name}")
    return tuple(findings)


def _preview_fixture_guard_payload(fixture: DesktopOverlayPreviewFixture) -> dict[str, object]:
    return {
        "id": fixture.id,
        "label": fixture.label,
        "i18n_key": fixture.i18n_key,
        "coverage_tags": tuple(sorted(fixture.coverage_tags)),
        "snapshot": fixture.snapshot.to_dict(),
    }


def _preview_catalog_control_guard_payload(
    catalog: DesktopOverlayPreviewCatalog,
) -> dict[str, object]:
    return {
        "background_surfaces": tuple(
            {
                "id": surface.id,
                "label": surface.label,
                "i18n_key": surface.i18n_key,
                "bgcolor": surface.bgcolor,
            }
            for surface in catalog.background_surfaces
        ),
        "size_presets": tuple(
            {
                "id": preset.id,
                "label": preset.label,
                "i18n_key": preset.i18n_key,
                "window_width": preset.window_width,
                "window_height": preset.window_height,
                "primary_font_size": preset.primary_font_size,
                "secondary_font_size": preset.secondary_font_size,
                "padding_horizontal": preset.padding_horizontal,
                "padding_vertical": preset.padding_vertical,
                "border_radius": preset.border_radius,
            }
            for preset in catalog.size_presets
        ),
        "background_alpha_presets": tuple(catalog.background_alpha_presets),
        "labels": {
            "fixture": catalog.labels.fixture,
            "size_preset": catalog.labels.size_preset,
            "background_alpha": catalog.labels.background_alpha,
            "background_surface": catalog.labels.background_surface,
        },
    }


def _iter_preview_guard_strings(value: object, path: str = "") -> tuple[tuple[str, str], ...]:
    strings: list[tuple[str, str]] = []
    if isinstance(value, str):
        strings.append((path, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            key_path = str(key) if not path else f"{path}.{key}"
            strings.extend(_iter_preview_guard_strings(item, key_path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            strings.extend(_iter_preview_guard_strings(item, f"{path}[{index}]"))
    return tuple(strings)


def _safe_preview_fixture_identifier(fixture_id: str) -> str:
    for _, pattern in _DESKTOP_PREVIEW_SECRET_PATTERNS:
        if pattern.search(fixture_id):
            return "<redacted-fixture-id>"
    return fixture_id


def _desktop_preview_fixture_data() -> tuple[
    tuple[str, str, OverlayPresentationSnapshot, frozenset[str]],
    ...,
]:
    return (
        (
            "korean_long_wrap",
            "flet.settings.overlay.desktop.preview.fixture.korean_long_wrap",
            OverlayPresentationSnapshot(
                revision=1,
                blocks=[
                    _preview_block(
                        "preview-ko-long-active-self",
                        channel="self",
                        block_variant="active_self",
                        appearance_seq=10,
                        primary_text=(
                            "긴 문장 미리보기입니다. 한국어 자막이 화면 너비에 맞춰 "
                            "자연스럽게 줄바꿈되는지 확인하기 위해 일부러 길게 작성했습니다. "
                            "밝은 배경에서도 반투명 자막 카드가 읽기 쉬운지 살펴보세요."
                        ),
                        secondary_text=(
                            "This long Korean sample checks wrapping, source color, "
                            "and the secondary translation line."
                        ),
                        secondary_enabled=True,
                    )
                ],
            ),
            frozenset({"ko", "en", "self", "primary", "secondary", "active", "long_wrap"}),
        ),
        (
            "japanese_peer_finalized",
            "flet.settings.overlay.desktop.preview.fixture.japanese_peer_finalized",
            OverlayPresentationSnapshot(
                revision=2,
                blocks=[
                    _preview_block(
                        "preview-ja-peer-finalized",
                        channel="peer",
                        block_variant="finalized",
                        appearance_seq=20,
                        primary_text="今日はゆっくり話してくれてありがとう。字幕カードも見やすいです。",
                        secondary_text="Thanks for speaking slowly today. The caption card is easy to read.",
                        secondary_enabled=True,
                    )
                ],
            ),
            frozenset({"ja", "en", "peer", "primary", "secondary", "finalized"}),
        ),
        (
            "chinese_self_finalized",
            "flet.settings.overlay.desktop.preview.fixture.chinese_self_finalized",
            OverlayPresentationSnapshot(
                revision=3,
                blocks=[
                    _preview_block(
                        "preview-zh-self-finalized",
                        channel="self",
                        block_variant="finalized",
                        appearance_seq=30,
                        primary_text="我这边的桌面字幕会保持居中，并且在深色背景上也要清晰。",
                        secondary_text="My desktop captions stay centered and readable on dark backgrounds.",
                        secondary_enabled=True,
                    )
                ],
            ),
            frozenset({"zh-CN", "en", "self", "primary", "secondary", "finalized"}),
        ),
        (
            "english_active_peer",
            "flet.settings.overlay.desktop.preview.fixture.english_active_peer",
            OverlayPresentationSnapshot(
                revision=4,
                blocks=[
                    _preview_block(
                        "preview-en-active-peer",
                        channel="peer",
                        block_variant="active_peer",
                        appearance_seq=40,
                        primary_text="",
                        secondary_text="Live peer captions are arriving right now...",
                        secondary_enabled=True,
                    )
                ],
            ),
            frozenset({"en", "peer", "primary", "active"}),
        ),
        (
            "mixed_script_emoji",
            "flet.settings.overlay.desktop.preview.fixture.mixed_script_emoji",
            OverlayPresentationSnapshot(
                revision=5,
                blocks=[
                    _preview_block(
                        "preview-mixed-emoji-peer",
                        channel="peer",
                        block_variant="finalized",
                        appearance_seq=50,
                        primary_text="今日は PuriPuly Heart 좋아요 你好 😊✨",
                        secondary_text="Mixed source: hello, 안녕, こんにちは, 你好 🎮",
                        secondary_enabled=True,
                    )
                ],
            ),
            frozenset(
                {
                    "mixed_script",
                    "emoji",
                    "en",
                    "ko",
                    "ja",
                    "zh-CN",
                    "peer",
                    "primary",
                    "secondary",
                    "finalized",
                }
            ),
        ),
        (
            "no_captions",
            "flet.settings.overlay.desktop.preview.fixture.no_captions",
            OverlayPresentationSnapshot(revision=6, blocks=[]),
            frozenset({"no_caption", "edit_placeholder", "pass_through_transparent"}),
        ),
    )


def _preview_block(
    block_id: str,
    *,
    channel: str,
    block_variant: str,
    appearance_seq: int,
    primary_text: str,
    secondary_text: str,
    secondary_enabled: bool,
) -> OverlayPresentationBlock:
    return OverlayPresentationBlock(
        id=block_id,
        occupant_key=f"preview:{channel}:{block_id}",
        appearance_seq=appearance_seq,
        channel=channel,  # type: ignore[arg-type]
        block_variant=block_variant,  # type: ignore[arg-type]
        primary_text=primary_text,
        secondary_text=secondary_text,
        secondary_enabled=secondary_enabled,
    )


# ---------------------------------------------------------------------------
# Section H — Caption line builders
# ---------------------------------------------------------------------------


def _caption_slots_for_snapshot(
    snapshot: OverlayPresentationSnapshot,
    *,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionSlot, ...]:
    slots: list[DesktopCaptionSlot] = []
    for block in sorted(snapshot.blocks, key=lambda item: (item.appearance_seq, item.occupant_key)):
        lines = _caption_lines_for_block(
            block,
            primary_font_size=primary_font_size,
            secondary_font_size=secondary_font_size,
        )
        if not lines:
            continue
        slots.append(
            DesktopCaptionSlot(
                block_id=block.id,
                occupant_key=block.occupant_key,
                channel=block.channel,
                block_variant=block.block_variant,
                appearance_seq=block.appearance_seq,
                lines=lines,
                secondary_enabled=block.secondary_enabled,
                active=block.block_variant in {"active_self", "active_peer"},
            )
        )
    return tuple(slots)


def _caption_lines_for_block(
    block: OverlayPresentationBlock,
    *,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    primary_text = block.primary_text.strip()
    secondary_text = block.secondary_text.strip()
    if not primary_text and not secondary_text:
        return ()

    if block.block_variant == "active_self":
        return _self_active_lines(
            block,
            primary_text=primary_text,
            secondary_text=secondary_text,
            primary_font_size=primary_font_size,
            secondary_font_size=secondary_font_size,
        )
    if block.block_variant == "active_peer":
        return _peer_active_lines(
            block,
            primary_text=primary_text,
            secondary_text=secondary_text,
            primary_font_size=primary_font_size,
            secondary_font_size=secondary_font_size,
        )
    if block.channel == "peer":
        return _peer_finalized_lines(
            block,
            primary_text=primary_text,
            secondary_text=secondary_text,
            primary_font_size=primary_font_size,
            secondary_font_size=secondary_font_size,
        )
    return _self_finalized_lines(
        block,
        primary_text=primary_text,
        secondary_text=secondary_text,
        primary_font_size=primary_font_size,
        secondary_font_size=secondary_font_size,
    )


def _self_active_lines(
    block: OverlayPresentationBlock,
    *,
    primary_text: str,
    secondary_text: str,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    lines: list[DesktopCaptionLine] = []
    if primary_text:
        lines.append(
            _caption_line(
                block,
                text=primary_text,
                role="active_self_source",
                slot="primary",
                priority=100,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.primary_language,
                active=True,
            )
        )
    if secondary_text and block.secondary_enabled:
        lines.append(
            _caption_line(
                block,
                text=secondary_text,
                role="active_self_translation",
                slot="secondary",
                priority=85,
                max_lines=_DESKTOP_CAPTION_SECONDARY_MAX_LINES,
                font_size=secondary_font_size,
                language=block.secondary_language,
                active=True,
            )
        )
    return tuple(lines)


def _peer_active_lines(
    block: OverlayPresentationBlock,
    *,
    primary_text: str,
    secondary_text: str,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    readable_text = primary_text or (secondary_text if block.secondary_enabled else "")
    if not readable_text:
        return ()
    promoted = not primary_text and bool(secondary_text) and block.secondary_enabled
    return (
        _caption_line(
            block,
            text=readable_text,
            role="active_peer_source",
            slot="primary" if not promoted else "primary",
            priority=95,
            max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
            font_size=primary_font_size if promoted else secondary_font_size,
            language=block.secondary_language if promoted else block.primary_language,
            promoted=promoted,
            active=True,
        ),
    )


def _peer_finalized_lines(
    block: OverlayPresentationBlock,
    *,
    primary_text: str,
    secondary_text: str,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    lines: list[DesktopCaptionLine] = []
    if primary_text:
        lines.append(
            _caption_line(
                block,
                text=primary_text,
                role="peer_translation",
                slot="primary",
                priority=90,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.primary_language,
            )
        )
        if secondary_text and block.secondary_enabled:
            lines.append(
                _caption_line(
                    block,
                    text=secondary_text,
                    role="peer_source_original",
                    slot="secondary",
                    priority=70,
                    max_lines=_DESKTOP_CAPTION_SECONDARY_MAX_LINES,
                    font_size=secondary_font_size,
                    language=block.secondary_language,
                )
            )
        return tuple(lines)
    if secondary_text and block.secondary_enabled:
        return (
            _caption_line(
                block,
                text=secondary_text,
                role="peer_source_original",
                slot="primary",
                priority=60,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.secondary_language,
                promoted=True,
            ),
        )
    return ()


def _self_finalized_lines(
    block: OverlayPresentationBlock,
    *,
    primary_text: str,
    secondary_text: str,
    primary_font_size: int,
    secondary_font_size: int,
) -> tuple[DesktopCaptionLine, ...]:
    lines: list[DesktopCaptionLine] = []
    if primary_text:
        lines.append(
            _caption_line(
                block,
                text=primary_text,
                role="self_source",
                slot="primary",
                priority=65,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.primary_language,
            )
        )
        if secondary_text and block.secondary_enabled:
            lines.append(
                _caption_line(
                    block,
                    text=secondary_text,
                    role="self_translation",
                    slot="secondary",
                    priority=50,
                    max_lines=_DESKTOP_CAPTION_SECONDARY_MAX_LINES,
                    font_size=secondary_font_size,
                    language=block.secondary_language,
                )
            )
        return tuple(lines)
    if secondary_text and block.secondary_enabled:
        return (
            _caption_line(
                block,
                text=secondary_text,
                role="self_translation",
                slot="primary",
                priority=55,
                max_lines=_DESKTOP_CAPTION_PRIMARY_MAX_LINES,
                font_size=primary_font_size,
                language=block.secondary_language,
                promoted=True,
            ),
        )
    return ()


def _caption_line(
    block: OverlayPresentationBlock,
    *,
    text: str,
    role: str,
    slot: str,
    priority: int,
    max_lines: int,
    font_size: int,
    language: str | None = None,
    promoted: bool = False,
    active: bool = False,
) -> DesktopCaptionLine:
    uses_cjk_font_policy = _desktop_caption_uses_cjk_font_policy(text, language)
    return DesktopCaptionLine(
        text=text,
        role=role,
        slot=slot,
        color=_desktop_caption_color_for_channel(block.channel),
        priority=priority,
        block_id=block.id,
        channel=block.channel,
        block_variant=block.block_variant,
        appearance_seq=block.appearance_seq,
        max_lines=max_lines,
        font_size=font_size,
        font_family=(
            _DESKTOP_CAPTION_CJK_FONT_FAMILY
            if uses_cjk_font_policy
            else _DESKTOP_CAPTION_LATIN_FONT_FAMILY
        ),
        weight="medium" if uses_cjk_font_policy else "semibold",
        promoted=promoted,
        active=active,
    )


def _caption_slot_with_dynamic_width(
    slot: DesktopCaptionSlot,
    *,
    padding_horizontal: int,
    max_card_width: int,
) -> DesktopCaptionSlot:
    max_width = max(1.0, float(max_card_width))
    minimum_width = min(_DESKTOP_CAPTION_MIN_DYNAMIC_CARD_WIDTH, max_width)
    estimated_text_width = max(
        (_estimated_caption_line_width(line.text, line.font_size) for line in slot.lines),
        default=0.0,
    )
    estimated_card_width = (
        estimated_text_width
        + (float(padding_horizontal) * 2)
        + _DESKTOP_CAPTION_DYNAMIC_WIDTH_SAFETY
    )
    card_width = min(max_width, max(minimum_width, estimated_card_width))
    card_text_width = max(1.0, card_width - (float(padding_horizontal) * 2))
    return replace(slot, card_width=card_width, card_text_width=card_text_width)


def _caption_card_width_memory_key(slot: DesktopCaptionSlot) -> tuple[str, str, int]:
    return (slot.block_id, slot.occupant_key, slot.appearance_seq)


def _caption_width_key_label(key: tuple[str, str, int]) -> str:
    return f"{key[0]}/{key[1]}/{key[2]}"


# ---------------------------------------------------------------------------
# Section I — Snapshot diagnostics
# ---------------------------------------------------------------------------


def _desktop_snapshot_rows_summary(snapshot: OverlayPresentationSnapshot) -> str:
    return "; ".join(
        _desktop_snapshot_block_summary(index, block) for index, block in enumerate(snapshot.blocks)
    )


def _desktop_snapshot_block_summary(
    index: int,
    block: OverlayPresentationBlock,
) -> str:
    secondary_len = len(block.secondary_text) if block.secondary_enabled else 0
    return (
        f"idx={index} "
        f"id={block.id} "
        f"occupant_key={block.occupant_key} "
        f"appearance_seq={block.appearance_seq} "
        f"channel={block.channel} "
        f"variant={block.block_variant} "
        f"primary_len={len(block.primary_text)} "
        f"secondary_len={secondary_len} "
        f"secondary_enabled={block.secondary_enabled} "
        f"update_id={_optional_log_value(block.update_id)} "
        f"origin_wall_clock_ms={_optional_log_value(block.origin_wall_clock_ms)} "
        f"session_scope={_optional_log_value(block.session_scope)}"
    )


def _optional_log_value(value: object | None) -> str:
    if value is None:
        return "none"
    return str(value)


# ---------------------------------------------------------------------------
# Section J — Char width estimation + CJK detection
# ---------------------------------------------------------------------------


def _estimated_caption_line_width(text: str, font_size: int) -> float:
    return sum(_estimated_caption_char_width(char, font_size) for char in text)


def _estimated_caption_char_width(char: str, font_size: int) -> float:
    codepoint = ord(char)
    if char.isspace():
        return font_size * _DESKTOP_CAPTION_SPACE_WIDTH_EM
    if _is_caption_emoji_or_symbol(codepoint):
        return font_size * _DESKTOP_CAPTION_EMOJI_WIDTH_EM
    if _is_caption_cjk_or_hangul(codepoint):
        return font_size * _DESKTOP_CAPTION_CJK_WIDTH_EM
    if char in ".,;:!?'\"-–—()[]{}·…":
        return font_size * _DESKTOP_CAPTION_PUNCT_WIDTH_EM
    if char in "ilI|":
        return font_size * _DESKTOP_CAPTION_LATIN_NARROW_WIDTH_EM
    if char.isascii():
        return font_size * _DESKTOP_CAPTION_LATIN_WIDE_WIDTH_EM
    return font_size * _DESKTOP_CAPTION_CJK_WIDTH_EM


def _is_caption_cjk_or_hangul(codepoint: int) -> bool:
    return (
        0x1100 <= codepoint <= 0x11FF
        or 0x3040 <= codepoint <= 0x30FF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _is_caption_emoji_or_symbol(codepoint: int) -> bool:
    return 0x1F000 <= codepoint <= 0x1FAFF


def _desktop_caption_char_is_cjk(char: str) -> bool:
    return _is_caption_cjk_or_hangul(ord(char))


def _desktop_caption_font_family_for_text(text: str, language: str | None = None) -> str:
    if _desktop_caption_uses_cjk_font_policy(text, language):
        return _DESKTOP_CAPTION_CJK_FONT_FAMILY
    return _DESKTOP_CAPTION_LATIN_FONT_FAMILY


def _desktop_caption_uses_cjk_font_policy(text: str, language: str | None = None) -> bool:
    return _desktop_caption_language_is_cjk(language) or _desktop_caption_text_contains_cjk(text)


def _desktop_caption_language_is_cjk(language: str | None) -> bool:
    primary_subtag = _desktop_caption_language_primary_subtag(language)
    return primary_subtag in _DESKTOP_CAPTION_CJK_LANGUAGE_PRIMARY_SUBTAGS


def _desktop_caption_language_primary_subtag(language: str | None) -> str | None:
    if language is None:
        return None
    normalized = language.strip().replace("_", "-").lower()
    if not normalized:
        return None
    return next((part for part in normalized.split("-") if part), None)


def _desktop_caption_text_contains_cjk(text: str) -> bool:
    return any(_desktop_caption_char_is_cjk(char) for char in text)


# ---------------------------------------------------------------------------
# Section K — Validation + preset resolution
# ---------------------------------------------------------------------------


def _validated_visual_state(
    visual_state: DesktopCaptionVisualState | None,
) -> DesktopCaptionVisualState:
    source = visual_state or DesktopCaptionVisualState()
    settings = DesktopFletOverlayVisualSettings(
        text_scale=source.text_scale,
        background_alpha=source.background_alpha,
        outline_width=source.outline_width,
    )
    settings.validate()
    return DesktopCaptionVisualState(
        text_scale=settings.text_scale,
        background_alpha=settings.background_alpha,
        outline_width=settings.outline_width,
    )


def _desktop_caption_size_preset_for_dimensions(
    width: int,
    height: int,
) -> DesktopCaptionSizePreset:
    for preset_id in DESKTOP_FLET_SIZE_PRESET_ORDER:
        preset = _DESKTOP_CAPTION_SIZE_PRESETS[preset_id]
        settings_dimensions = DESKTOP_FLET_SIZE_PRESETS[preset_id]
        if (preset.window_width, preset.window_height) != settings_dimensions:
            raise RuntimeError("desktop caption preset dimensions diverged from settings")
        if width == preset.window_width and height == preset.window_height:
            return preset
    return _DESKTOP_CAPTION_SIZE_PRESETS[DESKTOP_FLET_DEFAULT_SIZE_PRESET]


def _caption_background_color(background_alpha: float) -> str:
    if background_alpha <= 0:
        return _DESKTOP_CAPTION_TRANSPARENT
    alpha = int(round(_clamp(background_alpha, 0.0, 1.0) * 255))
    return f"#{alpha:02X}{_DESKTOP_CAPTION_BACKGROUND_RGB}"


def _positive_int_or_default(value: int | float, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    if value <= 0:
        return default
    return int(round(value))


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))
