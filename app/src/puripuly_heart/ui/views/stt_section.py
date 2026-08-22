from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import flet as ft

logger = logging.getLogger(__name__)

from puripuly_heart.config.settings import AppSettings
from puripuly_heart.domain.providers import STTProviderName
from puripuly_heart.domain.language import get_stt_compatibility_warning
from puripuly_heart.ui.components.settings import OptionItem, SettingsModal
from puripuly_heart.domain.i18n import language_name, provider_label, t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
)

from puripuly_heart.ui.views.settings_helpers import _update_control_if_mounted
from puripuly_heart.domain.settings_commands import (
    ChangeSTTProvider,
    ChangePeerSTTProvider,
    ChangeSTTQuant,
    ChangeSTTBackend,
    ChangeSTTCompute,
)

if TYPE_CHECKING:
    from puripuly_heart.ui.views.settings import SettingsView


# ATTRIBUTE OWNERSHIP — _build_stt_widgets and _build_peer_stt_widgets create:
#   _stt_text, _stt_compute_label/gpu_btn/cpu_btn/row,
#   _stt_quant_label/q8_btn/q6k_btn/f16_btn/int8_btn/row,
#   _stt_backend_label/onnx_btn/gguf_btn/row, _stt_title, _stt_provider_label
#   _peer_stt_text, _peer_stt_compute_label/gpu_btn/cpu_btn/row,
#   _peer_quant_label/q8_btn/q6k_btn/f16_btn/int8_btn/row,
#   _peer_stt_backend_label/onnx_btn/gguf_btn/row,
#   _peer_provider_title, _dashboard_language_redirect_text, _peer_stt_label
#
# These attributes are used by _update_api_visibility (settings.py) to control
# compute/backend/quant row visibility based on selected provider.

