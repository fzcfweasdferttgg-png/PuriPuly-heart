"""UI-independent telemetry toggle.

The app-active-day reporting flow was removed; the enable-toggle plumbing
stays so the settings switch keeps working (it only persists its flag).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class TelemetryReportingOwner:
    """Owns the telemetry-enable flow.

    Ports are injected callables so the flow is testable without a UI:
    - ``application``: apply_telemetry_enabled / settings_general_snapshot
    - ``run_background``: schedules a coroutine factory as a page-owned task
    - ``sync_telemetry``: optional view callback for snapshot sync
    """

    def __init__(
        self,
        application: Any,
        *,
        run_background: Callable[..., object],
        queue_settings_mutation: Callable[[Callable[[], Awaitable[None]]], None] | None = None,
        is_shutting_down: Callable[[], bool] | None = None,
        sync_telemetry: Callable[[Any], None] | None = None,
        clock: Callable[[], Any] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._application = application
        self._run_background = run_background
        self._queue_settings_mutation = queue_settings_mutation
        self._is_shutting_down = is_shutting_down or (lambda: False)
        self._sync_telemetry = sync_telemetry
        self._clock = clock
        self._sleep = sleep

    def apply_telemetry_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            return

        async def _task() -> None:
            await self._application.apply_telemetry_enabled(enabled)
            settings = self._application.settings_general_snapshot()
            if self._sync_telemetry is not None and settings is not None:
                self._sync_telemetry(settings)

        self._run_background(_task)

    def schedule_app_active_day_report(self) -> None:
        """No-op: app-active-day reporting was removed."""
