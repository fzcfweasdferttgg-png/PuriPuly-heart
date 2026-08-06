"""Runtime logging infrastructure — file, stream, and UI log sinks.

Architecture:
- **RotatingFileHandler** backed by a **QueueHandler + QueueListener** to
  avoid blocking the main thread on disk I/O.  File writes happen in a
  background thread managed by QueueListener.
- **Reference counting**: multiple RuntimeLoggingSinks can share the same
  file queue handler (e.g. root logger + session logger).  Refcount stored
  as custom attribute on the handler; closes only when refcount reaches 0.
- **Custom attributes on handlers**: metadata (log file path, file handler,
  listener, closed flag, refcount, queue) stored via setattr on handler
  objects — workaround for not subclassing standard logging handlers.
- **Session loggers**: unique name per session, propagate=False to avoid
  duplicate messages on root logger.
- **emit modes**: BASIC (always), DETAILED (only when mode=DETAILED),
  PERSISTED (bypasses queue, writes directly to file handler).

Called by controller.py, main.py, wiring.py, event_bridge.py,
diagnostics_manager.py, overlay_lifecycle.py, chatbox_paginator.py,
peer_runtime_manager.py.
Re-exports latency formatting functions from domain.logging_types.
"""

from __future__ import annotations

import contextlib
import logging
import queue
from dataclasses import dataclass
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from puripuly_heart.domain.logging_types import (  # re-export
    LATENCY_CAUSE_E2E_THRESHOLD_MS,
    LATENCY_DOMINANT_STAGE_NORMAL,
    LATENCY_DOMINANT_STAGE_POST_STT_OUTPUT,
    LATENCY_DOMINANT_STAGE_STT_FINALIZATION,
    LATENCY_DOMINANT_STAGE_THRESHOLD_MS,
    LATENCY_TRACE_POINT_CONTRACTS,
    LatencyTracePointContract,
    compute_latency_dominant_stage,
    format_basic_latency_summary,
    format_detailed_latency_breakdown,
    format_detailed_latency_trace,
    format_latency_cause_metric,
    format_translation_ready_for_output,
)
from puripuly_heart.domain.overlay_types import SessionLoggingMode
from puripuly_heart.ports.logging_sink import RealtimeLogSink

__all__ = [
    "RealtimeLogSink", "SessionLoggingMode", "RuntimeLoggingSinks", "SessionRuntimeLoggingService",
    "LATENCY_CAUSE_E2E_THRESHOLD_MS", "LATENCY_DOMINANT_STAGE_NORMAL",
    "LATENCY_DOMINANT_STAGE_POST_STT_OUTPUT", "LATENCY_DOMINANT_STAGE_STT_FINALIZATION",
    "LATENCY_DOMINANT_STAGE_THRESHOLD_MS", "LATENCY_TRACE_POINT_CONTRACTS",
    "LatencyTracePointContract", "compute_latency_dominant_stage",
    "format_basic_latency_summary", "format_detailed_latency_breakdown",
    "format_detailed_latency_trace", "format_latency_cause_metric",
    "format_translation_ready_for_output",
]

MAIN_LOG_FILENAME = "puripuly_heart.log"
MAIN_LOG_BACKUP_FILENAME = "puripuly_heart.backup.log"
_MAIN_STREAM_HANDLER_NAME = "puripuly_heart.main.stream"
_MAIN_FILE_HANDLER_NAME = "puripuly_heart.main.file"
_MAIN_FILE_QUEUE_HANDLER_NAME = "puripuly_heart.main.file.queue"
_SESSION_LOGGER_NAME = "puripuly_heart.runtime.session"
_QUEUE_HANDLER_LOG_FILE_ATTR = "_puripuly_heart_log_file"
_QUEUE_HANDLER_FILE_HANDLER_ATTR = "_puripuly_heart_file_handler"
_QUEUE_HANDLER_LISTENER_ATTR = "_puripuly_heart_queue_listener"
_QUEUE_HANDLER_CLOSED_ATTR = "_puripuly_heart_queue_closed"
_QUEUE_HANDLER_REFCOUNT_ATTR = "_puripuly_heart_queue_refcount"
_QUEUE_HANDLER_QUEUE_ATTR = "_puripuly_heart_queue"

LOG_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"


def _main_formatter() -> logging.Formatter:
    return logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)


