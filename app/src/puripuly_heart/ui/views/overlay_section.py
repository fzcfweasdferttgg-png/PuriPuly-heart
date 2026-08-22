"""Overlay-related mixin for the Settings view."""

from __future__ import annotations

import copy
import math
from typing import TYPE_CHECKING

import flet as ft

from puripuly_heart.config.settings import (
    DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA,
    DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER,
    DESKTOP_FLET_SIZE_PRESET_ORDER,
    OVERLAY_TARGET_DESKTOP,
    OVERLAY_TARGET_STEAMVR,
)
from puripuly_heart.ui.components.settings import (
    OptionItem,
    SettingsModal,
    SettingsUnitCard,
)
from puripuly_heart.domain.i18n import t
from puripuly_heart.domain.overlay_calibration import OverlayCalibration
from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
)
from puripuly_heart.domain.settings_commands import (
    ChangeOverlayTarget,
    ChangeOverlayDesktopSize,
    ChangeOverlayDesktopBackgroundAlpha,
    ChangeOverlayTranslation,
    ChangeOverlayPeerOriginal,
    ChangeOverlayDesktopPositionReset,
)

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings
    from puripuly_heart.domain.overlay_contract import OverlayPeerConsumerContract

from puripuly_heart.ui.views.overlay_constants import (
    _OVERLAY_DISTANCE_MIN,
    _OVERLAY_DISTANCE_MAX,
    _OVERLAY_DISTANCE_DIVISIONS,
    _OVERLAY_OFFSET_STEP,
    _DESKTOP_OVERLAY_BACKGROUND_ALPHA_STEP,
    _OVERLAY_TEXT_SCALE_PRESETS,
    _DESKTOP_OVERLAY_REOPEN_FAILURE_REASONS,
)


# ATTRIBUTE OWNERSHIP — _build_overlay_widgets creates ~30 controls:
#   _overlay_target_title/button, _overlay_translation_title/button,
#   _overlay_peer_original_title/button,
#   _desktop_overlay_size_title/button/card, _desktop_overlay_lock_title/button/card,
#   _desktop_overlay_background_alpha_* (title/value_text/card_content/increase/decrease),
#   _desktop_overlay_status_title/reason_text/helper_text/primary_action/view_logs_action/body/card,
#   _overlay_empty_card, _overlay_desktop_reset_spacer_a/b
#
# _sync_overlay_controls is the central sync method — updates ALL overlay control
# visibility and disabled states. Called from set_overlay_runtime_state,
# set_overlay_peer_contract, _on_overlay_target_selected, and apply_locale.
#
# SHARED CONSTANTS: _OVERLAY_DISTANCE_MIN/MAX, _OVERLAY_OFFSET_STEP,
# _OVERLAY_TEXT_SCALE_PRESETS are also imported by CalibrationSectionMixin.

