"""Main application window for the Tkinter/CTk GUI.

``TkApp`` is the root ``CTk`` window.  It owns the sidebar navigation,
content area, status bar, and notification toast system.  Business logic
is delegated entirely to ``GuiController``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

import customtkinter as ctk
import tkinter as tk

from puripuly_heart.domain.i18n import set_locale, t
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.views.dashboard import DashboardView
from puripuly_heart.ui_tkinter.views.settings import SettingsView
from puripuly_heart.ui_tkinter.views.logs import LogsView
from puripuly_heart.ui_tkinter.views.about import AboutView

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Toast notification
# ---------------------------------------------------------------------------


class _ToastWindow(ctk.CTkToplevel):
    """Small auto-dismissing notification popup."""

    def __init__(self, master: Any, message: str, level: str = "warning") -> None:
        super().__init__(master)

        color_map: dict[str, str] = {
            "warning": th.COLOR_TOAST_WARNING,
            "error": th.COLOR_TOAST_ERROR,
            "info": th.COLOR_TOAST_INFO,
        }
        bg = color_map.get(level, th.COLOR_TOAST_WARNING)

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=bg)

        self.geometry(f"{th.TOAST_WIDTH}x{th.TOAST_HEIGHT}+0+0")
        self._position(master)

        self._label = ctk.CTkLabel(
            self,
            text=message,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "normal"),
            text_color="#FFFFFF",
            wraplength=th.TOAST_WIDTH - 24,
        )
        self._label.pack(expand=True, padx=12, pady=4)

        self.after(th.TOAST_DURATION_MS, self._dismiss)

    def _position(self, master: Any) -> None:
        """Place toast in the bottom-right corner of the parent window."""
        try:
            mx = master.winfo_rootx()
            my = master.winfo_rooty()
            mw = master.winfo_width()
            mh = master.winfo_height()
            x = mx + mw - th.TOAST_WIDTH - 16
            y = my + mh - th.TOAST_HEIGHT - 48
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _dismiss(self) -> None:
        try:
            self.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# TkApp — root window
# ---------------------------------------------------------------------------

# Navigation items: (internal key, i18n label key, fallback label)
_NAV_ITEMS: list[tuple[str, str, str]] = [
    ("dashboard", "nav.dashboard", "Dashboard"),
    ("settings", "nav.settings", "Settings"),
    ("logs", "nav.logs", "Logs"),
    ("about", "nav.about", "About"),
]


class TkApp(ctk.CTk):
    """Root application window for the PuriPuly Heart Tkinter GUI."""

    def __init__(self, config_path: Path, *, debug_ui_preview: bool = False) -> None:
        super().__init__()

        # --- Debug mode ----------------------------------------------------
        self.debug_ui_preview = debug_ui_preview
        self._debug_labels: list[ctk.CTkLabel] = []
        self._debug_visible: bool = False

        # --- Window configuration ------------------------------------------
        self.title("PuriPuly Heart")
        self.geometry(f"{th.WINDOW_DEFAULT_WIDTH}x{th.WINDOW_DEFAULT_HEIGHT}")
        self.minsize(th.WINDOW_MIN_WIDTH, th.WINDOW_MIN_HEIGHT)
        self.configure(fg_color=th.COLOR_BACKGROUND)

        # --- GuiController ------------------------------------------------
        from puripuly_heart.app.services.gui_controller import TkinterGuiController

        self.controller: TkinterGuiController = TkinterGuiController(
            page=None,
            app=self,
            config_path=config_path,
        )

        # Pre-load settings synchronously so views can read them during build.
        # controller.start() will reload them again later (idempotent).
        from puripuly_heart.adapters.storage.settings_persistence import load_settings
        from puripuly_heart.config.settings import new_settings_for_first_run

        if config_path.exists():
            self.controller.settings = load_settings(config_path)
        else:
            self.controller.settings = new_settings_for_first_run()

        # --- i18n ----------------------------------------------------------
        locale: str = (
            self.controller.settings.ui.locale
            if self.controller.settings
            else "en"
        )
        set_locale(locale)

        # --- View references (set by _build_ui) ---------------------------
        self._views: dict[str, ctk.CTkFrame] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._current_view: str = "dashboard"

        # --- Build ---------------------------------------------------------
        self._build_ui()
        if self.debug_ui_preview:
            self._build_debug_toggle()
        self._start_controller()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble sidebar + content area + status bar."""
        # Root container: horizontal split
        self._root_row = ctk.CTkFrame(self, fg_color=th.COLOR_BACKGROUND)
        self._root_row.pack(fill="both", expand=True)

        self._build_sidebar(self._root_row)
        self._build_content(self._root_row)
        self._build_status_bar()

        # Show default view
        self._show_view("dashboard")

    def _build_sidebar(self, parent: ctk.CTkFrame) -> None:
        """Left sidebar with navigation buttons."""
        self._sidebar = ctk.CTkFrame(
            parent,
            width=th.SIDEBAR_WIDTH,
            fg_color=th.COLOR_SIDEBAR,
            corner_radius=0,
        )
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        # App title at top of sidebar
        self._sidebar_title = ctk.CTkLabel(
            self._sidebar,
            text="💖 PuriPuly",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING + 2, "bold"),
            text_color=th.COLOR_PRIMARY,
        )
        self._sidebar_title.pack(
            pady=(20, 16),
            padx=th.SIDEBAR_PAD_X,
            anchor="w",
        )

        # Divider
        ctk.CTkFrame(
            self._sidebar,
            height=1,
            fg_color=th.COLOR_DIVIDER,
        ).pack(fill="x", padx=th.SIDEBAR_PAD_X, pady=(0, 8))

        # Navigation buttons
        for key, i18n_key, fallback in _NAV_ITEMS:
            btn = ctk.CTkButton(
                self._sidebar,
                text=self._nav_label(i18n_key, fallback),
                anchor="w",
                height=th.SIDEBAR_BUTTON_HEIGHT,
                corner_radius=th.SIDEBAR_BUTTON_CORNER,
                fg_color="transparent",
                hover_color=th.COLOR_SIDEBAR_ACTIVE,
                text_color=th.COLOR_NAV_INACTIVE,
                font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "normal"),
                command=lambda k=key: self._show_view(k),
            )
            btn.pack(
                fill="x",
                padx=th.SIDEBAR_PAD_X,
                pady=(th.SIDEBAR_PAD_Y // 2),
            )
            self._nav_buttons[key] = btn
            self._add_debug_label(btn, f"nav.{key}")

    def _build_content(self, parent: ctk.CTkFrame) -> None:
        """Right content area holding view frames."""
        self._content = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_BACKGROUND,
            corner_radius=0,
        )
        self._content.pack(side="left", fill="both", expand=True)

        # Dashboard — real view (exposed as view_dashboard for GuiController)
        dashboard = DashboardView(self._content, controller=self.controller)
        dashboard.place(in_=self._content, relwidth=1.0, relheight=1.0)
        self._views["dashboard"] = dashboard
        self.view_dashboard = dashboard  # GuiController accesses this
        self._add_debug_label(dashboard, "view.dashboard")

        # Settings — real view
        settings_view = SettingsView(self._content, controller=self.controller)
        settings_view.place(in_=self._content, relwidth=1.0, relheight=1.0)
        self._views["settings"] = settings_view
        self.view_settings = settings_view  # GuiController may access this
        self._add_debug_label(settings_view, "view.settings")

        # Logs — real view (exposed as view_logs for GuiController)
        logs_view = LogsView(self._content, controller=self.controller)
        logs_view.place(in_=self._content, relwidth=1.0, relheight=1.0)
        self._views["logs"] = logs_view
        self.view_logs = logs_view  # GuiController accesses this
        self._add_debug_label(logs_view, "view.logs")

        # About — real view
        about_view = AboutView(self._content, controller=self.controller)
        about_view.place(in_=self._content, relwidth=1.0, relheight=1.0)
        self._views["about"] = about_view
        self._add_debug_label(about_view, "view.about")

    def _build_status_bar(self) -> None:
        """Bottom status bar."""
        self._status_bar = ctk.CTkFrame(
            self,
            height=th.STATUS_BAR_HEIGHT,
            fg_color=th.COLOR_SURFACE,
            corner_radius=0,
        )
        self._status_bar.pack(side="bottom", fill="x")
        self._status_bar.pack_propagate(False)

        self._status_label = ctk.CTkLabel(
            self._status_bar,
            text=t("status.ready", default="Ready"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL, "normal"),
            text_color=th.COLOR_TEXT_SECONDARY,
        )
        self._status_label.pack(side="left", padx=12, pady=4)

        self._add_debug_label(self._status_bar, "status_bar")

    # -----------------------------------------------------------------------
    # Navigation
    # -----------------------------------------------------------------------

    def _show_view(self, key: str) -> None:
        """Raise *key*'s frame to the front and highlight its nav button."""
        view = self._views.get(key)
        if view is None:
            return

        # Hide all, show target
        for v in self._views.values():
            v.lower()
        view.lift()

        # Update button styles
        for btn_key, btn in self._nav_buttons.items():
            if btn_key == key:
                btn.configure(
                    fg_color=th.COLOR_SIDEBAR_ACTIVE,
                    text_color=th.COLOR_NAV_ACTIVE,
                    font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=th.COLOR_NAV_INACTIVE,
                    font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "normal"),
                )

        self._current_view = key

    # -----------------------------------------------------------------------
    # GuiController integration
    # -----------------------------------------------------------------------

    def _start_controller(self) -> None:
        """Start GuiController in a background thread (CTk is synchronous)."""
        from puripuly_heart.ui_tkinter.app_controller import start_controller_async

        thread = threading.Thread(
            target=start_controller_async,
            args=(self.controller,),
            daemon=True,
        )
        thread.start()

    # -----------------------------------------------------------------------
    # Notification toast — called by GuiController via getattr
    # -----------------------------------------------------------------------

    def _show_notification(self, message: str, level: str = "warning") -> None:
        """Display a transient toast. Safe to call from any thread."""
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda: self._show_notification(message, level))
            return

        _ToastWindow(self, message, level=level)

    # -----------------------------------------------------------------------
    # Locale — called by GuiController via getattr
    # -----------------------------------------------------------------------

    def apply_locale(self) -> None:
        """Re-apply i18n labels after a locale change."""
        for key, i18n_key, fallback in _NAV_ITEMS:
            btn = self._nav_buttons.get(key)
            if btn is not None:
                btn.configure(text=self._nav_label(i18n_key, fallback))

        self._status_label.configure(
            text=t("status.ready", default="Ready"),
        )

        # Propagate locale to views that support it
        for view in self._views.values():
            apply_fn = getattr(view, "apply_locale", None)
            if callable(apply_fn):
                apply_fn()

    # -----------------------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------------------

    def _on_close(self) -> None:
        """Gracefully stop controller, then destroy the window."""
        from puripuly_heart.ui_tkinter.app_controller import stop_controller_async

        try:
            stop_controller_async(self.controller)
        except Exception:
            logger.exception("Error stopping controller during window close")
        finally:
            self.destroy()

    # -----------------------------------------------------------------------
    # Debug helpers
    # -----------------------------------------------------------------------

    def _build_debug_toggle(self) -> None:
        """Floating 🔍 button that toggles all debug labels on/off."""
        self._debug_toggle_btn = tk.Button(
            self,
            text="🔍",
            font=("Segoe UI Emoji", 10),
            relief="flat",
            bd=0,
            bg="#333333",
            fg="#FFFFFF",
            activebackground="#555555",
            activeforeground="#FFFFFF",
            cursor="hand2",
            command=self._toggle_debug_labels,
        )
        self._debug_toggle_btn.place(relx=1.0, rely=1.0, anchor="se", x=-8, y=-8)

    def _toggle_debug_labels(self) -> None:
        """Flip visibility of all registered debug labels."""
        self._debug_visible = not self._debug_visible
        for label in self._debug_labels:
            try:
                if self._debug_visible:
                    label.lift()
                else:
                    label.lower()
            except Exception:
                pass

    def _add_debug_label(self, widget: ctk.CTkFrame, widget_id: str) -> None:
        """Add a small debug label showing the widget identifier.

        Labels start hidden and are toggled via the 🔍 button.
        Clicking a label copies its text to the clipboard with a flash.
        """
        if not self.debug_ui_preview:
            return
        label = ctk.CTkLabel(
            widget,
            text=f"[{widget_id}]",
            font=("Consolas", 9),
            text_color="#00FF88",
            fg_color="transparent",
        )
        label.place(x=4, y=4)
        label.bind("<Button-1>", lambda e, t=widget_id: self._copy_debug_label(t))
        # Start hidden — lower below parent so it's invisible
        label.lower()
        self._debug_labels.append(label)

    def _copy_debug_label(self, text: str) -> None:
        """Copy *text* to the clipboard and flash the label for feedback."""
        self.clipboard_clear()
        self.clipboard_append(text)
        # Find the label whose text matches and flash it
        for lbl in self._debug_labels:
            try:
                if lbl.cget("text") == f"[{text}]":
                    lbl.configure(text_color="#000000")
                    self.after(150, lambda l=lbl: l.configure(text_color="#00FF88"))
                    break
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _nav_label(i18n_key: str, fallback: str) -> str:
        """Translate a nav label; return *fallback* when the key is missing."""
        translated = t(i18n_key, default=fallback)
        return translated if translated != i18n_key else fallback
