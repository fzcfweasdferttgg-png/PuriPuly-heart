"""ClipboardManagerMixin — DEPRECATED.

All clipboard watcher and manual-typing logic has been extracted to
:class:`puripuly_heart.app.services.clipboard_service.ClipboardService`.

This module is kept only to avoid import errors in any stale references.
It will be removed in a future cleanup.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "ClipboardManagerMixin is deprecated; use ClipboardService instead.",
    DeprecationWarning,
    stacklevel=2,
)


class ClipboardManagerMixin:
    """Deprecated — use ClipboardService."""

    pass
