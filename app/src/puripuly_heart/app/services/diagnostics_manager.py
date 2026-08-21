"""Deprecated — DiagnosticsManagerMixin has been extracted to DiagnosticsService.

This module is kept only to avoid ImportError on stale imports.
All logic now lives in diagnostics_service_runtime.py.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "diagnostics_manager.DiagnosticsManagerMixin is deprecated; "
    "use diagnostics_service_runtime.DiagnosticsService instead.",
    DeprecationWarning,
    stacklevel=2,
)


class DiagnosticsManagerMixin:
    """Deprecated stub — use DiagnosticsService instead."""
