"""PeerFlagsMixin — DEPRECATED.

This module is kept for backward compatibility only.
All peer flag logic has been extracted to PeerToggleCoordinator.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


class PeerFlagsMixin:
    """DEPRECATED: Use PeerToggleCoordinator instead."""

    def __init_subclass__(cls, **kwargs):
        warnings.warn(
            "PeerFlagsMixin is deprecated. Use PeerToggleCoordinator instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init_subclass__(**kwargs)
