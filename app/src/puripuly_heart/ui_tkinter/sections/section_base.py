"""Section frame and reusable row builder.

Every settings section in the Tkinter GUI inherits from
:class:`CollapsibleSection`.  It provides:
- A section header (non-interactive — all content is always visible)
- A consistent 2-column label/control layout via :meth:`add_row`
- Debounce support for sliders via :meth:`debounce_slider`
- Incremental locale updates via :meth:`apply_locale` (no widget rebuild)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th

logger = logging.getLogger(__name__)


@dataclass
class _TranslatableEntry:
    """A single widget registered for in-place locale updates."""

    widget: ctk.CTkBaseClass
    i18n_key: str
    default: str
    attr: str = "text"
    formatter: Callable[[str], str] | None = None


class CollapsibleSection(ctk.CTkFrame):
    """A settings section with a header and always-visible content area.

    All content is shown immediately — no collapsible behaviour.

    Parameters
    ----------
    master : widget
        Parent CTk widget.
    title : str
        Section header text (already translated).
    title_i18n_key : str, optional
        i18n key for the section title — used by :meth:`apply_locale`.
    title_default : str, optional
        Fallback text for the title when *title_i18n_key* is not found.
    expanded : bool
        Ignored — kept for API compatibility.  Content is always visible.
    """

    def __init__(
        self,
        master: Any,
        title: str,
        *,
        title_i18n_key: str | None = None,
        title_default: str | None = None,
        expanded: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._expanded: bool = True  # Always expanded

        # --- Header (non-interactive) ---
        self._header = ctk.CTkFrame(self, fg_color="transparent")
        self._header.pack(fill="x")

        self._title_label = ctk.CTkLabel(
            self._header,
            text=title,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        )
        self._title_label.pack(side="left", padx=(0, 0))

        # --- Content (always visible) ---
        self._content = ctk.CTkFrame(
            self,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        self._content.pack(fill="x", pady=(6, 0))

        # --- Translatables registry (for incremental locale updates) ---
        self._translatables: list[_TranslatableEntry] = []
        self._title_i18n_key: str | None = title_i18n_key
        self._title_default: str = title_default or title

    # ------------------------------------------------------------------
    # Row builder
    # ------------------------------------------------------------------

    def add_row(
        self,
        label_text: str,
        control_or_factory: ctk.CTkBaseClass | Callable[[ctk.CTkFrame], ctk.CTkBaseClass],
        *,
        label_id: str | None = None,
        control_id: str | None = None,
        full_width: bool = False,
        label_i18n_key: str | None = None,
        label_default: str | None = None,
        control_i18n_key: str | None = None,
        control_default: str | None = None,
    ) -> ctk.CTkBaseClass:
        """Add a label + control row inside the collapsible content area.

        When *full_width* is True the control spans the entire row
        (useful for standalone buttons like "Verify").

        *control_or_factory* can be either a pre-created widget or a
        callable that takes the row frame as its argument and returns
        the control widget.  Using a factory ensures the control is
        created with the correct parent for CustomTkinter rendering.

        Returns the control widget (useful when a factory is passed).
        """
        row = ctk.CTkFrame(self._content, fg_color="transparent")
        row.pack(fill="x", padx=th.CARD_PAD_X, pady=4)

        # Resolve control — factory or pre-created
        if callable(control_or_factory) and not isinstance(control_or_factory, ctk.CTkBaseClass):
            control = control_or_factory(row)
        else:
            control = control_or_factory

        if full_width:
            control.pack(fill="x", padx=0)
            if control_i18n_key:
                self._translatables.append(_TranslatableEntry(
                    widget=control,
                    i18n_key=control_i18n_key,
                    default=control_default or "",
                ))
            if control_id:
                self._add_debug_label(control, control_id)
            return control

        row.columnconfigure(0, weight=0, minsize=180)
        row.columnconfigure(1, weight=1)

        label = ctk.CTkLabel(
            row,
            text=label_text,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT,
            anchor="w",
        )
        label.grid(row=0, column=0, sticky="w", padx=(0, 8), in_=row)

        if label_i18n_key:
            self._translatables.append(_TranslatableEntry(
                widget=label,
                i18n_key=label_i18n_key,
                default=label_default or label_text,
            ))

        control.grid(row=0, column=1, sticky="w", padx=(8, 0), in_=row)

        if label_id:
            self._add_debug_label(label, label_id)
        if control_id:
            self._add_debug_label(control, control_id)

        return control

    # ------------------------------------------------------------------
    # Translatable registration (for widgets not created via add_row)
    # ------------------------------------------------------------------

    def _register_translatable(
        self,
        widget: ctk.CTkBaseClass,
        i18n_key: str,
        default: str,
        *,
        attr: str = "text",
        formatter: Callable[[str], str] | None = None,
    ) -> None:
        """Register any widget for in-place locale updates.

        Use this for widgets not created via :meth:`add_row` or for
        custom formatting (e.g. composite button text).
        """
        self._translatables.append(_TranslatableEntry(
            widget=widget,
            i18n_key=i18n_key,
            default=default,
            attr=attr,
            formatter=formatter,
        ))

    # ------------------------------------------------------------------
    # Incremental locale update
    # ------------------------------------------------------------------

    def apply_locale(self) -> None:
        """Update all registered translatable widgets in-place.

        Called by :meth:`SettingsView.apply_locale` when the UI language
        changes.  Unlike ``reload()``, this preserves widget state
        (entry text, dropdown selections, switch states, scroll position).
        """
        # Update section title
        if self._title_i18n_key:
            self._title_label.configure(text=t(self._title_i18n_key, default=self._title_default))
        # Update all registered translatables
        for entry in self._translatables:
            try:
                text = t(entry.i18n_key, default=entry.default)
                if entry.formatter:
                    text = entry.formatter(text)
                entry.widget.configure(**{entry.attr: text})
            except Exception as exc:
                logger.error("[locale] failed to update '%s': %s", entry.i18n_key, exc)

    # ------------------------------------------------------------------
    # Debug labels
    # ------------------------------------------------------------------

    def _add_debug_label(self, widget: ctk.CTkFrame, widget_id: str) -> None:
        """Add a small debug label showing the widget identifier.

        Labels start hidden and are toggled via the app's [D] button.
        Clicking a label copies its text to the clipboard with a flash.
        """
        controller = getattr(self, "_controller", None)
        if controller is None:
            return
        app = getattr(controller, "app", None)
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
