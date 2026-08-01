from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from puripuly_heart.config.settings import STTProviderName
from puripuly_heart.core.stt.controller import FinalTranscriptSuppressedNotification
from puripuly_heart.core.runtime_logging import SessionLoggingMode

if TYPE_CHECKING:
    from puripuly_heart.core.audio.source import AudioSource

logger = logging.getLogger(__name__)

LOCAL_QWEN_HALLUCINATION_GUIDANCE_TRIGGER_COUNT = 2


class DiagnosticsManagerMixin:
    """Audio and STT diagnostics extracted from GuiController."""

    @property
    def debug_capture_fault_profile(self) -> str:
        return self._debug_capture_fault_profile

    @property
    def debug_stt_fault_profile(self) -> str:
        return self._debug_stt_fault_profile

    def _debug_audio_fault_allowed(self) -> bool:
        return bool(getattr(self.app, "debug_ui_preview", False))

    def _detailed_audio_diag_enabled(self) -> bool:
        return self.runtime_logging.mode is SessionLoggingMode.DETAILED

    async def _on_self_terminal_failure(self, exc: Exception) -> None:
        self._stt_desired = False
        dash = getattr(self.app, "view_dashboard", None)
        if dash is not None:
            dash.set_stt_enabled(False)
        self._log_error(f"[STT] Terminal failure: {exc}")

    def _on_final_transcript_suppressed(
        self,
        notification: FinalTranscriptSuppressedNotification,
    ) -> None:
        self.log_detailed(
            "[STT][SuppressedFinalNotification] "
            f"provider={notification.stt_provider_name.value} "
            f"channel={notification.channel} "
            f"utterance_id={str(notification.utterance_id)[:8]}"
        )
        if notification.stt_provider_name in (STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B, STTProviderName.LOCAL_QWEN3_ASR_GGUF, STTProviderName.LOCAL_QWEN_17B_GGUF):
            self._record_local_qwen_hallucination_guidance_detection(notification)

    def _record_local_qwen_hallucination_guidance_detection(
        self,
        notification: FinalTranscriptSuppressedNotification,
    ) -> None:
        self._local_qwen_hallucination_detection_count += 1
        count = self._local_qwen_hallucination_detection_count
        self.log_detailed(
            "[STT][SuppressedFinalNotification] "
            f"local_qwen_guidance count={count} "
            f"channel={notification.channel} "
            f"modal_shown={self._local_qwen_hallucination_modal_shown}"
        )
        if count < LOCAL_QWEN_HALLUCINATION_GUIDANCE_TRIGGER_COUNT:
            return
        if self._local_qwen_hallucination_modal_shown:
            return

        show_dialog = getattr(self.app, "show_local_qwen_hallucination_dialog", None)
        if not callable(show_dialog):
            self.log_detailed(
                "[STT][SuppressedFinalNotification] "
                f"local_qwen_guidance count={count} guidance_modal=unavailable"
            )
            return

        self._local_qwen_hallucination_modal_shown = True
        show_dialog()

    def cycle_debug_capture_fault_profile(self) -> str:
        if not self._debug_audio_fault_allowed():
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
        self.log_detailed(
            "[AudioDiag][DebugFault] "
            f"capture_profile={next_profile.value} "
            "expected_signature="
            f"{EXPECTED_FAULT_SIGNATURES.get(next_profile.value, 'none')}"
        )
        return self._debug_capture_fault_profile

    def cycle_debug_stt_fault_profile(self) -> str:
        if not self._debug_audio_fault_allowed():
            return "none"

        from puripuly_heart.core.audio.diagnostics import (
            EXPECTED_FAULT_SIGNATURES,
            AudioFaultProfile,
        )

        profiles = [AudioFaultProfile.NONE, AudioFaultProfile.STT_INPUT_LOW_SNR_VAD_PASS]
        current = AudioFaultProfile(self._debug_stt_fault_profile)
        next_profile = profiles[(profiles.index(current) + 1) % len(profiles)]
        self._debug_stt_fault_profile = next_profile.value
        self.log_detailed(
            "[AudioDiag][DebugFault] "
            f"stt_profile={next_profile.value} "
            "expected_signature="
            f"{EXPECTED_FAULT_SIGNATURES.get(next_profile.value, 'none')}"
        )
        return self._debug_stt_fault_profile

    def clear_debug_audio_fault_profiles(self) -> None:
        self._debug_capture_fault_profile = "none"
        self._debug_stt_fault_profile = "none"
        self.log_detailed("[AudioDiag][DebugFault] capture_profile=none stt_profile=none")

    def _wrap_diagnostic_audio_source(
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
            is_detailed_enabled=self._detailed_audio_diag_enabled,
            log_detailed=lambda message: self.log_detailed(message),
            fault_profile_provider=lambda: (
                self._debug_capture_fault_profile
                if self._debug_audio_fault_allowed()
                else AudioFaultProfile.NONE.value
            ),
            extra_fields_provider=extra_fields,
        )

    def _schedule_audio_environment_snapshot(self) -> None:
        async def _task() -> None:
            await self._log_audio_environment_snapshot_async()

        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):
            try:
                run_task(_task)
                return
            except Exception as exc:
                self.log_detailed(
                    "[AudioDiag][Snapshot] failed to schedule via page.run_task",
                    level=logging.WARNING,
                    exception=exc,
                )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.log_detailed(
                "[AudioDiag][Snapshot] skipped reason=no_running_loop",
                level=logging.WARNING,
            )
            return

        task_coro = _task()
        try:
            loop.create_task(task_coro)
        except Exception as exc:
            task_coro.close()
            self.log_detailed(
                "[AudioDiag][Snapshot] skipped reason=create_task_failed",
                level=logging.WARNING,
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
            self.log_detailed(line)
        for line in loopback_lines:
            self.log_detailed(line)
