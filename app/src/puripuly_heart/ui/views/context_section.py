from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.domain.settings_commands import ChangeIntegratedContext
from puripuly_heart.ui.components.settings import (
    OptionItem,
    PromptEditor,
    SettingsModal,
)
from puripuly_heart.ui.components.shared_card_wrapper import SharedCardWrapper
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.domain.i18n import get_locale, t
from puripuly_heart.ui.views.settings_helpers import _make_text_button, _set_text_button_label
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
)

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings
    from puripuly_heart.ui.components.settings import SettingsUnitCard


# ATTRIBUTE OWNERSHIP — two widget builders:
#   _build_integrated_context_unit_card: _integrated_context_label/button/hint/card
#   _build_prompt_widgets: _prompt_editor, _prompt_mode, _prompt_single/dual_btn,
#     _prompt_mode_row, _persona_title, _prompt_for_text, _reset_prompt_btn
#
# DRAFT PATTERN: _on_prompt_change → _stage_prompt_draft (settings.py)
# stages prompt text in _provider_settings_draft without triggering apply.
# _on_prompt_commit → consume_prompt_apply_settings applies only if no pending
# provider changes. Prompt changes are SEPARATE from provider changes —
# they use has_pending_prompt_changes, not has_provider_changes.
#
# _prompt_mode toggles between "single" (default per-provider prompt) and "dual"
# (dual-translation template). Mode affects which prompt text is shown.

