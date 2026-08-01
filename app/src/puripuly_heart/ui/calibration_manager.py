from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from puripuly_heart.ui.overlay_calibration import OverlayCalibration

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings

logger = logging.getLogger(__name__)


class CalibrationManagerMixin:
    """Overlay calibration management extracted from GuiController."""

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

    def apply_overlay_calibration(self) -> OverlayCalibration:
        if self._overlay_calibration_draft is None:
            return self.overlay_calibration.copy()

        self._overlay_calibration_draft.validate()
        self.overlay_calibration = self._overlay_calibration_draft.copy()
        self._overlay_calibration_draft = None
        if self.settings is not None:
            self.settings.overlay.calibration = self.overlay_calibration.copy()
            self._save_settings()
        self._schedule_overlay_calibration_emit()
        return self.overlay_calibration.copy()

    def cancel_overlay_calibration(self) -> OverlayCalibration:
        self._overlay_calibration_draft = None
        return self.overlay_calibration.copy()

    def _sync_overlay_calibration_cache(self, settings: AppSettings | None = None) -> None:
        resolved_settings = settings or self.settings
        if resolved_settings is None:
            return
        self.overlay_calibration = resolved_settings.overlay.calibration.copy()

    async def _emit_overlay_calibration_update(self) -> None:
        presenter = self._overlay_presenter
        if presenter is None:
            return
        with contextlib.suppress(Exception):
            await presenter.update_calibration(self.overlay_calibration.copy())

    def _schedule_overlay_calibration_emit(self) -> None:
        if self._overlay_presenter is None:
            return
        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):
            try:
                run_task(self._emit_overlay_calibration_update)
                return
            except Exception as exc:
                self.log_detailed(
                    "[Overlay] Failed to schedule calibration update via page.run_task",
                    level=logging.WARNING,
                    exception=exc,
                )
                return

        try:
            asyncio.get_running_loop().create_task(self._emit_overlay_calibration_update())
        except RuntimeError:
            self.log_detailed(
                "[Overlay] Skipping calibration update; no running loop and page.run_task unavailable",
                level=logging.WARNING,
            )

    def begin_overlay_calibration_for_test(self) -> None:
        self.begin_overlay_calibration()

    def set_overlay_calibration_field_for_test(self, field_name: str, value: object) -> None:
        self.set_overlay_calibration_field(field_name, value)

    def apply_overlay_calibration_for_test(self) -> None:
        self.apply_overlay_calibration()

    def cancel_overlay_calibration_for_test(self) -> None:
        self.cancel_overlay_calibration()
