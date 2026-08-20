"""Microphone-test mixin for GuiController."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from puripuly_heart.core.audio.diagnostics import compute_audio_frame_metrics
from puripuly_heart.core.audio.source import (
    SoundDeviceAudioSource,
    determine_self_mic_capture_channels,
    observe_microphone_test_route,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings
    from puripuly_heart.core.audio.source import MicrophoneTestRouteObservation

logger = logging.getLogger(__name__)

_MICROPHONE_TEST_LEVEL_INTERVAL_S = 1.0


def _mic_test_log_value(value: object) -> str:
    if value is None:
        return "None"
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, float):
        return str(value)
    return str(value)


def _mic_test_db(value: float) -> float:
    if value <= 0.0:
        return -120.0
    return round(float(20.0 * math.log10(max(value, 1e-6))), 1)


@dataclass(slots=True)
class _MicrophoneTestLevelStats:
    frames: int = 0
    audio_ms: float = 0.0
    sample_count: int = 0
    square_sum: float = 0.0
    peak_abs: float = 0.0
    zero_count: int = 0

    def add_frame(self, frame) -> None:  # noqa: ANN001
        metrics = compute_audio_frame_metrics(frame)
        samples = np.asarray(frame.samples, dtype=np.float32)
        self.frames += 1
        self.audio_ms += metrics.audio_ms
        self.sample_count += int(samples.size)
        if samples.size == 0:
            return

        abs_samples = np.abs(samples)
        self.square_sum += float(np.sum(np.square(samples, dtype=np.float32)))
        self.peak_abs = max(self.peak_abs, float(np.max(abs_samples)))
        self.zero_count += int(np.count_nonzero(abs_samples < 1e-6))

    @property
    def rms_db(self) -> float:
        if self.sample_count <= 0:
            return -120.0
        return _mic_test_db(math.sqrt(self.square_sum / float(self.sample_count)))

    @property
    def peak_db(self) -> float:
        return _mic_test_db(self.peak_abs)

    @property
    def zero_ratio(self) -> float:
        if self.sample_count <= 0:
            return 1.0
        return round(float(self.zero_count) / float(self.sample_count), 3)

    def reset(self) -> None:
        self.frames = 0
        self.audio_ms = 0.0
        self.sample_count = 0
        self.square_sum = 0.0
        self.peak_abs = 0.0
        self.zero_count = 0


class MicTestManagerMixin:
    """Microphone-level-test session management.

    Mixed into :class:`GuiController`; all attributes are inherited from it.
    """

    @property
    def microphone_test_meter_level(self) -> float:
        return self._microphone_test_meter_level

    @property
    def microphone_test_active(self) -> bool:
        task = self._microphone_test_task
        return task is not None and not task.done()

    def _get_microphone_test_lifecycle_lock(self) -> asyncio.Lock:
        if self._microphone_test_lifecycle_lock is None:
            self._microphone_test_lifecycle_lock = asyncio.Lock()
        return self._microphone_test_lifecycle_lock

    @staticmethod
    def _microphone_test_audio_settings_signature(
        settings: AppSettings | None,
    ) -> tuple[object, ...] | None:
        if settings is None:
            return None
        return (
            settings.audio.input_host_api,
            settings.audio.input_device,
            settings.audio.internal_sample_rate_hz,
            settings.audio.internal_channels,
        )

    def _self_stt_active_or_desired_for_microphone_test(self) -> bool:
        return bool(
            self._stt_desired
            or self._local_stt_pending_enable_after_install
            or self._mic_task is not None
            or self._audio_source is not None
        )

    def _log_microphone_test_stt_auto_off(
        self,
        *,
        requested: bool,
        completed: bool,
        exception: BaseException | None = None,
    ) -> None:
        self.log_basic(
            "[MicTest] stt_auto_off "
            f"requested={requested} "
            f"completed={completed} "
            "exception_class="
            f"{_mic_test_log_value(type(exception).__name__ if exception else None)} "
            "exception_message="
            f"{_mic_test_log_value(str(exception) if exception else None)}"
        )

    async def _prepare_microphone_test_capture(self) -> bool:
        requested = self._self_stt_active_or_desired_for_microphone_test()
        if not requested:
            if self._last_mic_loop_close_exception is not None:
                self._log_microphone_test_stt_auto_off(
                    requested=False,
                    completed=False,
                    exception=self._last_mic_loop_close_exception,
                )
                return False
            self._log_microphone_test_stt_auto_off(
                requested=False,
                completed=True,
            )
            return True

        try:
            await self.set_stt_enabled(False)
            if self._last_mic_loop_close_exception is not None:
                raise self._last_mic_loop_close_exception
            if self._mic_task is not None or self._audio_source is not None:
                raise RuntimeError("self microphone source still open after STT auto-off")
        except Exception as exc:
            self._log_microphone_test_stt_auto_off(
                requested=True,
                completed=False,
                exception=exc,
            )
            return False

        self._log_microphone_test_stt_auto_off(
            requested=True,
            completed=True,
        )
        return True

    async def start_microphone_test(
        self,
        *,
        meter_callback: Callable[[float], object] | None = None,
        level_log_interval_s: float = _MICROPHONE_TEST_LEVEL_INTERVAL_S,
    ) -> bool:
        if self.settings is None:
            return False
        if self._signature_detector.last_microphone_test_audio_settings_signature is None:
            self._signature_detector.last_microphone_test_audio_settings_signature = (
                self._microphone_test_audio_settings_signature(self.settings)
            )
        async with self._get_microphone_test_lifecycle_lock():
            task = self._microphone_test_task
            if task is not None:
                if not task.done():
                    return False
                await asyncio.gather(task, return_exceptions=True)
                if self._microphone_test_task is task:
                    self._microphone_test_task = None

            if not await self._prepare_microphone_test_capture():
                return False

            self._microphone_test_task = asyncio.create_task(
                self._run_microphone_test_session(
                    meter_callback=meter_callback,
                    level_log_interval_s=level_log_interval_s,
                )
            )
            return True

    async def _run_microphone_test_session(
        self,
        *,
        meter_callback: Callable[[float], object] | None,
        level_log_interval_s: float,
    ) -> None:
        current_task = asyncio.current_task()
        try:
            await self.run_microphone_test_capture(
                meter_callback=meter_callback,
                level_log_interval_s=level_log_interval_s,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log_error(f"Microphone test error: {exc}")
        finally:
            if self._microphone_test_task is current_task:
                self._microphone_test_task = None

    async def stop_microphone_test(self) -> None:
        async with self._get_microphone_test_lifecycle_lock():
            task = self._microphone_test_task
            if task is None:
                return
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._microphone_test_task is task:
                self._microphone_test_task = None

    async def stop_microphone_test_for_audio_settings_change(self) -> None:
        await self.stop_microphone_test()

    async def _set_microphone_test_meter_level(
        self,
        value: float,
        meter_callback: Callable[[float], object] | None,
    ) -> None:
        level = max(0.0, min(1.0, float(value)))
        if level <= 1e-6:
            level = 0.0
        self._microphone_test_meter_level = level
        if meter_callback is None:
            return
        try:
            result = meter_callback(level)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("Microphone-test meter callback raised", exc_info=True)

    @staticmethod
    def _microphone_test_meter_level_from_frame(frame) -> float:  # noqa: ANN001
        samples = np.asarray(frame.samples, dtype=np.float32)
        if samples.size == 0:
            return 0.0
        peak_abs = float(np.max(np.abs(samples)))
        if peak_abs <= 1e-6:
            return 0.0
        return min(1.0, peak_abs)

    @staticmethod
    def _format_microphone_test_route_log(
        observation: MicrophoneTestRouteObservation,
    ) -> str:
        return (
            "[MicTest] route "
            f"saved_host_api={_mic_test_log_value(observation.saved_host_api)} "
            f"actual_host_api={_mic_test_log_value(observation.actual_host_api)} "
            f"requested_device={_mic_test_log_value(observation.requested_device)} "
            f"hostapi_index={_mic_test_log_value(observation.hostapi_index)} "
            f"resolved_device_idx={_mic_test_log_value(observation.resolved_device_idx)} "
            f"resolved_device_name={_mic_test_log_value(observation.resolved_device_name)} "
            "resolution_exception_class="
            f"{_mic_test_log_value(observation.resolution_exception_class)} "
            "resolution_exception_message="
            f"{_mic_test_log_value(observation.resolution_exception_message)}"
        )

    @staticmethod
    def _microphone_test_source_value(source: object | None, attr: str, fallback: object) -> object:
        if source is None:
            return fallback
        try:
            return getattr(source, attr, fallback)
        except Exception:
            return fallback

    @staticmethod
    def _microphone_test_source_int(
        source: object | None,
        attr: str,
        fallback: int | None,
    ) -> int | None:
        if source is None:
            return fallback
        try:
            value = getattr(source, attr, fallback)
            if value is None:
                return None
            return int(value)
        except Exception:
            return fallback

    def _log_microphone_test_open(
        self,
        *,
        attempted: bool,
        opened: bool,
        requested_channels: int | None,
        source: object | None,
        observation: MicrophoneTestRouteObservation,
        exception: BaseException | None = None,
    ) -> None:
        opened_channels = self._microphone_test_source_int(source, "opened_channels", None)
        frame_channels = self._microphone_test_source_int(source, "frame_channels", opened_channels)
        actual_sample_rate_hz = self._microphone_test_source_value(
            source,
            "actual_sample_rate_hz",
            None,
        )
        self.log_basic(
            "[MicTest] open "
            f"attempted={attempted} "
            f"opened={opened} "
            f"requested_channels={_mic_test_log_value(requested_channels)} "
            f"opened_channels={_mic_test_log_value(opened_channels)} "
            f"frame_channels={_mic_test_log_value(frame_channels)} "
            "requested_sample_rate_hz=None "
            f"actual_sample_rate_hz={_mic_test_log_value(actual_sample_rate_hz)} "
            f"wasapi_auto_convert={observation.wasapi_auto_convert} "
            f"wasapi_exclusive={observation.wasapi_exclusive} "
            "exception_class="
            f"{_mic_test_log_value(type(exception).__name__ if exception else None)} "
            "exception_message="
            f"{_mic_test_log_value(str(exception) if exception else None)}"
        )

    def _log_microphone_test_level(
        self,
        stats: _MicrophoneTestLevelStats,
        *,
        source: object | None,
    ) -> None:
        self.log_basic(
            "[MicTest] level "
            f"rms_db={stats.rms_db:.1f} "
            f"peak_db={stats.peak_db:.1f} "
            f"zero_ratio={stats.zero_ratio:.3f} "
            f"frames={stats.frames} "
            f"audio_ms={stats.audio_ms:.1f} "
            f"queue_drops={self._microphone_test_source_int(source, 'queue_drop_count', 0)} "
            "callback_statuses="
            f"{self._microphone_test_source_int(source, 'callback_status_count', 0)}"
        )

    def _log_microphone_test_end(
        self,
        *,
        opened: bool,
        stats: _MicrophoneTestLevelStats,
        source: object | None,
        exception: BaseException | None,
    ) -> None:
        self.log_basic(
            "[MicTest] end "
            f"opened={opened} "
            f"frames_total={stats.frames} "
            f"audio_ms_total={stats.audio_ms:.1f} "
            f"rms_db_total={stats.rms_db:.1f} "
            f"peak_db_max={stats.peak_db:.1f} "
            f"zero_ratio_total={stats.zero_ratio:.3f} "
            f"queue_drops={self._microphone_test_source_int(source, 'queue_drop_count', 0)} "
            "callback_statuses="
            f"{self._microphone_test_source_int(source, 'callback_status_count', 0)} "
            "exception_class="
            f"{_mic_test_log_value(type(exception).__name__ if exception else None)} "
            "exception_message="
            f"{_mic_test_log_value(str(exception) if exception else None)}"
        )

    async def run_microphone_test_capture(
        self,
        *,
        meter_callback: Callable[[float], object] | None = None,
        level_log_interval_s: float = _MICROPHONE_TEST_LEVEL_INTERVAL_S,
    ) -> None:
        assert self.settings is not None
        level_log_interval_s = max(0.0, float(level_log_interval_s))
        source: object | None = None
        opened = False
        end_exception: BaseException | None = None
        level_logged = False
        pending_frame: asyncio.Task[object] | None = None
        interval_stats = _MicrophoneTestLevelStats()
        total_stats = _MicrophoneTestLevelStats()

        await self._set_microphone_test_meter_level(0.0, meter_callback)
        observation = observe_microphone_test_route(
            saved_host_api=self.settings.audio.input_host_api,
            requested_device=self.settings.audio.input_device,
        )
        self.log_basic(self._format_microphone_test_route_log(observation))

        try:
            if not observation.should_attempt_open:
                self._log_microphone_test_open(
                    attempted=False,
                    opened=False,
                    requested_channels=None,
                    source=None,
                    observation=observation,
                )
                self._log_microphone_test_level(interval_stats, source=None)
                level_logged = True
                return

            decision = determine_self_mic_capture_channels(
                device_idx=observation.resolved_device_idx,
                internal_channels=self.settings.audio.internal_channels,
            )
            requested_channels = decision.preferred_capture_channels
            try:
                source = SoundDeviceAudioSource(
                    sample_rate_hz=None,
                    channels=requested_channels,
                    device=observation.resolved_device_idx,
                    wasapi_auto_convert=observation.wasapi_auto_convert,
                    wasapi_exclusive=observation.wasapi_exclusive,
                )
            except Exception as exc:
                end_exception = exc
                self._log_microphone_test_open(
                    attempted=True,
                    opened=False,
                    requested_channels=requested_channels,
                    source=None,
                    observation=observation,
                    exception=exc,
                )
                self._log_microphone_test_level(interval_stats, source=None)
                level_logged = True
                return

            opened = True
            self._log_microphone_test_open(
                attempted=True,
                opened=True,
                requested_channels=requested_channels,
                source=source,
                observation=observation,
            )

            frame_iterator = source.frames()  # type: ignore[attr-defined]
            pending_frame = asyncio.create_task(anext(frame_iterator))
            last_level_log_s = self.clock.now()
            while True:
                if level_log_interval_s > 0.0:
                    elapsed_s = max(0.0, self.clock.now() - last_level_log_s)
                    timeout_s = max(0.0, level_log_interval_s - elapsed_s)
                    done, _pending = await asyncio.wait({pending_frame}, timeout=timeout_s)
                    if not done:
                        self._log_microphone_test_level(interval_stats, source=source)
                        level_logged = True
                        interval_stats.reset()
                        last_level_log_s = self.clock.now()
                        continue
                else:
                    await asyncio.wait({pending_frame})

                try:
                    frame = pending_frame.result()
                except StopAsyncIteration:
                    pending_frame = None
                    break

                interval_stats.add_frame(frame)
                total_stats.add_frame(frame)
                await self._set_microphone_test_meter_level(
                    self._microphone_test_meter_level_from_frame(frame),
                    meter_callback,
                )
                pending_frame = asyncio.create_task(anext(frame_iterator))

                if level_log_interval_s <= 0.0 or (
                    self.clock.now() - last_level_log_s >= level_log_interval_s
                ):
                    self._log_microphone_test_level(interval_stats, source=source)
                    level_logged = True
                    interval_stats.reset()
                    last_level_log_s = self.clock.now()
        except asyncio.CancelledError as exc:
            end_exception = exc
            raise
        except Exception as exc:
            end_exception = exc
        finally:
            if pending_frame is not None and not pending_frame.done():
                pending_frame.cancel()
                with contextlib.suppress(Exception):
                    await asyncio.gather(pending_frame, return_exceptions=True)

            if source is not None:
                with contextlib.suppress(Exception):
                    await source.close()  # type: ignore[attr-defined]

            if source is not None and interval_stats.frames > 0:
                self._log_microphone_test_level(interval_stats, source=source)
                level_logged = True
            elif source is not None and not level_logged:
                self._log_microphone_test_level(interval_stats, source=source)

            self._log_microphone_test_end(
                opened=opened,
                stats=total_stats,
                source=source,
                exception=end_exception,
            )
            await self._set_microphone_test_meter_level(0.0, meter_callback)