@dataclass(slots=True)
class RuntimeLoggingSinks:
    stream_handler: logging.Handler
    file_handler: logging.Handler
    log_file: Path
    owner_logger: logging.Logger | None = None
    file_queue_handler: logging.Handler | None = None
    file_queue_listener: QueueListener | None = None
    file_queue: queue.Queue[logging.LogRecord] | None = None
    _closed: bool = False

    def close(self, *, force: bool = False) -> None:
        if self._closed and not force:
            return
        self._closed = True
        # Two paths: (1) shared queue handler — decrement refcount, close only at 0.
        # (2) standalone file handler — close directly.
        # force=True bypasses refcount and unconditionally tears down the queue handler.
        if self.owner_logger is not None and self.file_queue_handler is not None:
            _release_main_file_queue_handler(
                self.owner_logger,
                self.file_queue_handler,
                force=force,
            )
            return
        _close_file_handler(self.file_handler)


def default_main_log_file(*, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / MAIN_LOG_FILENAME


def _main_log_backup_namer(default_name: str) -> str:
    backup_path = Path(default_name)
    if backup_path.name == f"{MAIN_LOG_FILENAME}.1":
        return str(backup_path.with_name(MAIN_LOG_BACKUP_FILENAME))
    return default_name


def configure_main_logging(
    *,
    root_logger: logging.Logger | None = None,
    log_dir: Path,
) -> RuntimeLoggingSinks:
    # Sets up: stream handler + QueueHandler→QueueListener→RotatingFileHandler.
    # If a queue handler for the same log file already exists (e.g. from a
    # previous session restart), reuses it and increments refcount.
    target_logger = root_logger or logging.getLogger()
    log_file = default_main_log_file(log_dir=log_dir)

    stream_handler = _find_main_stream_handler(target_logger)
    if stream_handler is None:
        stream_handler = logging.StreamHandler()
        stream_handler.set_name(_MAIN_STREAM_HANDLER_NAME)
        target_logger.addHandler(stream_handler)
    stream_handler.setFormatter(_main_formatter())

    _remove_stale_main_file_queue_handlers(target_logger, log_file=log_file)
    existing_queue = _find_main_file_queue_handler(target_logger, log_file=log_file)
    if existing_queue is None:
        file_handler = _find_main_file_handler(target_logger, log_file=log_file)
        if file_handler is not None:
            with contextlib.suppress(Exception):
                target_logger.removeHandler(file_handler)
        else:
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=1,
                encoding="utf-8",
            )
        file_handler.namer = _main_log_backup_namer
        file_handler.set_name(_MAIN_FILE_HANDLER_NAME)
        file_handler.setFormatter(_main_formatter())
        file_queue: queue.Queue[logging.LogRecord] = queue.Queue()
        file_queue_handler = QueueHandler(file_queue)
        file_queue_handler.set_name(_MAIN_FILE_QUEUE_HANDLER_NAME)
        file_queue_listener = QueueListener(file_queue, file_handler, respect_handler_level=True)
        setattr(file_queue_handler, _QUEUE_HANDLER_LOG_FILE_ATTR, str(log_file.resolve()))
        setattr(file_queue_handler, _QUEUE_HANDLER_FILE_HANDLER_ATTR, file_handler)
        setattr(file_queue_handler, _QUEUE_HANDLER_LISTENER_ATTR, file_queue_listener)
        setattr(file_queue_handler, _QUEUE_HANDLER_CLOSED_ATTR, False)
        setattr(file_queue_handler, _QUEUE_HANDLER_REFCOUNT_ATTR, 1)
        setattr(file_queue_handler, _QUEUE_HANDLER_QUEUE_ATTR, file_queue)
        target_logger.addHandler(file_queue_handler)
        file_queue_listener.start()
    else:
        file_queue_handler, file_handler, file_queue_listener = existing_queue
        file_queue = _main_file_queue_for_handler(file_queue_handler)
        setattr(
            file_queue_handler,
            _QUEUE_HANDLER_REFCOUNT_ATTR,
            int(getattr(file_queue_handler, _QUEUE_HANDLER_REFCOUNT_ATTR, 1)) + 1,
        )
        file_handler.namer = _main_log_backup_namer
        file_handler.setFormatter(_main_formatter())

    target_logger.setLevel(logging.INFO)
    return RuntimeLoggingSinks(
        stream_handler=stream_handler,
        file_handler=file_handler,
        log_file=log_file,
        owner_logger=target_logger,
        file_queue_handler=file_queue_handler,
        file_queue_listener=file_queue_listener,
        file_queue=file_queue,
    )


