"""STT log sink — extracted logging infrastructure for ManagedSTTProvider.

Provides basic, detailed, and audio-diagnostic logging through
SessionRuntimeLoggingService with DEBUG fallback to stdlib logger.

AI-CONTEXT: This is a leaf node in the STT import graph — no intra-package imports.
  Safe to import from any other STT module without circular dependency risk.
  controller.py, stt_audio_diagnostics.py, stt_pending_tracker.py all depend on this.

AI-CONTEXT: detailed() ALWAYS logs at DEBUG level when runtime_logging is None,
  regardless of the caller's level parameter. This prevents diagnostic noise from
  flooding INFO-level logs. If you need non-DEBUG fallback, use basic() instead.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass

from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.domain.models import ChannelId

logger = logging.getLogger(__name__)


@dataclass
class STTLogSink:
    """Logging facade for an STT session — basic, detailed, and audio-diagnostic."""

    runtime_logging: SessionRuntimeLoggingService | None
    channel: ChannelId

    @staticmethod
    def _format(message: str, *args: object) -> str:
        """Format a message with %-style args."""
        return message % args if args else message

    def basic(
        self,
        message: str,
        *args: object,
        level: int = logging.INFO,
        fallback_level: int | None = None,
    ) -> None:
        """Basic logging — emits to runtime_logging if present, otherwise stdlib logger."""
        formatted = self._format(message, *args)
        if self.runtime_logging is not None:
            self.runtime_logging.emit_basic(formatted, level=level)
            return
        logger.log(level if fallback_level is None else fallback_level, formatted)

    def detailed(
        self,
        message: str,
        *args: object,
        level: int = logging.INFO,
    ) -> None:
        """Detailed logging — uses runtime_logging if available, otherwise DEBUG fallback.
        Unlike basic() which uses the caller's level, detailed always uses DEBUG
        to avoid flooding standard logs with diagnostic information."""
        formatted = self._format(message, *args)
        if self.runtime_logging is not None:
            self.runtime_logging.emit_detailed(formatted, level=level)
            return
        logger.log(logging.DEBUG, formatted)

    def audio_diag_detailed(
        self,
        message: str,
        *args: object,
        level: int = logging.INFO,
    ) -> None:
        """Audio-diagnostic logging — suppresses exceptions to avoid disrupting audio pipeline."""
        with contextlib.suppress(Exception):
            self.detailed(message, *args, level=level)

    def log_session_connected(self, *, attempts: int) -> None:
        """Log session connection with retry count."""
        retries = max(0, attempts - 1)
        if retries == 0:
            self.basic("[STT] Session connected")
            return
        suffix = "retry" if retries == 1 else "retries"
        self.basic(f"[STT] Session connected after {retries} {suffix}")
