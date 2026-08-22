"""Peer STT channel lifecycle — start/stop/restart with generation guard.

Manages the peer audio pipeline: STT provider + audio source + VAD engine.
Uses a **generation counter** pattern to prevent stale async operations
from corrupting current state:

1. apply_policy() increments _generation on every state transition
2. Every async step in _start_generation checks _is_superseded(generation)
3. If superseded → close resources silently, don't update state

This prevents race conditions when settings change rapidly (e.g. user
toggles peer translation on/off quickly while STT is still initializing).

Called by controller.py and peer_runtime_manager.py.
"""

from __future__ import annotations

import asyncio
import inspect
import logging

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from puripuly_heart.domain.peer_types import ResolvedPeerSTTConfig, PeerChannelRuntimeState, PeerRuntimeConfig
from puripuly_heart.core.clock import Clock
from puripuly_heart.core.pipeline.pipeline import Pipeline
from puripuly_heart.ports.peer import SpeechChannelRuntime

if TYPE_CHECKING:
    from puripuly_heart.ports.audio import AudioSource
    from puripuly_heart.ports.hub import STTProvider
    from puripuly_heart.ports.vad import VadEngine

__all__ = [
    "PeerChannelRuntimeState",
    "PeerRuntimeConfig",
    "SpeechChannelRuntime",
    "PeerChannelRuntime",
]


@dataclass(slots=True)
class _PeerHubVadSink:
    """Adapter: routes peer VAD events to Pipeline.handle_peer_vad_event()
    instead of handle_vad_event().  Passed to run_audio_loop as the sink."""
    hub: Pipeline

    async def handle_vad_event(self, event) -> None:  # noqa: ANN001
        await self.hub.handle_peer_vad_event(event)


