"""Toggle coordination service — translation and STT toggle state machine.

Manages:
- Translation toggle with intent tracking (stale request detection)
- STT toggle with local STT readiness checks
- STT switch state machine (start/stop/restart cycle)
- STT hot-replace (runtime provider swap without full restart)
- Idle release management for local STT providers

Does NOT know about Flet/UI. Uses callbacks for side-effects.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from puripuly_heart.core.stt.local_stt_manager import LOCAL_STT_PROVIDERS

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings
    from puripuly_heart.core.pipeline.pipeline import Pipeline
    from puripuly_heart.core.stt.local_stt_manager import LocalSTTManager

logger = logging.getLogger(__name__)


@dataclass
class ToggleCoordinator:
    """Coordinates translation and STT toggle operations.

    State machine for STT:
        OFF → (enable) → STARTING → ON
        ON → (disable) → STOPPING → OFF
        ON → (restart) → STOPPING → STARTING → ON

    Translation toggle uses generation-based intent tracking
    to handle rapid toggle sequences.
    """

    hub: Pipeline
    settings: AppSettings
    local_stt_manager: LocalSTTManager | None = None

    # STT state
    _stt_desired: bool = field(default=False)
    _stt_switch_lock: asyncio.Lock | None = field(default=None, repr=False)
    _stt_switch_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _stt_idle_release_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _stt_restart_requested: bool = field(default=False)

    # Stopping guard — controller sets _is_stopping = True in stop()
    _is_stopping: bool = field(default=False)

    # Translation toggle intent tracking
    _translation_toggle_intent_enabled: bool = field(default=False)
    _translation_toggle_generation: int = field(default=0)

    # Callbacks — UI side-effects (controller implements)
    on_translation_state_changed: Callable[[bool], None] | None = None
    on_stt_state_changed: Callable[[bool], None] | None = None
    on_error: Callable[[str], None] | None = None
    on_stt_downloading: Callable[[], None] | None = None
    log_basic: Callable[[str], None] | None = None
    log_detailed: Callable[[str], None] | None = None

    # External actions (injected via DI)
    start_mic_loop: Callable[[], Awaitable[None]] | None = None
    stop_mic_loop: Callable[[], Awaitable[None]] | None = None
    rebuild_stt_provider: Callable[[], Awaitable[None]] | None = None

    # --- Translation toggle ---

    def _record_translation_toggle_intent(self, enabled: bool) -> int:
        self._translation_toggle_intent_enabled = enabled
        self._translation_toggle_generation += 1
        return self._translation_toggle_generation

    def _translation_toggle_intent_matches(
        self, *, enabled: bool, generation: int,
    ) -> bool:
        return (
            self._translation_toggle_intent_enabled == enabled
            and self._translation_toggle_generation == generation
        )

    async def set_translation_enabled(self, enabled: bool) -> bool:
        """Toggle translation with stale request detection.

        Returns True if translation is now enabled.
        """
        if self._is_stopping:
            return False

        request_generation = self._record_translation_toggle_intent(enabled)
        if self.hub is None:
            return False

        if self.log_basic is not None:
            self.log_basic(f"[Translation] Toggle request: enabled={enabled}")

        if enabled and not self._translation_toggle_intent_matches(
            enabled=True, generation=request_generation,
        ):
            if self.log_detailed is not None:
                self.log_detailed("[Translation] Skipping stale enable request")
            return False

        if enabled and self.hub.llm is None:
            self.hub.translation_enabled = False
            if self.on_translation_state_changed is not None:
                self.on_translation_state_changed(False)
            if self.on_error is not None:
                self.on_error("Translation is ON but LLM provider is not configured.")
            return False

        if enabled and self.log_basic is not None:
            provider = self.settings.provider.llm.value
            self.log_basic(f"[Translation] Enabled with provider: {provider}")

        self.hub.clear_context()
        self.hub.translation_enabled = bool(enabled)
        return bool(self.hub.translation_enabled)

    # --- STT toggle ---

    async def set_stt_enabled(self, enabled: bool) -> None:
        """Toggle STT with local readiness checks.
        """
        if self._is_stopping:
            return

        if self.log_basic is not None:
            self.log_basic(f"[STT] Toggle request: enabled={enabled}")

        self._stt_desired = bool(enabled)
        if not enabled and self.local_stt_manager is not None:
            self.local_stt_manager.reset_pending_enable()

        if enabled and self.log_basic is not None:
            provider = self.settings.provider.stt.value
            self.log_basic(f"[STT] Enabled with provider: {provider}")

        if (
            enabled
            and self.local_stt_manager is not None
            and self.local_stt_manager.is_local_provider(self.settings.provider.stt)
        ):
            current_status = self.local_stt_manager.current_status()
            if current_status == "downloading":
                self.local_stt_manager.set_pending_enable(True)
                self._stt_desired = False
                if self.on_stt_state_changed is not None:
                    self.on_stt_state_changed(False)
                if self.on_stt_downloading is not None:
                    self.on_stt_downloading()
                return
            if current_status in ("missing", "invalid", "download_failed"):
                self.local_stt_manager.handle_unavailable(
                    current_status,
                    resume_self=True,
                    resume_peer=self.local_stt_manager.peer_local_stt_requested(self.settings),
                )
                return

        if enabled:
            self.hub.mark_promo_eligible()

        await self._ensure_stt_switch()

    async def _ensure_stt_switch(self) -> None:
        if self._stt_switch_task is None or self._stt_switch_task.done():
            self._stt_switch_task = asyncio.create_task(self._run_stt_switch())
        await self._stt_switch_task

    async def _replace_runtime_stt_provider(self) -> None:
        """Hot-replace STT provider without full pipeline restart.
        """
        self._cancel_stt_idle_release()
        if self.log_detailed is not None:
            self.log_detailed(
                "[STT] Replacing runtime provider detail: "
                f"desired={self._stt_desired}"
            )
        if self.log_basic is not None:
            self.log_basic(
                f"[Settings] STT provider replacement: "
                f"provider_type={self.settings.provider.stt_compute}"
            )
        if self._stt_switch_lock is None:
            self._stt_switch_lock = asyncio.Lock()
        async with self._stt_switch_lock:
            if self.stop_mic_loop is not None:
                await self.stop_mic_loop()
            self._stt_restart_requested = False
            if self.rebuild_stt_provider is not None:
                await self.rebuild_stt_provider()
        if (
            self._stt_desired
            and self.settings is not None
            and self.settings.provider.stt_compute == "gpu"
        ):
            # GPU settle delay — old backend's VRAM must be freed
            # before the new one allocates, or CUDA/Vulkan may OOM.
            await asyncio.sleep(0.5)
        if self._stt_desired:
            await self._ensure_stt_switch()

    async def _run_stt_switch(self) -> None:
        """STT switch state machine.
        """
        if self._stt_switch_lock is None:
            self._stt_switch_lock = asyncio.Lock()
        async with self._stt_switch_lock:
            # Snapshot _stt_desired here; re-check at loop bottom.
            # If user toggles STT while this iteration runs async work
            # (stop/start mic, rebuild provider), the loop re-iterates
            # with the new value instead of returning stale state.
            while True:
                desired = self._stt_desired
                restart = self._stt_restart_requested
                self._stt_restart_requested = False

                if not desired:
                    if self.stop_mic_loop is not None:
                        await self.stop_mic_loop()
                    if self.hub is not None:
                        self._cancel_stt_idle_release()
                        if (
                            not self._is_stopping
                            and self.settings is not None
                            and self.settings.provider.stt in LOCAL_STT_PROVIDERS
                        ):
                            from puripuly_heart.core.runtime.local_qwen_lifecycle import (
                                LOCAL_QWEN_IDLE_RELEASE_SECONDS,
                            )
                            # Delayed release: keeps the STT backend alive briefly
                            # so rapid on→off→on doesn't re-download the model.
                            self._stt_idle_release_task = asyncio.create_task(
                                self._stt_idle_release_after(LOCAL_QWEN_IDLE_RELEASE_SECONDS)
                            )
                        else:
                            await self.hub.close_stt()
                else:
                    self._cancel_stt_idle_release()
                    if restart:
                        if self.stop_mic_loop is not None:
                            await self.stop_mic_loop()
                        await self.hub.close_stt()
                    if (
                        self.local_stt_manager is not None
                        and not await self.local_stt_manager.ensure_ready(self.settings)
                    ):
                        break
                    if self.start_mic_loop is not None:
                        await self.start_mic_loop()
                    if (
                        self.hub is not None
                        and self.settings.provider.stt not in LOCAL_STT_PROVIDERS
                    ):
                        await self.hub.warmup_stt()

                if desired == self._stt_desired and not self._stt_restart_requested:
                    break

    def _cancel_stt_idle_release(self) -> None:
        task = self._stt_idle_release_task
        self._stt_idle_release_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _stt_idle_release_after(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if self.hub is not None:
            if self.log_basic is not None:
                self.log_basic(f"[STT] Idle release after {delay:.0f}s — closing backend")
            await self.hub.reset_stt_for_idle()

    @property
    def stt_desired(self) -> bool:
        return self._stt_desired

    @stt_desired.setter
    def stt_desired(self, value: bool) -> None:
        self._stt_desired = value
