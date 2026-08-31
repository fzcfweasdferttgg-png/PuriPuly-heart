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
        super().__init__(
            master,
            t("tk.settings.section.stt", default="STT"),
            title_i18n_key="tk.settings.section.stt",
            title_default="STT",
            **kwargs,
        )
        self._controller = controller
        self._build()

    def _build(self) -> None:
        provider = self._controller.settings.provider

        # --- STT Provider ---
        provider_values = [e.value for e in STTProviderName]
        provider_names = [_provider_label(v) for v in provider_values]
        self._provider_values = provider_values
        self._provider_names = provider_names

        def _make_provider_menu(row):
            menu = ctk.CTkOptionMenu(
                row,
                values=provider_names,
                width=200,
                command=lambda _: self._on_provider_change(),
                fg_color=th.COLOR_PRIMARY,
                button_color=th.COLOR_PRIMARY,
                button_hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_ON_PRIMARY,
                dropdown_fg_color=th.COLOR_SURFACE,
                dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
                dropdown_text_color=th.COLOR_TEXT,
            )
            menu.set(_provider_label(provider.stt.value))
            return menu

        self._provider_menu = self.add_row(
            t("tk.settings.stt_provider", default="Provider"),
            _make_provider_menu,
            label_i18n_key="tk.settings.stt_provider",
            label_default="Provider",
            label_id="label.stt_provider",
            control_id="dropdown.stt_provider",
        )

        # --- Compute mode ---
        compute_values = ["GPU", "CPU"]

        def _make_compute_menu(row):
            menu = ctk.CTkOptionMenu(
                row,
                values=compute_values,
                width=120,
                command=lambda _: self._on_compute_change(),
                fg_color=th.COLOR_PRIMARY,
                button_color=th.COLOR_PRIMARY,
                button_hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_ON_PRIMARY,
                dropdown_fg_color=th.COLOR_SURFACE,
                dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
                dropdown_text_color=th.COLOR_TEXT,
            )
            menu.set("GPU" if provider.stt_compute == "gpu" else "CPU")
            return menu

        self._compute_menu = self.add_row(
            t("tk.settings.compute_mode", default="Compute"),
            _make_compute_menu,
            label_i18n_key="tk.settings.compute_mode",
            label_default="Compute",
            label_id="label.compute_mode",
            control_id="dropdown.compute_mode",
        )

        # --- Backend ---
        backend_values = ["onnx", "gguf"]

        def _make_backend_menu(row):
            menu = ctk.CTkOptionMenu(
                row,
                values=backend_values,
                width=120,
                command=lambda _: self._on_backend_change(),
                fg_color=th.COLOR_PRIMARY,
                button_color=th.COLOR_PRIMARY,
                button_hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_ON_PRIMARY,
                dropdown_fg_color=th.COLOR_SURFACE,
                dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
                dropdown_text_color=th.COLOR_TEXT,
            )
            menu.set(provider.stt_backend)
            return menu

        self._backend_menu = self.add_row(
            t("tk.settings.backend", default="Backend"),
            _make_backend_menu,
            label_i18n_key="tk.settings.backend",
            label_default="Backend",
            label_id="label.stt_backend",
            control_id="dropdown.stt_backend",
        )

        # --- Quantization ---
        quant_values = ["", "int8", "q8_0", "q6_k", "f16"]
        self._quant_values = quant_values
        current_quant = provider.stt_quant if provider.stt_quant else "auto"

        def _make_quant_menu(row):
            menu = ctk.CTkOptionMenu(
                row,
                values=[q if q else "auto" for q in quant_values],
                width=120,
                command=lambda _: self._on_quant_change(),
                fg_color=th.COLOR_PRIMARY,
                button_color=th.COLOR_PRIMARY,
                button_hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_ON_PRIMARY,
                dropdown_fg_color=th.COLOR_SURFACE,
                dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
                dropdown_text_color=th.COLOR_TEXT,
            )
            menu.set(current_quant)
            return menu

        self._quant_menu = self.add_row(
            t("tk.settings.quantization", default="Quantization"),
            _make_quant_menu,
            label_i18n_key="tk.settings.quantization",
            label_default="Quantization",
            label_id="label.stt_quantization",
            control_id="dropdown.stt_quantization",
        )

        # --- Low latency toggle ---
        def _make_low_latency_switch(row):
            switch = ctk.CTkSwitch(
                row,
                text="",
                command=self._on_low_latency_toggle,
            )
            if self._controller.settings.stt.low_latency_mode:
                switch.select()
            return switch

        self._low_latency_switch = self.add_row(
            t("tk.settings.low_latency", default="Low Latency"),
            _make_low_latency_switch,
            label_i18n_key="tk.settings.low_latency",
            label_default="Low Latency",
            label_id="label.low_latency",
            control_id="switch.low_latency",
        )

        # --- Separator for Peer STT ---
        separator = ctk.CTkLabel(
            self._content,
            text=t("tk.settings.section.peer_stt", default="Peer STT"),
            font=(th.FONT_FAMILY_FALLBACK, th.FONT_SIZE_BODY, "bold"),
            text_color=th.COLOR_TEXT,
        )
        separator.pack(fill="x", padx=th.CARD_PAD_X, pady=(12, 4))
        self._register_translatable(
            separator,
            "tk.settings.section.peer_stt",
            "Peer STT",
        )

        # --- Peer STT Provider ---
        peer_provider_values = [e.value for e in STTProviderName]
        peer_provider_names = [_provider_label(v) for v in peer_provider_values]
        self._peer_provider_values = peer_provider_values
        self._peer_provider_names = peer_provider_names

        def _make_peer_provider_menu(row):
            menu = ctk.CTkOptionMenu(
                row,
                values=peer_provider_names,
                width=200,
                command=lambda _: self._on_peer_provider_change(),
                fg_color=th.COLOR_PRIMARY,
                button_color=th.COLOR_PRIMARY,
                button_hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_ON_PRIMARY,
                dropdown_fg_color=th.COLOR_SURFACE,
                dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
                dropdown_text_color=th.COLOR_TEXT,
            )
            menu.set(_provider_label(provider.peer_stt.value))
            return menu

        self._peer_provider_menu = self.add_row(
            t("tk.settings.peer_stt_provider", default="Peer Provider"),
            _make_peer_provider_menu,
            label_i18n_key="tk.settings.peer_stt_provider",
            label_default="Peer Provider",
            label_id="label.peer_stt_provider",
            control_id="dropdown.peer_stt_provider",
        )

        # --- Peer Compute mode ---
        def _make_peer_compute_menu(row):
            menu = ctk.CTkOptionMenu(
                row,
                values=compute_values,
                width=120,
                command=lambda _: self._on_peer_compute_change(),
                fg_color=th.COLOR_PRIMARY,
                button_color=th.COLOR_PRIMARY,
                button_hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_ON_PRIMARY,
                dropdown_fg_color=th.COLOR_SURFACE,
                dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
                dropdown_text_color=th.COLOR_TEXT,
            )
            menu.set("GPU" if provider.peer_stt_compute == "gpu" else "CPU")
            return menu

        self._peer_compute_menu = self.add_row(
            t("tk.settings.peer_compute_mode", default="Peer Compute"),
            _make_peer_compute_menu,
            label_i18n_key="tk.settings.peer_compute_mode",
            label_default="Peer Compute",
            label_id="label.peer_compute_mode",
            control_id="dropdown.peer_compute_mode",
        )

        # --- Peer Backend ---
        def _make_peer_backend_menu(row):
            menu = ctk.CTkOptionMenu(
                row,
                values=backend_values,
                width=120,
                command=lambda _: self._on_peer_backend_change(),
                fg_color=th.COLOR_PRIMARY,
                button_color=th.COLOR_PRIMARY,
                button_hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_ON_PRIMARY,
                dropdown_fg_color=th.COLOR_SURFACE,
                dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
                dropdown_text_color=th.COLOR_TEXT,
            )
            menu.set(provider.peer_stt_backend)
            return menu

        self._peer_backend_menu = self.add_row(
            t("tk.settings.peer_backend", default="Peer Backend"),
            _make_peer_backend_menu,
            label_i18n_key="tk.settings.peer_backend",
            label_default="Peer Backend",
            label_id="label.peer_backend",
            control_id="dropdown.peer_backend",
        )

        # --- Peer Quantization ---
        current_peer_quant = provider.peer_stt_quant if provider.peer_stt_quant else "auto"

        def _make_peer_quant_menu(row):
            menu = ctk.CTkOptionMenu(
                row,
                values=[q if q else "auto" for q in quant_values],
                width=120,
                command=lambda _: self._on_peer_quant_change(),
                fg_color=th.COLOR_PRIMARY,
                button_color=th.COLOR_PRIMARY,
                button_hover_color=th.COLOR_PRIMARY_CONTAINER,
                text_color=th.COLOR_ON_PRIMARY,
                dropdown_fg_color=th.COLOR_SURFACE,
                dropdown_hover_color=th.COLOR_PRIMARY_CONTAINER,
                dropdown_text_color=th.COLOR_TEXT,
            )
            menu.set(current_peer_quant)
            return menu

        self._peer_quant_menu = self.add_row(
            t("tk.settings.peer_quantization", default="Peer Quantization"),
            _make_peer_quant_menu,
            label_i18n_key="tk.settings.peer_quantization",
            label_default="Peer Quantization",
            label_id="label.peer_quantization",
            control_id="dropdown.peer_quantization",
        )

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

    # --- Peer STT Handlers ---

    def _on_peer_provider_change(self) -> None:
        idx = self._peer_provider_names.index(self._peer_provider_menu.get())
        value = self._peer_provider_values[idx]
        self._controller.settings.provider.peer_stt = STTProviderName(value)
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_peer_compute_change(self) -> None:
        self._controller.settings.provider.peer_stt_compute = self._peer_compute_menu.get().lower()
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_peer_backend_change(self) -> None:
        self._controller.settings.provider.peer_stt_backend = self._peer_backend_menu.get()
        self._controller.apply_settings_with_sync(self._controller.settings)

    def _on_peer_quant_change(self) -> None:
        idx = [q if q else "auto" for q in self._peer_quant_values].index(self._peer_quant_menu.get())
        value = self._peer_quant_values[idx]
        self._controller.settings.provider.peer_stt_quant = value
        self._controller.apply_settings_with_sync(self._controller.settings)