class ContextSectionMixin:

    # ------------------------------------------------------------------
    # Load from settings
    # ------------------------------------------------------------------

    def _load_context_from_settings(self, settings: "AppSettings") -> None:
        if not hasattr(self, '_prompt_editor'):
            return
        provider_name = self._active_prompt_key()
        self._prompt_editor.set_provider(provider_name)
        settings.system_prompts = {}
        if settings.system_prompt.strip():
            self._prompt_editor.value = settings.system_prompt
        else:
            self._prompt_editor.load_default_prompt(emit_change=False)
            settings.system_prompt = self._prompt_editor.value
        self._sync_prompt_tab_copy()

    # ------------------------------------------------------------------
    # Integrated context card
    # ------------------------------------------------------------------

    def _build_integrated_context_unit_card(self) -> SettingsUnitCard:
        self._integrated_context_label = ft.Text(
            t("flet.settings.integrated_context"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._integrated_context_button = self._build_clickable_text(
            t("flet.settings.context.local"),
            self._on_integrated_context_click,
        )
        self._integrated_context_hint = ft.Text("", size=13, color=COLOR_NEUTRAL)

        self._integrated_context_card = self._wrap_unit_card(
            title=self._integrated_context_label,
            value=self._integrated_context_button,
        )
        return self._integrated_context_card

    # ------------------------------------------------------------------
    # Integrated context selection
    # ------------------------------------------------------------------

    def _on_integrated_context_click(self, e) -> None:
        try:
            if not self.page or not self._settings:
                return
        except (AssertionError, RuntimeError):
            return
        options = [
            OptionItem(value="off", label=t("flet.settings.context.local")),
            OptionItem(
                value="on",
                label=t("flet.settings.context.integrated"),
                description=t("flet.settings.context.integrated_modal_helper"),
            ),
        ]
        modal = SettingsModal(
            self.page,
            t("flet.settings.integrated_context"),
            options,
            self._on_integrated_context_selected,
            show_description=True,
        )
        modal.open("on" if self._settings.ui.integrated_context_enabled else "off")

    def _on_integrated_context_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._command_executor.execute(ChangeIntegratedContext(enabled=(value == "on")))
        self._sync_overlay_controls()
        self._emit_settings_changed()

    # ------------------------------------------------------------------
    # Prompt widgets
    # ------------------------------------------------------------------

    def _build_prompt_widgets(self) -> ft.Control:
        self._prompt_editor = PromptEditor(
            on_change=self._on_prompt_change,
            on_commit=self._on_prompt_commit,
        )
        self._prompt_mode = "single"  # "single" or "dual"
        self._prompt_single_btn = self._make_quant_button(
            t("flet.settings.prompt_mode.single", default="Single"),
            self._on_prompt_mode_single,
        )
        self._prompt_dual_btn = self._make_quant_button(
            t("flet.settings.prompt_mode.dual", default="Dual"),
            self._on_prompt_mode_dual,
        )
        self._prompt_single_btn.bgcolor = COLOR_PRIMARY
        self._prompt_single_btn.border = ft.Border.all(1, COLOR_PRIMARY)
        self._prompt_single_btn.content.color = ft.Colors.WHITE
        self._prompt_single_btn.content.weight = ft.FontWeight.BOLD
        self._prompt_mode_row = ft.Row(
            [self._prompt_single_btn, self._prompt_dual_btn],
            spacing=4,
        )
        self._persona_title = ft.Text(
            t("flet.settings.section.persona"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        self._prompt_for_text = ft.Text(
            self._prompt_provider_copy(),
            size=16,
            color=COLOR_NEUTRAL,
        )

        # Reset button (matches Persona title color, hover -> primary)
        self._reset_prompt_btn = _make_text_button(
            t("flet.settings.reset_prompt"),
            icon=ft.Icons.REFRESH_ROUNDED,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                icon_color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                text_style=ft.TextStyle(
                    size=20,
                    font_family=font_for_language(get_locale()),
                ),
                overlay_color=ft.Colors.TRANSPARENT,
                animation_duration=0,
            ),
            on_click=self._on_reset_prompt,
        )

        # Header row with title, prompt mode toggle, and reset button
        persona_header = ft.Row(
            controls=[self._persona_title, self._prompt_mode_row, ft.Container(expand=True), self._reset_prompt_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Simple container like Licenses (no border, no internal scroll)
        prompt_container = ft.Container(
            content=self._prompt_editor,
            width=float("inf"),
        )

        persona_card = SharedCardWrapper(
            ft.Column(
                [
                    persona_header,
                    ft.Container(height=16),
                    prompt_container,
                ],
                spacing=0,
            ),
            height=None,
            expand=False,
        )
        return persona_card

    # ------------------------------------------------------------------
    # Prompt editing
    # ------------------------------------------------------------------

    def _on_prompt_change(self, value: str) -> None:
        self._stage_prompt_draft(value)

    def _on_prompt_commit(self, value: str) -> None:
        if not self.has_pending_prompt_changes and value == self._committed_prompt_value():
            return
        self._stage_prompt_draft(value)
        if self.has_provider_changes:
            return
        pending = self.consume_prompt_apply_settings()
        if pending is None:
            return
        self._emit_prompt_apply_settings(pending)

    def _on_reset_prompt(self, e) -> None:
        if self._prompt_mode != "single":
            self._prompt_mode = "single"
            self._sync_prompt_mode_buttons()
            try:
                if self.page:
                    self._prompt_mode_row.update()
            except (AssertionError, RuntimeError):
                pass
        self._prompt_editor.load_default_prompt()
        self._on_prompt_commit(self._prompt_editor.value)

    # ------------------------------------------------------------------
    # Prompt mode toggle
    # ------------------------------------------------------------------

    def _on_prompt_mode_single(self, e) -> None:
        if self._prompt_mode == "single":
            return
        self._prompt_mode = "single"
        self._sync_prompt_mode_buttons()
        self._prompt_editor.load_default_prompt(emit_change=False)
        try:
            if self.page:
                self._prompt_mode_row.update()
        except (AssertionError, RuntimeError):
            pass

    def _on_prompt_mode_dual(self, e) -> None:
        if self._prompt_mode == "dual":
            return
        self._prompt_mode = "dual"
        self._sync_prompt_mode_buttons()
        from puripuly_heart.config.prompts import get_dual_translation_prompt_template
        dual = get_dual_translation_prompt_template()
        self._prompt_editor.value = dual if dual else t("flet.settings.prompt_mode.dual_not_found", default="Dual prompt template not found")
        try:
            if self.page:
                self._prompt_mode_row.update()
        except (AssertionError, RuntimeError):
            pass

    def _sync_prompt_mode_buttons(self) -> None:
        is_single = self._prompt_mode == "single"
        self._prompt_single_btn.bgcolor = COLOR_PRIMARY if is_single else COLOR_SURFACE
        self._prompt_single_btn.border = ft.Border.all(1, COLOR_PRIMARY if is_single else COLOR_DIVIDER)
        self._prompt_single_btn.content.color = ft.Colors.WHITE if is_single else COLOR_ON_BACKGROUND
        self._prompt_single_btn.content.weight = ft.FontWeight.BOLD if is_single else ft.FontWeight.NORMAL
        self._prompt_dual_btn.bgcolor = COLOR_PRIMARY if not is_single else COLOR_SURFACE
        self._prompt_dual_btn.border = ft.Border.all(1, COLOR_PRIMARY if not is_single else COLOR_DIVIDER)
        self._prompt_dual_btn.content.color = ft.Colors.WHITE if not is_single else COLOR_ON_BACKGROUND
        self._prompt_dual_btn.content.weight = ft.FontWeight.BOLD if not is_single else ft.FontWeight.NORMAL

    # ------------------------------------------------------------------
    # Locale helpers
    # ------------------------------------------------------------------

    def _apply_locale_context(self) -> None:
        if not hasattr(self, '_persona_title'):
            return
        self._persona_title.value = t("flet.settings.section.persona")
        _set_text_button_label(self._reset_prompt_btn, t("flet.settings.reset_prompt"))
        self._sync_prompt_tab_copy()
        self._prompt_single_btn.content.value = t("flet.settings.prompt_mode.single", default="Single")
        self._prompt_dual_btn.content.value = t("flet.settings.prompt_mode.dual", default="Dual")
        self._integrated_context_label.value = t("flet.settings.integrated_context")

    def _locale_sensitive_controls(self) -> tuple[ft.Container, ...]:
        """Controls that need font/text updates on locale change."""
        return (
            self._integrated_context_button,
        )
