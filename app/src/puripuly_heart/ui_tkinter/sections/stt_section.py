"""STT (speech-to-text) settings section.

Provider, compute mode, backend, quantization, and low-latency toggle.
Fields map to ``controller.settings.provider.*`` and ``controller.settings.stt.*``.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.domain.providers import STTProviderName
from puripuly_heart.ui_tkinter import theme as th
from puripuly_heart.ui_tkinter.sections.section_base import CollapsibleSection

# Human-readable STT provider labels
_STT_LABELS: dict[str, str] = {
    "none": "None",
    "local_qwen": "Qwen ASR",
    "local_qwen_17b": "Qwen ASR 17B",
    "local_gigaam_rnnt": "GigaAM RNNT",
    "local_parakeet_tdt": "Parakeet TDT",
    "local_gigaam_rnnt_gguf": "GigaAM RNNT (GGUF)",
    "local_parakeet_tdt_gguf": "Parakeet TDT (GGUF)",
    "local_qwen3_asr_gguf": "Qwen3 ASR (GGUF)",
    "local_qwen_17b_gguf": "Qwen 17B (GGUF)",
}


def _provider_label(value: str) -> str:
    return _STT_LABELS.get(value, value)


class STTSection(CollapsibleSection):
    """STT provider, compute, backend, quantization, and low-latency mode."""

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, t("settings.section.stt", default="STT"), **kwargs)
        self._controller = controller
        self._build()

    def _build(self) -> None:
        provider = self._controller.settings.provider

        # --- STT Provider ---
        provider_values = [e.value for e in STTProviderName]
        provider_names = [_provider_label(v) for v in provider_values]
        self._provider_menu = ctk.CTkOptionMenu(
            self._content,
            values=provider_names,
            width=200,
            command=lambda _: self._on_provider_change(),
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._provider_menu.set(_provider_label(provider.stt.value))
        self._provider_values = provider_values
        self._provider_names = provider_names
        self.add_row(t("settings.stt_provider", default="Provider"), self._provider_menu)
        self._add_debug_label(self._provider_menu, "dropdown.stt_provider")

        # --- Compute mode ---
        compute_values = ["GPU", "CPU"]
        self._compute_menu = ctk.CTkOptionMenu(
            self._content,
            values=compute_values,
            width=120,
            command=lambda _: self._on_compute_change(),
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._compute_menu.set("GPU" if provider.stt_compute == "gpu" else "CPU")
        self.add_row(t("settings.compute_mode", default="Compute"), self._compute_menu)
        self._add_debug_label(self._compute_menu, "dropdown.compute_mode")

        # --- Backend ---
        backend_values = ["onnx", "gguf"]
        self._backend_menu = ctk.CTkOptionMenu(
            self._content,
            values=backend_values,
            width=120,
            command=lambda _: self._on_backend_change(),
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._backend_menu.set(provider.stt_backend)
        self.add_row(t("settings.backend", default="Backend"), self._backend_menu)
        self._add_debug_label(self._backend_menu, "dropdown.stt_backend")

        # --- Quantization ---
        quant_values = ["", "int8", "q8_0", "q6_k", "f16"]
        self._quant_menu = ctk.CTkOptionMenu(
            self._content,
            values=[q if q else "auto" for q in quant_values],
            width=120,
            command=lambda _: self._on_quant_change(),
            fg_color=th.COLOR_PRIMARY,
            button_color=th.COLOR_PRIMARY,
            button_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_fg_color=th.COLOR_SURFACE,
            dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
            dropdown_text_color=th.COLOR_TEXT,
        )
        self._quant_values = quant_values
        current_quant = provider.stt_quant if provider.stt_quant else "auto"
        self._quant_menu.set(current_quant)
        self.add_row(t("settings.quantization", default="Quantization"), self._quant_menu)
        self._add_debug_label(self._quant_menu, "dropdown.stt_quantization")

        # --- Low latency toggle ---
        self._low_latency_switch = ctk.CTkSwitch(
            self._content,
            text="",
            command=self._on_low_latency_toggle,
        )
        if self._controller.settings.stt.low_latency_mode:
            self._low_latency_switch.select()
        self.add_row(t("settings.low_latency", default="Low Latency"), self._low_latency_switch)
        self._add_debug_label(self._low_latency_switch, "switch.low_latency")

    # --- Handlers ---

    def _on_provider_change(self) -> None:
        idx = self._provider_names.index(self._provider_menu.get())
        value = self._provider_values[idx]
        self._controller.settings.provider.stt = STTProviderName(value)
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_compute_change(self) -> None:
        self._controller.settings.provider.stt_compute = self._compute_menu.get().lower()
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_backend_change(self) -> None:
        self._controller.settings.provider.stt_backend = self._backend_menu.get()
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_quant_change(self) -> None:
        idx = [q if q else "auto" for q in self._quant_values].index(self._quant_menu.get())
        value = self._quant_values[idx]
        self._controller.settings.provider.stt_quant = value
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_low_latency_toggle(self) -> None:
        self._controller.settings.stt.low_latency_mode = self._low_latency_switch.get() == 1
        self._controller.apply_settings_with_sync(self._controller.settings)
