"""Deprecated — use overlay_service.OverlayService instead.

OverlayLifecycleMixin has been merged into OverlayService along with
OverlayManagerMixin.  This module re-exports the constants for
backward compatibility only.
"""

from puripuly_heart.app.services.overlay_service import (
    DESKTOP_INTERACTION_MODE_EDIT,
    OVERLAY_SHUTDOWN_GRACE_S,
    OVERLAY_STARTUP_TIMEOUT_MS,
)

__all__ = [
    "DESKTOP_INTERACTION_MODE_EDIT",
    "OVERLAY_SHUTDOWN_GRACE_S",
    "OVERLAY_STARTUP_TIMEOUT_MS",
]
