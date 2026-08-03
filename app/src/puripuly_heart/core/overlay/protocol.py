"""Overlay presentation types — re-exports from domain.overlay_types.

Convenience re-export for core/overlay/ modules that need presentation
types (blocks, snapshots, calibration) without importing from domain/ directly.

NOTE: Currently unused — all consumers import directly from domain.overlay_types.
Kept as convenience layer for future use.
"""

from puripuly_heart.domain.overlay_types import (
    BlockVariant,
    ChannelId,
    NativeFreshRenderGenerations,
    NativeFreshRenderTargets,
    NativeQuietTailEpisode,
    NativeQuietTailEpisodes,
    OverlayPresentationBlock,
    OverlayPresentationCalibration,
    OverlayPresentationSnapshot,
    U64_MAX,
)

__all__ = [
    "BlockVariant",
    "ChannelId",
    "NativeFreshRenderGenerations",
    "NativeFreshRenderTargets",
    "NativeQuietTailEpisode",
    "NativeQuietTailEpisodes",
    "OverlayPresentationBlock",
    "OverlayPresentationCalibration",
    "OverlayPresentationSnapshot",
    "U64_MAX",
]
