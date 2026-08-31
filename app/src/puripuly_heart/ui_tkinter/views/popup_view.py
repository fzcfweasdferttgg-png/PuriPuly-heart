"""View/status popup window for the Tkinter/CTk GUI.

``PopupViewWindow`` is a ``CTkToplevel`` that provides:

1. **Status indicator** — connection state (● green/red/yellow) + text.
2. **Translation result display** — scrollable textbox for current translation.
3. **Manual text input + Send button** — bypass STT, send to chatbox via controller.
4. **Log display area** — migrated from ``LogsView``, scrollable + filterable.
5. **Conversation history** — migrated from ``LogsView``.

All public methods are thread-safe — they marshal to the Tk main loop via
``self.after(0, ...)``.  Every ``_method_ui`` callback guards with
``winfo_exists()`` to avoid crashes after the popup is withdrawn.

The popup is never destroyed; ``WM_DELETE_WINDOW`` calls ``withdraw()`` so
the controller can ``deiconify()`` it later.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Any

import customtkinter as ctk
import tkinter as tk

from puripuly_heart.domain.i18n import source_label, t
from puripuly_heart.ui_tkinter import theme as th

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — log buffer management
# ---------------------------------------------------------------------------

MAX_LOG_LINES: int = 4000
CLEANUP_BATCH: int = 500
MAX_CONVERSATION_RECORDS: int = 1000

# ---------------------------------------------------------------------------
# Constants — log filter levels
# ---------------------------------------------------------------------------

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
# Constants — connection states
# ---------------------------------------------------------------------------

_STATE_CONNECTED: str = "connected"
_STATE_DISCONNECTED: str = "disconnected"
_STATE_RECONNECTING: str = "reconnecting"

_STATUS_COLORS: dict[str, str] = {
    _STATE_CONNECTED: th.COLOR_SUCCESS,
    _STATE_DISCONNECTED: th.COLOR_ERROR,
    _STATE_RECONNECTING: th.COLOR_WARNING,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_timestamp(wall_clock_ms: int | None) -> str:
    """Format a wall-clock-millisecond timestamp as HH:MM:SS."""
    ts = wall_clock_ms / 1000.0 if wall_clock_ms is not None else time.time()
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# TkinterLogHandler — bridges Python logging → PopupViewWindow textbox
# ---------------------------------------------------------------------------


class TkinterLogHandler(logging.Handler):
    """``logging.Handler`` that forwards formatted records to a
    :class:`PopupViewWindow` via its thread-safe ``append_log`` method."""

    def __init__(self, view_window: "PopupViewWindow") -> None:
        super().__init__()
        self._view_window = view_window
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._view_window.append_log(msg, level=record.levelno)
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
# PopupViewWindow
# ---------------------------------------------------------------------------


class PopupViewWindow(ctk.CTkToplevel):
    """Popup window with status, translation display, manual input, logs,
    and conversation history.

    Parameters
    ----------
    master : widget
        Parent CTk widget (typically ``TkApp``).
    controller : GuiController
        Shared application controller (business logic lives there).
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self._controller = controller

        # --- Window setup --------------------------------------------------
        self.title(t("tk.popup.view.title", default="View"))
        self.configure(fg_color=th.COLOR_BACKGROUND)
        self.transient(master)
        self.resizable(True, True)

        # Intercept close → withdraw, never destroy.
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)

        # --- Connection state -----------------------------------------------
        self._connection_state: str = _STATE_DISCONNECTED
        self._connection_text: str = t(
            "tk.popup.view.status.disconnected", default="Disconnected"
        )

        # --- Log buffer (raw formatted lines + level) ----------------------
        self._log_buffer: list[tuple[str, int]] = []
        self._cleanup_count: int = 0
        self._current_filter: str = _FILTER_ALL
        self._auto_scroll: bool = True

        # --- Conversation records ------------------------------------------
        self._conversations: list[_ConversationEntry] = []
        self._conv_cleanup_count: int = 0

        # --- Internal handler reference (set externally via get_handler) ----
        self._handler: TkinterLogHandler | None = None

        # --- Widget references for locale updates --------------------------
        self._title_label: ctk.CTkLabel | None = None
        self._status_label: ctk.CTkLabel | None = None
        self._translation_title_label: ctk.CTkLabel | None = None
        self._input_label: ctk.CTkLabel | None = None
        self._filter_label: ctk.CTkLabel | None = None
        self._conv_title_label: ctk.CTkLabel | None = None

        # --- Build UI ------------------------------------------------------
        self._build_ui()

        # Deiconify in case the window manager starts hidden.
        self.deiconify()

    # ------------------------------------------------------------------
    # Popup lifecycle
    # ------------------------------------------------------------------

    def _on_close_request(self) -> None:
        """Withdraw (hide) the popup instead of destroying it.

        The controller may later call ``self.deiconify()`` to show it again.
        """
        self.withdraw()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble all popup sections."""
        # Default geometry — wide enough for log content
        self.geometry("520x680")

        # Title bar area
        self._title_label = ctk.CTkLabel(
            self,
            text=t("tk.popup.view.title", default="View"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_TITLE, "bold"),
            text_color=th.COLOR_TEXT,
        )
        self._title_label.pack(
            anchor="w", padx=th.CONTENT_PAD_X, pady=(th.CONTENT_PAD_Y, 8)
        )

        # Scrollable content
        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=th.COLOR_BACKGROUND,
            scrollbar_button_color=th.COLOR_DIVIDER,
        )
        self._scroll.pack(fill="both", expand=True, padx=th.CONTENT_PAD_X)

        self._build_status_section(self._scroll)
        self._build_translation_section(self._scroll)
        self._build_input_section(self._scroll)
        self._build_log_section(self._scroll)
        self._build_conversation_section(self._scroll)

    # --- Status indicator -------------------------------------------------

    def _build_status_section(self, parent: ctk.CTkScrollableFrame) -> None:
        """Connection state indicator with colored dot and text."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")

        # Colored status dot
        self._status_dot = ctk.CTkLabel(
            row,
            text="●",
            font=(th.FONT_FAMILY_FALLBACK, 18),
            text_color=_STATUS_COLORS[_STATE_DISCONNECTED],
            width=24,
        )
        self._status_dot.pack(side="left", padx=(0, 8))
        self._add_debug_label(self._status_dot, "popup.view.status.indicator")

        # Status text
        self._status_label = ctk.CTkLabel(
            row,
            text=self._connection_text,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT,
            anchor="w",
        )
        self._status_label.pack(side="left", fill="x", expand=True)
        self._add_debug_label(self._status_label, "popup.view.status.text")

    # --- Translation result display ---------------------------------------

    def _build_translation_section(self, parent: ctk.CTkScrollableFrame) -> None:
        """Scrollable textbox showing current translation result."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        self._translation_title_label = ctk.CTkLabel(
            inner,
            text=t("tk.popup.view.translation.title", default="Translation"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        )
        self._translation_title_label.pack(anchor="w", pady=(0, 6))

        self._translation_textbox = ctk.CTkTextbox(
            inner,
            height=100,
            fg_color=th.COLOR_BACKGROUND,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            corner_radius=8,
            border_width=1,
            border_color=th.COLOR_DIVIDER,
            state="disabled",
            wrap="word",
        )
        self._translation_textbox.pack(fill="x")
        self._add_debug_label(self._translation_textbox, "popup.view.translation.display")

    # --- Manual text input + Send button ----------------------------------

    def _build_input_section(self, parent: ctk.CTkScrollableFrame) -> None:
        """Text input field with Send button for manual text submission."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        self._input_label = ctk.CTkLabel(
            inner,
            text=t("tk.popup.view.input.label", default="Manual Input"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        )
        self._input_label.pack(anchor="w", pady=(0, 6))

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")

        self._input_field = ctk.CTkEntry(
            row,
            placeholder_text=t(
                "tk.popup.view.input.placeholder", default="Type text to translate…"
            ),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            fg_color=th.COLOR_BACKGROUND,
            text_color=th.COLOR_TEXT,
            border_color=th.COLOR_DIVIDER,
            corner_radius=8,
        )
        self._input_field.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._input_field.bind("<Return>", self._on_input_submit)
        self._add_debug_label(self._input_field, "popup.view.input.field")

        self._send_btn = ctk.CTkButton(
            row,
            text=t("tk.popup.view.input.send", default="Send"),
            width=80,
            height=32,
            corner_radius=8,
            fg_color=th.COLOR_PRIMARY,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color="#FFFFFF",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._on_send_click,
        )
        self._send_btn.pack(side="right")
        self._add_debug_label(self._send_btn, "popup.view.input.send")

    # --- Log display area -------------------------------------------------

    def _build_log_section(self, parent: ctk.CTkScrollableFrame) -> None:
        """Filterable, scrollable log display (migrated from LogsView)."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        # Toolbar row
        toolbar = ctk.CTkFrame(inner, fg_color="transparent")
        toolbar.pack(fill="x", pady=(0, 6))

        self._filter_label = ctk.CTkLabel(
            toolbar,
            text=t("tk.logs.filter", default="Filter:"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        )
        self._filter_label.pack(side="left", padx=(0, 6))

        filter_display = self._filter_display_values()
        self._filter_dd = ctk.CTkOptionMenu(
            toolbar,
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
        self._add_debug_label(self._filter_dd, "popup.view.log.filter")

        # Clear button
        self._clear_btn = ctk.CTkButton(
            toolbar,
            text=t("tk.logs.clear", default="Clear"),
            width=80,
            height=28,
            corner_radius=8,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            command=self._clear_logs,
        )
        self._clear_btn.pack(side="left", padx=(0, 12))

        # Auto-scroll toggle
        self._auto_scroll_btn = ctk.CTkButton(
            toolbar,
            text=self._auto_scroll_label(),
            width=120,
            height=28,
            corner_radius=8,
            fg_color=th.COLOR_PRIMARY_CONTAINER,
            hover_color=th.COLOR_PRIMARY,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            command=self._toggle_auto_scroll,
        )
        self._auto_scroll_btn.pack(side="left")

        # Log textbox
        self._log_textbox = ctk.CTkTextbox(
            inner,
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
        self._log_textbox.pack(fill="x")
        self._add_debug_label(self._log_textbox, "popup.view.log.display")

    # --- Conversation history ---------------------------------------------

    def _build_conversation_section(self, parent: ctk.CTkScrollableFrame) -> None:
        """Conversation history display (migrated from LogsView)."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        self._conv_title_label = ctk.CTkLabel(
            inner,
            text=t("tk.logs.conversation.show", default="Conversation History"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        )
        self._conv_title_label.pack(anchor="w", pady=(0, 6))

        self._conv_textbox = ctk.CTkTextbox(
            inner,
            height=180,
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
        self._add_debug_label(self._conv_textbox, "popup.view.conversation.display")

    # ------------------------------------------------------------------
    # Manual input handlers
    # ------------------------------------------------------------------

    def _on_input_submit(self, _event: tk.Event | None = None) -> None:
        """Handle Enter key in the input field."""
        self._on_send_click()

    def _on_send_click(self) -> None:
        """Send the manual input text to the controller."""
        text = self._input_field.get().strip()
        if not text:
            return
        self._input_field.delete(0, "end")
        # Fire-and-forget on the controller's async loop
        self._run_async(self._controller.submit_manual_text(text))

    # ------------------------------------------------------------------
    # Async helper
    # ------------------------------------------------------------------

    def _run_async(self, coro: Any) -> None:
        """Fire-and-forget an async coroutine on the controller's event loop.

        Prefers ``asyncio.run_coroutine_threadsafe`` on the stored loop for
        efficiency.  Falls back to a short-lived thread if the loop is not
        yet available (controller still starting).
        """
        loop = getattr(self._controller, "_async_loop", None)
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            future.add_done_callback(self._on_async_error)
            return

        # Fallback: dedicated thread (controller loop not ready yet).
        def _worker() -> None:
            try:
                asyncio.run(coro)
            except Exception:
                logger.exception("PopupView async call failed (fallback thread)")

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _on_async_error(future: asyncio.futures.Future[Any]) -> None:
        """Log exceptions from fire-and-forget coroutines."""
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            logger.exception("PopupView async call raised", exc_info=exc)

    # ------------------------------------------------------------------
    # Filter helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_display_values() -> list[str]:
        """Human-readable filter labels (order matches filter keys)."""
        return [
            t("tk.logs.filter.all", default="All"),
            t("tk.logs.filter.info", default="Info"),
            t("tk.logs.filter.warning", default="Warning"),
            t("tk.logs.filter.error", default="Error"),
        ]

    def _display_to_filter_key(self, display: str) -> str:
        """Map a display label back to the internal filter key."""
        mapping = {
            t("tk.logs.filter.all", default="All"): _FILTER_ALL,
            t("tk.logs.filter.info", default="Info"): _FILTER_INFO,
            t("tk.logs.filter.warning", default="Warning"): _FILTER_WARNING,
            t("tk.logs.filter.error", default="Error"): _FILTER_ERROR,
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
        state = (
            t("tk.logs.autoscroll.on", default="ON")
            if self._auto_scroll
            else t("tk.logs.autoscroll.off", default="OFF")
        )
        return f"{t('tk.logs.autoscroll', default='Auto-scroll')}: {state}"

    def _toggle_auto_scroll(self) -> None:
        self._auto_scroll = not self._auto_scroll
        self._auto_scroll_btn.configure(text=self._auto_scroll_label())
        if self._auto_scroll:
            self._scroll_log_to_end()

    # ------------------------------------------------------------------
    # Public API — connection status (thread-safe)
    # ------------------------------------------------------------------

    def set_connection_status(self, state: str, text: str = "") -> None:
        """Update the connection status indicator.

        Parameters
        ----------
        state : str
            One of ``"connected"``, ``"disconnected"``, ``"reconnecting"``.
        text : str
            Human-readable status text.  If empty, a default is used.
        """
        self.after(0, lambda: self._set_connection_status_ui(state, text))

    def _set_connection_status_ui(self, state: str, text: str) -> None:
        """UI-thread worker: update status dot and label."""
        if not self.winfo_exists():
            return
        self._connection_state = state
        color = _STATUS_COLORS.get(state, th.COLOR_TEXT_SECONDARY)
        self._status_dot.configure(text_color=color)

        if text:
            self._connection_text = text
        else:
            # Fallback defaults
            defaults = {
                _STATE_CONNECTED: t(
                    "tk.popup.view.status.connected", default="Connected"
                ),
                _STATE_DISCONNECTED: t(
                    "tk.popup.view.status.disconnected", default="Disconnected"
                ),
                _STATE_RECONNECTING: t(
                    "tk.popup.view.status.reconnecting", default="Reconnecting…"
                ),
            }
            self._connection_text = defaults.get(state, state)
        self._status_label.configure(text=self._connection_text)

    # ------------------------------------------------------------------
    # Public API — translation display (thread-safe)
    # ------------------------------------------------------------------

    def set_translation_text(self, text: str) -> None:
        """Update the translation result display.

        Parameters
        ----------
        text : str
            Translated text to display.
        """
        self.after(0, lambda: self._set_translation_text_ui(text))

    def _set_translation_text_ui(self, text: str) -> None:
        """UI-thread worker: replace translation textbox content."""
        if not self.winfo_exists():
            return
        self._translation_textbox.configure(state="normal")
        self._translation_textbox.delete("1.0", "end")
        if text:
            self._translation_textbox.insert("end", text)
        self._translation_textbox.configure(state="disabled")

    # ------------------------------------------------------------------
    # Public API — log buffer management (thread-safe)
    # ------------------------------------------------------------------

    def append_log(self, record: str, level: int = logging.DEBUG) -> None:
        """Append a log record.  Safe to call from any thread.

        Satisfies the ``RealtimeLogSink`` protocol (``append_log(line)``).

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
        if not self.winfo_exists():
            return

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
            entry[0] for entry in self._log_buffer if self._passes_filter(entry[1])
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
    # Public API — conversation history (thread-safe)
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
        if not self.winfo_exists():
            return

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

    def _add_debug_label(self, widget: ctk.CTkBaseClass, widget_id: str) -> None:
        """Add a small debug label showing the widget identifier.

        Labels start hidden and are toggled via the app's [D] button.
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
        label.bind("<Button-1>", lambda e, t=widget_id: app._copy_debug_label(t))
        # Start hidden — don't place until toggled via [D] button
        app._debug_labels.append(label)

    # ------------------------------------------------------------------
    # Locale refresh
    # ------------------------------------------------------------------

    def apply_locale(self) -> None:
        """Thread-safe: update all labels when locale changes."""
        self.after(0, self._apply_locale_ui)

    def _apply_locale_ui(self) -> None:
        """UI-thread worker: re-apply i18n labels after a locale change."""
        if not self.winfo_exists():
            return

        # Window title
        self.title(t("tk.popup.view.title", default="View"))

        # Section labels
        if self._title_label is not None:
            self._title_label.configure(
                text=t("tk.popup.view.title", default="View")
            )

        # Status text (preserve current state, just update locale)
        defaults = {
            _STATE_CONNECTED: t(
                "tk.popup.view.status.connected", default="Connected"
            ),
            _STATE_DISCONNECTED: t(
                "tk.popup.view.status.disconnected", default="Disconnected"
            ),
            _STATE_RECONNECTING: t(
                "tk.popup.view.status.reconnecting", default="Reconnecting…"
            ),
        }
        self._connection_text = defaults.get(
            self._connection_state, self._connection_text
        )
        if self._status_label is not None:
            self._status_label.configure(text=self._connection_text)

        # Translation section
        if self._translation_title_label is not None:
            self._translation_title_label.configure(
                text=t("tk.popup.view.translation.title", default="Translation")
            )

        # Input section
        if self._input_label is not None:
            self._input_label.configure(
                text=t("tk.popup.view.input.label", default="Manual Input")
            )
        self._input_field.configure(
            placeholder_text=t(
                "tk.popup.view.input.placeholder", default="Type text to translate…"
            )
        )
        self._send_btn.configure(
            text=t("tk.popup.view.input.send", default="Send")
        )

        # Log section
        if self._filter_label is not None:
            self._filter_label.configure(text=t("tk.logs.filter", default="Filter:"))
        self._clear_btn.configure(text=t("tk.logs.clear", default="Clear"))
        self._auto_scroll_btn.configure(text=self._auto_scroll_label())
        filter_display = self._filter_display_values()
        self._filter_dd.configure(values=filter_display)
        # Preserve current filter selection
        idx_map = {
            _FILTER_ALL: 0,
            _FILTER_INFO: 1,
            _FILTER_WARNING: 2,
            _FILTER_ERROR: 3,
        }
        idx = idx_map.get(self._current_filter, 0)
        self._filter_dd.set(filter_display[idx])

        # Conversation section
        if self._conv_title_label is not None:
            self._conv_title_label.configure(
                text=t("tk.logs.conversation.show", default="Conversation History")
            )
