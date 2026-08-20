"""STT session manager — handles STT backend lifecycle, session management, audio routing.

ManagedSTTProvider manages the STT session lifecycle:
- DISCONNECTED → CONNECTING → STREAMING → DRAINING → DISCONNECTED
- Connection retries with exponential backoff
- Reset timer with retry logic (reschedule on failure, terminal on max retries)
- Idle release via clear_idle_state() + reset_for_idle() (public API for ui/)

AI-STATE-MACHINE: Valid transitions only:
  DISCONNECTED → CONNECTING → STREAMING → DRAINING → DISCONNECTED
  CONNECTING → DISCONNECTED (on connection failure)
  Any → DISCONNECTED (on terminal failure via _handle_terminal_session_failure)
  STREAMING → STREAMING (session replacement — bridging/reconnect, no state change)

AI-TRIANGLE-COUPLING: Three fields are coupled and must be kept in sync:
  _active_utterance_id ↔ _pending (PendingUtteranceTracker) ↔ _audio_ring (RingBufferF32)
  - _active_utterance_id tracks the CURRENTLY speaking utterance
  - _pending tracks utterances between speech-end and final-transcript arrival
  - _audio_ring holds bridging audio for session reset
  CRITICAL: In _on_speech_end, _active_utterance_id is cleared BEFORE _pending.append().
  This creates a race window where concurrent _consume_session_events sees both as None.
  Fix is to swap the order — see _on_speech_end comment.

AI-DRAIN-SEMANTICS: _draining set contains background drain tasks.
  Each task auto-removes itself via done_callback (task.add_done_callback(self._draining.discard)).
  On close(), all remaining drain tasks are cancelled and awaited.
  If you add a new drain path, always add done_callback — otherwise task leaks in _draining.

AI-IMPORT-GRAPH: This is the HUB of the STT package. All 4 extracted modules are leaves.
  Circular dependency with stt_hallucination_filter.py resolved: FinalTranscriptSuppressedNotification
  moved to domain/events.py, SuppressionCallback Protocol defined in ports/stt.py.
  AudioFaultProfile import is for public API type hints, not internal use.

Key invariants:
- _draining set: tasks auto-remove via done_callback (no leak)
- STTLogSink: logs at DEBUG when runtime_logging is None (not discarded)
- _closing field removed: shutdown guard was never implemented
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable
from uuid import UUID

import numpy as np

from puripuly_heart.domain.providers import STTProviderName

logger = logging.getLogger(__name__)
MANAGED_STT_SAMPLE_RATE_HZ = 16000

from puripuly_heart.core.audio.diagnostics import AudioFaultProfile
from puripuly_heart.core.audio.format import float32_to_pcm16le_bytes
from puripuly_heart.core.audio.ring_buffer import RingBufferF32
from puripuly_heart.core.clock import Clock, SystemClock
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.ports.stt import (
    STTBackend,
    STTBackendFloat32Session,
    STTBackendSession,
    SuppressionCallback,
)
from puripuly_heart.core.stt.stt_hallucination_filter import (
    handle_suppressed_final_transcript,
    should_suppress_final_transcript,
)
from puripuly_heart.core.stt.stt_log_sink import STTLogSink
from puripuly_heart.core.stt.stt_audio_diagnostics import STTAudioDiagnostics
from puripuly_heart.core.stt.stt_pending_tracker import PendingUtteranceTracker
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart, VadEvent
from puripuly_heart.domain.events import (
    STTErrorEvent,
    STTEvent,
    STTFinalEvent,
    STTPartialEvent,
    STTSessionState,
    STTSessionStateEvent,
)
from puripuly_heart.domain.models import ChannelId, Transcript


@dataclass(slots=True)
class ManagedSTTProvider:
    backend: STTBackend
    sample_rate_hz: int
    stt_provider_name: STTProviderName | None = None
    channel: ChannelId = "self"
    clock: Clock = SystemClock()
    reset_deadline_s: float = 180.0
    drain_timeout_s: float = 1.5
    bridging_ms: int = 500
    finalize_grace_s: float = 0.2
    connect_attempts: int = 3
    connect_retry_base_s: float = 0.8
    connect_retry_max_s: float = 6.0
    reconnect_window_s: float = 20.0
    reset_max_retries: int = 3
    reset_retry_base_s: float = 60.0
    reset_retry_max_s: float = 300.0
    on_terminal_failure: Callable[[Exception], Awaitable[None] | None] | None = None
    on_final_transcript_suppressed: SuppressionCallback | None = None
    runtime_logging: SessionRuntimeLoggingService | None = None
    stt_input_fault_profile_provider: Callable[[], AudioFaultProfile | str | None] | None = None

    _state: STTSessionState = STTSessionState.DISCONNECTED
    _active_session: STTBackendSession | None = None
    _session_started_at: float | None = None
    _consumer_task: asyncio.Task[None] | None = None
    _draining: set[asyncio.Task[None]] = field(default_factory=set)
    _events: asyncio.Queue = field(default_factory=asyncio.Queue)

    _active_utterance_id: UUID | None = None
    # AI-TRIANGLE: One of three coupled fields. Set in _on_speech_start/_on_speech_chunk,
    # cleared in _on_speech_end. Read by _consume_session_events to correlate partial/final
    # transcripts. Must be transferred to _pending before clearing — see _on_speech_end.
    _audio_ring: RingBufferF32 | None = None
    _session_open_lock: asyncio.Lock = field(init=False, repr=False)
    _reset_timer: asyncio.Task[None] | None = None
    _last_speech_end_time: float | None = None
    _log: STTLogSink = field(init=False, repr=False)
    _diag: STTAudioDiagnostics = field(init=False, repr=False)
    _pending: PendingUtteranceTracker = field(init=False, repr=False)
    _reset_retry_count: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.channel not in ("self", "peer"):
            raise ValueError("channel must be 'self' or 'peer'")
        if self.stt_provider_name is not None and not isinstance(
            self.stt_provider_name,
            STTProviderName,
        ):
            self.stt_provider_name = STTProviderName(self.stt_provider_name)
        if self.sample_rate_hz != MANAGED_STT_SAMPLE_RATE_HZ:
            raise ValueError(f"sample_rate_hz must be {MANAGED_STT_SAMPLE_RATE_HZ}")
        if self.reset_deadline_s <= 0:
            raise ValueError("reset_deadline_s must be > 0")
        if self.drain_timeout_s <= 0:
            raise ValueError("drain_timeout_s must be > 0")
        if self.bridging_ms <= 0:
            raise ValueError("bridging_ms must be > 0")
        if self.connect_attempts <= 0:
            raise ValueError("connect_attempts must be > 0")
        if self.connect_retry_base_s <= 0:
            raise ValueError("connect_retry_base_s must be > 0")
        if self.connect_retry_max_s <= 0:
            raise ValueError("connect_retry_max_s must be > 0")

        self._log = STTLogSink(
            runtime_logging=self.runtime_logging,
            channel=self.channel,
        )
        self._diag = STTAudioDiagnostics(
            runtime_logging=self.runtime_logging,
            channel=self.channel,
            sample_rate_hz=self.sample_rate_hz,
            log_sink=self._log,
            stt_input_fault_profile_provider=self.stt_input_fault_profile_provider,
        )
        self._pending = PendingUtteranceTracker(
            clock=self.clock,
            reconnect_window_s=self.reconnect_window_s,
            channel=self.channel,
            log_sink=self._log,
        )
        self._session_open_lock = asyncio.Lock()
        capacity_samples = int(self.sample_rate_hz * (self.bridging_ms / 1000.0))
        self._audio_ring = RingBufferF32(capacity_samples=capacity_samples)

    @property
    def state(self) -> STTSessionState:
        return self._state

    async def close(self) -> None:
        async with self._session_open_lock:
            await self._close_locked()

    async def _close_locked(self) -> None:
        await self._set_state(
            STTSessionState.DRAINING if self._active_session else STTSessionState.DISCONNECTED
        )

        if self._reset_timer:
            self._reset_timer.cancel()
            self._reset_timer = None

        if self._active_session and self._consumer_task:
            await self._drain_and_close(
                self._active_session, self._consumer_task, allow_finalize=True
            )
        elif self._consumer_task:
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._consumer_task
        elif self._active_session:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._active_session.close()

        self._consumer_task = None
        self._active_session = None

        if self._draining:
            for task in list(self._draining):
                task.cancel()
            await asyncio.gather(*self._draining, return_exceptions=True)
            self._draining.clear()

        self._session_started_at = None
        await self._set_state(STTSessionState.DISCONNECTED)

        with contextlib.suppress(Exception):
            await self.backend.close()

    async def handle_vad_event(self, event: VadEvent) -> None:
        if isinstance(event, SpeechStart):
            await self._on_speech_start(event)
        elif isinstance(event, SpeechChunk):
            await self._on_speech_chunk(event)
        elif isinstance(event, SpeechEnd):
            await self._on_speech_end(event)
        else:
            raise TypeError(f"Unknown VadEvent: {type(event)}")

    def clear_idle_state(self) -> None:
        """Clear pending data and event queue for idle release."""
        self._pending.clear()
        if self._audio_ring is not None:
            self._audio_ring.clear()
        while True:
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def reset_for_idle(self) -> None:
        """Full idle release: clear state + close session."""
        self.clear_idle_state()
        await self.close()

    async def events(self) -> AsyncIterator[STTEvent]:
        while True:
            item = await self._events.get()
            yield item

    async def warmup(self) -> bool:
        """Pre-establish STT session for faster first response."""
        if await self._ensure_session():
            self._log.detailed("[STT] Session pre-warmed")
            return True
        return False

    async def _on_speech_start(self, event: SpeechStart) -> None:
        self._active_utterance_id = event.utterance_id
        self._diag.reset_for_utterance()

        if not await self._ensure_session():
            return

        await self._send_audio(event.pre_roll)
        await self._send_audio(event.chunk)

    async def _on_speech_chunk(self, event: SpeechChunk) -> None:
        self._active_utterance_id = event.utterance_id
        if not await self._ensure_session():
            return
        await self._send_audio(event.chunk)

    async def _on_speech_end(self, event: SpeechEnd) -> None:
        # AI-RACE-WINDOW: _active_utterance_id is cleared (line below) BEFORE _pending.append().
        # Between these two operations, _consume_session_events running in a parallel coroutine
        # can see _active_utterance_id=None AND _pending empty, causing the final transcript
        # to be silently dropped (utterance_id=None → continue).
        # SAFE FIX: swap order — call _pending.append() FIRST, then clear _active_utterance_id.
        # CURRENT: not fixed because the window is ~microseconds (no await between them) and
        # the consumer is blocked on `await session.events()`. Risk: very low in practice.
        if self._active_utterance_id == event.utterance_id:
            self._active_utterance_id = None
        self._last_speech_end_time = self.clock.now()

        # Delegate end-of-speech handling to the backend (silence + finalize etc.)
        if self._active_session is not None:
            ended_at = self.clock.now()
            self._pending.append(event.utterance_id, ended_at)
            self._log.detailed(
                "[STT] Speech end handling for id=%s (trailing_silence_ms=%s)",
                str(event.utterance_id)[:8],
                event.trailing_silence_ms
            )
            self._diag.emit_for_utterance(event.utterance_id, finalize=True)
            await self._active_session.on_speech_end(trailing_silence_ms=event.trailing_silence_ms)

    async def _send_audio(self, samples_f32: np.ndarray) -> None:
        samples_f32 = np.asarray(samples_f32, dtype=np.float32).reshape(-1)
        if samples_f32.size == 0:
            return
        samples_f32 = self._diag.process_input(samples_f32)
        self._audio_ring.append(samples_f32)  # type: ignore[union-attr]
        if self._active_session is None:
            raise RuntimeError("STT session is not active")
        await self._send_audio_to_session(self._active_session, samples_f32)

    async def _send_audio_to_session(
        self, session: STTBackendSession, samples_f32: np.ndarray
    ) -> None:
        if samples_f32.size == 0:
            return
        if isinstance(session, STTBackendFloat32Session):
            await session.send_audio_f32(samples_f32)
            return

        pcm = float32_to_pcm16le_bytes(samples_f32)
        if not pcm:
            return
        await session.send_audio(pcm)

    async def _ensure_session(self) -> bool:
        if self._active_session is not None:
            return True

        async with self._session_open_lock:
            if self._active_session is not None:
                return True
            return await self._open_session_locked()

    async def _open_session_locked(self) -> bool:
        await self._set_state(STTSessionState.CONNECTING)
        last_exc: Exception | None = None

        for attempt in range(1, self.connect_attempts + 1):
            # AI-RETRY: Connection retry with exponential backoff.
            # delay = min(connect_retry_base_s * 2^(attempt-1), connect_retry_max_s)
            # Default: 0.8s, 1.6s (capped at 6.0s) for 3 attempts.
            # On success: transition to STREAMING, start consumer task, schedule reset timer.
            # On all failures: transition to DISCONNECTED, emit STTErrorEvent.
            self._log.detailed(
                "[STT] Opening new session (attempt %s/%s)...",
                attempt,
                self.connect_attempts
            )
            try:
                session = await self.backend.open_session()
            except Exception as exc:
                last_exc = exc
                self._log.detailed(
                    "[STT] Failed to open session (attempt %s/%s): %s",
                    attempt,
                    self.connect_attempts,
                    exc,
                    level=logging.WARNING
                )
                if attempt < self.connect_attempts:
                    delay = min(
                        self.connect_retry_base_s * (2 ** (attempt - 1)),
                        self.connect_retry_max_s,
                    )
                    self._log.detailed(
                        "[STT] Retrying session in %.1fs",
                        delay
                    )
                    await asyncio.sleep(delay)
                    continue
                break
            else:
                self._active_session = session
                self._session_started_at = self.clock.now()
                self._consumer_task = asyncio.create_task(self._consume_session_events(session))
                self._schedule_reset_timer()
                await self._set_state(STTSessionState.STREAMING)
                self._log.log_session_connected(attempts=attempt)
                self._log.detailed(
                    "[STT] Session ready (reset_deadline=%ss)",
                    self.reset_deadline_s
                )
                return True

        reason = str(last_exc) if last_exc is not None else "unknown error"
        self._log.basic(
            "[STT] Failed to open session after %s attempts: %s",
            self.connect_attempts,
            reason,
            level=logging.ERROR
        )
        await self._set_state(STTSessionState.DISCONNECTED)
        await self._events.put(
            STTErrorEvent(
                f"Failed to open STT session after {self.connect_attempts} attempts: {reason}",
                channel=self.channel,
                runtime_log_handled=True,
            )
        )
        return False

    async def _reset_with_bridging(self) -> None:
        async with self._session_open_lock:
            await self._reset_with_bridging_locked()

    async def _reset_with_bridging_locked(self) -> None:
        old_session = self._active_session
        old_consumer = self._consumer_task

        bridging_audio = self._audio_ring.get_last_samples(self._audio_ring.capacity_samples)  # type: ignore[union-attr]
        bridging_ms = len(bridging_audio) / self.sample_rate_hz * 1000

        self._log.detailed(
            "[STT] Bridging buffered audio: %.0fms",
            bridging_ms
        )
        new_session = await self.backend.open_session()
        self._active_session = new_session
        self._session_started_at = self.clock.now()
        self._consumer_task = asyncio.create_task(
            self._consume_session_events(
                new_session,
            )
        )
        self._schedule_reset_timer()

        # AI-DUAL-CONSUMER: Two consumers are now active simultaneously — the old one
        # (still reading from old_session) and the new one (reading from new_session).
        # Both share _pending and _active_utterance_id. The old consumer may pop IDs
        # from _pending that the new consumer needs. Mitigated by drain_timeout_s
        # (1.5s) which bounds how long the old consumer runs.
        # This is a known limitation — fixing requires per-session pending queues.

        await self._set_state(STTSessionState.STREAMING)

        await self._send_audio_to_session(new_session, bridging_audio)
        self._log.basic("[STT] Session reset while speaking; bridged to a new session")

        if old_session and old_consumer:
            self._log.detailed(
                "[STT] Draining replaced session in background"
            )
            task = asyncio.create_task(
                self._drain_and_close(old_session, old_consumer, allow_finalize=False)
            )
            task.add_done_callback(self._draining.discard)
            self._draining.add(task)

    async def _reset_with_reconnect(self) -> None:
        """Close current session and immediately open a new one.

        Used when the session limit is reached during silence but there was
        recent speech activity. Unlike bridging, no audio buffer is sent.
        """
        async with self._session_open_lock:
            await self._reset_with_reconnect_locked()

    async def _reset_with_reconnect_locked(self) -> None:
        if self._active_session is None or self._consumer_task is None:
            return

        elapsed = self.clock.now() - (self._last_speech_end_time or 0)
        self._log.detailed(
            f"[STT] RECONNECT: Session limit during silence, "
            f"last speech {elapsed:.1f}s ago, reconnecting..."
        )

        old_session = self._active_session
        old_consumer = self._consumer_task

        # Open new session
        try:
            new_session = await self.backend.open_session()
        except Exception as e:
            self._log.basic(
                f"[STT] Reconnect failed; closing until next speech: {e}",
                level=logging.ERROR
            )
            await self._reset_on_silence()
            return

        self._active_session = new_session
        self._session_started_at = self.clock.now()
        self._consumer_task = asyncio.create_task(
            self._consume_session_events(
                new_session,
            )
        )
        self._schedule_reset_timer()

        await self._set_state(STTSessionState.STREAMING)
        self._log.basic("[STT] Session reconnected after recent speech")

        # Drain old session with finalize (unlike bridging)
        task = asyncio.create_task(
            self._drain_and_close(old_session, old_consumer, allow_finalize=True)
        )
        task.add_done_callback(self._draining.discard)
        self._draining.add(task)

    async def _reset_on_silence(self) -> None:
        if self._active_session is None or self._consumer_task is None:
            return

        old_session = self._active_session
        old_consumer = self._consumer_task
        self._active_session = None
        self._consumer_task = None
        self._session_started_at = None

        await self._set_state(STTSessionState.DRAINING)
        await self._drain_and_close(old_session, old_consumer, allow_finalize=True)
        await self._set_state(STTSessionState.DISCONNECTED)
        self._log.basic("[STT] Session closed after silence")

    async def _drain_and_close(
        self,
        session: STTBackendSession,
        consumer_task: asyncio.Task[None],
        *,
        allow_finalize: bool,
    ) -> None:
        self._log.detailed(
            f"[STT] DRAIN: Starting drain (timeout={self.drain_timeout_s}s)..."
        )
        if allow_finalize and self._should_finalize_before_stop():
            await self._finalize_before_stop(session)
        with contextlib.suppress(Exception):
            await session.stop()

        try:
            await asyncio.wait_for(consumer_task, timeout=self.drain_timeout_s)
            self._log.detailed(
                "[STT] DRAIN: Consumer task completed normally"
            )
        except asyncio.TimeoutError:
            self._log.detailed(
                f"[STT] DRAIN: Timeout after {self.drain_timeout_s}s, cancelling consumer task",
                level=logging.WARNING
            )
            # AI-TIMEOUT: Consumer task cancelled on timeout. This is the safety net that
            # prevents drain from hanging indefinitely. The consumer task's CancelledError
            # is caught by its except block which re-raises it.
            consumer_task.cancel()
            with contextlib.suppress(Exception):
                await consumer_task

        with contextlib.suppress(Exception):
            await session.close()
        self._log.detailed("[STT] DRAIN: Session closed")

    def _should_finalize_before_stop(self) -> bool:
        return self._active_utterance_id is not None or self._pending.has_pending()

    async def _finalize_before_stop(self, session: STTBackendSession) -> None:
        if self._active_utterance_id is not None:
            with contextlib.suppress(Exception):
                await session.on_speech_end()
        if self.finalize_grace_s <= 0:
            return
        await asyncio.sleep(self.finalize_grace_s)

    def _build_transcript(
        self,
        *,
        utterance_id: UUID,
        text: str,
        is_final: bool,
        created_at: float,
    ) -> Transcript:
        return Transcript(
            utterance_id=utterance_id,
            text=text,
            is_final=is_final,
            created_at=created_at,
            channel=self.channel,
        )

    async def _consume_session_events(
        self,
        session: STTBackendSession,
    ) -> None:
        # AI-CONSUMER: This is the main event loop for a session. Runs as an asyncio.Task.
        # Reads raw events from session.events(), correlates them with utterance IDs via
        # _pending queue and _active_utterance_id, applies hallucination filter, and emits
        # STTEvent objects to the output queue.
        #
        # AI-CONSUMER-FINAL: When is_final=True, the event is correlated via:
        #   1. drop_stale() — remove timed-out pending IDs
        #   2. peek_first() or _active_utterance_id — candidate assignment
        #   3. pop_next_final() — FIFO extraction from pending queue
        #   If pop_next_final() returns None, falls back to _active_utterance_id.
        #   If both are None → continue (silent drop). This can happen during the race
        #   window in _on_speech_end (see AI-RACE-WINDOW comment there).
        try:
            async for ev in session.events():
                if ev.is_final:
                    self._pending.drop_stale(has_active_utterance=self._active_utterance_id is not None)
                    pending_queue_size_before = self._pending.size()
                    assigned_utterance_id = self._pending.peek_first() or self._active_utterance_id
                    self._pending.check_finalization_lag(
                        utterance_id=assigned_utterance_id,
                        pending_queue_size_before=pending_queue_size_before,
                        text_len=len(ev.text),
                    )
                    utterance_id = self._pending.pop_next_final() or self._active_utterance_id
                else:
                    utterance_id = self._active_utterance_id or self._pending.peek_first()
                if utterance_id is None:
                    continue
                if ev.is_final and not ev.text.strip():
                    continue
                if ev.is_final and should_suppress_final_transcript(self.stt_provider_name, ev.text):
                    await handle_suppressed_final_transcript(
                        provider_name=self.stt_provider_name,
                        channel=self.channel,
                        utterance_id=utterance_id,
                        on_callback=self.on_final_transcript_suppressed,
                        log_sink=self._log,
                    )
                    continue
                created_at = self.clock.now()
                transcript = self._build_transcript(
                    utterance_id=utterance_id,
                    text=ev.text,
                    is_final=ev.is_final,
                    created_at=created_at,
                )
                if ev.is_final:
                    await self._events.put(STTFinalEvent(utterance_id, transcript))
                else:
                    await self._events.put(STTPartialEvent(utterance_id, transcript))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_terminal_session_failure(session, exc)

    async def _handle_terminal_session_failure(
        self,
        session: STTBackendSession,
        exc: Exception,
    ) -> None:
        # AI-TERMINAL: This is the LAST RESORT error handler. Called when _consume_session_events
        # catches an unexpected exception (not CancelledError). It:
        #   1. Checks if this is still the active session (prevents stale drain tasks from
        #      corrupting state after a session replacement)
        #   2. Clears ALL state: session, task, utterance, pending, timer
        #   3. Emits STTErrorEvent to the output queue
        #   4. Calls on_terminal_failure callback (async-safe via inspect.isawaitable)
        # After this, the provider is in DISCONNECTED state and will re-open on next speech.
        is_active_session = session is self._active_session
        if is_active_session:
            self._active_session = None
            self._consumer_task = None
            self._session_started_at = None
            self._active_utterance_id = None
            self._pending.clear()
            self._last_speech_end_time = None
            if self._reset_timer is not None:
                self._reset_timer.cancel()
                self._reset_timer = None
            await self._set_state(STTSessionState.DISCONNECTED)
            if self.on_terminal_failure is not None:
                maybe_awaitable = self.on_terminal_failure(exc)
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable

        with contextlib.suppress(Exception):
            await session.stop()
        with contextlib.suppress(Exception):
            await session.close()

        self._log.basic(
            "[STT] Session failed: %s",
            exc,
            level=logging.ERROR
        )
        await self._events.put(
            STTErrorEvent(
                f"STT session error: {exc}",
                channel=self.channel,
                runtime_log_handled=True,
            )
        )

    async def _set_state(self, state: STTSessionState) -> None:
        if self._state == state:
            return
        old_state = self._state
        self._state = state
        self._log.detailed(
            f"[STT] State: {old_state.name} -> {state.name}"
        )
        await self._events.put(STTSessionStateEvent(state, channel=self.channel))

    def _has_recent_speech(self) -> bool:
        """Check if speech ended recently within the reconnect window."""
        if self._last_speech_end_time is None:
            return False
        elapsed = self.clock.now() - self._last_speech_end_time
        return elapsed < self.reconnect_window_s

    def _schedule_reset_timer(self) -> None:
        """Schedule a timer to reset the session after reset_deadline_s."""
        if self._reset_timer:
            self._reset_timer.cancel()
        self._reset_retry_count = 0
        self._reset_timer = asyncio.create_task(self._reset_timer_task())

    async def _reset_timer_task(self) -> None:
        """Background task that resets the session when the deadline expires.

        Retry logic: on failure, reschedule with exponential backoff.
        After reset_max_retries failures, give up and trigger terminal failure.
        _schedule_reset_timer() resets _reset_retry_count on each new session.
        """
        try:
            # AI-RESET-TIMER: This timer fires after reset_deadline_s (default 180s).
            # Three strategies based on current state:
            #   1. _active_utterance_id is not None → _reset_with_bridging (speech in progress,
            #      bridge audio to new session)
            #   2. _has_recent_speech() → _reset_with_reconnect (silence but recent activity,
            #      reconnect without bridging)
            #   3. Neither → _reset_on_silence (full close, no bridging)
            # On failure: exponential backoff retry (reset_retry_base_s * 2^retry_count,
            # capped at reset_retry_max_s). After reset_max_retries failures → terminal failure.
            await asyncio.sleep(self.reset_deadline_s)
            if self._active_session is None:
                return
            self._log.detailed(
                f"[STT] Timer expired after {self.reset_deadline_s}s"
            )
            if self._active_utterance_id is not None:
                # Speaking: reset with bridging
                await self._reset_with_bridging()
            elif self._has_recent_speech():
                # Recent speech: reconnect immediately
                await self._reset_with_reconnect()
            else:
                # Silence: close session
                await self._reset_on_silence()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._reset_retry_count += 1
            if self._reset_retry_count >= self.reset_max_retries:
                logger.error(
                    "[STT] Reset failed %d times, giving up: %s",
                    self._reset_retry_count,
                    exc,
                )
                await self._handle_terminal_session_failure(self._active_session, exc)
                return
            delay = min(
                self.reset_retry_base_s * (2 ** (self._reset_retry_count - 1)),
                self.reset_retry_max_s,
            )
            logger.warning(
                "[STT] Reset failed: %s, retry %d/%d in %.1fs",
                exc,
                self._reset_retry_count,
                self.reset_max_retries,
                delay,
            )
            await asyncio.sleep(delay)
            self._schedule_reset_timer()
