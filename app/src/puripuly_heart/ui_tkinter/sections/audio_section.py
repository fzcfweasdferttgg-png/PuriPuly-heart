"""Audio settings section.

Input device, host API, VAD threshold, and VAD hangover configuration.
All fields map to ``controller.settings.audio.*`` and ``controller.settings.stt.*``.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection


class AudioSection(CollapsibleSection):
    """Audio input device, host API, and VAD settings."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, t("settings.section.audio", default="Audio"), expanded=True, **kwargs)
        self._controller = controller
        self._build()

    def _build(self) -> None:
        settings = self._controller.settings

        # --- Input device (text entry, populated externally) ---
        self._device_entry = ctk.CTkEntry(self._content, width=240)
        self._device_entry.insert(0, settings.audio.input_device)
        self._device_entry.bind("<FocusOut>", lambda _: self._apply_device())
        self._device_entry.bind("<Return>", lambda _: self._apply_device())
        self.add_row(t("settings.input_device", default="Input Device"), self._device_entry)
        self._add_debug_label(self._device_entry, "input.input_device")

        # --- Host API ---
        self._host_api_entry = ctk.CTkEntry(self._content, width=240)
        self._host_api_entry.insert(0, settings.audio.input_host_api)
        self._host_api_entry.bind("<FocusOut>", lambda _: self._apply_host_api())
        self._host_api_entry.bind("<Return>", lambda _: self._apply_host_api())
        self.add_row(t("settings.audio_host_api", default="Host API"), self._host_api_entry)
        self._add_debug_label(self._host_api_entry, "input.host_api")

        # --- VAD Threshold (slider 0.0–1.0) ---
        self._vad_slider = ctk.CTkSlider(
            self._content,
            from_=0.0,
            to=1.0,
            number_of_steps=100,
            width=200,
            command=self._on_vad_slider_change,
        )
        self._vad_slider.set(settings.stt.vad_speech_threshold)
        self.add_row(t("settings.vad_threshold", default="VAD Threshold"), self._vad_slider)
        self._add_debug_label(self._vad_slider, "slider.vad_threshold")

        # --- VAD Hangover ms ---
        self._hangover_entry = ctk.CTkEntry(self._content, width=120)
        self._hangover_entry.insert(0, str(settings.stt.low_latency_vad_hangover_ms))
        self._hangover_entry.bind("<FocusOut>", lambda _: self._apply_hangover())
        self._hangover_entry.bind("<Return>", lambda _: self._apply_hangover())
        self.add_row(
            t("settings.vad_hangover_ms", default="VAD Hangover (ms)"),
            self._hangover_entry,
        )
        self._add_debug_label(self._hangover_entry, "input.vad_hangover")

    # --- Handlers ---

    def _on_vad_slider_change(self, value: float) -> None:
        self.debounce_slider(value, self._apply_vad)

    def _apply_vad(self, value: float) -> None:
        self._controller.settings.stt.vad_speech_threshold = round(value, 2)
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _apply_device(self) -> None:
        self._controller.settings.audio.input_device = self._device_entry.get().strip()
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _apply_host_api(self) -> None:
        self._controller.settings.audio.input_host_api = self._host_api_entry.get().strip()
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _apply_hangover(self) -> None:
        try:
            val = int(self._hangover_entry.get().strip())
            if val >= 0:
                self._controller.settings.stt.low_latency_vad_hangover_ms = val
                self._controller.apply_settings_with_sync(self._controller.settings)
        except ValueError:
            pass
