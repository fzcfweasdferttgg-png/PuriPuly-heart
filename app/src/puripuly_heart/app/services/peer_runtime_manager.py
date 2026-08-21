"""Deprecated — PeerRuntimeManagerMixin moved to PeerRuntimeService.

This module is kept only for backward compatibility. Import from
``puripuly_heart.app.services.peer_runtime_service`` instead.
"""

from __future__ import annotations

import warnings

from puripuly_heart.app.services.peer_runtime_service import PeerRuntimeService

warnings.warn(
    "PeerRuntimeManagerMixin is deprecated; use PeerRuntimeService from "
    "puripuly_heart.app.services.peer_runtime_service",
    DeprecationWarning,
    stacklevel=2,
)


# Backward-compatible alias so existing ``from ... import PeerRuntimeManagerMixin``
# keeps working until callers are updated.
PeerRuntimeManagerMixin = PeerRuntimeService  # type: ignore[misc]
