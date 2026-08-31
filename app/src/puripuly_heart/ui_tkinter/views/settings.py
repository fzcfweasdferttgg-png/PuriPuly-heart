"""Settings view — scrollable container with settings sections.

Each section is a :class:`CollapsibleSection` (always-expanded) imported
from ``ui_tkinter.sections.*``.  The view arranges them vertically inside
a ``CTkScrollableFrame`` and exposes a ``reload`` method so the parent
window can refresh when settings change externally.
"""

from __future__ import annotations

import logging
from typing import Any

import customtkinter as ctk

from puripuly_heart.domain.i18n import t
from puripuly_heart.ui_tkinter import theme as th

logger = logging.getLogger(__name__)
from puripuly_heart.ui_tkinter.sections.audio_section import AudioSection
from puripuly_heart.ui_tkinter.sections.stt_section import STTSection
from puripuly_heart.ui_tkinter.sections.llm_section import LLMSection
from puripuly_heart.ui_tkinter.sections.overlay_section import OverlaySection
from puripuly_heart.ui_tkinter.sections.osc_section import OSCSection
from puripuly_heart.ui_tkinter.sections.prompt_context_section import PromptContextSection
from puripuly_heart.ui_tkinter.sections.secrets_section import SecretsSection

# Section order: stt → audio → llm → secrets → prompt_context → overlay → osc
_SECTION_ORDER: list[tuple[str, type]] = [
    ("stt", STTSection),
    ("audio", AudioSection),
    ("llm", LLMSection),
    ("secrets", SecretsSection),
    ("prompt_context", PromptContextSection),
    ("overlay", OverlaySection),
    ("osc", OSCSection),
]


