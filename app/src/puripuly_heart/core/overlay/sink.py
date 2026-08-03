"""Overlay event types and sink interface — re-exports from ports.overlay.

Provides a single import point for overlay event types used throughout
core/overlay/ modules. All types originate in domain/overlay_types.py
and the OverlaySink protocol in ports/overlay.py.

NOTE: Currently unused — all consumers import directly from ports.overlay.
Kept as convenience layer for future use.
"""

from puripuly_heart.ports.overlay import (
    OverlayEventUnion,
    OverlaySink,
    PeerActiveUpdate,
    PeerTranscriptFinal,
    SelfActiveClear,
    SelfActiveUpdate,
    SelfTranscriptFinal,
    TranslationFinal,
    TranslationStreamUpdate,
    UtteranceClosed,
)

__all__ = [
    "OverlayEventUnion",
    "OverlaySink",
    "PeerActiveUpdate",
    "PeerTranscriptFinal",
    "SelfActiveClear",
    "SelfActiveUpdate",
    "SelfTranscriptFinal",
    "TranslationFinal",
    "TranslationStreamUpdate",
    "UtteranceClosed",
]
