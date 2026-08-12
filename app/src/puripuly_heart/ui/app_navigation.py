"""Navigation and tab management mixin for TranslatorApp.

Handles tab navigation, settings auto-apply, dialog cleanup.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from puripuly_heart.ui.app_utilities import APP_CONTENT_PADDING

if TYPE_CHECKING:
    pass


class AppNavigationMixin:
    """Tab navigation, logs tab, bottom nav state."""

    # STATE MACHINE — _on_nav_change is the single entry point for ALL tab navigation.
    # Tracks _current_tab (getattr default: 0 = Dashboard). State transitions trigger:
    # 1. Dialog cleanup (if tab changed)
    # 2. Settings auto-apply (if leaving Settings tab 1)
    # 3. Content area swap (view assignment + padding)
    # 4. Tab-specific post-actions (Settings: refresh prompt, Logs: scroll to bottom)

    # PADDING EXCEPTION — Settings tab (index 1) has 0 padding because SettingsView
    # manages its own internal padding for the scrollable form layout.
    # All other views use APP_CONTENT_PADDING (16px).

    def _content_padding_for_index(self, index: int) -> int:
        return 0 if index == 1 else APP_CONTENT_PADDING

    # CROSS-BOUNDARY CALLS — this method calls:
    # - _close_open_dialog_for_navigation (defined HERE, in AppNavigationMixin)
    # - controller.auto_apply_pending_on_leave (defined in GuiController)
    #
    # AUTO-APPLY TRIGGER — leaving Settings tab (1→other) triggers settings apply.
    # This handles the case where user changed settings but didn't click Apply.
    # The auto-apply logic is in GuiController, not here — this mixin only triggers it.

    def _on_nav_change(self, index: int):
        # Track previous tab for Settings auto-apply
        previous_tab = getattr(self, "_current_tab", 0)
        if previous_tab != index:
            self._close_open_dialog_for_navigation()
        self._current_tab = index

        # Auto-apply Settings changes when leaving Settings (tab 1)
        if previous_tab == 1 and index != 1:
            self.controller.auto_apply_pending_on_leave()

        if index == 0:
            self.content_area.content = self.view_dashboard
        elif index == 1:
            self.content_area.content = self.view_settings
        elif index == 2:
            self.content_area.content = self.view_logs
        elif index == 3:
            self.content_area.content = self.view_about

        self.content_area.padding = self._content_padding_for_index(index)
        self.content_area.update()
        if index == 1:
            self.view_settings.refresh_prompt_if_empty()
        elif index == 2:
            # TIMING HACK — asyncio.sleep(0.05) waits for Flet to render the Logs view
            # before scrolling. 50ms is a heuristic; on slow systems it may not be enough.
            # If scroll fails, worst case is logs don't auto-scroll — no crash, no corruption.
            # The coroutine is fire-and-forget via page.run_task.

            # Async scroll after rendering completes
            async def _scroll():
                await asyncio.sleep(0.05)
                await self.view_logs.scroll_to_bottom()

            self.page.run_task(_scroll)

    # PROGRAMMATIC NAVIGATION — called from SettingsView (via on_view_logs callback).
    # Must update BOTH content area (_on_nav_change) AND bottom nav highlight
    # (_set_bottom_nav_selected). When user clicks a tab directly, BottomNavBar handles
    # its own highlight before calling _on_change. But programmatic navigation bypasses
    # BottomNavBar's click handler, so explicit highlight sync is needed.

    def _open_logs_tab(self) -> None:
        self._on_nav_change(2)
        self._set_bottom_nav_selected(2)

    # PRIVATE API ACCESS — accesses bottom_nav._selected and bottom_nav._update_visuals().
    # These are private members of BottomNavBar. Necessary because BottomNavBar has no
    # public select(index, fire_callback=False) method. _on_tab_click would trigger
    # _on_nav_change again (infinite recursion). If BottomNavBar adds a public API,
    # replace this with it.

    def _set_bottom_nav_selected(self, index: int) -> None:
        selected_attr = getattr(self.bottom_nav, "_selected", None)
        if selected_attr != index and hasattr(self.bottom_nav, "_selected"):
            self.bottom_nav._selected = index
        update_visuals = getattr(self.bottom_nav, "_update_visuals", None)
        if callable(update_visuals):
            with contextlib.suppress(Exception):
                update_visuals()

    # INCOMPLETE DIALOG CLEANUP — only closes:
    # 1. _microphone_test_dialog (has is_open property, close with notify=True)
    # 2. page.dialog (legacy Flet API, effectively always None)
    #
    # NOT CLOSED: founder_letter_dialog, peer_translation_eula_dialog,
    # local_qwen_hallucination_dialog (from app_debug.py). These use page.open()
    # (warm_document_dialog pattern) which has no is_open property.
    # KNOWN BUG: navigating tabs with these dialogs open leaves them visible.
    #
    # The return after mic dialog close is intentional — only one dialog type
    # should be open at a time, so closing mic dialog short-circuits the rest.

    def _close_open_dialog_for_navigation(self) -> None:
        microphone_test_dialog = getattr(self, "_microphone_test_dialog", None)
        if microphone_test_dialog is not None and getattr(
            microphone_test_dialog,
            "is_open",
            False,
        ):
            microphone_test_dialog.close(notify=True)
            return

        dialog = getattr(self.page, "dialog", None)
        close_dialog = getattr(self.page, "close", None)
        if dialog is None or not callable(close_dialog):
            return
        try:
            close_dialog(dialog)
        except Exception:
            logging.getLogger(__name__).exception("Failed to close dialog during navigation")
