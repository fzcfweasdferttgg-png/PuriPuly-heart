"""Microphone test dialog lifecycle mixin for TranslatorApp.

Extracted from app.py during mixin-decomposition (Phase 4).
Handles mic test start, stop, dismiss, sync.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from puripuly_heart.ui.components.microphone_test_dialog import MicrophoneTestDialog

if TYPE_CHECKING:
    pass

from puripuly_heart.ui.app_utilities import _callable_accepts_keyword


class AppMicTestMixin:
    """Microphone test dialog: start, stop, dismiss, sync."""

    # AI: CALLBACK VALIDATION — uses _callable_accepts_keyword + inspect.isawaitable
    # to handle controller.start_microphone_test which MAY be sync or async,
    # and MAY or MAY NOT accept meter_callback. This dual flexibility exists because
    # mic_test_manager evolved through multiple refactorings. If you're sure the method
    # is always async with meter_callback, simplify to direct await.

    # AI: DIALOG-FIRST — dialog.open() runs BEFORE start_microphone_test().
    # This ensures the user sees the dialog immediately, even if start takes time.
    # If start fails, dialog.show_failure() displays error in the already-open dialog.
    # meter_callback=dialog.set_level connects the audio meter to the dialog's level display.
    # If start_microphone_test doesn't accept meter_callback (legacy path), callback is skipped.

    def _on_start_microphone_test(self) -> None:
        async def _task():
            dialog = self._get_microphone_test_dialog()
            dialog.reset()
            dialog.open()
            start_microphone_test = self.controller.start_microphone_test
            if _callable_accepts_keyword(start_microphone_test, "meter_callback"):
                start_result = start_microphone_test(meter_callback=dialog.set_level)
            else:
                start_result = start_microphone_test()
            started = await start_result if inspect.isawaitable(start_result) else start_result
            if not started:
                dialog.show_failure()
                return

        self._queue_settings_mutation_task(_task)

    # AI: DEFENSIVE STOP — getattr guard handles case where controller.stop_microphone_test
    # doesn't exist (e.g., controller rebuilt without mic test support).
    # _close_microphone_test_dialog always runs regardless of stop result.
    # Dialog cleanup must happen even if stop fails — otherwise UI shows stale dialog.

    def _on_stop_microphone_test(self) -> None:
        async def _task() -> None:
            stop_microphone_test = getattr(self.controller, "stop_microphone_test", None)
            if callable(stop_microphone_test):
                result = stop_microphone_test()
                if inspect.isawaitable(result):
                    await result
            self._close_microphone_test_dialog()

        self._queue_settings_mutation_task(_task)

    # AI: LAZY INIT — dialog created on first use, then cached on self._microphone_test_dialog.
    # self._microphone_test_dialog is initialized to None in TranslatorApp.__init__.
    # AppNavigationMixin._close_open_dialog_for_navigation also reads this attribute
    # (via getattr with None default). If you rename it, update BOTH mixins.

    def _get_microphone_test_dialog(self) -> MicrophoneTestDialog:
        dialog = getattr(self, "_microphone_test_dialog", None)
        if dialog is None:
            dialog = MicrophoneTestDialog(
                self.page,
                on_close=self._on_microphone_test_dialog_dismiss,
            )
            self._microphone_test_dialog = dialog
        return dialog

    def _close_microphone_test_dialog(self) -> None:
        dialog = getattr(self, "_microphone_test_dialog", None)
        if dialog is None:
            return
        dialog.close(notify=False)
        dialog.reset()

    # AI: DISMISS → STOP → CLOSE chain:
    # 1. User closes dialog (or navigation closes it with notify=True)
    # 2. _on_microphone_test_dialog_dismiss fires
    # 3. Calls _on_stop_microphone_test which queues stop+close task
    # 4. Inside task: _close_microphone_test_dialog calls dialog.close(notify=False)
    #    notify=False prevents re-triggering dismiss → avoids infinite loop.
    #
    # CRITICAL: _close_microphone_test_dialog always uses notify=False.
    # Only user-initiated close uses notify=True (via MicrophoneTestDialog's close button).

    def _on_microphone_test_dialog_dismiss(self) -> None:
        self._on_stop_microphone_test()

    # AI: SETTINGS CHANGE GUARD — called after settings apply (from app_settings._on_settings_changed).
    # If mic test is not active (controller.microphone_test_active is False), closes any
    # lingering dialog. This handles the case where settings change invalidates mic config
    # while dialog is open (e.g., changing audio input device in settings).
    # If mic test IS active, does nothing — let the test run.

    def _sync_microphone_test_dialog_if_inactive(self) -> None:
        controller = getattr(self, "controller", None)
        if bool(getattr(controller, "microphone_test_active", False)):
            return
        self._close_microphone_test_dialog()