class SttSectionMixin:

    _ONNX_QUANTS = ["int8"]  # extend here when fp16/fp32 are available
    _GGUF_QUANTS = ["q8_0", "q6_k", "f16"]
    _INITIAL_QUANTS_FOR_PROVIDER: dict[STTProviderName, list[str]] = {
        STTProviderName.NONE: [],
        STTProviderName.LOCAL_QWEN: ["int8"],
        STTProviderName.LOCAL_QWEN_17B: ["int8"],
        STTProviderName.LOCAL_GIGAAM_RNNT: ["int8"],
        STTProviderName.LOCAL_PARAKEET_TDT: ["int8"],
        STTProviderName.LOCAL_GIGAAM_RNNT_GGUF: ["q8_0", "q6_k", "f16"],
        STTProviderName.LOCAL_PARAKEET_TDT_GGUF: ["q8_0", "q6_k", "f16"],
        STTProviderName.LOCAL_QWEN3_ASR_GGUF: ["q8_0", "q6_k", "f16"],
        STTProviderName.LOCAL_QWEN_17B_GGUF: ["q8_0", "q6_k", "f16"],
    }

    def _load_stt_from_settings(self, settings: "AppSettings") -> None:
        if not hasattr(self, '_stt_text'):
            return
        self._set_unit_card_value_text(
            self._stt_text,
            provider_label(settings.provider.stt.value),
        )
        self._set_unit_card_value_text(
            self._peer_stt_text,
            provider_label(self._effective_peer_stt_provider(settings).value),
        )

    def _effective_peer_stt_provider(self, settings: AppSettings | None) -> STTProviderName:
        if settings is None:
            return STTProviderName.NONE
        return settings.provider.peer_stt

    def _peer_stt_option_item(self, provider: STTProviderName) -> OptionItem:
        return OptionItem(
            value=provider.value,
            label=provider_label(provider.value),
            description=t(f"provider.{provider.value}.description", default=""),
        )

    def _on_stt_click(self, e) -> None:
        try:
            if not self.page:
                return
        except (AssertionError, RuntimeError):
            return
        _VULKAN_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        _DIRECTML_PROVIDERS = {
            STTProviderName.LOCAL_QWEN,
            STTProviderName.LOCAL_QWEN_17B,
            STTProviderName.LOCAL_GIGAAM_RNNT,
            STTProviderName.LOCAL_PARAKEET_TDT,
        }
        display_settings = self._build_settings_with_provider_draft()
        backend = display_settings.provider.stt_backend if display_settings is not None else "onnx"
        if backend == "gguf":
            allowed = _VULKAN_PROVIDERS
        else:
            allowed = _DIRECTML_PROVIDERS
        options = [
            OptionItem(
                value=STTProviderName.NONE.value,
                label=provider_label(STTProviderName.NONE.value),
                description=t(f"provider.{STTProviderName.NONE.value}.description", default=""),
            )
        ] + [
            OptionItem(
                value=p.value,
                label=provider_label(p.value),
                description=t(f"provider.{p.value}.description", default=""),
            )
            for p in STTProviderName if p in allowed
        ]
        current = (
            display_settings.provider.stt.value
            if display_settings is not None
            else STTProviderName.NONE.value
        )
        modal = SettingsModal(
            self.page,
            t("settings.section.stt"),
            options,
            self._on_stt_selected,
            show_description=True,
        )
        modal.open(current)

    def _on_stt_selected(self, value: str) -> None:
        if not self._settings:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        provider = STTProviderName(value)
        old_provider = current_settings.provider.stt.value
        if old_provider == provider.value:
            return
        self._emit_runtime_basic(
            f"[Settings] STT provider changed: {old_provider} -> {provider.value}"
        )
        # Determine auto-set backend and quant
        _GGUF_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        auto_backend = "gguf" if provider in _GGUF_PROVIDERS else "onnx"
        available_quants = self._get_quant_options(provider)
        current_settings = self._build_settings_with_provider_draft()
        auto_quant = None
        if available_quants and (current_settings is None or current_settings.provider.stt_quant not in available_quants):
            auto_quant = available_quants[0]
        result = self._command_executor.execute(ChangeSTTProvider(
            provider=provider.value,
            backend=auto_backend,
            quant=auto_quant,
        ))
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        self.has_provider_changes = True

        # Update text
        self._set_unit_card_value_text(self._stt_text, provider_label(provider.value))

        # Check compatibility warning
        source_lang = self._settings.languages.source_language
        warning = get_stt_compatibility_warning(source_lang, provider.value)
        if warning:
            lang_display = language_name(warning.language_code)
            message = t(warning.key, language=lang_display, lang=lang_display)
            if self.show_snackbar:
                self.show_snackbar(message, ft.Colors.ORANGE_700)
            elif self.page:
                self.page.show_dialog(
                    ft.SnackBar(
                        ft.Text(
                            message,
                            color=ft.Colors.WHITE,
                        ),
                        bgcolor=ft.Colors.ORANGE_700,
                        duration=4000,
                        behavior=ft.SnackBarBehavior.FLOATING,
                        margin=ft.Margin.only(bottom=90),
                        padding=20,
                    )
                )

        try:
            if self.page:
                self._stt_text.update()
        except (AssertionError, RuntimeError):
            pass

    def _on_peer_stt_click(self, e) -> None:
        try:
            if not self.page:
                return
        except (AssertionError, RuntimeError):
            return
        _VULKAN_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        _DIRECTML_PROVIDERS = {
            STTProviderName.LOCAL_QWEN,
            STTProviderName.LOCAL_QWEN_17B,
            STTProviderName.LOCAL_GIGAAM_RNNT,
            STTProviderName.LOCAL_PARAKEET_TDT,
        }
        display_settings = self._build_settings_with_provider_draft()
        backend = display_settings.provider.peer_stt_backend if display_settings is not None else "onnx"
        if backend == "gguf":
            allowed = _VULKAN_PROVIDERS
        else:
            allowed = _DIRECTML_PROVIDERS
        options = [self._peer_stt_option_item(STTProviderName.NONE)] + [
            self._peer_stt_option_item(provider) for provider in STTProviderName if provider in allowed
        ]
        current_provider = (
            display_settings.provider.peer_stt
            if display_settings is not None
            else STTProviderName.NONE
        )
        current = current_provider.value
        SettingsModal(
            self.page,
            t("settings.peer_stt_provider"),
            options,
            self._on_peer_stt_selected,
            show_description=True,
        ).open(current)

    def _on_peer_stt_selected(self, value: str) -> None:
        if not self._settings:
            return
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        provider = STTProviderName(value)
        if current_settings.provider.peer_stt == provider:
            return
        # Determine auto-set backend and quant
        _GGUF_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        auto_backend = "gguf" if provider in _GGUF_PROVIDERS else "onnx"
        available_quants = self._get_quant_options(provider)
        current_settings = self._build_settings_with_provider_draft()
        auto_quant = None
        if available_quants and (current_settings is None or current_settings.provider.peer_stt_quant not in available_quants):
            auto_quant = available_quants[0]
        result = self._command_executor.execute(ChangePeerSTTProvider(
            provider=provider.value,
            backend=auto_backend,
            quant=auto_quant,
        ))
        self._set_unit_card_value_text(self._peer_stt_text, provider_label(value))
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        try:
            if self.page:
                self._peer_stt_text.update()
        except (AssertionError, RuntimeError):
            pass
        self.has_provider_changes = True

    def _is_local_stt(self, provider: STTProviderName) -> bool:
        return provider in (STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B, STTProviderName.LOCAL_GIGAAM_RNNT, STTProviderName.LOCAL_PARAKEET_TDT, STTProviderName.LOCAL_GIGAAM_RNNT_GGUF, STTProviderName.LOCAL_PARAKEET_TDT_GGUF, STTProviderName.LOCAL_QWEN3_ASR_GGUF, STTProviderName.LOCAL_QWEN_17B_GGUF)

    # SHARED QUANT SYNC — used by both self-stt and peer-stt quant buttons.
    # Buttons are pre-created in _build_stt_widgets / _build_peer_stt_widgets.
    # Visibility + style are set atomically. _update_control_if_mounted is safe
    # to call before widgets are mounted to a page (no-op if page is None).

    def _sync_quant_buttons(
        self,
        active_quant: str,
        available_quants: list[str],
        label: ft.Text,
        buttons: dict[str, ft.Container],
    ) -> None:
        label.visible = bool(available_quants)
        for quant, btn in buttons.items():
            btn.visible = quant in available_quants
            is_active = quant == active_quant
            btn.bgcolor = COLOR_PRIMARY if is_active else COLOR_SURFACE
            btn.border = ft.Border.all(1, COLOR_PRIMARY if is_active else COLOR_DIVIDER)
            btn.content.color = ft.Colors.WHITE if is_active else COLOR_ON_BACKGROUND
            btn.content.weight = ft.FontWeight.BOLD if is_active else ft.FontWeight.NORMAL
            _update_control_if_mounted(btn)
        _update_control_if_mounted(label)

    def _get_quant_options(self, provider: STTProviderName) -> list[str]:
        _ONNX_PROVIDERS = {
            STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B,
            STTProviderName.LOCAL_GIGAAM_RNNT, STTProviderName.LOCAL_PARAKEET_TDT,
        }
        _GGUF_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF, STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF, STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        if provider in _ONNX_PROVIDERS:
            return list(self._ONNX_QUANTS)
        if provider in _GGUF_PROVIDERS:
            return list(self._GGUF_QUANTS)
        return []

    def _sync_stt_quant_buttons(self, provider: STTProviderName, quant: str) -> None:
        available = self._get_quant_options(provider)
        resolved = quant if quant in available else ""
        self._stt_quant_row.visible = bool(available)
        self._sync_quant_buttons(
            resolved, available,
            self._stt_quant_label,
            {"q8_0": self._stt_quant_q8_btn, "q6_k": self._stt_quant_q6k_btn, "f16": self._stt_quant_f16_btn, "int8": self._stt_quant_int8_btn},
        )
        _update_control_if_mounted(self._stt_quant_row)

    def _sync_peer_quant_buttons(self, provider: STTProviderName, quant: str) -> None:
        available = self._get_quant_options(provider)
        resolved = quant if quant in available else ""
        self._peer_quant_row.visible = bool(available)
        self._sync_quant_buttons(
            resolved, available,
            self._peer_quant_label,
            {"q8_0": self._peer_quant_q8_btn, "q6_k": self._peer_quant_q6k_btn, "f16": self._peer_quant_f16_btn, "int8": self._peer_quant_int8_btn},
        )
        _update_control_if_mounted(self._peer_quant_row)

    def _apply_stt_quant(self, quant: str) -> None:
        if not self._settings:
            return
        logger.info("[STT] User selected self quant: %s", quant)
        self._command_executor.execute(ChangeSTTQuant(quant=quant, channel="self"))
        merged = self._build_settings_with_provider_draft()
        logger.info("[STT] Draft after self quant: stt_quant=%s", merged.provider.stt_quant)
        self._sync_stt_quant_buttons(merged.provider.stt, quant)
        self.has_provider_changes = True

    def _apply_peer_quant(self, quant: str) -> None:
        if not self._settings:
            return
        logger.info("[STT] User selected peer quant: %s", quant)
        self._command_executor.execute(ChangeSTTQuant(quant=quant, channel="peer"))
        merged = self._build_settings_with_provider_draft()
        logger.info("[STT] Draft after peer quant: peer_stt_quant=%s", merged.provider.peer_stt_quant)
        self._sync_peer_quant_buttons(merged.provider.peer_stt, quant)
        self.has_provider_changes = True

    def _sync_stt_compute_buttons(self, compute: str) -> None:
        is_gpu = compute == "gpu"
        self._stt_compute_gpu_btn.bgcolor = COLOR_PRIMARY if is_gpu else COLOR_SURFACE
        self._stt_compute_gpu_btn.border = ft.Border.all(1, COLOR_PRIMARY if is_gpu else COLOR_DIVIDER)
        self._stt_compute_gpu_btn.content.color = ft.Colors.WHITE if is_gpu else COLOR_ON_BACKGROUND
        self._stt_compute_gpu_btn.content.weight = ft.FontWeight.BOLD if is_gpu else ft.FontWeight.NORMAL
        self._stt_compute_cpu_btn.bgcolor = COLOR_PRIMARY if not is_gpu else COLOR_SURFACE
        self._stt_compute_cpu_btn.border = ft.Border.all(1, COLOR_PRIMARY if not is_gpu else COLOR_DIVIDER)
        self._stt_compute_cpu_btn.content.color = ft.Colors.WHITE if not is_gpu else COLOR_ON_BACKGROUND
        self._stt_compute_cpu_btn.content.weight = ft.FontWeight.BOLD if not is_gpu else ft.FontWeight.NORMAL
        _update_control_if_mounted(self._stt_compute_gpu_btn)
        _update_control_if_mounted(self._stt_compute_cpu_btn)

    def _sync_peer_stt_compute_buttons(self, compute: str) -> None:
        is_gpu = compute == "gpu"
        self._peer_stt_compute_gpu_btn.bgcolor = COLOR_PRIMARY if is_gpu else COLOR_SURFACE
        self._peer_stt_compute_gpu_btn.border = ft.Border.all(1, COLOR_PRIMARY if is_gpu else COLOR_DIVIDER)
        self._peer_stt_compute_gpu_btn.content.color = ft.Colors.WHITE if is_gpu else COLOR_ON_BACKGROUND
        self._peer_stt_compute_gpu_btn.content.weight = ft.FontWeight.BOLD if is_gpu else ft.FontWeight.NORMAL
        self._peer_stt_compute_cpu_btn.bgcolor = COLOR_PRIMARY if not is_gpu else COLOR_SURFACE
        self._peer_stt_compute_cpu_btn.border = ft.Border.all(1, COLOR_PRIMARY if not is_gpu else COLOR_DIVIDER)
        self._peer_stt_compute_cpu_btn.content.color = ft.Colors.WHITE if not is_gpu else COLOR_ON_BACKGROUND
        self._peer_stt_compute_cpu_btn.content.weight = ft.FontWeight.BOLD if not is_gpu else ft.FontWeight.NORMAL
        _update_control_if_mounted(self._peer_stt_compute_gpu_btn)
        _update_control_if_mounted(self._peer_stt_compute_cpu_btn)

    def _on_stt_compute_gpu_click(self, e) -> None:
        self._apply_stt_compute("gpu")

    def _on_stt_compute_cpu_click(self, e) -> None:
        self._apply_stt_compute("cpu")

    def _on_peer_stt_compute_gpu_click(self, e) -> None:
        self._apply_peer_stt_compute("gpu")

    def _on_peer_stt_compute_cpu_click(self, e) -> None:
        self._apply_peer_stt_compute("cpu")

    def _apply_stt_compute(self, value: str) -> None:
        if not self._settings:
            return
        if self._settings.provider.stt_compute == value:
            return
        self._command_executor.execute(ChangeSTTCompute(compute=value, channel="self"))
        self._sync_stt_compute_buttons(value)
        self.has_provider_changes = True
        self._emit_runtime_basic(f"[Settings] STT compute changed: {value}")

    def _apply_peer_stt_compute(self, value: str) -> None:
        if not self._settings:
            return
        if self._settings.provider.peer_stt_compute == value:
            return
        self._command_executor.execute(ChangeSTTCompute(compute=value, channel="peer"))
        self._sync_peer_stt_compute_buttons(value)
        self.has_provider_changes = True
        self._emit_runtime_basic(f"[Settings] Peer STT compute changed: {value}")

    def _sync_stt_backend_buttons(self, backend: str) -> None:
        is_onnx = backend == "onnx"
        self._stt_backend_onnx_btn.bgcolor = COLOR_PRIMARY if is_onnx else COLOR_SURFACE
        self._stt_backend_onnx_btn.border = ft.Border.all(1, COLOR_PRIMARY if is_onnx else COLOR_DIVIDER)
        self._stt_backend_onnx_btn.content.color = ft.Colors.WHITE if is_onnx else COLOR_ON_BACKGROUND
        self._stt_backend_onnx_btn.content.weight = ft.FontWeight.BOLD if is_onnx else ft.FontWeight.NORMAL
        self._stt_backend_gguf_btn.bgcolor = COLOR_PRIMARY if not is_onnx else COLOR_SURFACE
        self._stt_backend_gguf_btn.border = ft.Border.all(1, COLOR_PRIMARY if not is_onnx else COLOR_DIVIDER)
        self._stt_backend_gguf_btn.content.color = ft.Colors.WHITE if not is_onnx else COLOR_ON_BACKGROUND
        self._stt_backend_gguf_btn.content.weight = ft.FontWeight.BOLD if not is_onnx else ft.FontWeight.NORMAL
        _update_control_if_mounted(self._stt_backend_onnx_btn)
        _update_control_if_mounted(self._stt_backend_gguf_btn)

    def _sync_peer_stt_backend_buttons(self, backend: str) -> None:
        is_onnx = backend == "onnx"
        self._peer_stt_backend_onnx_btn.bgcolor = COLOR_PRIMARY if is_onnx else COLOR_SURFACE
        self._peer_stt_backend_onnx_btn.border = ft.Border.all(1, COLOR_PRIMARY if is_onnx else COLOR_DIVIDER)
        self._peer_stt_backend_onnx_btn.content.color = ft.Colors.WHITE if is_onnx else COLOR_ON_BACKGROUND
        self._peer_stt_backend_onnx_btn.content.weight = ft.FontWeight.BOLD if is_onnx else ft.FontWeight.NORMAL
        self._peer_stt_backend_gguf_btn.bgcolor = COLOR_PRIMARY if not is_onnx else COLOR_SURFACE
        self._peer_stt_backend_gguf_btn.border = ft.Border.all(1, COLOR_PRIMARY if not is_onnx else COLOR_DIVIDER)
        self._peer_stt_backend_gguf_btn.content.color = ft.Colors.WHITE if not is_onnx else COLOR_ON_BACKGROUND
        self._peer_stt_backend_gguf_btn.content.weight = ft.FontWeight.BOLD if not is_onnx else ft.FontWeight.NORMAL
        _update_control_if_mounted(self._peer_stt_backend_onnx_btn)
        _update_control_if_mounted(self._peer_stt_backend_gguf_btn)

    def _on_stt_backend_onnx_click(self, e) -> None:
        self._apply_stt_backend("onnx")

    def _on_stt_backend_gguf_click(self, e) -> None:
        self._apply_stt_backend("gguf")

    def _on_peer_stt_backend_onnx_click(self, e) -> None:
        self._apply_peer_stt_backend("onnx")

    def _on_peer_stt_backend_gguf_click(self, e) -> None:
        self._apply_peer_stt_backend("gguf")

    # BACKEND SWITCH — when switching ONNX↔GGUF, also changes the STT PROVIDER
    # (e.g. LOCAL_QWEN → LOCAL_QWEN3_ASR_GGUF). The provider enum carries the backend
    # implicitly. After switching, resets quant to first available for new provider.
    # Calls _update_api_visibility to show/hide compute/backend rows for new provider.

    def _apply_stt_backend(self, value: str) -> None:
        if not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        if draft.provider.stt_backend == value:
            return
        # Executor handles backend field + ONNX↔GGUF provider switching
        result = self._command_executor.execute(ChangeSTTBackend(backend=value, channel="self"))
        # Auto-adjust quant if incompatible with new provider
        merged = self._build_settings_with_provider_draft()
        if merged:
            available_quants = self._get_quant_options(merged.provider.stt)
            if available_quants and merged.provider.stt_quant not in available_quants:
                self._command_executor.execute(ChangeSTTQuant(quant=available_quants[0], channel="self"))
            self._set_unit_card_value_text(self._stt_text, provider_label(merged.provider.stt.value))
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        self.has_provider_changes = True

    def _apply_peer_stt_backend(self, value: str) -> None:
        if not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        if draft.provider.peer_stt_backend == value:
            return
        # Executor handles backend field + ONNX↔GGUF provider switching
        result = self._command_executor.execute(ChangeSTTBackend(backend=value, channel="peer"))
        # Auto-adjust quant if incompatible with new provider
        merged = self._build_settings_with_provider_draft()
        if merged:
            available_quants = self._get_quant_options(merged.provider.peer_stt)
            if available_quants and merged.provider.peer_stt_quant not in available_quants:
                self._command_executor.execute(ChangeSTTQuant(quant=available_quants[0], channel="peer"))
            self._set_unit_card_value_text(self._peer_stt_text, provider_label(merged.provider.peer_stt.value))
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        self.has_provider_changes = True

    # --- Widget builders (called from _build_api_tab) ---

    # WIDGET BUILD ORDER — called from _build_api_tab (settings.py).
    # Creates all self-stt UI controls AND reads self._initial_settings for
    # initial quant/backend button state. Uses _make_quant_button from SettingsHelpersMixin.
    # Returns _wrap_unit_card (the stt_card) but also creates ~20 self._* attributes
    # as side effect — the card is just the visible container.

    def _build_stt_widgets(self) -> ft.Control:
        self._stt_text = self._build_clickable_text(
            provider_label(STTProviderName.NONE.value),
            self._on_stt_click,
        )
        self._stt_compute_label = ft.Text(
            t("settings.compute.label"), size=14, color=COLOR_ON_BACKGROUND
        )
        self._stt_compute_gpu_btn = ft.Container(
            content=ft.Text("GPU", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
            bgcolor=COLOR_PRIMARY,
            border=ft.Border.all(1, COLOR_PRIMARY),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_stt_compute_gpu_click,
        )
        self._stt_compute_cpu_btn = ft.Container(
            content=ft.Text("CPU", size=14, color=COLOR_ON_BACKGROUND),
            bgcolor=COLOR_SURFACE,
            border=ft.Border.all(1, COLOR_DIVIDER),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_stt_compute_cpu_click,
        )
        self._stt_compute_row = ft.Row(
            [self._stt_compute_label, self._stt_compute_gpu_btn, self._stt_compute_cpu_btn],
            spacing=8,
            visible=False,
        )
        self._stt_quant_label = ft.Text(
            t("settings.quant.label", default="Quant:"), size=14, color=COLOR_ON_BACKGROUND
        )
        self._stt_quant_q8_btn = self._make_quant_button("Q8_0", lambda e: self._apply_stt_quant("q8_0"))
        self._stt_quant_q6k_btn = self._make_quant_button("Q6_K", lambda e: self._apply_stt_quant("q6_k"))
        self._stt_quant_f16_btn = self._make_quant_button("F16", lambda e: self._apply_stt_quant("f16"))
        self._stt_quant_int8_btn = self._make_quant_button("int8", lambda e: self._apply_stt_quant("int8"))
        # Set initial quant button state based on loaded settings
        _init_stt = self._initial_settings.provider.stt if self._initial_settings else STTProviderName.NONE
        _init_quant = self._initial_settings.provider.stt_quant if self._initial_settings else ""
        _init_available = self._INITIAL_QUANTS_FOR_PROVIDER.get(_init_stt, ["int8"])
        _init_active = _init_quant if _init_quant in _init_available else ""
        for _q, _btn in [("q8_0", self._stt_quant_q8_btn), ("q6_k", self._stt_quant_q6k_btn), ("f16", self._stt_quant_f16_btn), ("int8", self._stt_quant_int8_btn)]:
            _btn.visible = _q in _init_available
            if _q == _init_active:
                _btn.bgcolor = COLOR_PRIMARY
                _btn.border = ft.Border.all(1, COLOR_PRIMARY)
                _btn.content.color = ft.Colors.WHITE
                _btn.content.weight = ft.FontWeight.BOLD
        self._stt_quant_row = ft.Row(
            [self._stt_quant_label, self._stt_quant_q8_btn, self._stt_quant_q6k_btn, self._stt_quant_f16_btn, self._stt_quant_int8_btn],
            spacing=8,
        )
        self._stt_backend_label = ft.Text(
            t("settings.backend.label", default="Backend:"), size=14, color=COLOR_ON_BACKGROUND
        )
        # Set initial backend button state based on loaded settings
        _init_backend = self._initial_settings.provider.stt_backend if self._initial_settings else "onnx"
        _init_is_onnx = _init_backend == "onnx"
        self._stt_backend_onnx_btn = ft.Container(
            content=ft.Text("DirectML", size=14, weight=ft.FontWeight.BOLD if _init_is_onnx else ft.FontWeight.NORMAL, color=ft.Colors.WHITE if _init_is_onnx else COLOR_ON_BACKGROUND),
            bgcolor=COLOR_PRIMARY if _init_is_onnx else COLOR_SURFACE,
            border=ft.Border.all(1, COLOR_PRIMARY if _init_is_onnx else COLOR_DIVIDER),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_stt_backend_onnx_click,
        )
        self._stt_backend_gguf_btn = ft.Container(
            content=ft.Text("Vulkan", size=14, weight=ft.FontWeight.BOLD if not _init_is_onnx else ft.FontWeight.NORMAL, color=ft.Colors.WHITE if not _init_is_onnx else COLOR_ON_BACKGROUND),
            bgcolor=COLOR_PRIMARY if not _init_is_onnx else COLOR_SURFACE,
            border=ft.Border.all(1, COLOR_PRIMARY if not _init_is_onnx else COLOR_DIVIDER),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_stt_backend_gguf_click,
        )
        self._stt_backend_row = ft.Row(
            [self._stt_backend_label, self._stt_backend_onnx_btn, self._stt_backend_gguf_btn],
            spacing=8,
            visible=False,
        )
        self._stt_title = ft.Text(
            t("settings.section.stt"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        self._stt_provider_label = ft.Text(
            t("settings.self_stt_provider"), size=16, color=COLOR_ON_BACKGROUND
        )
        return self._wrap_unit_card(
            title=self._stt_title,
            value=self._stt_text,
        )

    # PEER STT BUILD — mirrors _build_stt_widgets for peer side.
    # Reads self._initial_settings for initial peer quant/backend state.
    # Returns peer_stt_card, but creates ~15 self._* peer_stt_* attributes.

    def _build_peer_stt_widgets(self) -> ft.Control:
        self._peer_provider_title = ft.Text(
            t("settings.section.peer_stt"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._dashboard_language_redirect_text = ft.Text(
            t("settings.dashboard_language_redirect"),
            size=16,
            color=COLOR_NEUTRAL,
        )
        self._peer_stt_text = self._build_clickable_text(
            provider_label(STTProviderName.NONE.value),
            self._on_peer_stt_click,
        )
        self._peer_stt_compute_label = ft.Text(
            t("settings.compute.label"), size=14, color=COLOR_ON_BACKGROUND
        )
        self._peer_stt_compute_gpu_btn = ft.Container(
            content=ft.Text("GPU", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
            bgcolor=COLOR_PRIMARY,
            border=ft.Border.all(1, COLOR_PRIMARY),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_peer_stt_compute_gpu_click,
        )
        self._peer_stt_compute_cpu_btn = ft.Container(
            content=ft.Text("CPU", size=14, color=COLOR_ON_BACKGROUND),
            bgcolor=COLOR_SURFACE,
            border=ft.Border.all(1, COLOR_DIVIDER),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_peer_stt_compute_cpu_click,
        )
        self._peer_stt_compute_row = ft.Row(
            [self._peer_stt_compute_label, self._peer_stt_compute_gpu_btn, self._peer_stt_compute_cpu_btn],
            spacing=8,
            visible=False,
        )
        self._peer_quant_label = ft.Text(
            t("settings.quant.label", default="Quant:"), size=14, color=COLOR_ON_BACKGROUND
        )
        self._peer_quant_q8_btn = self._make_quant_button("Q8_0", lambda e: self._apply_peer_quant("q8_0"))
        self._peer_quant_q6k_btn = self._make_quant_button("Q6_K", lambda e: self._apply_peer_quant("q6_k"))
        self._peer_quant_f16_btn = self._make_quant_button("F16", lambda e: self._apply_peer_quant("f16"))
        self._peer_quant_int8_btn = self._make_quant_button("int8", lambda e: self._apply_peer_quant("int8"))
        # Set initial PEER quant button state based on loaded settings
        _init_peer = self._initial_settings.provider.peer_stt if self._initial_settings else STTProviderName.NONE
        _init_peer_quant = self._initial_settings.provider.peer_stt_quant if self._initial_settings else ""
        _init_peer_available = self._INITIAL_QUANTS_FOR_PROVIDER.get(_init_peer, ["int8"])
        _init_peer_active = _init_peer_quant if _init_peer_quant in _init_peer_available else ""
        for _q, _btn in [("q8_0", self._peer_quant_q8_btn), ("q6_k", self._peer_quant_q6k_btn), ("f16", self._peer_quant_f16_btn), ("int8", self._peer_quant_int8_btn)]:
            _btn.visible = _q in _init_peer_available
            if _q == _init_peer_active:
                _btn.bgcolor = COLOR_PRIMARY
                _btn.border = ft.Border.all(1, COLOR_PRIMARY)
                _btn.content.color = ft.Colors.WHITE
                _btn.content.weight = ft.FontWeight.BOLD
        self._peer_quant_row = ft.Row(
            [self._peer_quant_label, self._peer_quant_q8_btn, self._peer_quant_q6k_btn, self._peer_quant_f16_btn, self._peer_quant_int8_btn],
            spacing=8,
        )
        self._peer_stt_backend_label = ft.Text(
            t("settings.backend.label", default="Backend:"), size=14, color=COLOR_ON_BACKGROUND
        )
        # Set initial PEER backend button state based on loaded settings
        _init_peer_backend = self._initial_settings.provider.peer_stt_backend if self._initial_settings else "onnx"
        _init_peer_is_onnx = _init_peer_backend == "onnx"
        self._peer_stt_backend_onnx_btn = ft.Container(
            content=ft.Text("DirectML", size=14, weight=ft.FontWeight.BOLD if _init_peer_is_onnx else ft.FontWeight.NORMAL, color=ft.Colors.WHITE if _init_peer_is_onnx else COLOR_ON_BACKGROUND),
            bgcolor=COLOR_PRIMARY if _init_peer_is_onnx else COLOR_SURFACE,
            border=ft.Border.all(1, COLOR_PRIMARY if _init_peer_is_onnx else COLOR_DIVIDER),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_peer_stt_backend_onnx_click,
        )
        self._peer_stt_backend_gguf_btn = ft.Container(
            content=ft.Text("Vulkan", size=14, weight=ft.FontWeight.BOLD if not _init_peer_is_onnx else ft.FontWeight.NORMAL, color=ft.Colors.WHITE if not _init_peer_is_onnx else COLOR_ON_BACKGROUND),
            bgcolor=COLOR_PRIMARY if not _init_peer_is_onnx else COLOR_SURFACE,
            border=ft.Border.all(1, COLOR_PRIMARY if not _init_peer_is_onnx else COLOR_DIVIDER),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            on_click=self._on_peer_stt_backend_gguf_click,
        )
        self._peer_stt_backend_row = ft.Row(
            [self._peer_stt_backend_label, self._peer_stt_backend_onnx_btn, self._peer_stt_backend_gguf_btn],
            spacing=8,
            visible=False,
        )
        self._peer_stt_label = ft.Text(
            t("settings.peer_stt_provider"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        return self._wrap_unit_card(
            title=self._peer_provider_title,
            value=self._peer_stt_text,
        )

    # --- Locale ---

    def _apply_locale_stt(self) -> None:
        if not hasattr(self, '_stt_backend_label'):
            return
        self._stt_backend_label.value = t("settings.backend.label", default="Engine:")
        self._stt_quant_label.value = t("settings.quant.label", default="Quality:")
        self._peer_stt_backend_label.value = t("settings.backend.label", default="Engine:")
        self._peer_quant_label.value = t("settings.quant.label", default="Quality:")
        self._stt_title.value = t("settings.section.stt")
        self._stt_compute_label.value = t("settings.compute.label")
        self._peer_stt_compute_label.value = t("settings.compute.label")
        self._stt_provider_label.value = t("settings.self_stt_provider")
        self._peer_provider_title.value = t("settings.section.peer_stt")
        self._dashboard_language_redirect_text.value = t("settings.dashboard_language_redirect")
        self._peer_stt_label.value = t("settings.peer_stt_provider")

    def _locale_sensitive_controls(self) -> tuple[ft.Container, ...]:
        """Controls that need font/text updates on locale change."""
        return (
            self._stt_text,
            self._peer_stt_text,
        )