class PeerChannelRuntime:
    def __init__(
        self,
        *,
        hub: Pipeline,
        clock: Clock,
        stt_factory: Callable[
            [PeerRuntimeConfig, Callable[[Exception], Awaitable[None]]],
            Awaitable[STTProvider] | STTProvider,
        ],
        source_factory: Callable[[PeerRuntimeConfig], AudioSource],
        vad_factory: Callable[[PeerRuntimeConfig, Path], VadEngine],
        vad_model_resolver: Callable[[], Path],
        run_audio_loop: Callable[..., Awaitable[None]],
    ) -> None:
        self.hub = hub
        self.clock = clock
        self._stt_factory = stt_factory
        self._source_factory = source_factory
        self._vad_factory = vad_factory
        self._vad_model_resolver = vad_model_resolver
        self._run_audio_loop = run_audio_loop

        self._config: PeerRuntimeConfig | None = None
        self._stt: object | None = None
        self._audio_source: object | None = None
        self._vad: object | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._signature: tuple[object, ...] | None = None
        self._state = PeerChannelRuntimeState.STOPPED
        self._generation = 0
        self._desired_active = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> PeerChannelRuntimeState:
        return self._state

    @property
    def current_signature(self) -> object | None:
        return self._signature

    async def apply_policy(self, *, config: PeerRuntimeConfig, desired_active: bool) -> None:
        # Decision tree under lock:
        # 1. No-op: already running with same config → just update config
        # 2. Stop: desired_active=False → teardown
        # 3. Restart: config changed or not running → start new generation
        async with self._lock:
            if (
                desired_active
                and self._desired_active
                and self._state == PeerChannelRuntimeState.RUNNING
                and self._signature == config.runtime_signature
            ):
                self._config = config
                return
            self._generation += 1
            generation = self._generation
            self._config = config
            self._desired_active = desired_active
            if not desired_active:
                self._state = PeerChannelRuntimeState.STOPPING
            else:
                self._state = PeerChannelRuntimeState.STARTING

        if not desired_active:
            await self._teardown_resources(target_state=PeerChannelRuntimeState.STOPPED)
            return

        await self._start_generation(generation, config)

    async def warmup(self) -> None:
        async with self._lock:
            stt = self._stt
            if (
                self._desired_active
                and stt is not None
                and self._state == PeerChannelRuntimeState.RUNNING
                and hasattr(stt, "warmup")
            ):
                await stt.warmup()

    async def close(self) -> None:
        async with self._lock:
            self._generation += 1
            self._desired_active = False
            self._state = PeerChannelRuntimeState.STOPPING
        await self._teardown_resources(target_state=PeerChannelRuntimeState.STOPPED)

    async def _start_generation(self, generation: int, config: PeerRuntimeConfig) -> None:
        # Resource creation lifecycle: STT → source → VAD → loop.
        # Each step checks _is_superseded to bail early if a newer
        # generation was requested while we were creating resources.
        logger.info("[PeerChannel] _start_generation: generation=%d", generation)
        try:
            stt = self._stt_factory(
                config,
                lambda exc, *, _generation=generation: self._on_terminal_stt_failure(
                    exc, generation=_generation
                ),
            )
            if inspect.isawaitable(stt):
                stt = await stt
        except Exception:
            await self._mark_faulted_if_current(generation, detach_provider=False)
            return

        if self._is_superseded(generation):
            logger.info("[PeerChannel] _start_generation: superseded after STT creation")
            await self._close_if_possible(stt)
            return
        logger.info("[PeerChannel] _start_generation: STT created, creating source+VAD")

        source = None
        try:
            source = self._source_factory(config)
            model_path = self._vad_model_resolver()
            vad = self._vad_factory(config, model_path)
        except Exception as exc:
            logger.error("[PeerChannel] _start_generation: source/VAD creation failed: %s", exc, exc_info=exc)
            await self._close_if_possible(source)
            await self._close_if_possible(stt)
            await self._mark_faulted_if_current(generation, detach_provider=True)
            return

        if self._is_superseded(generation):
            logger.info("[PeerChannel] _start_generation: superseded after source/VAD creation")
            await self._close_if_possible(source)
            await self._close_if_possible(stt)
            return
        logger.info("[PeerChannel] _start_generation: source+VAD created, replacing provider")

        loop_to_cancel = None
        source_to_close = None
        old_stt = None
        async with self._lock:
            if self._is_superseded(generation):
                pass
            else:
                loop_to_cancel = self._loop_task
                source_to_close = self._audio_source
                old_stt = self._stt
                self._loop_task = None
                self._audio_source = None
                self._vad = None
                self._stt = None
        if self._is_superseded(generation):
            await self._close_if_possible(source)
            await self._close_if_possible(stt)
            return

        await self._cancel_loop(loop_to_cancel)
        await self._close_if_possible(source_to_close)
        if old_stt is not None:
            await self.hub.replace_peer_stt_provider(None)

        await self.hub.replace_peer_stt_provider(stt)
        if self._is_superseded(generation):
            await self._close_if_possible(source)
            await self._close_peer_provider_if_current(stt)
            return

        loop_task = asyncio.create_task(
            self._run_peer_loop_guarded(
                source=source,
                vad=vad,
                target_sample_rate_hz=config.backend.sample_rate_hz,
                generation=generation,
            )
        )
        async with self._lock:
            if self._is_superseded(generation):
                logger.info("[PeerChannel] _start_generation: superseded before lock, cancelling loop")
                loop_task.cancel()
            else:
                self._stt = stt
                self._audio_source = source
                self._vad = vad
                self._loop_task = loop_task
                self._signature = config.runtime_signature
                self._state = PeerChannelRuntimeState.RUNNING
                logger.info("[PeerChannel] _start_generation: RUNNING, loop task created")

        if self._is_superseded(generation):
            await asyncio.gather(loop_task, return_exceptions=True)
            await self._close_peer_provider_if_current(stt)
            await self._close_if_possible(source)

    async def _run_peer_loop_guarded(
        self,
        *,
        source: object,
        vad: object,
        target_sample_rate_hz: int,
        generation: int,
    ) -> None:
        logger.info("[PeerChannel] loop starting: target_rate=%d generation=%d", target_sample_rate_hz, generation)
        try:
            await self._run_audio_loop(
                source=source,
                vad=vad,
                sink=_PeerHubVadSink(hub=self.hub),
                target_sample_rate_hz=target_sample_rate_hz,
            )
        except asyncio.CancelledError:
            logger.info("[PeerChannel] loop cancelled: generation=%d", generation)
            raise
        except Exception as exc:
            logger.error("[PeerChannel] loop failed: %s", exc, exc_info=exc)
            await self._on_runtime_failure(exc, generation=generation)
        else:
            logger.info("[PeerChannel] loop exited normally: generation=%d", generation)

    async def _on_runtime_failure(self, exc: Exception, *, generation: int) -> None:
        logger.error("[PeerChannel] runtime failure: %s", exc, exc_info=exc)
        await self._mark_faulted_if_current(generation, detach_provider=True)

    async def _on_terminal_stt_failure(
        self,
        exc: Exception,
        *,
        generation: int | None = None,
    ) -> None:
        logger.error("[PeerChannel] terminal STT failure: %s", exc, exc_info=exc)
        target_generation = self._generation if generation is None else generation
        async with self._lock:
            if self._is_superseded(target_generation):
                return
            if (
                self._desired_active
                and self._state == PeerChannelRuntimeState.RUNNING
                and self._stt is not None
            ):
                return
        await self._mark_faulted_if_current(target_generation, detach_provider=True)

    async def _mark_faulted_if_current(self, generation: int, *, detach_provider: bool) -> None:
        if self._is_superseded(generation):
            return
        await self._teardown_resources(target_state=PeerChannelRuntimeState.FAULTED)
        if not detach_provider:
            return
        if getattr(self.hub, "peer_stt", None) is not None:
            # Detach faulted STT from hub — triggers full lifecycle reset
            # (stop event loop, reset runtime state, clear turns, close provider).
            await self.hub.replace_peer_stt_provider(None)

    async def _teardown_resources(self, *, target_state: PeerChannelRuntimeState) -> None:
        async with self._lock:
            loop_task = self._loop_task
            source = self._audio_source
            stt = self._stt
            self._loop_task = None
            self._audio_source = None
            self._vad = None
            self._stt = None
            self._signature = None

        await self._cancel_loop(loop_task)
        await self._close_if_possible(source)
        await self._close_peer_provider_if_current(stt)

        async with self._lock:
            if not self._desired_active and target_state == PeerChannelRuntimeState.FAULTED:
                self._state = PeerChannelRuntimeState.STOPPED
            else:
                self._state = target_state

    async def _cancel_loop(self, loop_task: asyncio.Task[None] | None) -> None:
        if loop_task is None:
            return
        if loop_task is asyncio.current_task():
            return
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)

    async def _close_if_possible(self, resource: object | None) -> None:
        if resource is None or not hasattr(resource, "close"):
            return
        result = resource.close()
        if inspect.isawaitable(result):
            await result

    async def _close_peer_provider_if_current(self, stt: object | None) -> None:
        if stt is None:
            return
        if getattr(self.hub, "peer_stt", None) is stt:
            await self.hub.replace_peer_stt_provider(None)
            return
        await self._close_if_possible(stt)

    def _is_superseded(self, generation: int) -> bool:
        # Generation guard: returns True if a newer config was applied
        # or if peer was deactivated.  Used after every await point
        # to detect stale operations that should be abandoned.
        return generation != self._generation or not self._desired_active
