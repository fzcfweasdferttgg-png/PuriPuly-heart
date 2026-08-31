"""ClipboardService — clipboard watcher and manual-typing state management.

Extracted from ClipboardManagerMixin. Owns clipboard watcher lifecycle and
manual-typing state machine (inactive → active(typing) → active(submit) → inactive).
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from puripuly_heart.core.clipboard.watcher import create_clipboard_watcher

MANUAL_TYPING_IDLE_TIMEOUT_S = 3.0
MANUAL_TYPING_IDLE_POLL_S = 0.25
MANUAL_INPUT_TYPING_REASON = "manual_input"
MANUAL_SUBMIT_TYPING_REASON = "manual_submit_pending"


@dataclass
class ClipboardService:
    """Clipboard watcher and manual-typing state management."""

    # Callbacks — injected by GuiController at construction
    _submit_text_and_wait: Callable[[str, str], Awaitable[None]] | None = None
    _set_osc_typing: Callable[[str, bool], None] | None = None
    _clock: object | None = None  # SystemClock with .now() method
    _log_error: Callable[[str], None] | None = None
    _settings_provider: Callable[[], object | None] | None = None
    _page_run_task: Callable[[Callable[..., object]], object] | None = None

    # Internal state — owned by service
    _clipboard_watcher: object | None = field(init=False, default=None, repr=False)
    _clipboard_loop: asyncio.AbstractEventLoop | None = field(init=False, default=None, repr=False)
    _clipboard_watcher_lock: asyncio.Lock | None = field(init=False, default=None, repr=False)
    _manual_typing_active: bool = field(init=False, default=False)
    _manual_typing_last_activity_at: float = field(init=False, default=0.0)
    _manual_typing_idle_task: object | None = field(init=False, default=None, repr=False)
    _manual_submit_typing_generation: int = field(init=False, default=0)
    _manual_submit_typing_reasons: set[str] = field(init=False, default_factory=set)

    def _emit_error(self, message: str) -> None:
        if self._log_error is not None:
            self._log_error(message)

    def _get_clipboard_watcher_lock(self) -> asyncio.Lock:
        if self._clipboard_watcher_lock is None:
            self._clipboard_watcher_lock = asyncio.Lock()
        return self._clipboard_watcher_lock

    async def sync_clipboard_watcher(self) -> None:
        settings = self._settings_provider() if self._settings_provider else None
        enabled = bool(
            settings is not None
            and getattr(getattr(settings, "ui", None), "clipboard_auto_translate_enabled", False)
        )
        if not enabled or sys.platform != "win32":
            await self.stop_clipboard_watcher()
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
                self._emit_error(f"Clipboard watcher failed to start: {exc}")
                return
            self._clipboard_watcher = watcher

    async def stop_clipboard_watcher(self) -> None:
        async with self._get_clipboard_watcher_lock():
            watcher = self._clipboard_watcher
            self._clipboard_watcher = None
            self._clipboard_loop = None
            if watcher is None:
                return
            try:
                await asyncio.to_thread(watcher.stop)
            except Exception as exc:
                self._emit_error(f"Clipboard watcher failed to stop: {exc}")

    # Windows clipboard-thread → asyncio bridge.
    # Called from a background thread; uses loop.call_soon_threadsafe to
    # schedule work back on the event loop without blocking the watcher.
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
            task = asyncio.create_task(self._submit_clipboard_text(text))
            task.add_done_callback(self._handle_clipboard_task_error)
        except RuntimeError as exc:
            self._emit_error(f"Clipboard submit scheduling failed: {exc}")

    def _handle_clipboard_task_error(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._emit_error(f"Clipboard submit task failed: {exc}")

    async def _submit_clipboard_text(self, text: str) -> None:
        if self._submit_text_and_wait is None:
            return
        try:
            await self._submit_text_and_wait(text, "Clipboard")
        except Exception as exc:
            self._emit_error(f"Clipboard submit failed: {exc}")

    # State machine: inactive → active(typing) → active(submit pending) → inactive
    def note_manual_input_activity(self, has_text: bool) -> None:
        if not has_text:
            self._clear_manual_input_typing()
            return
        if self._clock is not None:
            self._manual_typing_last_activity_at = self._clock.now()
        if not self._manual_typing_active:
            self._manual_typing_active = True
        self._invoke_osc_typing(MANUAL_INPUT_TYPING_REASON, True)
        self._ensure_manual_typing_idle_task()

    def _ensure_manual_typing_idle_task(self) -> None:
        if self._manual_typing_idle_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if self._page_run_task is not None:
                task = self._page_run_task(self._run_manual_typing_idle_loop)
                self._manual_typing_idle_task = task if task is not None else object()
            return
        self._manual_typing_idle_task = loop.create_task(self._run_manual_typing_idle_loop())

    async def _run_manual_typing_idle_loop(self) -> None:
        try:
            while self._manual_typing_active:
                await asyncio.sleep(MANUAL_TYPING_IDLE_POLL_S)
                if not self._manual_typing_active:
                    return
                if self._clock is not None:
                    elapsed = self._clock.now() - self._manual_typing_last_activity_at
                    if elapsed >= MANUAL_TYPING_IDLE_TIMEOUT_S:
                        self._clear_manual_input_typing(cancel_idle_task=False)
                        return
        finally:
            self._manual_typing_idle_task = None

    def _clear_manual_input_typing(self, *, cancel_idle_task: bool = True) -> None:
        if self._manual_typing_active:
            self._manual_typing_active = False
            self._invoke_osc_typing(MANUAL_INPUT_TYPING_REASON, False)
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

    async def reset_manual_typing_state(self) -> None:
        self._manual_typing_active = False
        self._manual_typing_last_activity_at = 0.0
        self._invoke_osc_typing(MANUAL_INPUT_TYPING_REASON, False)
        for reason in tuple(self._manual_submit_typing_reasons):
            self._invoke_osc_typing(reason, False)
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

    def _invoke_osc_typing(self, reason: str, active: bool) -> None:
        if self._set_osc_typing is not None:
            self._set_osc_typing(reason, active)

    async def submit_text(self, text: str) -> None:
        self._manual_submit_typing_generation += 1
        submit_reason = f"{MANUAL_SUBMIT_TYPING_REASON}:{self._manual_submit_typing_generation}"
        self._manual_submit_typing_reasons.add(submit_reason)
        self._invoke_osc_typing(submit_reason, True)
        self._clear_manual_input_typing()
        try:
            if self._submit_text_and_wait is not None:
                await self._submit_text_and_wait(text, "You")
        except Exception as exc:
            self._emit_error(f"Submit failed: {exc}")
        finally:
            self._invoke_osc_typing(submit_reason, False)
            self._manual_submit_typing_reasons.discard(submit_reason)
