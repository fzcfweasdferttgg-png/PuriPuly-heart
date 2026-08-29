"""Logs view — live application log display and conversation history.

Provides a scrollable, filterable log view backed by a Python
``logging.Handler``.  Thread-safe: all UI mutations go through
``widget.after()``.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import source_label, t
from puripuly_heart.ui_tkinter import theme as th

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_LOG_LINES: int = 4000
CLEANUP_BATCH: int = 500
MAX_CONVERSATION_RECORDS: int = 1000

_FILTER_ALL: str = "all"
_FILTER_INFO: str = "info"
_FILTER_WARNING: str = "warning"
_FILTER_ERROR: str = "error"
_FILTER_LEVELS: dict[str, int] = {
    _FILTER_ALL: logging.DEBUG,
    _FILTER_INFO: logging.INFO,
    _FILTER_WARNING: logging.WARNING,
    _FILTER_ERROR: logging.ERROR,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_log_dir() -> Path:
    """Return the directory where log files are stored."""
    from puripuly_heart.config.paths import user_config_dir

    return user_config_dir()


def _format_timestamp(wall_clock_ms: int | None) -> str:
    """Format a wall-clock-millisecond timestamp as HH:MM:SS."""
    ts = wall_clock_ms / 1000.0 if wall_clock_ms is not None else time.time()
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# TkinterLogHandler — bridges Python logging → LogsView textbox
# ---------------------------------------------------------------------------


class TkinterLogHandler(logging.Handler):
    """``logging.Handler`` that forwards formatted records to a
    :class:`LogsView` via its thread-safe ``append_log`` method."""

    def __init__(self, logs_view: "LogsView") -> None:
        super().__init__()
        self._logs_view = logs_view
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._logs_view.append_log(msg, level=record.levelno)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Conversation record
# ---------------------------------------------------------------------------


class _ConversationEntry:
    """Single conversation entry (speaker + source text + translation)."""

    __slots__ = ("timestamp", "source", "channel", "source_text", "translated_text")

    def __init__(
        self,
        timestamp: str,
        source: str,
        channel: str,
        source_text: str,
        translated_text: str,
    ) -> None:
        self.timestamp = timestamp
        self.source = source
        self.channel = channel
        self.source_text = source_text
        self.translated_text = translated_text


# ---------------------------------------------------------------------------
# LogsView
# ---------------------------------------------------------------------------


class LogsView(ctk.CTkFrame):
    """System logs view with filterable display and conversation history.

    Parameters
    ----------
    master : widget
        Parent CTk widget.
    controller : GuiController
        Shared application controller (business logic lives there).
    """

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, fg_color=th.COLOR_BACKGROUND, **kwargs)
        self._controller = controller

        # Log buffer (raw formatted lines + level)
        self._log_buffer: list[tuple[str, int]] = []
        self._cleanup_count: int = 0
        self._current_filter: str = _FILTER_ALL
        self._auto_scroll: bool = True
        self._showing_conversation: bool = False

        # Conversation records
        self._conversations: list[_ConversationEntry] = []
        self._conv_cleanup_count: int = 0

        # Internal handler reference (set externally via get_handler)
        self._handler: TkinterLogHandler | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble the logs view."""
        # Title
        ctk.CTkLabel(
            self,
            text=t("logs.title", default="System Logs"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_TITLE, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", padx=th.CONTENT_PAD_X, pady=(th.CONTENT_PAD_Y, 8))

        # Scrollable area
        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=th.COLOR_BACKGROUND,
            scrollbar_button_color=th.COLOR_DIVIDER,
        )
        self._scroll.pack(fill="both", expand=True, padx=th.CONTENT_PAD_X)

        # --- Toolbar card ---------------------------------------------------
        toolbar_card = ctk.CTkFrame(
            self._scroll,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        toolbar_card.pack(fill="x", pady=(0, 12))

        toolbar_inner = ctk.CTkFrame(toolbar_card, fg_color="transparent")
        toolbar_inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        self._build_toolbar(toolbar_inner)

        # --- Log display card -----------------------------------------------
        log_card = ctk.CTkFrame(
            self._scroll,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        log_card.pack(fill="x", pady=(0, 12))

        log_inner = ctk.CTkFrame(log_card, fg_color="transparent")
        log_inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        self._build_log_display(log_inner)

        # --- Conversation history card --------------------------------------
        conv_card = ctk.CTkFrame(
            self._scroll,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        conv_card.pack(fill="x", pady=(0, 12))

        conv_inner = ctk.CTkFrame(conv_card, fg_color="transparent")
        conv_inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        self._build_conversation_display(conv_inner)

    def _build_toolbar(self, parent: ctk.CTkFrame) -> None:
        """Filter dropdown, clear button, auto-scroll toggle, open-folder."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")

        # Filter dropdown
        ctk.CTkLabel(
            row,
            text=t("logs.filter", default="Filter:"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(side="left", padx=(0, 6))

        filter_display = self._filter_display_values()
        self._filter_dd = ctk.CTkOptionMenu(
            row,
            values=filter_display,
            width=120,
            command=self._on_filter_change,
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._filter_dd.set(filter_display[0])
        self._filter_dd.pack(side="left", padx=(0, 12))
        self._add_debug_label(self._filter_dd, "log.filter")

        # Clear button
        self._clear_btn = ctk.CTkButton(
            row,
            text=t("logs.clear", default="Clear"),
            width=80,
            height=30,
            corner_radius=8,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            command=self._clear_logs,
        )
        self._clear_btn.pack(side="left", padx=(0, 12))
        self._add_debug_label(self._clear_btn, "btn.clear_logs")

        # Auto-scroll toggle
        self._auto_scroll_btn = ctk.CTkButton(
            row,
            text=self._auto_scroll_label(),
            width=120,
            height=30,
            corner_radius=8,
            fg_color=th.COLOR_PRIMARY_CONTAINER,
            hover_color=th.COLOR_PRIMARY,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            command=self._toggle_auto_scroll,
        )
        self._auto_scroll_btn.pack(side="left", padx=(0, 12))
        self._add_debug_label(self._auto_scroll_btn, "btn.auto_scroll")

        # Open log folder
        self._folder_btn = ctk.CTkButton(
            row,
            text=t("logs.open_folder", default="Open Folder"),
            width=110,
            height=30,
            corner_radius=8,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            command=self._open_log_folder,
        )
        self._folder_btn.pack(side="left")
        self._add_debug_label(self._folder_btn, "btn.open_folder")

    def _build_log_display(self, parent: ctk.CTkFrame) -> None:
        """Read-only textbox for log entries."""
        self._log_textbox = ctk.CTkTextbox(
            parent,
            height=300,
            fg_color=th.COLOR_BACKGROUND,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            corner_radius=8,
            border_width=1,
            border_color=th.COLOR_DIVIDER,
            state="disabled",
            wrap="word",
        )
        self._log_textbox.pack(fill="x")
        self._add_debug_label(self._log_textbox, "log.display")

    def _build_conversation_display(self, parent: ctk.CTkFrame) -> None:
        """Read-only textbox for conversation history."""
        ctk.CTkLabel(
            parent,
            text=t("logs.conversation.show", default="Conversation History"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 6))

        self._conv_textbox = ctk.CTkTextbox(
            parent,
            height=200,
            fg_color=th.COLOR_BACKGROUND,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            corner_radius=8,
            border_width=1,
            border_color=th.COLOR_DIVIDER,
            state="disabled",
            wrap="word",
        )
        self._conv_textbox.pack(fill="x")
        self._add_debug_label(self._conv_textbox, "log.conversation")

    # ------------------------------------------------------------------
    # Filter helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_display_values() -> list[str]:
        """Human-readable filter labels (order matches filter keys)."""
        return [
            t("logs.filter.all", default="All"),
            t("logs.filter.info", default="Info"),
            t("logs.filter.warning", default="Warning"),
            t("logs.filter.error", default="Error"),
        ]

    def _display_to_filter_key(self, display: str) -> str:
        """Map a display label back to the internal filter key."""
        mapping = {
            t("logs.filter.all", default="All"): _FILTER_ALL,
            t("logs.filter.info", default="Info"): _FILTER_INFO,
            t("logs.filter.warning", default="Warning"): _FILTER_WARNING,
            t("logs.filter.error", default="Error"): _FILTER_ERROR,
        }
        return mapping.get(display, _FILTER_ALL)

    def _on_filter_change(self, value: str) -> None:
        """Handle filter dropdown selection."""
        self._current_filter = self._display_to_filter_key(value)
        self._refresh_log_display()

    # ------------------------------------------------------------------
    # Auto-scroll
    # ------------------------------------------------------------------

    def _auto_scroll_label(self) -> str:
        state = t("logs.autoscroll.on", default="ON") if self._auto_scroll else t("logs.autoscroll.off", default="OFF")
        return f"{t('logs.autoscroll', default='Auto-scroll')}: {state}"

    def _toggle_auto_scroll(self) -> None:
        self._auto_scroll = not self._auto_scroll
        self._auto_scroll_btn.configure(text=self._auto_scroll_label())
        if self._auto_scroll:
            self._scroll_log_to_end()

    # ------------------------------------------------------------------
    # Log buffer management (thread-safe)
    # ------------------------------------------------------------------

    def append_log(self, record: str, level: int = logging.DEBUG) -> None:
        """Append a log record.  Safe to call from any thread.

        Parameters
        ----------
        record : str
            Pre-formatted log line.
        level : int
            Python logging level (used for filtering).
        """
        self.after(0, lambda: self._append_log_ui(record, level))

    def _append_log_ui(self, record: str, level: int) -> None:
        """UI-thread worker: buffer + (maybe) render the new line."""
        self._log_buffer.append((record, level))

        # Evict oldest when buffer exceeds cap
        if len(self._log_buffer) > MAX_LOG_LINES + CLEANUP_BATCH:
            del self._log_buffer[:CLEANUP_BATCH]
            self._cleanup_count += 1

        # Only render if this level passes the filter
        if self._passes_filter(level):
            self._log_textbox.configure(state="normal")
            self._log_textbox.insert("end", record + "\n")
            self._log_textbox.configure(state="disabled")
            if self._auto_scroll:
                self._scroll_log_to_end()

    def _passes_filter(self, level: int) -> bool:
        """Return ``True`` if *level* passes the current filter."""
        threshold = _FILTER_LEVELS.get(self._current_filter, logging.DEBUG)
        return level >= threshold

    def _refresh_log_display(self) -> None:
        """Re-render the log textbox from the buffer (after filter change)."""
        self._log_textbox.configure(state="normal")
        self._log_textbox.delete("1.0", "end")
        lines = [
            entry[0]
            for entry in self._log_buffer
            if self._passes_filter(entry[1])
        ]
        if lines:
            self._log_textbox.insert("end", "\n".join(lines) + "\n")
        self._log_textbox.configure(state="disabled")
        if self._auto_scroll:
            self._scroll_log_to_end()

    def _clear_logs(self) -> None:
        """Clear the log buffer and display."""
        self._log_buffer.clear()
        self._cleanup_count = 0
        self._log_textbox.configure(state="normal")
        self._log_textbox.delete("1.0", "end")
        self._log_textbox.configure(state="disabled")

    def _scroll_log_to_end(self) -> None:
        """Scroll the log textbox to the last line."""
        self._log_textbox.see("end")

    # ------------------------------------------------------------------
    # Conversation history
    # ------------------------------------------------------------------

    def append_conversation_record(
        self,
        *,
        source: str,
        channel: str,
        source_text: str,
        translated_text: str,
        origin_wall_clock_ms: int | None = None,
    ) -> None:
        """Append a conversation entry.  Thread-safe.

        Parameters
        ----------
        source : str
            Speaker identity (e.g. ``"Mic"``, ``"Peer"``).
        channel : str
            Channel name.
        source_text : str
            Original text.
        translated_text : str
            Translated text.
        origin_wall_clock_ms : int | None
            Wall-clock milliseconds for the timestamp.
        """
        cleaned_src = source_text.strip()
        cleaned_tgt = translated_text.strip()
        if not cleaned_src or not cleaned_tgt:
            return

        entry = _ConversationEntry(
            timestamp=_format_timestamp(origin_wall_clock_ms),
            source=source.strip() or "Mic",
            channel=channel,
            source_text=cleaned_src,
            translated_text=cleaned_tgt,
        )
        self.after(0, lambda: self._append_conversation_ui(entry))

    def _append_conversation_ui(self, entry: _ConversationEntry) -> None:
        """UI-thread worker: buffer + render conversation entry."""
        self._conversations.append(entry)

        if len(self._conversations) > MAX_CONVERSATION_RECORDS + CLEANUP_BATCH:
            del self._conversations[:CLEANUP_BATCH]
            self._conv_cleanup_count += 1

        source_label_text = source_label(entry.source)
        line = (
            f"[{entry.timestamp}] {source_label_text}\n"
            f"{entry.source_text}\n"
            f"{entry.translated_text}\n"
        )
        self._conv_textbox.configure(state="normal")
        self._conv_textbox.insert("end", line + "\n")
        self._conv_textbox.configure(state="disabled")
        self._conv_textbox.see("end")

    # ------------------------------------------------------------------
    # Open log folder
    # ------------------------------------------------------------------

    def _open_log_folder(self) -> None:
        """Open the log directory in the system file explorer."""
        log_dir = _get_log_dir()
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(log_dir)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(log_dir)])
            else:
                subprocess.Popen(["xdg-open", str(log_dir)])
        except FileNotFoundError:
            logger.warning("Could not open log folder: %s", log_dir)

    # ------------------------------------------------------------------
    # Public API — get handler for controller registration
    # ------------------------------------------------------------------

    def get_handler(self) -> TkinterLogHandler:
        """Return (and lazily create) a ``logging.Handler`` for this view."""
        if self._handler is None:
            self._handler = TkinterLogHandler(self)
        return self._handler

    # ------------------------------------------------------------------
    # Debug labels
    # ------------------------------------------------------------------

    def _add_debug_label(self, widget: ctk.CTkFrame, widget_id: str) -> None:
        """Add a small debug label showing the widget identifier.

        Labels start hidden and are toggled via the app's 🔍 button.
        Clicking a label copies its text to the clipboard with a flash.
        """
        app = getattr(self._controller, "app", None)
        if app is None or not getattr(app, "debug_ui_preview", False):
            return
        label = ctk.CTkLabel(
            widget,
            text=f"[{widget_id}]",
            font=("Consolas", 9),
            text_color="#00FF88",
            fg_color="transparent",
        )
        label.place(x=4, y=4)
        label.bind("<Button-1>", lambda e, t=widget_id: app._copy_debug_label(t))
        label.lower()
        app._debug_labels.append(label)

    # ------------------------------------------------------------------
    # Locale refresh
    # ------------------------------------------------------------------

    def apply_locale(self) -> None:
        """Re-apply i18n labels after a locale change."""
        # Rebuild is expensive; just update known labels
        self._auto_scroll_btn.configure(text=self._auto_scroll_label())
        self._clear_btn.configure(
            text=t("logs.clear", default="Clear"),
        )
        self._folder_btn.configure(
            text=t("logs.open_folder", default="Open Folder"),
        )
        filter_display = self._filter_display_values()
        self._filter_dd.configure(values=filter_display)
        # Preserve current selection
        idx_map = {
            _FILTER_ALL: 0,
            _FILTER_INFO: 1,
            _FILTER_WARNING: 2,
            _FILTER_ERROR: 3,
        }
        idx = idx_map.get(self._current_filter, 0)
        self._filter_dd.set(filter_display[idx])


# Module-level logger for the _open_log_folder except clause
logger = logging.getLogger(__name__)
