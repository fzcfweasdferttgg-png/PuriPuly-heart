"""Main application window for the Tkinter/CTk GUI.

``TkApp`` is the root ``CTk`` window.  It owns the settings sections
directly (no sidebar navigation), a status bar, popup lifecycle management
for ``PopupControlView`` and ``PopupViewWindow``, and the notification
toast system.  Business logic is delegated entirely to ``GuiController``.

Architecture: 3-window (main settings + 2 popup windows).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

import customtkinter as ctk
import tkinter as tk

from puripuly_heart.domain.i18n import (
    available_locales,
    get_locale,
    native_locale_label,
    set_gui,
    set_locale,
    t,
)
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.views.popup_control import PopupControlView
from puripuly_heart.ui_tkinter.views.popup_view import PopupViewWindow
from puripuly_heart.ui_tkinter.views.settings import SettingsView
from puripuly_heart.ui_tkinter.views.about import _load_third_party_notices, _system_info_lines

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Section navigation mapping (key, i18n_key, fallback_label)
# ---------------------------------------------------------------------------

_NAV_SECTIONS: list[tuple[str, str, str]] = [
    ("stt", "tk.nav.section.stt", "STT"),
    ("audio", "tk.nav.section.audio", "Audio"),
    ("llm", "tk.nav.section.llm", "LLM"),
    ("secrets", "tk.nav.section.secrets", "Keys"),
    ("prompt_context", "tk.nav.section.prompt", "Prompt"),
    ("overlay", "tk.nav.section.overlay", "Overlay"),
    ("osc", "tk.nav.section.osc", "VRChat"),
]


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


class TkApp(ctk.CTk):
    """Root application window for the PuriPuly Heart Tkinter GUI.

    Architecture: main settings window + 2 popup windows (Control, View).
    The main window contains all settings sections in a scrollable frame.
    Popups are created on demand and managed via ``_popup_control`` /
    ``_popup_view`` references.
    """

    def __init__(self, config_path: Path, *, debug_ui_preview: bool = False) -> None:
        super().__init__()

        # --- File logging --------------------------------------------------
        # Removed redundant FileHandler — controller already writes to
        # puripuly_heart.log via RotatingFileHandler (see runtime_logging).

        # --- Debug mode ----------------------------------------------------
        self.debug_ui_preview = debug_ui_preview
        self._debug_labels: list[ctk.CTkLabel] = []
        self._debug_visible: bool = False

        # --- Window configuration ------------------------------------------
        self.title(t("tk.app.title", default="PuriPuly Heart"))
        self.geometry(f"{th.WINDOW_DEFAULT_WIDTH}x{th.WINDOW_DEFAULT_HEIGHT}")
        self.minsize(th.WINDOW_MIN_WIDTH, th.WINDOW_MIN_HEIGHT)
        self.resizable(False, False)  # Fixed size — no manual resize
        self.configure(fg_color=th.COLOR_BACKGROUND)

        # --- GuiController ------------------------------------------------
        from puripuly_heart.app.services.gui_controller import TkinterGuiController
        from puripuly_heart.ui_tkinter.views.popup_view import TkinterLogHandler

        self.controller: TkinterGuiController = TkinterGuiController(
            page=None,
            app=self,
            config_path=config_path,
            log_handler_factory=TkinterLogHandler,
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
        set_gui("tk")
        set_locale(locale)

        # --- Popup references (created on demand) --------------------------
        self._popup_control: PopupControlView | None = None
        self._popup_view: PopupViewWindow | None = None

        # --- Controller compatibility attributes ---------------------------
        # GuiController accesses these via getattr on the app instance.
        self.view_dashboard: PopupControlView | None = None  # Set when popup opens
        self.view_settings: SettingsView  # Set during _build_ui
        self.view_logs: PopupViewWindow | None = None  # Set when popup opens

        # --- Build ---------------------------------------------------------
        self.withdraw()  # Hide window during build to prevent flash
        self._build_ui()
        if self.debug_ui_preview:
            self._build_debug_toggle()
        self._start_controller()
        self.update_idletasks()  # Force layout completion before showing
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(500, self.deiconify)  # Show window after build completes

        # --- Popup sync on window restore ----------------------------------
        self._was_iconic: bool = False
        self.bind("<Unmap>", self._on_main_window_unmap)
        self.bind("<Map>", self._on_main_window_map)

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble menu bar + scrollable settings + action buttons."""
        self._build_menu_bar()
        self._build_scrollable_settings()
        self._build_action_buttons()

    def _build_menu_bar(self) -> None:
        """Create the menu bar: Language (native), About."""
        self._menubar = tk.Menu(self)
        self.configure(menu=self._menubar)

        # --- Language menu (index 0) ---
        self._lang_menu = tk.Menu(self._menubar, tearoff=0)
        current = get_locale()
        for code in available_locales():
            native = native_locale_label(code)
            self._lang_menu.add_command(
                label=native,
                command=lambda c=code: self._change_locale(c),
            )
        current_native = native_locale_label(current)
        self._menubar.add_cascade(
            label=f"Language ({current_native})",
            menu=self._lang_menu,
        )

        # --- Theme toggle (index 1) — direct item ---
        self._menubar.add_command(
            label=self._theme_label(),
            command=self._toggle_theme,
        )

        # --- About command (index 2) — direct item ---
        self._menubar.add_command(
            label=t("tk.about.menu", default="About"),
            command=self._show_about_dialog,
        )

    def _build_scrollable_settings(self) -> None:
        """Place the SettingsView (scrollable sections) in the main window."""
        self._settings_container = ctk.CTkFrame(
            self,
            fg_color=th.COLOR_BACKGROUND,
            corner_radius=0,
        )
        self._settings_container.pack(fill="both", expand=True)

        # Left-side navigation panel
        self._nav_panel = ctk.CTkFrame(
            self._settings_container,
            fg_color=th.COLOR_SURFACE,
            corner_radius=0,
            width=110,
        )
        self._nav_panel.pack(side="left", fill="y")
        self._nav_panel.pack_propagate(False)

        settings_view = SettingsView(
            self._settings_container,
            controller=self.controller,
        )
        settings_view.pack(side="left", fill="both", expand=True)
        self.view_settings = settings_view
        self._add_debug_label(settings_view, "main.scroll_frame")

        self._build_nav_panel()

        # Show the first section by default
        if _NAV_SECTIONS:
            first_key = _NAV_SECTIONS[0][0]
            self._select_section(first_key)

    def _build_nav_panel(self) -> None:
        """Build the left-side section navigation buttons."""
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._active_nav_key: str | None = None

        for key, i18n_key, fallback in _NAV_SECTIONS:
            btn = ctk.CTkButton(
                self._nav_panel,
                text=t(i18n_key, default=fallback),
                fg_color="transparent",
                hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_TEXT,
                font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
                anchor="w",
                height=32,
                command=lambda k=key: self._select_section(k),
            )
            btn.pack(fill="x", padx=4, pady=1)
            self._nav_buttons[key] = btn

    def _select_section(self, key: str) -> None:
        """Switch the center area to show only the section identified by *key*."""
        # Update nav button highlighting
        for btn_key, btn in self._nav_buttons.items():
            if btn_key == key:
                btn.configure(fg_color=th.COLOR_PRIMARY_CONTAINER)
            else:
                btn.configure(fg_color="transparent")
        self._active_nav_key = key

        # Show only the selected section in the settings view
        if hasattr(self.view_settings, "show_section"):
            self.view_settings.show_section(key)

    def _build_action_buttons(self) -> None:
        """Bottom action bar with Control and View popup buttons."""
        self._action_bar = ctk.CTkFrame(
            self,
            fg_color=th.COLOR_SURFACE,
            corner_radius=0,
            height=48,
        )
        self._action_bar.pack(side="bottom", fill="x", before=self._settings_container)
        self._action_bar.pack_propagate(False)

        # Control button
        self._btn_control = ctk.CTkButton(
            self._action_bar,
            text=t("tk.btn.open_control", default="Control"),
            width=140,
            height=36,
            corner_radius=th.NAV_BUTTON_CORNER_RADIUS,
            fg_color=th.COLOR_PRIMARY,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color="#FFFFFF",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._open_control_popup,
        )
        self._btn_control.pack(side="left", padx=(16, 8), pady=6)
        self._add_debug_label(self._btn_control, "main.btn.open_control")

        # View button
        self._btn_view = ctk.CTkButton(
            self._action_bar,
            text=t("tk.btn.open_view", default="View"),
            width=140,
            height=36,
            corner_radius=th.NAV_BUTTON_CORNER_RADIUS,
            fg_color=th.COLOR_PRIMARY,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color="#FFFFFF",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._open_view_popup,
        )
        self._btn_view.pack(side="left", padx=(0, 16), pady=6)
        self._add_debug_label(self._btn_view, "main.btn.open_view")

    # -----------------------------------------------------------------------
    # Popup lifecycle
    # -----------------------------------------------------------------------

    def _open_control_popup(self) -> None:
        """Open or raise the Control popup window."""
        if self._popup_control is None or not self._popup_control.winfo_exists():
            self._popup_control = PopupControlView(
                self, controller=self.controller
            )
            self.view_dashboard = self._popup_control  # Controller compatibility
        else:
            self._popup_control.deiconify()
            self._popup_control.lift()

    def _open_view_popup(self) -> None:
        """Open or raise the View popup window."""
        if self._popup_view is None or not self._popup_view.winfo_exists():
            self._popup_view = PopupViewWindow(
                self, controller=self.controller
            )
            self.view_logs = self._popup_view  # Controller compatibility
            # Retroactively attach the realtime log sink if runtime_logging
            # was already initialized before the popup existed.
            if hasattr(self.controller, "attach_view_logs_if_ready"):
                self.controller.attach_view_logs_if_ready()
        else:
            self._popup_view.deiconify()
            self._popup_view.lift()

    def _on_main_window_unmap(self, _event: tk.Event | None = None) -> None:
        """Track when the main window is minimized (iconified)."""
        try:
            if self.state() == "iconic":
                self._was_iconic = True
        except Exception:
            pass

    def _on_main_window_map(self, _event: tk.Event | None = None) -> None:
        """Restore visible popups when main window is restored from minimized.

        Only acts when the window was previously iconified to avoid
        accidentally raising popups during section switches or other
        internal pack/unpack cycles.
        """
        if not self._was_iconic:
            return
        self._was_iconic = False

        if self._popup_control and self._popup_control.winfo_exists():
            self._popup_control.deiconify()
        if self._popup_view and self._popup_view.winfo_exists():
            self._popup_view.deiconify()

    # -----------------------------------------------------------------------
    # Locale helpers
    # -----------------------------------------------------------------------

    def _change_locale(self, locale_code: str) -> None:
        """Apply a new locale and refresh all UI labels.

        Locale change is a UI-only operation — we do NOT go through the
        async ``apply_settings_with_sync`` pipeline because it can race
        and overwrite the locale with stale state.
        """
        logger.info("[locale] switching to '%s'", locale_code)
        self.controller.settings.ui.locale = locale_code
        set_locale(locale_code)
        # Save to disk without triggering the full async apply pipeline
        try:
            from puripuly_heart.adapters.storage.settings_persistence import save_settings
            save_settings(self.controller.config_path, self.controller.settings)
        except Exception as exc:
            logger.warning("[locale] save failed: %s", exc)
        self.apply_locale()

    def _update_lang_menu_label(self) -> None:
        """Update the Language cascade label to show the current native name."""
        current_native = native_locale_label(get_locale())
        # Index 0 is the hidden tearoff entry, so cascade is at index 1
        self._menubar.entryconfigure(1, label=f"Language ({current_native})")

    # -----------------------------------------------------------------------
    # Theme toggle
    # -----------------------------------------------------------------------

    def _theme_label(self) -> str:
        """Return menu label for theme toggle."""
        return "Theme (Dark)" if th.is_dark() else "Theme (Light)"

    def _toggle_theme(self) -> None:
        """Switch between light and dark theme."""
        th.toggle_theme()
        th.refresh_colors()
        self._apply_theme()

    def _apply_theme(self) -> None:
        """Apply current theme colors to all widgets."""
        # Update menu label
        self._menubar.entryconfigure(2, label=self._theme_label())

        # Update main window
        self.configure(fg_color=th.COLOR_BACKGROUND)

        # Update settings container and nav panel
        if hasattr(self, "_settings_container"):
            self._settings_container.configure(fg_color=th.COLOR_BACKGROUND)
        if hasattr(self, "_nav_panel"):
            self._nav_panel.configure(fg_color=th.COLOR_SURFACE)

        # Update nav buttons
        if hasattr(self, "_nav_buttons"):
            for key, btn in self._nav_buttons.items():
                if key == self._active_nav_key:
                    btn.configure(fg_color=th.COLOR_PRIMARY_CONTAINER, text_color=th.COLOR_TEXT)
                else:
                    btn.configure(fg_color="transparent", text_color=th.COLOR_TEXT)

        # Update action bar
        if hasattr(self, "_action_bar"):
            self._action_bar.configure(fg_color=th.COLOR_SURFACE)
        if hasattr(self, "_btn_control"):
            self._btn_control.configure(fg_color=th.COLOR_PRIMARY, hover_color=th.COLOR_PRIMARY_CONTAINER)
        if hasattr(self, "_btn_view"):
            self._btn_view.configure(fg_color=th.COLOR_PRIMARY, hover_color=th.COLOR_PRIMARY_CONTAINER)

        # Update settings view
        if hasattr(self, "view_settings"):
            self.view_settings.configure(fg_color=th.COLOR_BACKGROUND)
            # Rebuild sections with new colors
            self.view_settings.reload()

    # -----------------------------------------------------------------------
    # About dialog
    # -----------------------------------------------------------------------

    def _show_about_dialog(self) -> None:
        """Open a popup About dialog with full version, links, system info, and licenses."""
        import webbrowser
        from puripuly_heart import __version__

        dlg = ctk.CTkToplevel(self)
        dlg.title("About PuriPuly Heart")
        dlg.geometry("560x680")
        dlg.resizable(True, True)
        dlg.minsize(480, 500)
        dlg.transient(self)
        dlg.grab_set()
        dlg.configure(fg_color=th.COLOR_BACKGROUND)

        # Scrollable content area
        scroll = ctk.CTkScrollableFrame(
            dlg,
            fg_color=th.COLOR_BACKGROUND,
            scrollbar_button_color=th.COLOR_DIVIDER,
        )
        scroll.pack(fill="both", expand=True, padx=16, pady=12)

        # --- Header card ---
        header_card = ctk.CTkFrame(
            scroll,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        header_card.pack(fill="x", pady=(0, 12))

        header_inner = ctk.CTkFrame(header_card, fg_color="transparent")
        header_inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        ctk.CTkLabel(
            header_inner,
            text=t("tk.app.title", default="PuriPuly Heart"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_TITLE + 4, "bold"),
            text_color=th.COLOR_PRIMARY,
        ).pack(anchor="w")

        ctk.CTkLabel(
            header_inner,
            text=f"{t('tk.about.version', default='Version')} {__version__}",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkLabel(
            header_inner,
            text=t(
                "tk.about.description",
                default="LLM-powered real-time translator for VRChat",
            ),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT,
            wraplength=500,
        ).pack(anchor="w", pady=(8, 0))

        ctk.CTkLabel(
            header_inner,
            text="License: AGPL-3.0-or-later  |  \u00a9 2026 TriOmegaOptimum",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(anchor="w", pady=(8, 0))

        # --- Fork notice ---
        fork_card = ctk.CTkFrame(
            scroll,
            fg_color=th.COLOR_PRIMARY_CONTAINER,
            corner_radius=th.CARD_CORNER_RADIUS,
            border_width=1,
            border_color=th.COLOR_PRIMARY,
        )
        fork_card.pack(fill="x", pady=(0, 12))

        fork_inner = ctk.CTkFrame(fork_card, fg_color="transparent")
        fork_inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        ctk.CTkLabel(
            fork_inner,
            text=t(
                "tk.about.fork_notice",
                default=(
                    "This is an unofficial fork. "
                    "The original author has no relation to this version."
                ),
            ),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            text_color=th.COLOR_ON_PRIMARY_CONTAINER,
            wraplength=500,
            justify="left",
        ).pack(anchor="w")

        # --- Links card ---
        links_card = ctk.CTkFrame(
            scroll,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        links_card.pack(fill="x", pady=(0, 12))

        links_inner = ctk.CTkFrame(links_card, fg_color="transparent")
        links_inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        def _make_link(parent: ctk.CTkFrame, text: str, url: str) -> None:
            """Create a styled link button."""
            btn = ctk.CTkButton(
                parent,
                text=text,
                fg_color="transparent",
                hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_PRIMARY,
                anchor="w",
                font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "normal"),
                command=lambda u=url: webbrowser.open(u),
            )
            btn.pack(fill="x", pady=2)

        ctk.CTkLabel(
            links_inner,
            text=t("tk.about.developed_by", default="Developed by"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 4))

        _make_link(
            links_inner,
            "salee \u2014 github.com/kapitalismho/PuriPuly-heart",
            "https://github.com/kapitalismho/PuriPuly-heart",
        )

        ctk.CTkLabel(
            links_inner,
            text=t("tk.about.inspired_by", default="Inspired by"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(12, 4))

        for label, url in [
            ("VRCT \u2014 github.com/misyaguziya/VRCT", "https://github.com/misyaguziya/VRCT"),
            ("mimiuchi \u2014 github.com/naeruru/mimiuchi", "https://github.com/naeruru/mimiuchi"),
            ("Yakutan \u2014 github.com/febilly/Yakutan", "https://github.com/febilly/Yakutan"),
        ]:
            _make_link(links_inner, label, url)

        ctk.CTkLabel(
            links_inner,
            text=t("tk.about.fork", default="Fork:"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(12, 4))

        _make_link(
            links_inner,
            "github.com/fzcfweasdferttgg-png/PuriPuly-heart",
            "https://github.com/fzcfweasdferttgg-png/PuriPuly-heart",
        )

        # --- System info card ---
        sys_card = ctk.CTkFrame(
            scroll,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        sys_card.pack(fill="x", pady=(0, 12))

        sys_inner = ctk.CTkFrame(sys_card, fg_color="transparent")
        sys_inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        ctk.CTkLabel(
            sys_inner,
            text=t("tk.about.system_info", default="System Info"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 8))

        for label, value in _system_info_lines():
            row = ctk.CTkFrame(sys_inner, fg_color="transparent")
            row.pack(fill="x", pady=2)

            ctk.CTkLabel(
                row,
                text=f"{label}:",
                font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
                text_color=th.COLOR_TEXT_SECONDARY,
                width=80,
                anchor="w",
            ).pack(side="left")

            ctk.CTkLabel(
                row,
                text=value,
                font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
                text_color=th.COLOR_TEXT,
                anchor="w",
            ).pack(side="left", padx=(4, 0))

        # --- Licenses card ---
        lic_card = ctk.CTkFrame(
            scroll,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        lic_card.pack(fill="x", pady=(0, 12))

        lic_inner = ctk.CTkFrame(lic_card, fg_color="transparent")
        lic_inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        ctk.CTkLabel(
            lic_inner,
            text=t("tk.about.licenses", default="Licenses"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 6))

        notices_text = _load_third_party_notices()
        textbox = ctk.CTkTextbox(
            lic_inner,
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
        textbox.pack(fill="x")
        textbox.configure(state="normal")
        textbox.insert("end", notices_text)
        textbox.configure(state="disabled")

        # --- OK button ---
        ctk.CTkButton(
            dlg,
            text=t("tk.about.ok", default="OK"),
            width=100,
            fg_color=th.COLOR_PRIMARY,
            hover_color=th.COLOR_ERROR,
            command=dlg.destroy,
        ).pack(pady=(0, 12))

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
        # Update menu bar language label
        try:
            self._update_lang_menu_label()
        except Exception as exc:
            logger.error("[locale] _update_lang_menu_label failed: %s", exc)

        # Update action button labels
        try:
            self._btn_control.configure(text=t("tk.btn.open_control", default="Control"))
            self._btn_view.configure(text=t("tk.btn.open_view", default="View"))
        except Exception as exc:
            logger.error("[locale] action buttons failed: %s", exc)

        # Update nav panel labels
        try:
            if hasattr(self, "_nav_buttons"):
                for key, i18n_key, fallback in _NAV_SECTIONS:
                    btn = self._nav_buttons.get(key)
                    if btn is not None:
                        btn.configure(text=t(i18n_key, default=fallback))
        except Exception as exc:
            logger.error("[locale] nav buttons failed: %s", exc)

        # Propagate locale to settings view (in-place label updates)
        if hasattr(self.view_settings, "apply_locale"):
            try:
                self.view_settings.apply_locale()
            except Exception as exc:
                logger.error("[locale] view_settings.apply_locale failed: %s", exc, exc_info=True)

        # Re-highlight the active nav button after section rebuild
        if hasattr(self, "_active_nav_key") and self._active_nav_key:
            self._select_section(self._active_nav_key)

        # Propagate locale to popups
        for popup_attr in ("_popup_control", "_popup_view"):
            popup = getattr(self, popup_attr, None)
            if popup is not None and popup.winfo_exists() and hasattr(popup, "apply_locale"):
                try:
                    popup.apply_locale()
                except Exception as exc:
                    logger.error("[locale] %s.apply_locale failed: %s", popup_attr, exc)

    # -----------------------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------------------

    def _on_close(self) -> None:
        """Gracefully close popups, stop controller, then destroy the window."""
        # Close popups first
        for popup in (self._popup_control, self._popup_view):
            if popup and popup.winfo_exists():
                popup.destroy()

        # Destroy settings view before the main window to prevent
        # _update_dimensions_event errors on already-destroyed canvases
        if hasattr(self, "view_settings") and self.view_settings:
            self.view_settings.destroy()

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
        """Floating [D] button that toggles all debug labels on/off."""
        self._debug_toggle_btn = tk.Button(
            self,
            text="D",
            font=("Consolas", 10, "bold"),
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
        alive: list[ctk.CTkLabel] = []
        for label in self._debug_labels:
            try:
                if not label.winfo_exists():
                    continue
                if self._debug_visible:
                    label.place(x=4, y=4)
                else:
                    label.place_forget()
                alive.append(label)
            except Exception:
                pass
        self._debug_labels = alive

    def _add_debug_label(self, widget: ctk.CTkFrame, widget_id: str) -> None:
        """Add a small debug label showing the widget identifier.

        Labels start hidden and are toggled via the [D] button.
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
        label.bind("<Button-1>", lambda e, t=widget_id: self._copy_debug_label(t))
        # Start hidden — don't place until toggled via [D] button
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