class SessionRuntimeLoggingService:
    # Satisfies ports.logging.SessionLogger protocol implicitly.
    # All adapters (openai_compatible, local_openai, chatbox_paginator)
    # and wiring.py type-hint against SessionLogger, not this class.
    # If SessionLogger changes, update this class to match.

    def __init__(
        self,
        *,
        root_logger: logging.Logger | None = None,
        session_logger: logging.Logger | None = None,
        sinks: RuntimeLoggingSinks | None = None,
        ui_handler_factory: Callable[[RealtimeLogSink], logging.Handler] | None = None,
        log_dir: Path,
    ) -> None:
        self._root_logger = root_logger or logging.getLogger()
        self._owns_sinks = sinks is None
        self._sinks = sinks or configure_main_logging(root_logger=self._root_logger, log_dir=log_dir)
        self._session_logger = session_logger or logging.getLogger(_new_session_logger_name())
        self._root_logger.setLevel(logging.INFO)
        self._session_logger.setLevel(logging.INFO)
        # MUST be False — session logger shares stream+file handlers with root.
        # If True, every message duplicates on root (propagated + root's own handlers).
        self._session_logger.propagate = False
        self._ui_handler_factory = ui_handler_factory
        self._realtime_sink: RealtimeLogSink | None = None
        self._ui_handler: logging.Handler | None = None
        self._session_handlers: list[logging.Handler] = []
        self._mode = SessionLoggingMode.BASIC

        file_output_handler = (
            getattr(self._sinks, "file_queue_handler", None) or self._sinks.file_handler
        )
        _ensure_handler(self._root_logger, self._sinks.stream_handler)
        _ensure_handler(self._root_logger, file_output_handler)
        if _ensure_handler(self._session_logger, self._sinks.stream_handler):
            self._session_handlers.append(self._sinks.stream_handler)
        if _ensure_handler(self._session_logger, file_output_handler):
            self._session_handlers.append(file_output_handler)

    @property
    def mode(self) -> SessionLoggingMode:
        return self._mode

    @property
    def log_file(self) -> Path:
        return self._sinks.log_file

    def set_mode(self, mode: SessionLoggingMode | str) -> None:
        self._mode = SessionLoggingMode(mode)

    def attach_realtime_sink(self, sink: RealtimeLogSink) -> None:
        if self._realtime_sink is sink:
            return

        self.detach_realtime_sink()
        self._realtime_sink = sink
        if self._ui_handler_factory is None:
            return

        handler = self._ui_handler_factory(sink)
        self._ui_handler = handler
        _ensure_handler(self._root_logger, handler)
        _ensure_handler(self._session_logger, handler)

    def detach_realtime_sink(self) -> None:
        if self._ui_handler is not None:
            with contextlib.suppress(Exception):
                self._root_logger.removeHandler(self._ui_handler)
            with contextlib.suppress(Exception):
                self._session_logger.removeHandler(self._ui_handler)
            with contextlib.suppress(Exception):
                self._ui_handler.close()
        self._realtime_sink = None
        self._ui_handler = None

    def emit_basic(self, message: str, *, level: int = logging.INFO) -> None:
        self._session_logger.log(level, message)

    def emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool:
        if self._mode is not SessionLoggingMode.DETAILED:
            return False
        self._session_logger.log(level, message)
        return True

    def emit_detailed_lazy(
        self,
        build_message: Callable[[], str],
        *,
        level: int = logging.INFO,
    ) -> bool:
        if self._mode is not SessionLoggingMode.DETAILED:
            return False
        self._session_logger.log(level, build_message())
        return True

    def emit_persisted(self, message: str, *, level: int = logging.INFO) -> None:
        # Bypass queue — write directly to file handler.
        # Used for critical messages that must survive process crash.
        # Joins pending queue first to maintain chronological order.
        #
        # Record carries session logger name for output, but is written
        # directly to the RotatingFileHandler (bypassing both queue and
        # stream handler).  Do NOT route through session_logger.handle() —
        # that would hit the queue + stream handler, defeating the bypass
        # and causing duplicate console output.
        record = self._session_logger.makeRecord(
            self._session_logger.name,
            level,
            fn="",
            lno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        _join_pending_file_queue(self._sinks)
        self._sinks.file_handler.handle(record)
        with contextlib.suppress(Exception):
            self._sinks.file_handler.flush()

    def close(self) -> None:
        self.detach_realtime_sink()
        for handler in self._session_handlers:
            with contextlib.suppress(Exception):
                self._session_logger.removeHandler(handler)
        self._session_handlers.clear()
        if self._owns_sinks:
            self._sinks.close()


def _ensure_handler(logger: logging.Logger, handler: logging.Handler) -> bool:
    if handler not in logger.handlers:
        logger.addHandler(handler)
        return True
    return False


def _new_session_logger_name() -> str:
    return f"{_SESSION_LOGGER_NAME}.{uuid4()}"


def _find_main_stream_handler(logger: logging.Logger) -> logging.Handler | None:
    fallback: logging.Handler | None = None
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, RotatingFileHandler
        ):
            if handler.get_name() == _MAIN_STREAM_HANDLER_NAME:
                return handler
            fallback = fallback or handler
    if fallback is not None:
        fallback.set_name(_MAIN_STREAM_HANDLER_NAME)
    return fallback


