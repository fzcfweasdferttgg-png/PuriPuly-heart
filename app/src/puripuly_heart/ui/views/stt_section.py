"""SttSectionMixin — STT provider, quant, compute, and backend controls."""

from __future__ import annotations

import flet as ft

from puripuly_heart.config.settings import AppSettings, STTProviderName
from puripuly_heart.domain.language import get_stt_compatibility_warning
from puripuly_heart.ui.components.settings import OptionItem, SettingsModal
from puripuly_heart.ui.i18n import language_name, provider_label, t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
)


def _update_control_if_mounted(control: ft.Control) -> None:
    """Update a Flet control only while it is attached to a page."""
    if getattr(control, "page", None) is None:
        return
    try:
        control.update()
    except AssertionError as exc:
        if "Control must be added" not in str(exc):
            raise


class SttSectionMixin:
    """Mixin providing STT section methods for SettingsView."""

    _ONNX_QUANTS = ["int8"]  # extend here when fp16/fp32 are available
    _GGUF_QUANTS = ["q8_0", "q6_k", "f16"]
    _INITIAL_QUANTS_FOR_PROVIDER: dict[STTProviderName, list[str]] = {
        STTProviderName.LOCAL_QWEN: ["int8"],
        STTProviderName.LOCAL_QWEN_17B: ["int8"],
        STTProviderName.LOCAL_GIGAAM_RNNT: ["int8"],
        STTProviderName.LOCAL_PARAKEET_TDT: ["int8"],
        STTProviderName.LOCAL_GIGAAM_RNNT_GGUF: ["q8_0", "q6_k", "f16"],
        STTProviderName.LOCAL_PARAKEET_TDT_GGUF: ["q8_0", "q6_k", "f16"],
        STTProviderName.LOCAL_QWEN3_ASR_GGUF: ["q8_0", "q6_k", "f16"],
        STTProviderName.LOCAL_QWEN_17B_GGUF: ["q8_0", "q6_k", "f16"],
    }

    def _effective_peer_stt_provider(self, settings: AppSettings | None) -> STTProviderName:
        if settings is None:
            return STTProviderName.LOCAL_QWEN
        return settings.provider.peer_stt

    def _peer_stt_option_item(self, provider: STTProviderName) -> OptionItem:
        return OptionItem(
            value=provider.value,
            label=provider_label(provider.value),
            description=t(f"provider.{provider.value}.description", default=""),
        )

    def _on_stt_click(self, e) -> None:
        """Open STT provider selection modal."""
        if not self.page:
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
                value=p.value,
                label=provider_label(p.value),
                description=t(f"provider.{p.value}.description", default=""),
            )
            for p in STTProviderName if p in allowed
        ]
        current = (
            display_settings.provider.stt.value
            if display_settings is not None
            else STTProviderName.LOCAL_QWEN.value
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
        """Handle STT provider selection from modal."""
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
        draft = self._ensure_provider_settings_draft()
        draft.provider.stt = provider
        _GGUF_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        if provider in _GGUF_PROVIDERS:
            draft.provider.stt_backend = "gguf"
        else:
            draft.provider.stt_backend = "onnx"
        # Reset quant to first available when provider changes
        available_quants = self._get_quant_options(provider)
        if available_quants and draft.provider.stt_quant not in available_quants:
            draft.provider.stt_quant = available_quants[0]
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
                self.page.open(
                    ft.SnackBar(
                        ft.Text(
                            message,
                            color=ft.Colors.WHITE,
                        ),
                        bgcolor=ft.Colors.ORANGE_700,
                        duration=4000,
                        behavior=ft.SnackBarBehavior.FLOATING,
                        margin=ft.margin.only(bottom=90),
                        padding=20,
                    )
                )

        if self.page:
            self._qwen_region_btn.update()
            self._api_keys_column.update()
            self._stt_text.update()

    def _on_peer_stt_click(self, e) -> None:
        if not self.page:
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
        options = [self._peer_stt_option_item(provider) for provider in STTProviderName if provider in allowed]
        current_provider = (
            display_settings.provider.peer_stt
            if display_settings is not None
            else STTProviderName.LOCAL_QWEN
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
        draft = self._ensure_provider_settings_draft()
        draft.provider.peer_stt = provider
        _GGUF_PROVIDERS = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        if provider in _GGUF_PROVIDERS:
            draft.provider.peer_stt_backend = "gguf"
        else:
            draft.provider.peer_stt_backend = "onnx"
        # Reset quant to first available when provider changes
        available_quants = self._get_quant_options(provider)
        if available_quants and draft.provider.peer_stt_quant not in available_quants:
            draft.provider.peer_stt_quant = available_quants[0]
        self._set_unit_card_value_text(self._peer_stt_text, provider_label(value))
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        if self.page:
            self._peer_stt_text.update()
            self._qwen_region_btn.update()
            self._api_keys_column.update()
        self.has_provider_changes = True

    def _is_local_stt(self, provider: STTProviderName) -> bool:
        return provider in (STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B, STTProviderName.LOCAL_GIGAAM_RNNT, STTProviderName.LOCAL_PARAKEET_TDT, STTProviderName.LOCAL_GIGAAM_RNNT_GGUF, STTProviderName.LOCAL_PARAKEET_TDT_GGUF, STTProviderName.LOCAL_QWEN3_ASR_GGUF, STTProviderName.LOCAL_QWEN_17B_GGUF)

    def _make_quant_button(self, label: str, on_click) -> ft.Container:
        return ft.Container(
            content=ft.Text(label, size=14, color=COLOR_ON_BACKGROUND),
            bgcolor=COLOR_SURFACE,
            border=ft.border.all(1, COLOR_DIVIDER),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=16, vertical=6),
            on_click=on_click,
        )

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
            btn.border = ft.border.all(1, COLOR_PRIMARY if is_active else COLOR_DIVIDER)
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
        resolved = quant if quant in available else (available[0] if available else quant)
        self._stt_quant_row.visible = bool(available)
        self._sync_quant_buttons(
            resolved, available,
            self._stt_quant_label,
            {"q8_0": self._stt_quant_q8_btn, "q6_k": self._stt_quant_q6k_btn, "f16": self._stt_quant_f16_btn, "int8": self._stt_quant_int8_btn},
        )
        _update_control_if_mounted(self._stt_quant_row)

    def _sync_peer_quant_buttons(self, provider: STTProviderName, quant: str) -> None:
        available = self._get_quant_options(provider)
        resolved = quant if quant in available else (available[0] if available else quant)
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
        draft = self._ensure_provider_settings_draft()
        draft.provider.stt_quant = quant
        self._sync_stt_quant_buttons(draft.provider.stt, quant)
        self.has_provider_changes = True

    def _apply_peer_quant(self, quant: str) -> None:
        if not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        draft.provider.peer_stt_quant = quant
        self._sync_peer_quant_buttons(draft.provider.peer_stt, quant)
        self.has_provider_changes = True

    def _sync_stt_compute_buttons(self, compute: str) -> None:
        is_gpu = compute == "gpu"
        self._stt_compute_gpu_btn.bgcolor = COLOR_PRIMARY if is_gpu else COLOR_SURFACE
        self._stt_compute_gpu_btn.border = ft.border.all(1, COLOR_PRIMARY if is_gpu else COLOR_DIVIDER)
        self._stt_compute_gpu_btn.content.color = ft.Colors.WHITE if is_gpu else COLOR_ON_BACKGROUND
        self._stt_compute_gpu_btn.content.weight = ft.FontWeight.BOLD if is_gpu else ft.FontWeight.NORMAL
        self._stt_compute_cpu_btn.bgcolor = COLOR_PRIMARY if not is_gpu else COLOR_SURFACE
        self._stt_compute_cpu_btn.border = ft.border.all(1, COLOR_PRIMARY if not is_gpu else COLOR_DIVIDER)
        self._stt_compute_cpu_btn.content.color = ft.Colors.WHITE if not is_gpu else COLOR_ON_BACKGROUND
        self._stt_compute_cpu_btn.content.weight = ft.FontWeight.BOLD if not is_gpu else ft.FontWeight.NORMAL
        _update_control_if_mounted(self._stt_compute_gpu_btn)
        _update_control_if_mounted(self._stt_compute_cpu_btn)

    def _sync_peer_stt_compute_buttons(self, compute: str) -> None:
        is_gpu = compute == "gpu"
        self._peer_stt_compute_gpu_btn.bgcolor = COLOR_PRIMARY if is_gpu else COLOR_SURFACE
        self._peer_stt_compute_gpu_btn.border = ft.border.all(1, COLOR_PRIMARY if is_gpu else COLOR_DIVIDER)
        self._peer_stt_compute_gpu_btn.content.color = ft.Colors.WHITE if is_gpu else COLOR_ON_BACKGROUND
        self._peer_stt_compute_gpu_btn.content.weight = ft.FontWeight.BOLD if is_gpu else ft.FontWeight.NORMAL
        self._peer_stt_compute_cpu_btn.bgcolor = COLOR_PRIMARY if not is_gpu else COLOR_SURFACE
        self._peer_stt_compute_cpu_btn.border = ft.border.all(1, COLOR_PRIMARY if not is_gpu else COLOR_DIVIDER)
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
        self._settings.provider.stt_compute = value
        self._sync_stt_compute_buttons(value)
        self.has_provider_changes = True
        self._emit_runtime_basic(f"[Settings] STT compute changed: {value}")

    def _apply_peer_stt_compute(self, value: str) -> None:
        if not self._settings:
            return
        if self._settings.provider.peer_stt_compute == value:
            return
        self._settings.provider.peer_stt_compute = value
        self._sync_peer_stt_compute_buttons(value)
        self.has_provider_changes = True
        self._emit_runtime_basic(f"[Settings] Peer STT compute changed: {value}")

    def _sync_stt_backend_buttons(self, backend: str) -> None:
        is_onnx = backend == "onnx"
        self._stt_backend_onnx_btn.bgcolor = COLOR_PRIMARY if is_onnx else COLOR_SURFACE
        self._stt_backend_onnx_btn.border = ft.border.all(1, COLOR_PRIMARY if is_onnx else COLOR_DIVIDER)
        self._stt_backend_onnx_btn.content.color = ft.Colors.WHITE if is_onnx else COLOR_ON_BACKGROUND
        self._stt_backend_onnx_btn.content.weight = ft.FontWeight.BOLD if is_onnx else ft.FontWeight.NORMAL
        self._stt_backend_gguf_btn.bgcolor = COLOR_PRIMARY if not is_onnx else COLOR_SURFACE
        self._stt_backend_gguf_btn.border = ft.border.all(1, COLOR_PRIMARY if not is_onnx else COLOR_DIVIDER)
        self._stt_backend_gguf_btn.content.color = ft.Colors.WHITE if not is_onnx else COLOR_ON_BACKGROUND
        self._stt_backend_gguf_btn.content.weight = ft.FontWeight.BOLD if not is_onnx else ft.FontWeight.NORMAL
        _update_control_if_mounted(self._stt_backend_onnx_btn)
        _update_control_if_mounted(self._stt_backend_gguf_btn)

    def _sync_peer_stt_backend_buttons(self, backend: str) -> None:
        is_onnx = backend == "onnx"
        self._peer_stt_backend_onnx_btn.bgcolor = COLOR_PRIMARY if is_onnx else COLOR_SURFACE
        self._peer_stt_backend_onnx_btn.border = ft.border.all(1, COLOR_PRIMARY if is_onnx else COLOR_DIVIDER)
        self._peer_stt_backend_onnx_btn.content.color = ft.Colors.WHITE if is_onnx else COLOR_ON_BACKGROUND
        self._peer_stt_backend_onnx_btn.content.weight = ft.FontWeight.BOLD if is_onnx else ft.FontWeight.NORMAL
        self._peer_stt_backend_gguf_btn.bgcolor = COLOR_PRIMARY if not is_onnx else COLOR_SURFACE
        self._peer_stt_backend_gguf_btn.border = ft.border.all(1, COLOR_PRIMARY if not is_onnx else COLOR_DIVIDER)
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

    def _apply_stt_backend(self, value: str) -> None:
        if not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        if draft.provider.stt_backend == value:
            return
        draft.provider.stt_backend = value
        _ONNX_TO_GGUF = {
            STTProviderName.LOCAL_GIGAAM_RNNT: STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT: STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN: STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B: STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        _GGUF_TO_ONNX = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF: STTProviderName.LOCAL_GIGAAM_RNNT,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF: STTProviderName.LOCAL_PARAKEET_TDT,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF: STTProviderName.LOCAL_QWEN,
            STTProviderName.LOCAL_QWEN_17B_GGUF: STTProviderName.LOCAL_QWEN_17B,
        }
        current = draft.provider.stt
        if value == "gguf" and current in _ONNX_TO_GGUF:
            draft.provider.stt = _ONNX_TO_GGUF[current]
            self._set_unit_card_value_text(self._stt_text, provider_label(draft.provider.stt.value))
        elif value == "onnx" and current in _GGUF_TO_ONNX:
            draft.provider.stt = _GGUF_TO_ONNX[current]
            self._set_unit_card_value_text(self._stt_text, provider_label(draft.provider.stt.value))
        # Reset quant when backend changes (ONNX→GGUF or GGUF→ONNX)
        available_quants = self._get_quant_options(draft.provider.stt)
        if available_quants and draft.provider.stt_quant not in available_quants:
            draft.provider.stt_quant = available_quants[0]
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        self.has_provider_changes = True

    def _apply_peer_stt_backend(self, value: str) -> None:
        if not self._settings:
            return
        draft = self._ensure_provider_settings_draft()
        if draft.provider.peer_stt_backend == value:
            return
        draft.provider.peer_stt_backend = value
        _ONNX_TO_GGUF = {
            STTProviderName.LOCAL_GIGAAM_RNNT: STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
            STTProviderName.LOCAL_PARAKEET_TDT: STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
            STTProviderName.LOCAL_QWEN: STTProviderName.LOCAL_QWEN3_ASR_GGUF,
            STTProviderName.LOCAL_QWEN_17B: STTProviderName.LOCAL_QWEN_17B_GGUF,
        }
        _GGUF_TO_ONNX = {
            STTProviderName.LOCAL_GIGAAM_RNNT_GGUF: STTProviderName.LOCAL_GIGAAM_RNNT,
            STTProviderName.LOCAL_PARAKEET_TDT_GGUF: STTProviderName.LOCAL_PARAKEET_TDT,
            STTProviderName.LOCAL_QWEN3_ASR_GGUF: STTProviderName.LOCAL_QWEN,
            STTProviderName.LOCAL_QWEN_17B_GGUF: STTProviderName.LOCAL_QWEN_17B,
        }
        current = draft.provider.peer_stt
        if value == "gguf" and current in _ONNX_TO_GGUF:
            draft.provider.peer_stt = _ONNX_TO_GGUF[current]
            self._set_unit_card_value_text(self._peer_stt_text, provider_label(draft.provider.peer_stt.value))
        elif value == "onnx" and current in _GGUF_TO_ONNX:
            draft.provider.peer_stt = _GGUF_TO_ONNX[current]
            self._set_unit_card_value_text(self._peer_stt_text, provider_label(draft.provider.peer_stt.value))
        available_quants = self._get_quant_options(draft.provider.peer_stt)
        if available_quants and draft.provider.peer_stt_quant not in available_quants:
            draft.provider.peer_stt_quant = available_quants[0]
        # Build merged settings once and pass to _update_api_visibility to avoid redundant deepcopy
        merged = self._build_settings_with_provider_draft()
        self._update_api_visibility(merged)
        self.has_provider_changes = True
