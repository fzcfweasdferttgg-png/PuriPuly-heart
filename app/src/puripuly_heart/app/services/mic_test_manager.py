"""DEPRECATED — use mic_test_service.MicTestService instead.

This module is kept only to avoid ImportError on stale imports.
All logic has been moved to :mod:`mic_test_service`.
"""

from __future__ import annotations

import warnings

from puripuly_heart.app.services.mic_test_service import MicTestService

warnings.warn(
    "mic_test_manager.MicTestManagerMixin is deprecated; "
    "use mic_test_service.MicTestService instead.",
    DeprecationWarning,
    stacklevel=2,
)


# Backward-compatible alias so any stale ``from mic_test_manager import MicTestManagerMixin``
# does not crash at import time.
MicTestManagerMixin = MicTestService