def _find_main_file_handler(logger: logging.Logger, *, log_file: Path) -> logging.Handler | None:
    expected_path = str(log_file.resolve())
    for handler in logger.handlers:
        if not isinstance(handler, RotatingFileHandler):
            continue
        if handler.get_name() == _MAIN_FILE_HANDLER_NAME:
            return handler
        if str(Path(handler.baseFilename).resolve()) == expected_path:
            handler.set_name(_MAIN_FILE_HANDLER_NAME)
            return handler
    return None


def _close_file_handler(file_handler: logging.Handler) -> None:
    with contextlib.suppress(Exception):
        file_handler.flush()
    with contextlib.suppress(Exception):
        file_handler.close()


def _main_file_queue_for_handler(
    handler: logging.Handler,
) -> queue.Queue[logging.LogRecord] | None:
    file_queue = getattr(handler, _QUEUE_HANDLER_QUEUE_ATTR, None)
    if isinstance(file_queue, queue.Queue):
        return file_queue
    if isinstance(handler, QueueHandler) and isinstance(handler.queue, queue.Queue):
        setattr(handler, _QUEUE_HANDLER_QUEUE_ATTR, handler.queue)
        return handler.queue
    return None


def _join_pending_file_queue(sinks: RuntimeLoggingSinks) -> None:
    file_queue_handler = getattr(sinks, "file_queue_handler", None)
    if file_queue_handler is None:
        return
    if getattr(file_queue_handler, _QUEUE_HANDLER_CLOSED_ATTR, False):
        return
    file_queue = getattr(sinks, "file_queue", None) or _main_file_queue_for_handler(
        file_queue_handler
    )
    if file_queue is not None:
        file_queue.join()


def _close_main_file_queue_handler(logger: logging.Logger, handler: logging.Handler) -> None:
    with contextlib.suppress(Exception):
        logger.removeHandler(handler)
    setattr(handler, _QUEUE_HANDLER_CLOSED_ATTR, True)
    setattr(handler, _QUEUE_HANDLER_REFCOUNT_ATTR, 0)

    listener = getattr(handler, _QUEUE_HANDLER_LISTENER_ATTR, None)
    if isinstance(listener, QueueListener):
        with contextlib.suppress(Exception):
            listener.stop()

    file_handler = getattr(handler, _QUEUE_HANDLER_FILE_HANDLER_ATTR, None)
    if isinstance(file_handler, logging.Handler):
        _close_file_handler(file_handler)


def _release_main_file_queue_handler(
    logger: logging.Logger,
    handler: logging.Handler,
    *,
    force: bool = False,
) -> None:
    if getattr(handler, _QUEUE_HANDLER_CLOSED_ATTR, False):
        return
    if force:
        _close_main_file_queue_handler(logger, handler)
        return
    refcount = int(getattr(handler, _QUEUE_HANDLER_REFCOUNT_ATTR, 1))
    remaining_refcount = max(0, refcount - 1)
    setattr(handler, _QUEUE_HANDLER_REFCOUNT_ATTR, remaining_refcount)
    if remaining_refcount > 0:
        return
    _close_main_file_queue_handler(logger, handler)


def _remove_stale_main_file_queue_handlers(logger: logging.Logger, *, log_file: Path) -> None:
    expected_path = str(log_file.resolve())
    for handler in list(logger.handlers):
        if handler.get_name() != _MAIN_FILE_QUEUE_HANDLER_NAME:
            continue
        if getattr(handler, _QUEUE_HANDLER_LOG_FILE_ATTR, None) == expected_path and not getattr(
            handler, _QUEUE_HANDLER_CLOSED_ATTR, False
        ):
            continue
        _close_main_file_queue_handler(logger, handler)


def _find_main_file_queue_handler(
    logger: logging.Logger,
    *,
    log_file: Path,
) -> tuple[logging.Handler, logging.Handler, QueueListener] | None:
    expected_path = str(log_file.resolve())
    for handler in logger.handlers:
        if handler.get_name() != _MAIN_FILE_QUEUE_HANDLER_NAME:
            continue
        if getattr(handler, _QUEUE_HANDLER_CLOSED_ATTR, False):
            continue
        if getattr(handler, _QUEUE_HANDLER_LOG_FILE_ATTR, None) != expected_path:
            continue
        file_handler = getattr(handler, _QUEUE_HANDLER_FILE_HANDLER_ATTR, None)
        listener = getattr(handler, _QUEUE_HANDLER_LISTENER_ATTR, None)
        if isinstance(listener, QueueListener) and isinstance(file_handler, logging.Handler):
            return handler, file_handler, listener
    return None
