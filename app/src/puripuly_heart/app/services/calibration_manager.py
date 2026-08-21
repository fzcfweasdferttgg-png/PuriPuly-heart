"""CalibrationManagerMixin — DEPRECATED.

Moved to :class:`CalibrationService` in ``calibration_service.py``.
This module is kept as a redirect stub so that any stale imports fail fast.
"""

# ruff: noqa: I001
raise ImportError(
    "CalibrationManagerMixin has been extracted to CalibrationService.  "
    "Update your import to: "
    "from puripuly_heart.app.services.calibration_service import CalibrationService"
)