class SettingsView(ctk.CTkFrame):
    """Settings form with always-visible sections.

    Parameters
    ----------
    master : widget
        Parent CTk widget.
    controller : GuiController
        Shared application controller.
    """

    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(
            master,
            fg_color=th.COLOR_BACKGROUND,
            **kwargs,
        )
        self._controller = controller
        self._sections: dict[str, Any] = {}
        self._active_section: str | None = None

        self._build_sections()
        # Sections are NOT packed during build — only show_section() packs them.
        # This prevents the startup flash where all sections are briefly visible.
        self.__post_init_widgets()

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _build_sections(self) -> None:
        """Instantiate all sections — do NOT pack them yet.

        Only :meth:`show_section` packs a section into the scrollable frame.
        """
        for key, section_cls in _SECTION_ORDER:
            try:
                section = section_cls(self, self._controller)
                self._sections[key] = section
                self._add_debug_label(section, f"section.{key}")
            except Exception as exc:
                logger.error("[SettingsView] Failed to build section '%s': %s", key, exc, exc_info=True)

    def _ensure_section(self, key: str) -> Any:
        """Return the section if it exists."""
        return self._sections.get(key)

    def _add_debug_label(self, widget: ctk.CTkFrame, widget_id: str) -> None:
        """Add a small debug label showing the widget identifier.

        Labels start hidden and are toggled via the app's [D] button.
        Clicking a label copies its text to the clipboard with a flash.
        """
        app = getattr(self._controller, "app", None)
        if app is None or not getattr(app, "debug_ui_preview", False):
            return
        label = ctk.CTkLabel(
            widget,
            text=f"[{widget_id}]",
            font=("Consolas", 9),
            text_color="#00FF88",
            fg_color="transparent",
        )
        label.bind("<Button-1>", lambda e, t=widget_id: app._copy_debug_label(t))
        # Start hidden — don't place until toggled via [D] button
        app._debug_labels.append(label)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_section(self, key: str) -> Any | None:
        """Return a section by its key, or ``None`` if not found."""
        return self._sections.get(key)

    def show_section(self, key: str) -> None:
        """Hide all sections and show only the one identified by *key*."""
        for section in self._sections.values():
            section.pack_forget()
        section = self._ensure_section(key)
        if section is not None:
            section.pack(fill="both", expand=True, pady=(0, 8))
            self._active_section = key

    def reload(self) -> None:
        """Destroy and rebuild all sections from current settings.

        Call this when settings change externally (e.g. locale switch).
        Preserves the currently active section.
        """
        active = self._active_section
        for section in self._sections.values():
            section.destroy()
        self._sections.clear()
        self._build_sections()
        # Re-initialize widget-like references for controller compatibility
        self.__post_init_widgets()
        # Re-show the previously active section (nothing was packed during build)
        if active and active in self._sections:
            self._sections[active].pack(fill="both", expand=True, pady=(0, 8))
            self._active_section = active
        elif self._sections:
            first_key = next(iter(self._sections))
            self._sections[first_key].pack(fill="both", expand=True, pady=(0, 8))
            self._active_section = first_key

    def scroll_to_section(self, key: str) -> None:
        """Scroll to the section with the given key."""
        section = self._sections.get(key)
        if section is None:
            return
        # Ensure geometry is up-to-date before measuring
        self.update_idletasks()
        y = section.winfo_y()
        canvas = getattr(self, "_parent_canvas", None)
        if canvas is None:
            return
        bbox = canvas.bbox("all")
        if bbox is None or bbox[3] <= 0:
            return
        fraction = min(y / bbox[3], 1.0)
        canvas.yview_moveto(fraction)

    # ------------------------------------------------------------------
    # Controller compatibility stubs
    # ------------------------------------------------------------------

    def load_from_settings(self, settings: Any, config_path: Any = None, **kwargs: Any) -> None:
        """Stub — reload sections from updated settings."""
        self.reload()

    def set_overlay_calibration(self, calibration: Any) -> None:
        """Stub — overlay calibration state (not applicable to Tkinter)."""

    def consume_prompt_apply_settings(self) -> Any | None:
        """Stub — no pending prompt changes in Tkinter settings view."""
        return None

    def consume_provider_apply_settings(self) -> Any | None:
        """Stub — no pending provider changes in Tkinter settings view."""
        return None

    def apply_locale(self) -> None:
        """Update all sections in-place with the new locale translations.

        Unlike ``reload()``, this preserves widget state (entry text,
        dropdown selections, switch states, scroll position).
        """
        for key, section in self._sections.items():
            if hasattr(section, "apply_locale"):
                try:
                    section.apply_locale()
                except Exception as exc:
                    logger.error("[locale] section '%s' failed: %s", key, exc)

    # ------------------------------------------------------------------
    # Additional controller interface members
    # ------------------------------------------------------------------

    @property
    def has_provider_changes(self) -> bool:
        """Flag read by controller to check pending provider changes."""
        return False

    @property
    def has_pending_prompt_changes(self) -> bool:
        """Flag read by controller to check pending prompt changes."""
        return False

    def set_overlay_peer_contract(self, contract: Any) -> None:
        """Propagate overlay/peer contract (getattr-guarded in controller)."""

    def set_overlay_runtime_state(
        self,
        state: str,
        failure_reason: str | None = None,
        overlay_target: str | None = None,
        desktop_captions_locked: bool | None = None,
    ) -> None:
        """Sync overlay runtime state (getattr-guarded in controller)."""

    def sync_desktop_overlay_settings(self, settings: Any) -> None:
        """Sync desktop overlay settings (getattr-guarded in controller)."""

    # --- Internal attributes accessed by controller ---

    class _CommandExecutorStub:
        """Stub for command executor — controller writes this and calls update_settings."""
        def update_settings(self, settings: Any) -> None:
            pass

    _command_executor: _CommandExecutorStub | None = None

    class _WidgetLikeKey:
        """Widget-like object with .value property — controller reads getattr(field, 'value', None)."""
        def __init__(self, get_value: Any = None) -> None:
            self._get_value = get_value
        @property
        def value(self) -> str | None:
            if self._get_value:
                return self._get_value()
            return None

    def __post_init_widgets(self) -> None:
        """Initialize widget-like attributes after sections are built."""
        # These are read by the controller via getattr(field, "value", None)
        secrets_section = self._sections.get("secrets")
        if secrets_section:
            entries = getattr(secrets_section, "_entries", {})
            self._openai_compatible_key = self._WidgetLikeKey(
                lambda: entries.get("openai_compatible", type("", (), {"get": lambda s: None})()).get() or None
            )
            self._fallback_api_key = self._WidgetLikeKey(
                lambda: entries.get("backup_openai_compatible", type("", (), {"get": lambda s: None})()).get() or None
            )
        else:
            self._openai_compatible_key = self._WidgetLikeKey()
            self._fallback_api_key = self._WidgetLikeKey()
