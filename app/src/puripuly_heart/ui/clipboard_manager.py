"""Clipboard watcher and manual-typing mixin for GuiController."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import sys

from puripuly_heart.core.clipboard.watcher import create_clipboard_watcher

# ---------------------------------------------------------------------------
# Constants (originally in controller.py)
# ---------------------------------------------------------------------------

MANUAL_TYPING_IDLE_TIMEOUT_S = 3.0
MANUAL_TYPING_IDLE_POLL_S = 0.25
MANUAL_INPUT_TYPING_REASON = "manual_input"
MANUAL_SUBMIT_TYPING_REASON = "manual_submit_pending"


class ClipboardManagerMixin:
    """Clipboard watcher and manual-typing state management.

    Mixed into :class:`GuiController`; all attributes are inherited from it.
    """

    # ------------------------------------------------------------------
    # Clipboard watcher helpers
    # ------------------------------------------------------------------

    def _get_clipboard_watcher_lock(self) -> asyncio.Lock:
        if self._clipboard_watcher_lock is None:
            self._clipboard_watcher_lock = asyncio.Lock()
        return self._clipboard_watcher_lock

    async def _sync_clipboard_watcher(self) -> None:
        enabled = bool(
            self.settings is not None and self.settings.ui.clipboard_auto_translate_enabled
        )
        if not enabled or sys.platform != "win32":
            await self._stop_clipboard_watcher()
            return
        async with self._get_clipboard_watcher_lock():
            if self._clipboard_watcher is not None:
                return

            self._clipboard_loop = asyncio.get_running_loop()
            watcher = create_clipboard_watcher(self._on_clipboard_text_from_thread)
            try:
                await asyncio.to_thread(watcher.start)
            except Exception as exc:
                self._clipboard_loop = None
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(watcher.stop)
                self._log_error(f"Clipboard watcher failed to start: {exc}")
                return
            self._clipboard_watcher = watcher

    async def _stop_clipboard_watcher(self) -> None:
        async with self._get_clipboard_watcher_lock():
            watcher = self._clipboard_watcher
            self._clipboard_watcher = None
            self._clipboard_loop = None
            if watcher is None:
                return
            try:
                await asyncio.to_thread(watcher.stop)
            except Exception as exc:
                self._log_error(f"Clipboard watcher failed to stop: {exc}")

    def _on_clipboard_text_from_thread(self, text: str) -> None:
        trimmed = text.strip()
        if not trimmed or len(trimmed) > 300:
            return
        loop = self._clipboard_loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._schedule_clipboard_submit, trimmed)

    def _schedule_clipboard_submit(self, text: str) -> None:
        try:
            asyncio.create_task(self._submit_clipboard_text(text))
        except RuntimeError as exc:
            self._log_error(f"Clipboard submit scheduling failed: {exc}")

    async def _submit_clipboard_text(self, text: str) -> None:
        if self.hub is None:
            return
        try:
            await self.hub.submit_text(text, source="Clipboard")
        except Exception as exc:
            self._log_error(f"Clipboard submit failed: {exc}")

    # ------------------------------------------------------------------
    # Manual typing / idle detection
    # ------------------------------------------------------------------

    def note_manual_input_activity(self, has_text: bool) -> None:
        if not has_text:
            self._clear_manual_input_typing()
            return
        self._manual_typing_last_activity_at = self.clock.now()
        if not self._manual_typing_active:
            self._manual_typing_active = True
        self._set_osc_typing_reason(MANUAL_INPUT_TYPING_REASON, True)
        self._ensure_manual_typing_idle_task()

    def _ensure_manual_typing_idle_task(self) -> None:
        if self._manual_typing_idle_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            run_task = getattr(self.page, "run_task", None)
            if callable(run_task):
                task = run_task(self._run_manual_typing_idle_loop)
                self._manual_typing_idle_task = task if task is not None else object()
            return
        self._manual_typing_idle_task = loop.create_task(self._run_manual_typing_idle_loop())

    async def _run_manual_typing_idle_loop(self) -> None:
        try:
            while self._manual_typing_active:
                await asyncio.sleep(MANUAL_TYPING_IDLE_POLL_S)
                if not self._manual_typing_active:
                    return
                elapsed = self.clock.now() - self._manual_typing_last_activity_at
                if elapsed >= MANUAL_TYPING_IDLE_TIMEOUT_S:
                    self._clear_manual_input_typing(cancel_idle_task=False)
                    return
        finally:
            self._manual_typing_idle_task = None

    def _clear_manual_input_typing(self, *, cancel_idle_task: bool = True) -> None:
        if self._manual_typing_active:
            self._manual_typing_active = False
            self._set_osc_typing_reason(MANUAL_INPUT_TYPING_REASON, False)
        self._manual_typing_last_activity_at = 0.0
        if cancel_idle_task:
            self._cancel_manual_typing_idle_task()

    def _cancel_manual_typing_idle_task(self) -> None:
        task = self._manual_typing_idle_task
        if task is None:
            return
        current_task = None
        with contextlib.suppress(RuntimeError):
            current_task = asyncio.current_task()
        if task is current_task:
            return
        self._manual_typing_idle_task = None
        cancel = getattr(task, "cancel", None)
        if callable(cancel):
            cancel()

    async def _reset_manual_typing_state(self) -> None:
        self._manual_typing_active = False
        self._manual_typing_last_activity_at = 0.0
        self._set_osc_typing_reason(MANUAL_INPUT_TYPING_REASON, False)
        for reason in tuple(self._manual_submit_typing_reasons):
            self._set_osc_typing_reason(reason, False)
        self._manual_submit_typing_reasons.clear()
        await self._stop_manual_typing_idle_task()

    async def _stop_manual_typing_idle_task(self) -> None:
        task = self._manual_typing_idle_task
        self._manual_typing_idle_task = None
        if task is None:
            return
        cancel = getattr(task, "cancel", None)
        if callable(cancel):
            cancel()
        if isinstance(task, asyncio.Task):
            await asyncio.gather(task, return_exceptions=True)

    def _set_osc_typing_reason(self, reason: str, active: bool) -> None:
        osc = self.osc
        if osc is None:
            return
        set_reason = getattr(osc, "set_typing_reason", None)
        if callable(set_reason):
            set_reason(reason, active)
            return
        osc.send_typing(active)

    # ------------------------------------------------------------------
    # Submit text
    # ------------------------------------------------------------------

    async def submit_text(self, text: str) -> None:
        self._manual_submit_typing_generation += 1
        submit_reason = f"{MANUAL_SUBMIT_TYPING_REASON}:{self._manual_submit_typing_generation}"
        self._manual_submit_typing_reasons.add(submit_reason)
        self._set_osc_typing_reason(submit_reason, True)
        self._clear_manual_input_typing()
        try:
            if self.hub is None:
                return
            utterance_id = await self.hub.submit_text(text, source="You")
            await self._wait_for_manual_submit_output(utterance_id)
        except Exception as exc:
            self._log_error(f"Submit failed: {exc}")
        finally:
            self._set_osc_typing_reason(submit_reason, False)
            self._manual_submit_typing_reasons.discard(submit_reason)

    async def _wait_for_manual_submit_output(self, utterance_id: object) -> None:
        hub = self.hub
        if hub is None:
            return
        runtime = getattr(hub, "self_runtime", None)
        tasks = getattr(runtime, "translation_tasks", None)
        task = tasks.get(utterance_id) if isinstance(tasks, dict) else None
        if isinstance(task, asyncio.Task):
            await asyncio.gather(task, return_exceptions=True)
            return
        if inspect.isawaitable(task):
            await task
