"""Deprecated — use overlay_service.OverlayService instead.

OverlayManagerMixin has been merged into OverlayService along with
OverlayLifecycleMixin.  This module re-exports the constants for
backward compatibility only.
"""

from puripuly_heart.app.services.overlay_service import (
    DESKTOP_BOUNDS_PERSIST_DEBOUNCE_S,
    DESKTOP_INTERACTION_MODE_EDIT,
    DESKTOP_INTERACTION_MODE_PASS_THROUGH,
    DESKTOP_INTERACTION_MODES,
)

__all__ = [
    "DESKTOP_BOUNDS_PERSIST_DEBOUNCE_S",
    "DESKTOP_INTERACTION_MODE_EDIT",
    "DESKTOP_INTERACTION_MODE_PASS_THROUGH",
    "DESKTOP_INTERACTION_MODES",
]
