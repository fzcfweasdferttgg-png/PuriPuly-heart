"""Diagnostics computation — pure functions extracted from DiagnosticsManagerMixin.

Stateless, testable functions for audio diagnostics and fault profile management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from puripuly_heart.core.runtime_logging import SessionLoggingMode

if TYPE_CHECKING:
    from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService


def is_detailed_audio_diag_enabled(runtime_logging: SessionRuntimeLoggingService) -> bool:
    return runtime_logging.mode is SessionLoggingMode.DETAILED


def is_debug_audio_fault_allowed(debug_ui_preview: bool) -> bool:
    return bool(debug_ui_preview)


def cycle_fault_profile(
    current_profile: str,
    profiles: list[str],
) -> str:
    """Cycle to next fault profile in the list."""
    from puripuly_heart.core.audio.diagnostics import AudioFaultProfile
    current = AudioFaultProfile(current_profile)
    idx = profiles.index(current) if current in profiles else 0
    next_idx = (idx + 1) % len(profiles)
    return profiles[next_idx]
