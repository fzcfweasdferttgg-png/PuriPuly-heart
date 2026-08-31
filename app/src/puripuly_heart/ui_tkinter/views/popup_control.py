"""Control popup window for the Tkinter/CTk GUI.

``PopupControlView`` is a ``CTkToplevel`` that provides language selectors,
feature toggle buttons, and swap controls.  It replaces the inline
``DashboardView`` as the primary control surface when the main window is
minimised or the user prefers a floating control panel.

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
from typing import Any, Callable

import customtkinter as ctk
import tkinter as tk

from puripuly_heart.domain.i18n import t
from puripuly_heart.domain.language import get_all_language_options
from puripuly_heart.ui_tkinter import theme as th

logger = logging.getLogger(__name__)

# Pre-load language options once at module level (same as DashboardView).
_LANG_OPTIONS: tuple[tuple[str, str], ...] = tuple(get_all_language_options())
_LANG_CODE_TO_NAME: dict[str, str] = {code: name for code, name in _LANG_OPTIONS}
_LANG_NAMES: list[str] = [name for _, name in _LANG_OPTIONS]
_LANG_NAME_TO_CODE: dict[str, str] = {name: code for code, name in _LANG_OPTIONS}


class PopupControlView(ctk.CTkToplevel):
    """Floating control popup: language selectors, toggles, swap buttons.

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
        self.title(t("tk.popup.control.title", default="Control"))
        self.configure(fg_color=th.COLOR_BACKGROUND)
        self.transient(master)
        self.resizable(False, False)

        # Intercept close → withdraw, never destroy.
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)

        # --- Local toggle state mirrors ------------------------------------
        self._stt_on: bool = False
        self._trans_on: bool = False
        self._overlay_on: bool = False
        self._peer_on: bool = False

        # --- Settable attributes (written directly by controller) ----------
        self.on_recent_languages_change: Callable[..., Any] | None = None
        self.stt_needs_key: bool = False
        self.translation_needs_key: bool = False

        # --- Widget references for locale updates --------------------------
        self._title_label: ctk.CTkLabel | None = None
        self._self_voice_label: ctk.CTkLabel | None = None
        self._peer_voice_label: ctk.CTkLabel | None = None
        self._self_src_label: ctk.CTkLabel | None = None
        self._self_tgt_label: ctk.CTkLabel | None = None
        self._peer_src_label: ctk.CTkLabel | None = None
        self._peer_tgt_label: ctk.CTkLabel | None = None
        self._stt_btn: ctk.CTkButton | None = None
        self._trans_btn: ctk.CTkButton | None = None
        self._overlay_btn: ctk.CTkButton | None = None
        self._peer_btn: ctk.CTkButton | None = None
        self._stt_key_indicator: ctk.CTkLabel | None = None
        self._trans_key_indicator: ctk.CTkLabel | None = None
        self._local_stt_label: ctk.CTkLabel | None = None

        # --- Build UI ------------------------------------------------------
        self._build_ui()
        self._load_state()

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
        # Fixed width for the popup
        self.geometry("620x385")

        # Menu bar (like settings window)
        self._menubar = tk.Menu(self)
        self.configure(menu=self._menubar)

        # Theme toggle (index 0)
        self._menubar.add_command(
            label=self._theme_label(),
            command=self._toggle_theme,
        )

        # Content frame (no scroll)
        self._scroll = ctk.CTkFrame(
            self,
            fg_color=th.COLOR_BACKGROUND,
        )
        self._scroll.pack(fill="both", expand=True, padx=th.CONTENT_PAD_X)

        self._build_language_section(self._scroll)
        self._build_toggle_section(self._scroll)

    # --- Language selector ------------------------------------------------

    def _build_language_section(self, parent: ctk.CTkScrollableFrame) -> None:
        """Language selector card with self and peer rows."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        # --- Self voice section ---
        self._self_voice_label = ctk.CTkLabel(
            inner,
            text=t("tk.dashboard.language.self", default="My voice"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        )
        self._self_voice_label.pack(anchor="w", pady=(0, 6))

        self._build_self_language_row(inner)

        # --- Peer voice section ---
        self._peer_voice_label = ctk.CTkLabel(
            inner,
            text=t("tk.dashboard.language.peer", default="Their voice"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        )
        self._peer_voice_label.pack(anchor="w", pady=(14, 6))

        self._build_peer_language_row(inner)

    def _build_self_language_row(self, parent: ctk.CTkFrame) -> None:
        """Source → Target dropdowns for the self channel."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        row.columnconfigure(0, weight=0)  # Source label
        row.columnconfigure(1, weight=1)  # Source dropdown
        row.columnconfigure(2, weight=0)  # Arrow
        row.columnconfigure(3, weight=0)  # Target label
        row.columnconfigure(4, weight=1)  # Target dropdown

        # Source label
        self._self_src_label = ctk.CTkLabel(
            row,
            text=t("tk.dashboard.language.source_label", default="Source"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        )
        self._self_src_label.grid(row=0, column=0, padx=(0, 6))

        # Source dropdown
        self._source_dd = ctk.CTkOptionMenu(
            row,
            values=_LANG_NAMES,
            command=self._on_self_lang_change,
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_ON_PRIMARY,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._source_dd.grid(row=0, column=1, padx=(0, 6), sticky="ew")
        self._add_debug_label(self._source_dd, "popup.control.dropdown.src_lang")

        # Arrow
        ctk.CTkLabel(
            row,
            text="\u2192",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).grid(row=0, column=2, padx=6)

        # Target label
        self._self_tgt_label = ctk.CTkLabel(
            row,
            text=t("tk.dashboard.language.target_label", default="Target"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        )
        self._self_tgt_label.grid(row=0, column=3, padx=(6, 6))

        # Target dropdown
        self._target_dd = ctk.CTkOptionMenu(
            row,
            values=_LANG_NAMES,
            command=self._on_self_lang_change,
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_ON_PRIMARY,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._target_dd.grid(row=0, column=4, sticky="ew")
        self._add_debug_label(self._target_dd, "popup.control.dropdown.tgt_lang")

    def _build_peer_language_row(self, parent: ctk.CTkFrame) -> None:
        """Peer Source → Peer Target dropdowns."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        row.columnconfigure(0, weight=0)  # Peer source label
        row.columnconfigure(1, weight=1)  # Peer source dropdown
        row.columnconfigure(2, weight=0)  # Arrow
        row.columnconfigure(3, weight=0)  # Peer target label
        row.columnconfigure(4, weight=1)  # Peer target dropdown

        # Peer source label
        self._peer_src_label = ctk.CTkLabel(
            row,
            text=t("tk.dashboard.language.source_label", default="Source"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        )
        self._peer_src_label.grid(row=0, column=0, padx=(0, 6))

        # Peer source dropdown
        self._peer_source_dd = ctk.CTkOptionMenu(
            row,
            values=_LANG_NAMES,
            command=self._on_peer_lang_change,
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_ON_PRIMARY,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._peer_source_dd.grid(row=0, column=1, padx=(0, 6), sticky="ew")
        self._add_debug_label(self._peer_source_dd, "popup.control.dropdown.peer_src_lang")

        # Arrow
        ctk.CTkLabel(
            row,
            text="\u2192",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).grid(row=0, column=2, padx=6)

        # Peer target label
        self._peer_tgt_label = ctk.CTkLabel(
            row,
            text=t("tk.dashboard.language.target_label", default="Target"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        )
        self._peer_tgt_label.grid(row=0, column=3, padx=(6, 6))

        # Peer target dropdown
        self._peer_target_dd = ctk.CTkOptionMenu(
            row,
            values=_LANG_NAMES,
            command=self._on_peer_lang_change,
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_ON_PRIMARY,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._peer_target_dd.grid(row=0, column=4, sticky="ew")
        self._add_debug_label(self._peer_target_dd, "popup.control.dropdown.peer_tgt_lang")

    # --- Toggle buttons ---------------------------------------------------

    def _build_toggle_section(self, parent: ctk.CTkScrollableFrame) -> None:
        """Grid of feature toggle buttons with key-need indicators."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        # Row 1: STT + Translation
        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 8))

        # STT button container (button + indicator)
        stt_container = ctk.CTkFrame(row1, fg_color="transparent")
        stt_container.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self._stt_btn = ctk.CTkButton(
            stt_container,
            text=t("tk.dashboard.stt_label", default="STT"),
            height=42,
            corner_radius=th.NAV_BUTTON_CORNER_RADIUS,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._toggle_stt,
        )
        self._stt_btn.pack(fill="x")
        self._add_debug_label(self._stt_btn, "popup.control.toggle.stt")

        # STT "needs key" indicator (hidden by default)
        self._stt_key_indicator = ctk.CTkLabel(
            stt_container,
            text=t("tk.popup.control.needs_key", default="\u26a0 Key needed"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_CAPTION),
            text_color=th.COLOR_WARNING,
            fg_color="transparent",
        )
        self._stt_key_indicator.pack(anchor="w", padx=4)
        self._stt_key_indicator.pack_forget()  # hidden initially

        # Translation button container
        trans_container = ctk.CTkFrame(row1, fg_color="transparent")
        trans_container.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self._trans_btn = ctk.CTkButton(
            trans_container,
            text=t("tk.dashboard.trans_label", default="TRANS"),
            height=42,
            corner_radius=th.NAV_BUTTON_CORNER_RADIUS,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._toggle_translation,
        )
        self._trans_btn.pack(fill="x")
        self._add_debug_label(self._trans_btn, "popup.control.toggle.trans")

        # Translation "needs key" indicator (hidden by default)
        self._trans_key_indicator = ctk.CTkLabel(
            trans_container,
            text=t("tk.popup.control.needs_key", default="\u26a0 Key needed"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_CAPTION),
            text_color=th.COLOR_WARNING,
            fg_color="transparent",
        )
        self._trans_key_indicator.pack(anchor="w", padx=4)
        self._trans_key_indicator.pack_forget()  # hidden initially

        # Row 2: Overlay + Peer
        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x")

        self._overlay_btn = ctk.CTkButton(
            row2,
            text=t("tk.dashboard.overlay_label", default="Subtitles"),
            height=42,
            corner_radius=th.NAV_BUTTON_CORNER_RADIUS,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._toggle_overlay,
        )
        self._overlay_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._add_debug_label(self._overlay_btn, "popup.control.toggle.overlay")

        self._peer_btn = ctk.CTkButton(
            row2,
            text=t("tk.dashboard.peer_label", default="PEER"),
            height=42,
            corner_radius=th.NAV_BUTTON_CORNER_RADIUS,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._toggle_peer,
        )
        self._peer_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._add_debug_label(self._peer_btn, "popup.control.toggle.peer")

        # Row 3: Local STT download notice (hidden by default)
        self._local_stt_label = ctk.CTkLabel(
            inner,
            text="",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_SMALL),
            text_color=th.COLOR_TEXT_SECONDARY,
            fg_color="transparent",
        )
        self._local_stt_label.pack(anchor="w", pady=(8, 0))
        self._local_stt_label.pack_forget()  # hidden initially

    # ------------------------------------------------------------------
    # State loading (settings may arrive asynchronously)
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Populate dropdowns and sync buttons once settings are available."""
        settings = self._controller.settings
        if settings is None:
            # Controller still starting — retry shortly.
            self.after(200, self._load_state)
            return

        lang = settings.languages

        src_name = _LANG_CODE_TO_NAME.get(lang.source_language, lang.source_language)
        tgt_name = _LANG_CODE_TO_NAME.get(lang.target_language, lang.target_language)
        self._source_dd.set(src_name)
        self._target_dd.set(tgt_name)

        peer_src = lang.effective_peer_source
        peer_tgt = lang.effective_peer_target
        self._peer_source_dd.set(_LANG_CODE_TO_NAME.get(peer_src, peer_src))
        self._peer_target_dd.set(_LANG_CODE_TO_NAME.get(peer_tgt, peer_tgt))

        self._sync_toggle_buttons()

    def _sync_toggle_buttons(self) -> None:
        """Refresh button appearance from local state mirrors."""
        self._apply_btn_state(self._stt_btn, self._stt_on)
        self._apply_btn_state(self._trans_btn, self._trans_on)
        self._apply_btn_state(self._overlay_btn, self._overlay_on)
        self._apply_btn_state(self._peer_btn, self._peer_on)

    # ------------------------------------------------------------------
    # Language change handlers
    # ------------------------------------------------------------------

    def _on_self_lang_change(self, _value: str) -> None:  # noqa: ARG002
        """Handle self source/target dropdown change."""
        self._apply_language_settings()

    def _on_peer_lang_change(self, _value: str) -> None:  # noqa: ARG002
        """Handle peer source/target dropdown change."""
        self._apply_language_settings()

    def _apply_language_settings(self) -> None:
        """Read dropdown values → update controller settings → sync."""
        settings = self._controller.settings
        if settings is None:
            return

        new_src = self._code_from_dd(self._source_dd)
        new_tgt = self._code_from_dd(self._target_dd)
        new_peer_src = self._code_from_dd(self._peer_source_dd)
        new_peer_tgt = self._code_from_dd(self._peer_target_dd)

        settings.languages.source_language = new_src
        settings.languages.target_language = new_tgt
        settings.languages.peer_source_language = new_peer_src
        settings.languages.peer_target_language = new_peer_tgt

        self._controller.apply_settings_with_sync(settings)

    def _swap_self(self) -> None:
        """Swap self source ↔ target languages."""
        src_cur = self._source_dd.get()
        tgt_cur = self._target_dd.get()
        self._source_dd.set(tgt_cur)
        self._target_dd.set(src_cur)
        self._apply_language_settings()

    def _swap_peer(self) -> None:
        """Swap peer source ↔ target languages."""
        src_cur = self._peer_source_dd.get()
        tgt_cur = self._peer_target_dd.get()
        self._peer_source_dd.set(tgt_cur)
        self._peer_target_dd.set(src_cur)
        self._apply_language_settings()

    # ------------------------------------------------------------------
    # Toggle handlers
    # ------------------------------------------------------------------

    def _toggle_stt(self) -> None:
        """Toggle STT on/off and notify controller."""
        new = not self._stt_on
        self._stt_on = new
        self._apply_btn_state(self._stt_btn, new)
        self._run_async(self._controller.set_stt_enabled(new))

    def _toggle_translation(self) -> None:
        """Toggle translation on/off and notify controller."""
        new = not self._trans_on
        self._trans_on = new
        self._apply_btn_state(self._trans_btn, new)
        self._run_async(self._controller.set_translation_enabled(new))

    def _toggle_overlay(self) -> None:
        """Toggle overlay on/off and notify controller."""
        new = not self._overlay_on
        self._overlay_on = new
        self._apply_btn_state(self._overlay_btn, new)
        self._run_async(self._controller.set_overlay_enabled(new))

    def _toggle_peer(self) -> None:
        """Toggle peer on/off and notify controller."""
        new = not self._peer_on
        self._peer_on = new
        self._apply_btn_state(self._peer_btn, new)
        self._run_async(self._controller.set_peer_translation_enabled(new))

    # ------------------------------------------------------------------
    # Public API — called by GuiController from the async thread
    # ------------------------------------------------------------------

    def set_stt_enabled(self, enabled: bool) -> None:
        """Thread-safe: update STT button visual state."""
        self._stt_on = enabled
        self.after(0, lambda: self._set_stt_enabled_ui(enabled))

    def _set_stt_enabled_ui(self, enabled: bool) -> None:
        if not self.winfo_exists():
            return
        self._apply_btn_state(self._stt_btn, enabled)

    def set_translation_enabled(self, enabled: bool) -> None:
        """Thread-safe: update translation button visual state."""
        self._trans_on = enabled
        self.after(0, lambda: self._set_translation_enabled_ui(enabled))

    def _set_translation_enabled_ui(self, enabled: bool) -> None:
        if not self.winfo_exists():
            return
        self._apply_btn_state(self._trans_btn, enabled)

    def set_overlay_enabled(self, enabled: bool) -> None:
        """Thread-safe: update overlay button visual state."""
        self._overlay_on = enabled
        self.after(0, lambda: self._set_overlay_enabled_ui(enabled))

    def _set_overlay_enabled_ui(self, enabled: bool) -> None:
        if not self.winfo_exists():
            return
        self._apply_btn_state(self._overlay_btn, enabled)

    def set_peer_enabled(self, enabled: bool) -> None:
        """Thread-safe: update peer button visual state."""
        self._peer_on = enabled
        self.after(0, lambda: self._set_peer_enabled_ui(enabled))

    def _set_peer_enabled_ui(self, enabled: bool) -> None:
        if not self.winfo_exists():
            return
        self._apply_btn_state(self._peer_btn, enabled)

    def set_stt_needs_key(self, needs_key: bool) -> None:
        """Thread-safe: show/hide 'needs API key' indicator on STT button."""
        self.after(0, lambda: self._set_stt_needs_key_ui(needs_key))

    def _set_stt_needs_key_ui(self, needs_key: bool) -> None:
        if not self.winfo_exists():
            return
        if self._stt_key_indicator is None:
            return
        if needs_key:
            self._stt_key_indicator.pack(anchor="w", padx=4)
        else:
            self._stt_key_indicator.pack_forget()

    def set_translation_needs_key(self, needs_key: bool, update_ui: bool = True) -> None:
        """Thread-safe: show/hide 'needs API key' indicator on translation."""
        if not update_ui:
            return
        self.after(0, lambda: self._set_translation_needs_key_ui(needs_key))

    def _set_translation_needs_key_ui(self, needs_key: bool) -> None:
        if not self.winfo_exists():
            return
        if self._trans_key_indicator is None:
            return
        if needs_key:
            self._trans_key_indicator.pack(anchor="w", padx=4)
        else:
            self._trans_key_indicator.pack_forget()

    def set_languages_from_codes(
        self,
        self_code: str,
        peer_code: str | None = None,
        self_target_code: str | None = None,
        peer_target_code: str | None = None,
    ) -> None:
        """Thread-safe: set language dropdowns from locale codes."""
        self.after(
            0,
            lambda: self._set_languages_from_codes_ui(
                self_code, peer_code, self_target_code, peer_target_code
            ),
        )

    def _set_languages_from_codes_ui(
        self,
        self_code: str,
        peer_code: str | None,
        self_target_code: str | None,
        peer_target_code: str | None,
    ) -> None:
        if not self.winfo_exists():
            return
        if self_code:
            name = _LANG_CODE_TO_NAME.get(self_code, self_code)
            self._source_dd.set(name)
        if self_target_code:
            name = _LANG_CODE_TO_NAME.get(self_target_code, self_target_code)
            self._target_dd.set(name)
        if peer_code:
            name = _LANG_CODE_TO_NAME.get(peer_code, peer_code)
            self._peer_source_dd.set(name)
        if peer_target_code:
            name = _LANG_CODE_TO_NAME.get(peer_target_code, peer_target_code)
            self._peer_target_dd.set(name)

    def set_recent_languages(self, recent_list: Any) -> None:
        """Thread-safe: update recent languages list.

        The *recent_list* format is determined by the controller.  This
        implementation stores it and notifies the callback if set.
        """
        self.after(0, lambda: self._set_recent_languages_ui(recent_list))

    def _set_recent_languages_ui(self, recent_list: Any) -> None:
        if not self.winfo_exists():
            return
        # Store for potential future use (e.g., dropdown filtering).
        self._recent_languages = recent_list
        if callable(self.on_recent_languages_change):
            try:
                self.on_recent_languages_change(recent_list)
            except Exception:
                logger.exception("on_recent_languages_change callback failed")

    def show_peer_eula_dialog(self, on_accept: Callable[..., Any] | None = None) -> None:
        """Thread-safe: show EULA dialog for peer feature.

        Falls back to auto-accepting if the dialog cannot be shown.
        """
        self.after(0, lambda: self._show_peer_eula_dialog_ui(on_accept))

    def _show_peer_eula_dialog_ui(self, on_accept: Callable[..., Any] | None) -> None:
        if not self.winfo_exists():
            if callable(on_accept):
                on_accept()
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title(t("tk.popup.control.eula_title", default="Peer EULA"))
        dlg.geometry("480x360")
        dlg.transient(self)
        dlg.grab_set()
        dlg.configure(fg_color=th.COLOR_BACKGROUND)

        ctk.CTkLabel(
            dlg,
            text=t("tk.popup.control.eula_heading", default="Peer Translation Agreement"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", padx=16, pady=(16, 8))

        eula_text = t(
            "tk.popup.control.eula_body",
            default=(
                "By enabling peer translation, you agree that voice data "
                "will be shared with the connected peer for translation "
                "purposes.  Please review the full terms before accepting."
            ),
        )
        textbox = ctk.CTkTextbox(
            dlg,
            height=180,
            fg_color=th.COLOR_BACKGROUND,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            corner_radius=8,
            border_width=1,
            border_color=th.COLOR_DIVIDER,
            state="normal",
            wrap="word",
        )
        textbox.pack(fill="x", padx=16, pady=(0, 12))
        textbox.insert("end", eula_text)
        textbox.configure(state="disabled")

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))

        def _accept() -> None:
            dlg.destroy()
            if callable(on_accept):
                on_accept()

        def _decline() -> None:
            dlg.destroy()

        ctk.CTkButton(
            btn_row,
            text=t("tk.popup.control.eula_accept", default="Accept"),
            fg_color=th.COLOR_PRIMARY,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            command=_accept,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text=t("tk.popup.control.eula_decline", default="Decline"),
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            command=_decline,
        ).pack(side="left")

    def set_local_stt_notice(self, status: str | None, percent: float | None = None) -> None:
        """Thread-safe: show local STT download progress."""
        self.after(0, lambda: self._set_local_stt_notice_ui(status, percent))

    def _set_local_stt_notice_ui(self, status: str | None, percent: float | None) -> None:
        if not self.winfo_exists():
            return
        if self._local_stt_label is None:
            return
        if not status:
            self._local_stt_label.pack_forget()
            return
        # Format the notice text
        if percent is not None:
            text = f"{status} ({percent:.0f}%)"
        else:
            text = status
        self._local_stt_label.configure(text=text)
        self._local_stt_label.pack(anchor="w", pady=(8, 0))

    def set_overlay_peer_contract(self, contract: Any) -> None:
        """Thread-safe: propagate overlay/peer contract.

        Stores the contract for potential future use by the popup UI.
        """
        self.after(0, lambda: self._set_overlay_peer_contract_ui(contract))

    def _set_overlay_peer_contract_ui(self, contract: Any) -> None:
        if not self.winfo_exists():
            return
        self._overlay_peer_contract = contract

    def apply_locale(self) -> None:
        """Thread-safe: update all labels when locale changes."""
        self.after(0, self._apply_locale_ui)

    def _apply_locale_ui(self) -> None:
        if not self.winfo_exists():
            return
        # Window title
        self.title(t("tk.popup.control.title", default="Control"))
        # Section labels
        if self._title_label is not None:
            self._title_label.configure(text=t("tk.popup.control.title", default="Control"))
        if self._self_voice_label is not None:
            self._self_voice_label.configure(
                text=t("tk.dashboard.language.self", default="My voice")
            )
        if self._peer_voice_label is not None:
            self._peer_voice_label.configure(
                text=t("tk.dashboard.language.peer", default="Their voice")
            )
        if self._self_src_label is not None:
            self._self_src_label.configure(
                text=t("tk.dashboard.language.source_label", default="Source")
            )
        if self._self_tgt_label is not None:
            self._self_tgt_label.configure(
                text=t("tk.dashboard.language.target_label", default="Target")
            )
        if self._peer_src_label is not None:
            self._peer_src_label.configure(
                text=t("tk.dashboard.language.source_label", default="Source")
            )
        if self._peer_tgt_label is not None:
            self._peer_tgt_label.configure(
                text=t("tk.dashboard.language.target_label", default="Target")
            )
        # Toggle button labels
        if self._stt_btn is not None:
            self._stt_btn.configure(text=t("tk.dashboard.stt_label", default="STT"))
        if self._trans_btn is not None:
            self._trans_btn.configure(text=t("tk.dashboard.trans_label", default="TRANS"))
        if self._overlay_btn is not None:
            self._overlay_btn.configure(
                text=t("tk.dashboard.overlay_label", default="Subtitles")
            )
        if self._peer_btn is not None:
            self._peer_btn.configure(text=t("tk.dashboard.peer_label", default="PEER"))
        # Key indicators
        if self._stt_key_indicator is not None:
            self._stt_key_indicator.configure(
                text=t("tk.popup.control.needs_key", default="\u26a0 Key needed")
            )
        if self._trans_key_indicator is not None:
            self._trans_key_indicator.configure(
                text=t("tk.popup.control.needs_key", default="\u26a0 Key needed")
            )

    # ------------------------------------------------------------------
    # Theme toggle
    # ------------------------------------------------------------------

    def _theme_label(self) -> str:
        """Return button label for theme toggle."""
        return "Theme (Dark)" if th.is_dark() else "Theme (Light)"

    def _toggle_theme(self) -> None:
        """Switch between light and dark theme."""
        th.toggle_theme()
        th.refresh_colors()
        self._apply_theme()

    def _apply_theme(self) -> None:
        """Apply current theme colors to this popup."""
        self._menubar.entryconfigure(1, label=self._theme_label())
        self.configure(fg_color=th.COLOR_BACKGROUND)
        self._scroll.configure(fg_color=th.COLOR_BACKGROUND)
        # Rebuild sections with new colors
        for widget in self._scroll.winfo_children():
            widget.destroy()
        self._build_language_section(self._scroll)
        self._build_toggle_section(self._scroll)

    # ------------------------------------------------------------------
    # Helpers
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

    @staticmethod
    def _apply_btn_state(btn: ctk.CTkButton, on: bool) -> None:
        """Switch a toggle button between active/inactive appearance."""
        if on:
            # Light theme: blue bg → white text; Dark theme: light blue bg → dark text
            active_text = "#FFFFFF" if not th.is_dark() else "#1A1A1A"
            btn.configure(
                fg_color=th.COLOR_PRIMARY,
                hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=active_text,
            )
        else:
            btn.configure(
                fg_color=th.COLOR_SURFACE_TONAL,
                hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_TEXT,
            )

    @staticmethod
    def _code_from_dd(dd: ctk.CTkOptionMenu) -> str:
        """Resolve the currently-selected dropdown display name to a locale code."""
        return _LANG_NAME_TO_CODE.get(dd.get(), dd.get())

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
                logger.exception("Async toggle call failed (fallback thread)")

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _on_async_error(future: asyncio.futures.Future[Any]) -> None:
        """Log exceptions from fire-and-forget coroutines."""
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            logger.exception("PopupControl async call raised", exc_info=exc)