class OverlaySectionMixin:
    """Mixin that provides overlay-related helpers for SettingsView.

    This class has **no** ``__init__`` — it relies on attributes set by the
    host ``SettingsView``.
    """

    def _load_overlay_from_settings(self, settings: "AppSettings") -> None:
        if not hasattr(self, '_overlay_target_card'):
            return
        self._overlay_peer_contract = None
        self._sync_overlay_controls()
        self.set_overlay_calibration(
            settings.overlay.calibration,
            preserve_draft=self._overlay_calibration_session_active,
        )

    def _overlay_anchor_label_for(self, anchor: str) -> str:
        return t(f"settings.overlay.calibration.anchor.{anchor}")

    def _overlay_text_scale_label_for(self, value: float) -> str:
        return t(
            f"settings.overlay.calibration.text_scale.{self._overlay_text_scale_preset_key_for(value)}"
        )

    def _overlay_text_scale_preset_key_for(self, value: float) -> str:
        return min(
            _OVERLAY_TEXT_SCALE_PRESETS,
            key=lambda preset: abs(preset[1] - value),
        )[0]

    def _overlay_text_scale_value_for(self, preset_key: str) -> float:
        for key, scale in _OVERLAY_TEXT_SCALE_PRESETS:
            if key == preset_key:
                return scale
        try:
            return float(preset_key)
        except (TypeError, ValueError):
            return 1.0

    def _apply_locale_overlay(self) -> None:
        if not hasattr(self, '_overlay_target_title'):
            return
        self._overlay_target_title.value = t("settings.overlay.caption_location")
        self._overlay_translation_title.value = t("settings.overlay.show_translation")
        self._overlay_peer_original_title.value = t("settings.overlay.show_peer_original")
        self._desktop_overlay_size_title.value = t("settings.overlay.desktop.size.title")
        self._desktop_overlay_background_alpha_title.value = t(
            "settings.overlay.desktop.background_alpha.title"
        )
        self._desktop_overlay_lock_title.value = t("settings.overlay.desktop.lock.title")
        self._desktop_overlay_view_logs_action.content.value = t(
            "settings.overlay.desktop.recovery.action.view_details"
        )
        self._overlay_desktop_reset_title.value = t("settings.overlay.position_reset.desktop.title")
        self._set_unit_card_value_text(
            self._overlay_desktop_reset_button,
            t("settings.overlay.position_reset.action.desktop"),
        )

    def _build_overlay_toggle_widgets(self) -> tuple[ft.Control, ft.Control, ft.Control]:
        self._overlay_translation_title = ft.Text(
            t("settings.overlay.show_translation"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_translation_button = self._build_clickable_text(
            t("settings.option.on"),
            self._on_overlay_translation_click,
        )
        self._overlay_translation_card = self._wrap_unit_card(
            title=self._overlay_translation_title,
            value=self._overlay_translation_button,
        )

        self._overlay_peer_original_title = ft.Text(
            t("settings.overlay.show_peer_original"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_peer_original_button = self._build_clickable_text(
            t("settings.option.on"),
            self._on_overlay_peer_original_click,
        )
        self._overlay_peer_original_card = self._wrap_unit_card(
            title=self._overlay_peer_original_title,
            value=self._overlay_peer_original_button,
        )

        self._overlay_target_title = ft.Text(
            t("settings.overlay.caption_location"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_target_button = self._build_clickable_text(
            self._overlay_target_label_for(OVERLAY_TARGET_STEAMVR),
            self._on_overlay_target_click,
            size=28,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._overlay_target_card = self._wrap_unit_card(
            title=self._overlay_target_title,
            value=self._overlay_target_button,
        )

        return (self._overlay_target_card, self._overlay_translation_card, self._overlay_peer_original_card)

    def _build_desktop_overlay_widgets(self) -> tuple[ft.Control, ft.Control, ft.Control]:
        self._overlay_desktop_reset_title = ft.Text(
            t("settings.overlay.position_reset.desktop.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_desktop_reset_button = self._build_clickable_text(
            t("settings.overlay.position_reset.action.desktop"),
            self._on_desktop_overlay_position_reset,
            height=72,
            expand=False,
        )
        self._overlay_desktop_reset_card = self._wrap_unit_card(
            title=self._overlay_desktop_reset_title,
            value=self._overlay_desktop_reset_button,
        )
        self._overlay_reset_title = self._overlay_vr_reset_title

        self._desktop_overlay_size_title = ft.Text(
            t("settings.overlay.desktop.size.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._desktop_overlay_size_button = self._build_clickable_text(
            self._desktop_overlay_size_label_for("medium"),
            self._on_desktop_overlay_size_click,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._desktop_overlay_size_card = self._wrap_unit_card(
            title=self._desktop_overlay_size_title,
            value=self._desktop_overlay_size_button,
        )

        self._desktop_overlay_background_alpha_title = ft.Text(
            t("settings.overlay.desktop.background_alpha.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._desktop_overlay_background_alpha_value_text = ft.Text(
            "40%",
            size=28,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        (
            self._desktop_overlay_background_alpha_card_content,
            self._desktop_overlay_background_alpha_decrease_button,
            self._desktop_overlay_background_alpha_increase_button,
            self._desktop_overlay_background_alpha_decrease_glyph,
            self._desktop_overlay_background_alpha_increase_glyph,
        ) = self._build_overlay_step_split_layout(
            title=self._desktop_overlay_background_alpha_title,
            value_text=self._desktop_overlay_background_alpha_value_text,
            decrease_text="\uff0d",
            increase_text="\uff0b",
            on_decrease=lambda _e: self._on_desktop_overlay_background_alpha_step(
                -_DESKTOP_OVERLAY_BACKGROUND_ALPHA_STEP
            ),
            on_increase=lambda _e: self._on_desktop_overlay_background_alpha_step(
                _DESKTOP_OVERLAY_BACKGROUND_ALPHA_STEP
            ),
        )
        self._desktop_overlay_background_alpha_card = self._wrap_card(
            self._desktop_overlay_background_alpha_card_content,
            expand=True,
            height=SettingsUnitCard.DEFAULT_HEIGHT,
        )

        self._desktop_overlay_lock_title = ft.Text(
            t("settings.overlay.desktop.lock.title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._desktop_overlay_lock_button = self._build_clickable_text(
            self._desktop_overlay_lock_label_for(False),
            self._on_desktop_overlay_lock_click,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._desktop_overlay_lock_card = self._wrap_unit_card(
            title=self._desktop_overlay_lock_title,
            value=self._desktop_overlay_lock_button,
        )

        self._desktop_overlay_status_title = ft.Text(
            t("settings.overlay.status.off"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._desktop_overlay_reason_text = ft.Text(
            "",
            size=15,
            color=COLOR_NEUTRAL,
            text_align=ft.TextAlign.CENTER,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            visible=False,
        )
        self._desktop_overlay_helper_text = ft.Text(
            "",
            size=14,
            color=COLOR_NEUTRAL,
            text_align=ft.TextAlign.CENTER,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            visible=False,
        )
        self._desktop_overlay_primary_action = self._build_clickable_text(
            "",
            self._on_desktop_overlay_primary_action,
            size=20,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._desktop_overlay_primary_action.visible = False
        self._desktop_overlay_view_logs_action = self._build_clickable_text(
            t("settings.overlay.desktop.recovery.action.view_details"),
            self._on_desktop_overlay_view_logs,
            size=16,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._desktop_overlay_view_logs_action.visible = False
        self._desktop_overlay_status_body = ft.Column(
            [
                self._desktop_overlay_reason_text,
                self._desktop_overlay_primary_action,
                self._desktop_overlay_view_logs_action,
                self._desktop_overlay_helper_text,
            ],
            spacing=6,
            expand=True,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._desktop_overlay_status_card = self._wrap_unit_card(
            title=self._desktop_overlay_status_title,
            value=self._desktop_overlay_status_body,
        )
        self._overlay_empty_card = self._wrap_empty_unit_card()
        self._overlay_desktop_reset_spacer_a = self._wrap_empty_unit_card()
        self._overlay_desktop_reset_spacer_b = self._wrap_empty_unit_card()

        return (self._desktop_overlay_size_card, self._desktop_overlay_lock_card, self._desktop_overlay_background_alpha_card)

    def _settings_with_desktop_overlay_runtime_state(
        self,
        settings: AppSettings | None,
    ) -> AppSettings | None:
        if settings is None:
            return None
        pending_position_reset = getattr(self, "_desktop_overlay_pending_position_reset", False)
        desktop_settings = settings.overlay.desktop_flet
        size_preset = self._current_desktop_overlay_size_preset()
        needs_copy = desktop_settings.size_preset != size_preset or pending_position_reset
        if not needs_copy:
            return settings

        updated = copy.deepcopy(settings)
        updated_desktop = updated.overlay.desktop_flet
        updated_desktop.size_preset = size_preset
        if pending_position_reset:
            updated_desktop.position.x = None
            updated_desktop.position.y = None
            updated_desktop.locked = False
        updated_desktop.validate()
        return updated

    def _normalized_overlay_target(self, value: object) -> str:
        return OVERLAY_TARGET_DESKTOP if value == OVERLAY_TARGET_DESKTOP else OVERLAY_TARGET_STEAMVR

    def _current_overlay_target(self) -> str:
        if self._settings is None:
            return OVERLAY_TARGET_STEAMVR
        return self._normalized_overlay_target(self._settings.overlay.target)

    def _overlay_target_label_for(self, target: object) -> str:
        normalized_target = self._normalized_overlay_target(target)
        return t(f"settings.overlay.target.{normalized_target}")

    def _sync_overlay_target_control(self) -> None:
        self._set_unit_card_value_text(
            self._overlay_target_button,
            self._overlay_target_label_for(self._current_overlay_target()),
            size=28,
        )
        self._overlay_target_button.disabled = self._settings is None

    def _sync_overlay_target_specific_visibility(self) -> None:
        desktop_selected = self._current_overlay_target() == OVERLAY_TARGET_DESKTOP
        for row in getattr(self, "_overlay_vr_rows", ()):
            row.visible = not desktop_selected
        for row in getattr(self, "_overlay_desktop_rows", ()):
            row.visible = desktop_selected

    @staticmethod
    def _normalize_desktop_overlay_size_preset(value: object) -> str:
        if isinstance(value, str) and value in DESKTOP_FLET_SIZE_PRESET_ORDER:
            return value
        return "medium"

    @staticmethod
    def _normalize_desktop_overlay_background_alpha(value: object) -> float:
        if isinstance(value, bool):
            return DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
        try:
            alpha = float(value)
        except (TypeError, ValueError):
            return DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
        if not math.isfinite(alpha):
            return DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
        return max(0.0, min(1.0, alpha))

    def _desktop_overlay_background_alpha_label_for(self, value: object) -> str:
        alpha = self._normalize_desktop_overlay_background_alpha(value)
        transparency = 1.0 - alpha
        return f"{int(round(transparency * 100))}%"

    def _desktop_overlay_size_label_for(self, size_preset: object) -> str:
        normalized = self._normalize_desktop_overlay_size_preset(size_preset)
        return t(f"settings.overlay.desktop.size.option.{normalized}")

    def _current_desktop_overlay_size_preset(self) -> str:
        pending_size_preset = getattr(self, "_desktop_overlay_pending_size_preset", None)
        if pending_size_preset is not None:
            return pending_size_preset
        if self._settings is None:
            return "medium"
        return self._normalize_desktop_overlay_size_preset(
            self._settings.overlay.desktop_flet.size_preset
        )

    def _current_desktop_overlay_background_alpha(self) -> float:
        if self._settings is None:
            return DESKTOP_FLET_DEFAULT_BACKGROUND_ALPHA
        return self._normalize_desktop_overlay_background_alpha(
            self._settings.overlay.desktop_flet.visual.background_alpha
        )

    def _desktop_overlay_lock_label_for(self, locked: bool) -> str:
        return t(
            "settings.overlay.desktop.lock.value.locked"
            if locked
            else "settings.overlay.desktop.lock.value.move"
        )

    def _current_desktop_overlay_locked(self) -> bool:
        if self._settings is None:
            return False
        if getattr(self, "_desktop_overlay_pending_position_reset", False):
            return False
        if not self._desktop_overlay_runtime_lock_applies():
            return False
        pending_locked = getattr(self, "_desktop_overlay_pending_locked", None)
        if pending_locked is not None:
            return bool(pending_locked)
        return bool(getattr(self, "_desktop_overlay_captions_locked", False))

    def _desktop_overlay_runtime_lock_applies(self) -> bool:
        if getattr(self, "_overlay_state", "off") not in {"connected", "running"}:
            return False
        return (
            self._normalized_overlay_target(
                getattr(self, "_overlay_runtime_target", OVERLAY_TARGET_STEAMVR)
            )
            == OVERLAY_TARGET_DESKTOP
        )

    def _sync_desktop_overlay_main_controls(self) -> None:
        self._set_unit_card_value_text(
            self._desktop_overlay_size_button,
            self._desktop_overlay_size_label_for(self._current_desktop_overlay_size_preset()),
        )
        self._set_unit_card_value_text(
            self._desktop_overlay_lock_button,
            self._desktop_overlay_lock_label_for(self._current_desktop_overlay_locked()),
        )
        self._desktop_overlay_background_alpha_value_text.value = (
            self._desktop_overlay_background_alpha_label_for(
                self._current_desktop_overlay_background_alpha()
            )
        )
        disabled = self._settings is None
        self._desktop_overlay_size_button.disabled = disabled
        self._desktop_overlay_background_alpha_decrease_button.disabled = disabled
        self._desktop_overlay_background_alpha_increase_button.disabled = disabled
        self._desktop_overlay_lock_button.disabled = disabled
        self._overlay_vr_reset_button.disabled = disabled
        self._overlay_desktop_reset_button.disabled = disabled

    def _desktop_overlay_status_is_visible(self) -> bool:
        return bool(
            self._current_overlay_target() == OVERLAY_TARGET_DESKTOP
            or self._normalized_overlay_target(self._overlay_runtime_target)
            == OVERLAY_TARGET_DESKTOP
        )

    def _desktop_overlay_failure_action_kind(self) -> str:
        if self._overlay_failure_reason in _DESKTOP_OVERLAY_REOPEN_FAILURE_REASONS:
            return "reopen"
        return "retry"

    def _set_desktop_overlay_primary_action(
        self,
        *,
        label_key: str | None,
        action_kind: str | None,
        visible: bool,
    ) -> None:
        self._set_unit_card_value_text(
            self._desktop_overlay_primary_action,
            t(label_key) if label_key else "",
            size=20,
        )
        self._desktop_overlay_primary_action_kind = action_kind
        self._desktop_overlay_primary_action.visible = visible

    def _sync_desktop_overlay_status_control(self) -> None:
        state = self._overlay_state
        desktop_status_visible = self._desktop_overlay_status_is_visible() and state == "failed"
        self._desktop_overlay_status_card.visible = desktop_status_visible
        self._desktop_overlay_recovery_row.visible = desktop_status_visible
        self._desktop_overlay_reason_text.visible = False
        self._desktop_overlay_reason_text.value = ""
        self._desktop_overlay_helper_text.visible = False
        self._desktop_overlay_helper_text.value = ""
        self._desktop_overlay_view_logs_action.visible = False
        self._desktop_overlay_view_logs_action.disabled = False

        if state == "failed":
            self._desktop_overlay_status_title.value = t("settings.overlay.desktop.status.failed")
            action_kind = self._desktop_overlay_failure_action_kind()
            self._desktop_overlay_reason_text.value = t(
                f"settings.overlay.desktop.recovery.message.{action_kind}",
                default=t("settings.overlay.desktop.recovery.message.retry"),
            )
            self._desktop_overlay_reason_text.visible = True
            action_key = (
                "settings.overlay.desktop.recovery.action.reopen"
                if action_kind == "reopen"
                else "settings.overlay.desktop.recovery.action.retry"
            )
            self._set_desktop_overlay_primary_action(
                label_key=action_key,
                action_kind=action_kind,
                visible=True,
            )
            self._desktop_overlay_view_logs_action.visible = True
        else:
            self._desktop_overlay_status_title.value = t(
                "settings.overlay.status.stopping"
                if state == "stopping"
                else "settings.overlay.status.off"
            )
            self._set_desktop_overlay_primary_action(
                label_key=None,
                action_kind=None,
                visible=False,
            )

    def _on_overlay_target_click(self, e) -> None:
        _ = e
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(
                value=OVERLAY_TARGET_STEAMVR,
                label=self._overlay_target_label_for(OVERLAY_TARGET_STEAMVR),
            ),
            OptionItem(
                value=OVERLAY_TARGET_DESKTOP,
                label=self._overlay_target_label_for(OVERLAY_TARGET_DESKTOP),
            ),
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.caption_location"),
            options,
            self._on_overlay_target_selected,
            show_description=True,
        )
        modal.open(self._current_overlay_target())

    def _on_overlay_target_selected(self, value: str) -> None:
        if not self._settings:
            return
        target = self._normalized_overlay_target(value)
        if self._current_overlay_target() == target:
            return
        self._command_executor.execute(ChangeOverlayTarget(target=target))
        if self._overlay_state == "off":
            self._overlay_runtime_target = target
        self._sync_overlay_controls()
        self._emit_settings_changed()

    def _on_desktop_overlay_size_click(self, e) -> None:
        _ = e
        if not self.page or not self._settings or self._desktop_overlay_size_button.disabled:
            return
        options = [
            OptionItem(
                value=preset,
                label=self._desktop_overlay_size_label_for(preset),
            )
            for preset in DESKTOP_FLET_SIZE_PRESET_DISPLAY_ORDER
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.desktop.size.title"),
            options,
            self._on_desktop_overlay_size_selected,
            show_description=False,
        )
        modal.open(self._current_desktop_overlay_size_preset())

    def _on_desktop_overlay_size_selected(self, value: str) -> None:
        if not self._settings:
            return
        size_preset = self._normalize_desktop_overlay_size_preset(value)
        if self._current_desktop_overlay_size_preset() == size_preset:
            return
        if self.on_desktop_overlay_size_change:
            self._desktop_overlay_pending_size_preset = size_preset
            self._sync_desktop_overlay_main_controls()
            self.on_desktop_overlay_size_change(size_preset)
            return
        self._command_executor.execute(ChangeOverlayDesktopSize(size_preset=size_preset))
        self._desktop_overlay_pending_size_preset = None
        self._sync_desktop_overlay_main_controls()
        self._emit_settings_changed()

    def _on_desktop_overlay_lock_click(self, e) -> None:
        _ = e
        if not self._settings or self._desktop_overlay_lock_button.disabled:
            return
        next_value = "move" if self._current_desktop_overlay_locked() else "locked"
        self._on_desktop_overlay_lock_selected(next_value)

    def _on_desktop_overlay_lock_selected(self, value: str) -> None:
        if not self._settings:
            return
        locked = value == "locked"
        if self._current_desktop_overlay_locked() == locked:
            return
        if not self._desktop_overlay_runtime_lock_applies():
            self._sync_desktop_overlay_main_controls()
            return
        if self.on_desktop_overlay_lock_change:
            self._desktop_overlay_pending_locked = locked
            self._desktop_overlay_captions_locked = locked
            self._sync_desktop_overlay_main_controls()
            self.on_desktop_overlay_lock_change(locked)
            return
        self._desktop_overlay_pending_locked = locked
        self._desktop_overlay_captions_locked = locked
        self._sync_desktop_overlay_main_controls()

    def _on_desktop_overlay_background_alpha_step(self, delta: float) -> None:
        if not self._settings or self._desktop_overlay_background_alpha_decrease_button.disabled:
            return
        current = self._current_desktop_overlay_background_alpha()
        current_transparency = 1.0 - current
        next_transparency = self._normalize_desktop_overlay_background_alpha(
            round(current_transparency + delta, 2)
        )
        next_alpha = self._normalize_desktop_overlay_background_alpha(
            round(1.0 - next_transparency, 2)
        )
        if current == next_alpha:
            self._sync_desktop_overlay_main_controls()
            try:
                if self.page:
                    self.update()
            except (AssertionError, RuntimeError):
                pass
            return
        self._command_executor.execute(ChangeOverlayDesktopBackgroundAlpha(alpha=next_alpha))
        self._sync_desktop_overlay_main_controls()
        try:
            if self.page:
                self.update()
        except (AssertionError, RuntimeError):
            pass
        self._emit_settings_changed()

    def _on_desktop_overlay_primary_action(self, e) -> None:
        _ = e
        action_kind = self._desktop_overlay_primary_action_kind
        if action_kind == "lock" and self.on_desktop_overlay_lock_change:
            self.on_desktop_overlay_lock_change(True)
        elif action_kind == "edit" and self.on_desktop_overlay_lock_change:
            self.on_desktop_overlay_lock_change(False)
        elif action_kind in {"retry", "reopen"} and self.on_desktop_overlay_recovery_action:
            self.on_desktop_overlay_recovery_action(action_kind)

    def _on_desktop_overlay_view_logs(self, e) -> None:
        _ = e
        if self.on_view_logs:
            self.on_view_logs()

    def _on_desktop_overlay_position_reset(self, e) -> None:
        _ = e
        if not self._settings or self._overlay_desktop_reset_button.disabled:
            return
        if self.on_desktop_overlay_position_reset:
            self._desktop_overlay_pending_position_reset = True
            self._desktop_overlay_captions_locked = False
            self._sync_desktop_overlay_main_controls()
            self.on_desktop_overlay_position_reset()
            return
        self._command_executor.execute(ChangeOverlayDesktopPositionReset())
        self._desktop_overlay_captions_locked = False
        self._desktop_overlay_pending_position_reset = False
        self._sync_desktop_overlay_main_controls()
        self._emit_settings_changed()

    def sync_desktop_overlay_settings(self, settings: AppSettings) -> None:
        self._settings = settings
        self._desktop_overlay_pending_size_preset = None
        self._desktop_overlay_pending_position_reset = False
        self._desktop_overlay_pending_locked = None
        self._desktop_overlay_captions_locked = False
        if self._overlay_state == "off":
            self._overlay_runtime_target = self._current_overlay_target()
        self._sync_overlay_controls()

    def set_overlay_peer_contract(self, contract: OverlayPeerConsumerContract) -> None:
        self._overlay_peer_contract = contract
        if self._settings is not None:
            self._settings.ui.overlay_enabled = contract.overlay.intent_enabled
            self._settings.ui.peer_translation_enabled = contract.peer.intent_enabled
            self._update_api_visibility()
        self._sync_overlay_controls()

    def _sync_overlay_controls(self) -> None:
        """Central sync method — updates ALL overlay control visibility and states."""
        self._sync_overlay_value_texts()
        self._sync_overlay_target_control()
        self._sync_overlay_target_specific_visibility()
        self._sync_desktop_overlay_main_controls()
        self._sync_desktop_overlay_status_control()
        self._sync_overlay_disabled_states()
        self._sync_calibration_disabled_states()
        self._sync_context_disabled_states()

        # Execute registered sync hooks from section mixins
        for hook in self._sync_hooks:
            hook()

        try:
            if self.page:
                self.update()
        except (AssertionError, RuntimeError):
            pass

    def _sync_overlay_value_texts(self) -> None:
        """Update overlay translation/peer-original button value texts."""
        overlay_translation_enabled = bool(
            self._settings and self._settings.overlay.show_translation
        )
        overlay_peer_original_enabled = bool(
            self._settings and self._settings.overlay.show_peer_original
        )
        self._set_unit_card_value_text(
            self._overlay_translation_button,
            t("settings.option.on" if overlay_translation_enabled else "settings.option.off"),
        )
        self._set_unit_card_value_text(
            self._overlay_peer_original_button,
            t("settings.option.on" if overlay_peer_original_enabled else "settings.option.off"),
        )

    def _sync_overlay_disabled_states(self) -> None:
        """Update disabled state of overlay controls based on settings availability."""
        self._overlay_translation_button.disabled = self._settings is None
        self._overlay_peer_original_button.disabled = self._settings is None
        self._overlay_target_button.disabled = self._settings is None
        self._overlay_distance_decrease_button.disabled = self._settings is None
        self._overlay_distance_increase_button.disabled = self._settings is None
        self._overlay_offset_x_decrease_button.disabled = self._settings is None
        self._overlay_offset_x_increase_button.disabled = self._settings is None
        self._overlay_offset_y_decrease_button.disabled = self._settings is None
        self._overlay_offset_y_increase_button.disabled = self._settings is None
        self._desktop_overlay_background_alpha_decrease_button.disabled = self._settings is None
        self._desktop_overlay_background_alpha_increase_button.disabled = self._settings is None
        self._overlay_vr_reset_button.disabled = self._settings is None
        self._overlay_desktop_reset_button.disabled = self._settings is None

    def _sync_calibration_disabled_states(self) -> None:
        """Update disabled state of calibration controls."""
        self._overlay_anchor_button.disabled = self._settings is None

    def _sync_context_disabled_states(self) -> None:
        """Update disabled state of context controls."""
        integrated_context_enabled = bool(
            self._settings and self._settings.ui.integrated_context_enabled
        )
        self._set_unit_card_value_text(
            self._integrated_context_button,
            t(
                "settings.context.integrated"
                if integrated_context_enabled
                else "settings.context.local"
            ),
        )
        self._integrated_context_button.disabled = self._settings is None
        self._integrated_context_hint.value = ""

    def set_overlay_runtime_state(
        self,
        state: str,
        *,
        failure_reason: str | None = None,
        overlay_target: str | None = None,
        desktop_captions_locked: bool | None = None,
    ) -> None:
        self._overlay_state = state
        self._overlay_failure_reason = failure_reason
        if overlay_target is not None:
            self._overlay_runtime_target = self._normalized_overlay_target(overlay_target)
        elif state == "off":
            self._overlay_runtime_target = self._current_overlay_target()
        if desktop_captions_locked is not None:
            if self._desktop_overlay_runtime_lock_applies():
                self._desktop_overlay_pending_locked = None
                self._desktop_overlay_captions_locked = bool(desktop_captions_locked)
            else:
                self._desktop_overlay_pending_locked = None
                self._desktop_overlay_captions_locked = False
        self._sync_overlay_controls()

    def _on_overlay_calibration_reset(self, e) -> None:
        _ = e
        self._begin_overlay_calibration_session()
        self._overlay_calibration_draft = OverlayCalibration()
        self._sync_overlay_calibration_controls(self._overlay_calibration_draft)

        try:
            if self.page:
                self.update()
        except (AssertionError, RuntimeError):
            pass

    def _on_overlay_translation_click(self, e) -> None:
        if not self._settings or self._overlay_translation_button.disabled:
            return
        next_value = "off" if self._settings.overlay.show_translation else "on"
        self._on_overlay_translation_selected(next_value)

    def _on_overlay_translation_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._command_executor.execute(ChangeOverlayTranslation(show=(value == "on")))
        self._sync_overlay_controls()
        self._emit_settings_changed()

    def _on_overlay_peer_original_click(self, e) -> None:
        if not self._settings or self._overlay_peer_original_button.disabled:
            return
        next_value = "off" if self._settings.overlay.show_peer_original else "on"
        self._on_overlay_peer_original_selected(next_value)

    def _on_overlay_peer_original_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._command_executor.execute(ChangeOverlayPeerOriginal(show=(value == "on")))
        self._sync_overlay_controls()
        self._emit_settings_changed()

    def _locale_sensitive_controls(self) -> tuple[ft.Container, ...]:
        """Controls that need font/text updates on locale change."""
        return (
            self._overlay_translation_button,
            self._overlay_peer_original_button,
            self._overlay_target_button,
            self._overlay_text_scale_text,
            self._desktop_overlay_size_button,
            self._desktop_overlay_lock_button,
            self._overlay_vr_reset_button,
            self._overlay_desktop_reset_button,
            self._desktop_overlay_primary_action,
            self._desktop_overlay_view_logs_action,
        )
