"""Collapsible section frame and reusable row builder.

Every settings section in the Tkinter GUI inherits from
:class:`CollapsibleSection`.  It provides:
- A clickable header that toggles content visibility
- A consistent 2-column label/control layout via :meth:`add_row`
- Debounce support for sliders via :meth:`debounce_slider`
"""

from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

from puripuly_heart.ui_tkinter import theme as th


class CollapsibleSection(ctk.CTkFrame):
    """A settings section with a clickable header and collapsible content area.

    Parameters
    ----------
    master : widget
        Parent CTk widget.
    title : str
        Section header text (already translated).
    expanded : bool
        Whether the section starts expanded.
    """

    def __init__(
        self,
        master: Any,
        title: str,
        *,
        expanded: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._expanded: bool = expanded

        # --- Header ---
        self._header = ctk.CTkFrame(self, fg_color="transparent")
        self._header.pack(fill="x")

        self._arrow = ctk.CTkLabel(
            self._header,
            text="▼" if expanded else "▶",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
            width=20,
        )
        self._arrow.pack(side="left")

        self._title_label = ctk.CTkLabel(
            self._header,
            text=title,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        )
        self._title_label.pack(side="left", padx=(2, 0))

        # Clickable header
        for widget in (self._header, self._arrow, self._title_label):
            widget.bind("<Button-1>", self._toggle)
            widget.configure(cursor="hand2")

        # --- Content ---
        self._content = ctk.CTkFrame(
            self,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        if expanded:
            self._content.pack(fill="x", pady=(6, 0))

    # ------------------------------------------------------------------
    # Toggle
    # ------------------------------------------------------------------

    def _toggle(self, _event: Any = None) -> None:
        self._expanded = not self._expanded
        self._arrow.configure(text="▼" if self._expanded else "▶")
        if self._expanded:
            self._content.pack(fill="x", pady=(6, 0))
        else:
            self._content.pack_forget()

    # ------------------------------------------------------------------
    # Row builder
    # ------------------------------------------------------------------

    def add_row(
        self,
        label_text: str,
        control: ctk.CTkBaseClass,
    ) -> None:
        """Add a label + control row inside the collapsible content area."""
        row = ctk.CTkFrame(self._content, fg_color="transparent")
        row.pack(fill="x", padx=th.CARD_PAD_X, pady=4)

        ctk.CTkLabel(
            row,
            text=label_text,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT,
            width=160,
            anchor="w",
        ).pack(side="left")

        control.pack(side="right", padx=(8, 0))

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
    # Slider debounce
    # ------------------------------------------------------------------

    def debounce_slider(
        self,
        value: float,
        callback: Callable[[float], None],
        delay_ms: int = 300,
    ) -> None:
        """Debounce a slider value change.

        Only invokes *callback* after *delay_ms* of inactivity.
        """
        if hasattr(self, "_debounce_id"):
            self.after_cancel(self._debounce_id)
        self._debounce_id = self.after(delay_ms, lambda: callback(value))
