"""Dashboard view for the Tkinter/CTk GUI.

Displays language selectors, status indicators, and feature toggle buttons.
All user-visible strings use ``t()`` for i18n.  Async controller calls are
dispatched through ``_run_async`` which submits coroutines to the controller's
event loop via ``asyncio.run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.domain.language import get_all_language_options
from puripuly_heart.ui_tkinter import theme as th

logger = logging.getLogger(__name__)

# Pre-load language options once at module level (same as Flet dashboard).
_LANG_OPTIONS: tuple[tuple[str, str], ...] = tuple(get_all_language_options())
_LANG_CODE_TO_NAME: dict[str, str] = {code: name for code, name in _LANG_OPTIONS}
_LANG_NAMES: list[str] = [name for _, name in _LANG_OPTIONS]
_LANG_NAME_TO_CODE: dict[str, str] = {name: code for code, name in _LANG_OPTIONS}


class DashboardView(ctk.CTkFrame):
    """Main dashboard: language selector, status, toggle buttons.

    Constructor parameters
    ----------------------
    master : widget
        Parent CTk widget.
    controller : GuiController
        Shared application controller (business logic lives there).
    """

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, fg_color=th.COLOR_BACKGROUND, **kwargs)
        self._controller = controller

        # Local toggle state mirrors (updated by controller callbacks too).
        self._stt_on: bool = False
        self._trans_on: bool = False
        self._overlay_on: bool = False
        self._peer_on: bool = False

        self._build_ui()
        # Settings may not be loaded yet; poll until available.
        self._load_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble the three dashboard sections."""
        # Page title
        ctk.CTkLabel(
            self,
            text=t("nav.dashboard", default="Dashboard"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_TITLE, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", padx=th.CONTENT_PAD_X, pady=(th.CONTENT_PAD_Y, 8))

        # Scrollable content area
        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=th.COLOR_BACKGROUND,
            scrollbar_button_color=th.COLOR_DIVIDER,
        )
        self._scroll.pack(fill="both", expand=True, padx=th.CONTENT_PAD_X)

        self._build_language_section(self._scroll)
        self._build_status_section(self._scroll)
        self._build_toggle_section(self._scroll)

    # --- Language selector ------------------------------------------------

    def _build_language_section(self, parent: ctk.CTkScrollableFrame) -> None:
        """Two-column language selector: self channel and peer channel."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        ctk.CTkLabel(
            inner,
            text=t("dashboard.language.self", default="My voice"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(0, 6))

        self._build_language_row(
            inner,
            label_src=t("dashboard.language.source_label", default="Source"),
            label_tgt=t("dashboard.language.target_label", default="Target"),
            arrow="→",
        )

        ctk.CTkLabel(
            inner,
            text=t("dashboard.language.peer", default="Their voice"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_HEADING, "bold"),
            text_color=th.COLOR_TEXT,
        ).pack(anchor="w", pady=(14, 6))

        self._build_peer_row(inner)

    def _build_language_row(
        self,
        parent: ctk.CTkFrame,
        *,
        label_src: str,
        label_tgt: str,
        arrow: str,
    ) -> None:
        """Source → Target dropdowns for the self channel."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")

        ctk.CTkLabel(
            row, text=label_src, width=60,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(side="left")

        self._source_dd = ctk.CTkOptionMenu(
            row, values=_LANG_NAMES, width=180,
            command=self._on_self_lang_change,
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._source_dd.pack(side="left", padx=(4, 8))
        self._add_debug_label(self._source_dd, "lang.source_selector")

        ctk.CTkLabel(
            row, text=arrow,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(side="left")

        ctk.CTkLabel(
            row, text=label_tgt, width=60,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(side="left", padx=(8, 0))

        self._target_dd = ctk.CTkOptionMenu(
            row, values=_LANG_NAMES, width=180,
            command=self._on_self_lang_change,
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._target_dd.pack(side="left", padx=4)
        self._add_debug_label(self._target_dd, "lang.target_selector")

        # Swap button
        swap_btn = ctk.CTkButton(
            row, text="⇄", width=36,
            command=self._swap_self,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
        )
        swap_btn.pack(side="left", padx=(8, 0))
        self._add_debug_label(swap_btn, "lang.swap_self")

    def _build_peer_row(self, parent: ctk.CTkFrame) -> None:
        """Peer Source → Peer Target dropdowns."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")

        ctk.CTkLabel(
            row, text=t("dashboard.language.source_label", default="Source"), width=60,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(side="left")

        self._peer_source_dd = ctk.CTkOptionMenu(
            row, values=_LANG_NAMES, width=180,
            command=self._on_peer_lang_change,
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._peer_source_dd.pack(side="left", padx=(4, 8))
        self._add_debug_label(self._peer_source_dd, "lang.peer_source_selector")

        ctk.CTkLabel(
            row, text="→",
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(side="left")

        ctk.CTkLabel(
            row, text=t("dashboard.language.target_label", default="Target"), width=60,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT_SECONDARY,
        ).pack(side="left", padx=(8, 0))

        self._peer_target_dd = ctk.CTkOptionMenu(
            row, values=_LANG_NAMES, width=180,
            command=self._on_peer_lang_change,
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._peer_target_dd.pack(side="left", padx=4)
        self._add_debug_label(self._peer_target_dd, "lang.peer_target_selector")

        swap_peer_btn = ctk.CTkButton(
            row, text="⇄", width=36,
            command=self._swap_peer,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
        )
        swap_peer_btn.pack(side="left", padx=(8, 0))
        self._add_debug_label(swap_peer_btn, "lang.swap_peer")

    # --- Status display ---------------------------------------------------

    def _build_status_section(self, parent: ctk.CTkScrollableFrame) -> None:
        """Compact status card."""
        card = ctk.CTkFrame(
            parent,
            fg_color=th.COLOR_SURFACE,
            corner_radius=th.CARD_CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=th.CARD_PAD_X, pady=th.CARD_PAD_Y)

        self._status_label = ctk.CTkLabel(
            inner,
            text=t("dashboard.ready", default="Ready to translate…"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY),
            text_color=th.COLOR_TEXT,
            anchor="w",
        )
        self._status_label.pack(fill="x")
        self._add_debug_label(self._status_label, "status.connection")

    # --- Toggle buttons ---------------------------------------------------

    def _build_toggle_section(self, parent: ctk.CTkScrollableFrame) -> None:
        """Grid of feature toggle buttons."""
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

        self._stt_btn = ctk.CTkButton(
            row1,
            text=f"🎤  {t('dashboard.stt_label', default='STT')}",
            height=42,
            corner_radius=th.NAV_BUTTON_CORNER_RADIUS,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._toggle_stt,
        )
        self._stt_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._add_debug_label(self._stt_btn, "toggle.stt")

        self._trans_btn = ctk.CTkButton(
            row1,
            text=f"🔄  {t('dashboard.trans_label', default='TRANS')}",
            height=42,
            corner_radius=th.NAV_BUTTON_CORNER_RADIUS,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._toggle_translation,
        )
        self._trans_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._add_debug_label(self._trans_btn, "toggle.translation")

        # Row 2: Overlay + Peer
        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x")

        self._overlay_btn = ctk.CTkButton(
            row2,
            text=f"📡  {t('dashboard.overlay_label', default='Subtitles')}",
            height=42,
            corner_radius=th.NAV_BUTTON_CORNER_RADIUS,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._toggle_overlay,
        )
        self._overlay_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._add_debug_label(self._overlay_btn, "toggle.overlay")

        self._peer_btn = ctk.CTkButton(
            row2,
            text=f"👥  {t('dashboard.peer_label', default='PEER')}",
            height=42,
            corner_radius=th.NAV_BUTTON_CORNER_RADIUS,
            fg_color=th.COLOR_SURFACE_TONAL,
            hover_color=th.COLOR_PRIMARY_CONTAINER,
            text_color=th.COLOR_TEXT,
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            command=self._toggle_peer,
        )
        self._peer_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._add_debug_label(self._peer_btn, "toggle.peer")

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
        self._apply_language_settings()

    def _on_peer_lang_change(self, _value: str) -> None:  # noqa: ARG002
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
        """Swap self source ↔ target."""
        src_cur = self._source_dd.get()
        tgt_cur = self._target_dd.get()
        self._source_dd.set(tgt_cur)
        self._target_dd.set(src_cur)
        self._apply_language_settings()

    def _swap_peer(self) -> None:
        """Swap peer source ↔ target."""
        src_cur = self._peer_source_dd.get()
        tgt_cur = self._peer_target_dd.get()
        self._peer_source_dd.set(tgt_cur)
        self._peer_target_dd.set(src_cur)
        self._apply_language_settings()

    # ------------------------------------------------------------------
    # Toggle handlers
    # ------------------------------------------------------------------

    def _toggle_stt(self) -> None:
        new = not self._stt_on
        self._stt_on = new
        self._apply_btn_state(self._stt_btn, new)
        self._run_async(self._controller.set_stt_enabled(new))

    def _toggle_translation(self) -> None:
        new = not self._trans_on
        self._trans_on = new
        self._apply_btn_state(self._trans_btn, new)
        self._run_async(self._controller.set_translation_enabled(new))

    def _toggle_overlay(self) -> None:
        new = not self._overlay_on
        self._overlay_on = new
        self._apply_btn_state(self._overlay_btn, new)
        self._run_async(self._controller.set_overlay_enabled(new))

    def _toggle_peer(self) -> None:
        new = not self._peer_on
        self._peer_on = new
        self._apply_btn_state(self._peer_btn, new)
        self._run_async(self._controller.set_peer_translation_enabled(new))

    # ------------------------------------------------------------------
    # Public API — called by GuiController from the async thread
    # ------------------------------------------------------------------

    def set_stt_enabled(self, enabled: bool) -> None:
        """Thread-safe: called from controller's async context."""
        self._stt_on = enabled
        self.after(0, lambda: self._apply_btn_state(self._stt_btn, enabled))

    def set_translation_enabled(self, enabled: bool) -> None:
        """Thread-safe: called from controller's async context."""
        self._trans_on = enabled
        self.after(0, lambda: self._apply_btn_state(self._trans_btn, enabled))

    def set_overlay_enabled(self, enabled: bool) -> None:
        """Thread-safe: called from controller's async context."""
        self._overlay_on = enabled
        self.after(0, lambda: self._apply_btn_state(self._overlay_btn, enabled))

    def set_peer_enabled(self, enabled: bool) -> None:
        """Thread-safe: called from controller's async context."""
        self._peer_on = enabled
        self.after(0, lambda: self._apply_btn_state(self._peer_btn, enabled))

    # ------------------------------------------------------------------
    # Helpers
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

    @staticmethod
    def _apply_btn_state(btn: ctk.CTkButton, on: bool) -> None:
        """Switch a toggle button between active/inactive appearance."""
        if on:
            btn.configure(
                fg_color=th.COLOR_PRIMARY,
                hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color="#FFFFFF",
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
            logger.exception("Dashboard async call raised", exc_info=exc)

    # ------------------------------------------------------------------
    # Controller compatibility stubs
    # ------------------------------------------------------------------

    def set_stt_needs_key(self, needs_key: bool) -> None:
        """Stub — Tkinter dashboard does not show key status indicators."""

    def set_translation_needs_key(self, needs_key: bool, update_ui: bool = True) -> None:
        """Stub — Tkinter dashboard does not show key status indicators."""

    def set_local_stt_notice(self, status: str | None, *, percent: float | None = None) -> None:
        """Stub — local STT download notice (future enhancement)."""

    def set_languages_from_codes(
        self,
        source: str,
        target: str,
        peer_source: str = "",
        peer_target: str = "",
        second_target: str = "",
    ) -> None:
        """Stub — language selector sync (future enhancement)."""

    def set_recent_languages(self, source: list[str], target: list[str]) -> None:
        """Stub — recent languages list (future enhancement)."""

    def show_peer_eula_dialog(self, on_accept: object = None) -> None:
        """Stub — peer translation EULA dialog (future enhancement).

        Falls back to auto-accepting since Tkinter has no dialog yet.
        """
        if callable(on_accept):
            on_accept()
