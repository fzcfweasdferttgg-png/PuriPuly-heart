"""DiagnosticsService — owns audio diagnostics state and fault profile lifecycle.

Extracted from DiagnosticsManagerMixin.  No Flet dependency — uses asyncio
for scheduling and callbacks for UI interactions.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from puripuly_heart.domain.providers import STTProviderName
from puripuly_heart.domain.events import FinalTranscriptSuppressedNotification
from puripuly_heart.app.services.diagnostics_service import (
    is_detailed_audio_diag_enabled as _is_detailed_audio_diag_enabled_impl,
    is_debug_audio_fault_allowed as _is_debug_audio_fault_allowed_impl,
)

if TYPE_CHECKING:
    from puripuly_heart.core.audio.source import AudioSource

logger = logging.getLogger(__name__)

LOCAL_QWEN_HALLUCINATION_GUIDANCE_TRIGGER_COUNT = 2


@dataclass
class DiagnosticsService:
    """Audio diagnostics state and fault profile lifecycle.

    Owns all diagnostic state previously held by DiagnosticsManagerMixin.
    Interacts with the host via callbacks — no direct Flet or app dependency.
    """

    # --- Callbacks (injected by host) ---
    is_debug_ui_preview: Callable[[], bool] = lambda: False
    show_hallucination_dialog: Callable[[], None] | None = None
    set_stt_desired: Callable[[bool], None] | None = None
    set_dash_stt_enabled: Callable[[bool], None] | None = None
    log_basic: Callable[[str], None] | None = None
    log_detailed: Callable[[str, BaseException | None], bool] | None = None
    log_error: Callable[[str], None] | None = None
    runtime_logging: object | None = None

    # --- State (owned by service) ---
    _debug_capture_fault_profile: str = field(init=False, default="none")
    _debug_stt_fault_profile: str = field(init=False, default="none")
    _local_qwen_hallucination_detection_count: int = field(init=False, default=0)
    _hallucination_count_lock: threading.Lock = field(init=False, default_factory=threading.Lock, repr=False)
    _local_qwen_hallucination_modal_shown: bool = field(init=False, default=False)

    # --- Internal logging helpers ---

    def _emit_log_detailed(
        self,
        message: str,
        *,
        level: int = logging.INFO,
        exception: BaseException | None = None,
    ) -> bool:
        if self.log_detailed is not None:
            return self.log_detailed(message, exception)
        return False

    def _emit_log_basic(self, message: str) -> None:
        if self.log_basic is not None:
            self.log_basic(message)

    def _emit_log_error(self, message: str) -> None:
        if self.log_error is not None:
            self.log_error(message)

    # --- Properties ---

    @property
    def debug_capture_fault_profile(self) -> str:
        return self._debug_capture_fault_profile

    @property
    def debug_stt_fault_profile(self) -> str:
        return self._debug_stt_fault_profile

    # --- Diagnostics checks ---

    def debug_audio_fault_allowed(self) -> bool:
        return _is_debug_audio_fault_allowed_impl(self.is_debug_ui_preview())

    def detailed_audio_diag_enabled(self) -> bool:
        return _is_detailed_audio_diag_enabled_impl(self.runtime_logging)

    # --- Terminal failure handler ---

    async def on_self_terminal_failure(self, exc: Exception) -> None:
        if self.set_stt_desired is not None:
            self.set_stt_desired(False)
        if self.set_dash_stt_enabled is not None:
            self.set_dash_stt_enabled(False)
        self._emit_log_error(f"[STT] Terminal failure: {exc}")

    # --- Suppressed transcript handler ---

    # Threading: called from STT callback thread, not Flet UI thread
    def on_final_transcript_suppressed(
        self,
        notification: FinalTranscriptSuppressedNotification,
    ) -> None:
        self._emit_log_detailed(
            "[STT][SuppressedFinalNotification] "
            f"provider={notification.stt_provider_name.value} "
            f"channel={notification.channel} "
            f"utterance_id={str(notification.utterance_id)[:8]}"
        )
        if notification.stt_provider_name in (
            STTProviderName.LOCAL_QWEN,
            STTProviderName.LOCAL_QWEN_17B,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        ):
            self._record_local_qwen_hallucination_guidance_detection(notification)

    # State machine: count < threshold → wait; count >= threshold AND not shown → show; count >= threshold AND shown → no-op
    def _record_local_qwen_hallucination_guidance_detection(
        self,
        notification: FinalTranscriptSuppressedNotification,
    ) -> None:
        with self._hallucination_count_lock:
            self._local_qwen_hallucination_detection_count += 1
            count = self._local_qwen_hallucination_detection_count
        self._emit_log_detailed(
            "[STT][SuppressedFinalNotification] "
            f"local_qwen_guidance count={count} "
            f"channel={notification.channel} "
            f"modal_shown={self._local_qwen_hallucination_modal_shown}"
        )
        if count < LOCAL_QWEN_HALLUCINATION_GUIDANCE_TRIGGER_COUNT:
            return
        if self._local_qwen_hallucination_modal_shown:
            return

        if self.show_hallucination_dialog is None or not callable(self.show_hallucination_dialog):
            self._emit_log_detailed(
                "[STT][SuppressedFinalNotification] "
                f"local_qwen_guidance count={count} guidance_modal=unavailable"
            )
            return

        self._local_qwen_hallucination_modal_shown = True
        self.show_hallucination_dialog()

    # --- Fault profile cycling ---

    def cycle_debug_capture_fault_profile(self) -> str:
        if not self.debug_audio_fault_allowed():
            return "none"

        from puripuly_heart.core.audio.diagnostics import (
            EXPECTED_FAULT_SIGNATURES,
            AudioFaultProfile,
        )

        profiles = [
            AudioFaultProfile.NONE,
            AudioFaultProfile.CAPTURE_SILENT_FIRST_CHANNEL,
            AudioFaultProfile.CAPTURE_ATTENUATE_40DB,
            AudioFaultProfile.CAPTURE_NEAR_SILENCE_NOISE,
            AudioFaultProfile.CAPTURE_BUFFER_DROPOUTS,
        ]
        current = AudioFaultProfile(self._debug_capture_fault_profile)
        next_profile = profiles[(profiles.index(current) + 1) % len(profiles)]
        self._debug_capture_fault_profile = next_profile.value
        self._emit_log_detailed(
            "[AudioDiag][DebugFault] "
            f"capture_profile={next_profile.value} "
            "expected_signature="
            f"{EXPECTED_FAULT_SIGNATURES.get(next_profile.value, 'none')}"
        )
        return self._debug_capture_fault_profile

    def cycle_debug_stt_fault_profile(self) -> str:
        if not self.debug_audio_fault_allowed():
            return "none"

        from puripuly_heart.core.audio.diagnostics import (
            EXPECTED_FAULT_SIGNATURES,
            AudioFaultProfile,
        )

        profiles = [AudioFaultProfile.NONE, AudioFaultProfile.STT_INPUT_LOW_SNR_VAD_PASS]
        current = AudioFaultProfile(self._debug_stt_fault_profile)
        next_profile = profiles[(profiles.index(current) + 1) % len(profiles)]
        self._debug_stt_fault_profile = next_profile.value
        self._emit_log_detailed(
            "[AudioDiag][DebugFault] "
            f"stt_profile={next_profile.value} "
            "expected_signature="
            f"{EXPECTED_FAULT_SIGNATURES.get(next_profile.value, 'none')}"
        )
        return self._debug_stt_fault_profile

    def clear_debug_audio_fault_profiles(self) -> None:
        self._debug_capture_fault_profile = "none"
        self._debug_stt_fault_profile = "none"
        self._emit_log_detailed("[AudioDiag][DebugFault] capture_profile=none stt_profile=none")

    # --- Diagnostic audio source wrapper ---

    def wrap_diagnostic_audio_source(
        self,
        source: AudioSource,
        *,
        channel_label: str,
    ) -> AudioSource:
        from puripuly_heart.core.audio.diagnostics import AudioFaultProfile, DiagnosticAudioSource

        def extra_fields() -> dict[str, object]:
            return {
                "queue_drops": getattr(source, "queue_drop_count", 0),
                "callback_statuses": getattr(source, "callback_status_count", 0),
                "last_callback_status": getattr(source, "last_callback_status", None),
                "resolved_device_name": getattr(source, "resolved_device_name", None),
                "resolved_device_index": getattr(source, "resolved_device_index", None),
                "resolved_channels": getattr(source, "resolved_channels", None),
                "actual_sample_rate_hz": getattr(source, "actual_sample_rate_hz", None),
                "used_default_fallback": getattr(source, "used_default_fallback", None),
            }

        return DiagnosticAudioSource(
            source=source,
            channel_label=channel_label,
            is_detailed_enabled=self.detailed_audio_diag_enabled,
            log_detailed=lambda message: self._emit_log_detailed(message),
            fault_profile_provider=lambda: (
                self._debug_capture_fault_profile
                if self.debug_audio_fault_allowed()
                else AudioFaultProfile.NONE.value
            ),
            extra_fields_provider=extra_fields,
        )

    # --- Audio environment snapshot ---

    def schedule_audio_environment_snapshot(
        self,
        page_run_task: Callable | None = None,
    ) -> None:
        async def _task() -> None:
            await self._log_audio_environment_snapshot_async()

        if callable(page_run_task):
            try:
                page_run_task(_task)
                return
            except Exception as exc:
                self._emit_log_detailed(
                    "[AudioDiag][Snapshot] failed to schedule via page.run_task",
                    exception=exc,
                )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._emit_log_detailed(
                "[AudioDiag][Snapshot] skipped reason=no_running_loop",
            )
            return

        task_coro = _task()
        try:
            loop.create_task(task_coro)
        except Exception as exc:
            task_coro.close()
            self._emit_log_detailed(
                "[AudioDiag][Snapshot] skipped reason=create_task_failed",
                exception=exc,
            )

    async def _log_audio_environment_snapshot_async(self) -> None:
        from puripuly_heart.core.audio.diagnostics import (
            collect_pyaudiowpatch_snapshot_lines,
            collect_sounddevice_snapshot_lines,
        )

        sounddevice_lines, loopback_lines = await asyncio.gather(
            asyncio.to_thread(collect_sounddevice_snapshot_lines),
            asyncio.to_thread(collect_pyaudiowpatch_snapshot_lines),
        )
        for line in sounddevice_lines:
            self._emit_log_detailed(line)
        for line in loopback_lines:
            self._emit_log_detailed(line)
