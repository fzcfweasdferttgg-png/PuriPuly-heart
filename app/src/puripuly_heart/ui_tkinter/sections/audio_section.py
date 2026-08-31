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
        super().__init__(
            master,
            t("tk.settings.section.audio", default="Audio"),
            title_i18n_key="tk.settings.section.audio",
            title_default="Audio",
            expanded=True,
            **kwargs,
        )
        self._controller = controller
        self._build()

    def _build(self) -> None:
        settings = self._controller.settings

        # --- Input device (text entry, populated externally) ---
        def _make_device_entry(row):
            entry = ctk.CTkEntry(row, width=240)
            entry.insert(0, settings.audio.input_device)
            entry.bind("<FocusOut>", lambda _: self._apply_device())
            entry.bind("<Return>", lambda _: self._apply_device())
            return entry

        self._device_entry = self.add_row(
            t("tk.settings.input_device", default="Input Device"),
            _make_device_entry,
            label_i18n_key="tk.settings.input_device",
            label_default="Input Device",
            label_id="label.input_device",
            control_id="input.input_device",
        )

        # --- Host API ---
        def _make_host_api_entry(row):
            entry = ctk.CTkEntry(row, width=240)
            entry.insert(0, settings.audio.input_host_api)
            entry.bind("<FocusOut>", lambda _: self._apply_host_api())
            entry.bind("<Return>", lambda _: self._apply_host_api())
            return entry

        self._host_api_entry = self.add_row(
            t("tk.settings.audio_host_api", default="Host API"),
            _make_host_api_entry,
            label_i18n_key="tk.settings.audio_host_api",
            label_default="Host API",
            label_id="label.host_api",
            control_id="input.host_api",
        )

        # --- VAD Threshold (slider 0.0–1.0) ---
        def _make_vad_slider(row):
            slider = ctk.CTkSlider(
                row,
                from_=0.0,
                to=1.0,
                number_of_steps=100,
                width=200,
                command=self._on_vad_slider_change,
            )
            slider.set(settings.stt.vad_speech_threshold)
            return slider

        self._vad_slider = self.add_row(
            t("tk.settings.vad_threshold", default="VAD Threshold"),
            _make_vad_slider,
            label_i18n_key="tk.settings.vad_threshold",
            label_default="VAD Threshold",
            label_id="label.vad_threshold",
            control_id="slider.vad_threshold",
        )

        # --- VAD Hangover ms ---
        def _make_hangover_entry(row):
            entry = ctk.CTkEntry(row, width=120)
            entry.insert(0, str(settings.stt.low_latency_vad_hangover_ms))
            entry.bind("<FocusOut>", lambda _: self._apply_hangover())
            entry.bind("<Return>", lambda _: self._apply_hangover())
            return entry

        self._hangover_entry = self.add_row(
            t("tk.settings.vad_hangover_ms", default="VAD Hangover (ms)"),
            _make_hangover_entry,
            label_i18n_key="tk.settings.vad_hangover_ms",
            label_default="VAD Hangover (ms)",
            label_id="label.vad_hangover",
            control_id="input.vad_hangover",
        )

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
