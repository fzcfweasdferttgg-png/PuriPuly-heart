"""CalibrationService — overlay calibration draft/apply/cancel lifecycle.

Extracted from CalibrationManagerMixin.  Owns calibration state (current + draft).
No Flet dependency — uses asyncio.Task for scheduling.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from puripuly_heart.domain.overlay_calibration import OverlayCalibration

logger = logging.getLogger(__name__)


@dataclass
class CalibrationService:
    """Overlay calibration draft/apply/cancel lifecycle."""

    _overlay_presenter: object | None = None
    _save_settings: Callable[[], None] | None = None
    _log_detailed: Callable[[str], None] | None = None

    # State — owned by service
    overlay_calibration: OverlayCalibration = field(default_factory=OverlayCalibration)
    _overlay_calibration_draft: OverlayCalibration | None = None

    def _emit_log(self, message: str) -> None:
        if self._log_detailed is not None:
            self._log_detailed(message)

    def set_overlay_presenter(self, presenter: object | None) -> None:
        """Public setter for overlay presenter — used by OverlayService and GuiController."""
        self._overlay_presenter = presenter

    def sync_from_settings(self, settings: object | None = None) -> None:
        """Sync calibration cache from settings.  Called on settings load."""
        resolved_settings = settings
        if resolved_settings is None:
            return
        calibration = getattr(
            getattr(resolved_settings, "overlay", None), "calibration", None
        )
        if calibration is not None:
            self.overlay_calibration = calibration.copy()

    def begin_overlay_calibration(self) -> OverlayCalibration:
        if self._overlay_calibration_draft is None:
            self._overlay_calibration_draft = self.overlay_calibration.copy()
        return self._overlay_calibration_draft.copy()

    def set_overlay_calibration_field(
        self,
        field_name: str,
        value: object,
    ) -> OverlayCalibration:
        if self._overlay_calibration_draft is None:
            self._overlay_calibration_draft = self.overlay_calibration.copy()

        if field_name not in OverlayCalibration.__dataclass_fields__:
            raise ValueError(f"unknown overlay calibration field: {field_name}")

        if field_name == "anchor":
            setattr(self._overlay_calibration_draft, field_name, str(value))
        else:
            setattr(self._overlay_calibration_draft, field_name, float(value))

        self._overlay_calibration_draft.validate()
        return self._overlay_calibration_draft.copy()

    def apply_overlay_calibration(
        self, settings: object | None = None
    ) -> OverlayCalibration:
        if self._overlay_calibration_draft is None:
            return self.overlay_calibration.copy()

        self._overlay_calibration_draft.validate()
        self.overlay_calibration = self._overlay_calibration_draft.copy()
        self._overlay_calibration_draft = None
        if settings is not None:
            overlay = getattr(settings, "overlay", None)
            if overlay is not None:
                overlay.calibration = self.overlay_calibration.copy()
            if self._save_settings is not None:
                self._save_settings()
        self._schedule_calibration_emit()
        return self.overlay_calibration.copy()

    def cancel_overlay_calibration(self) -> OverlayCalibration:
        self._overlay_calibration_draft = None
        return self.overlay_calibration.copy()

    async def _emit_calibration_update(self) -> None:
        presenter = self._overlay_presenter
        if presenter is None:
            return
        update_calibration = getattr(presenter, "update_calibration", None)
        if callable(update_calibration):
            with contextlib.suppress(Exception):
                await update_calibration(self.overlay_calibration.copy())

    def _schedule_calibration_emit(self) -> None:
        if self._overlay_presenter is None:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._emit_calibration_update())
        except RuntimeError:
            self._emit_log(
                "[Overlay] Skipping calibration update; no running loop"
            )
