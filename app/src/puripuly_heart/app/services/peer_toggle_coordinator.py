"""PeerToggleCoordinator — peer translation flag computation and toggle orchestration.

Extracted from PeerFlagsMixin. Computes effective peer flags, manages EULA gate,
and synchronizes hub flags.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from puripuly_heart.domain.providers import STTProviderName
from puripuly_heart.domain.overlay_contract import OverlayPeerConsumerContract
from puripuly_heart.app.services.peer_flag_service import (
    effective_integrated_context_enabled as _effective_integrated_context_enabled_impl,
    effective_peer_overlay_enabled as _effective_peer_overlay_enabled_impl,
    effective_peer_translation_enabled as _effective_peer_translation_enabled_impl,
    peer_runtime_should_be_active as _peer_runtime_should_be_active_impl,
    peer_translation_activation_requested as _peer_translation_activation_requested_impl,
    peer_translation_eula_accepted as _peer_translation_eula_accepted_impl,
    build_overlay_peer_consumer_contract_from_state as _build_overlay_peer_consumer_contract_impl,
)

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings

logger = logging.getLogger(__name__)


@dataclass
class PeerToggleCoordinator:
    """Peer translation flag computation and toggle orchestration."""

    # Callbacks
    _settings_provider: Callable[[], object | None] | None = None
    _hub_provider: Callable[[], object | None] | None = None
    _overlay_state_provider: Callable[[], str] | None = None
    _overlay_bridge_provider: Callable[[], object | None] | None = None
    _failure_reason_provider: Callable[[], str | None] | None = None
    _signature_detector_provider: Callable[[], object | None] | None = None
    _log_basic: Callable[[str], None] | None = None
    _log_detailed: Callable[[str], None] | None = None
    _ensure_peer_local_stt_ready: Callable[[], Awaitable[bool]] | None = None
    _begin_overlay_start: Callable[[], Awaitable[None]] | None = None
    _refresh_overlay_runtime_dependencies: Callable[[], Awaitable[None]] | None = None
    _save_settings: Callable[[], None] | None = None
    _enqueue_peer_translation_disclosure: Callable[[], None] | None = None
    _clear_local_stt_pending_enable: Callable[[], None] | None = None
    _sync_local_stt_notice: Callable[[], None] | None = None
    _refresh_overlay_peer_contract: Callable[[], None] | None = None

    def _emit_log_basic(self, message: str) -> None:
        if self._log_basic is not None:
            self._log_basic(message)

    def _emit_log_detailed(self, message: str) -> None:
        if self._log_detailed is not None:
            self._log_detailed(message)

    @property
    def effective_peer_translation_enabled(self) -> bool:
        settings = self._settings_provider() if self._settings_provider else None
        if settings is None:
            return False
        return self._effective_peer_translation_enabled_for(settings)

    def _effective_peer_translation_enabled_for(self, settings: AppSettings) -> bool:
        overlay_state = self._overlay_state_provider() if self._overlay_state_provider else "off"
        hub = self._hub_provider() if self._hub_provider else None
        return _effective_peer_translation_enabled_impl(
            settings,
            overlay_state,
            hub_has_peer_stt=(hub is not None and getattr(hub, "peer_stt", None) is not None),
        )

    def _peer_translation_eula_accepted_for(self, settings: AppSettings) -> bool:
        return _peer_translation_eula_accepted_impl(settings)

    def _peer_translation_activation_requested_for(self, settings: AppSettings) -> bool:
        return _peer_translation_activation_requested_impl(settings)

    def _effective_peer_overlay_enabled_for(self, settings: AppSettings) -> bool:
        overlay_state = self._overlay_state_provider() if self._overlay_state_provider else "off"
        return _effective_peer_overlay_enabled_impl(overlay_state)

    def _effective_integrated_context_enabled_for(self, settings: AppSettings) -> bool:
        overlay_state = self._overlay_state_provider() if self._overlay_state_provider else "off"
        hub = self._hub_provider() if self._hub_provider else None
        return _effective_integrated_context_enabled_impl(
            settings,
            overlay_state,
            hub_has_peer_stt=(hub is not None and getattr(hub, "peer_stt", None) is not None),
        )

    def sync_effective_hub_flags(self, settings: AppSettings | None = None) -> None:
        resolved_settings = settings
        if resolved_settings is None:
            resolved_settings = self._settings_provider() if self._settings_provider else None
        if resolved_settings is None:
            return
        hub = self._hub_provider() if self._hub_provider else None
        if hub is None:
            return
        hub.peer_translation_enabled = self._effective_peer_translation_enabled_for(resolved_settings)
        hub.integrated_context_enabled = self._effective_integrated_context_enabled_for(resolved_settings)

    def build_overlay_peer_consumer_contract(self) -> OverlayPeerConsumerContract | None:
        settings = self._settings_provider() if self._settings_provider else None
        if settings is None:
            return None
        overlay_state = self._overlay_state_provider() if self._overlay_state_provider else "off"
        failure_reason = self._failure_reason_provider() if self._failure_reason_provider else None
        return _build_overlay_peer_consumer_contract_impl(
            settings,
            overlay_state,
            failure_reason,
            self._effective_peer_translation_enabled_for(settings),
        )

    def refresh_overlay_peer_consumers(self) -> None:
        if self._refresh_overlay_peer_contract is not None:
            try:
                self._refresh_overlay_peer_contract()
            except Exception:
                logger.debug("[PeerToggle] refresh_overlay_peer_contract failed", exc_info=True)

    def peer_runtime_should_be_active(self, settings: AppSettings) -> bool:
        overlay_state = self._overlay_state_provider() if self._overlay_state_provider else "off"
        bridge = self._overlay_bridge_provider() if self._overlay_bridge_provider else None
        return _peer_runtime_should_be_active_impl(
            settings,
            overlay_state,
            bridge is not None,
        )

    async def set_peer_translation_enabled(self, enabled: bool) -> None:
        settings = self._settings_provider() if self._settings_provider else None
        if settings is None:
            return

        enabled = bool(enabled)
        self._emit_log_basic(f"[Peer] Toggle request: enabled={enabled}")
        hub = self._hub_provider() if self._hub_provider else None
        self._emit_log_detailed(
            "[Peer] Toggle detail: "
            f"overlay_enabled={settings.ui.overlay_enabled} "
            f"overlay_state={self._overlay_state_provider() if self._overlay_state_provider else 'off'} "
            f"peer_stt_available={hub is not None and getattr(hub, 'peer_stt', None) is not None} "
            f"eula_accepted={settings.ui.peer_translation_eula_accepted}"
        )

        if enabled and not self._peer_translation_eula_accepted_for(settings):
            settings.ui.peer_translation_enabled = False
            det = self._signature_detector_provider() if self._signature_detector_provider else None
            if det is not None:
                det.last_peer_translation_enabled = False
                det.last_peer_translation_activation_requested = False
            self.sync_effective_hub_flags(settings)
            self.refresh_overlay_peer_consumers()
            self._emit_log_basic("[Peer] Toggle ignored: eula_accepted=False")
            return

        if enabled and settings.provider.peer_stt in (STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B, STTProviderName.LOCAL_GIGAAM_RNNT, STTProviderName.LOCAL_PARAKEET_TDT, STTProviderName.LOCAL_GIGAAM_RNNT_GGUF, STTProviderName.LOCAL_PARAKEET_TDT_GGUF, STTProviderName.LOCAL_QWEN3_ASR_GGUF, STTProviderName.LOCAL_QWEN_17B_GGUF):
            peer_ready = await self._ensure_peer_local_stt_ready() if self._ensure_peer_local_stt_ready else True
            if not peer_ready:
                if self._clear_local_stt_pending_enable is not None:
                    self._clear_local_stt_pending_enable()
                if self._sync_local_stt_notice is not None:
                    self._sync_local_stt_notice()
                return
        if enabled and not settings.ui.overlay_enabled:
            settings.ui.overlay_enabled = True
        settings.ui.peer_translation_enabled = enabled
        det = self._signature_detector_provider() if self._signature_detector_provider else None
        if det is not None:
            det.last_peer_translation_enabled = enabled
            det.last_peer_translation_activation_requested = (
                self._peer_translation_activation_requested_for(settings)
            )
        if self._clear_local_stt_pending_enable is not None:
            self._clear_local_stt_pending_enable()
        if self._sync_local_stt_notice is not None:
            self._sync_local_stt_notice()
        self.refresh_overlay_peer_consumers()

        overlay_state = self._overlay_state_provider() if self._overlay_state_provider else "off"
        if enabled and overlay_state not in {"starting", "connected"}:
            if self._begin_overlay_start is not None:
                await self._begin_overlay_start()
        else:
            if self._refresh_overlay_runtime_dependencies is not None:
                await self._refresh_overlay_runtime_dependencies()
        self.sync_effective_hub_flags(settings)
        if enabled and self._enqueue_peer_translation_disclosure is not None:
            self._enqueue_peer_translation_disclosure()
        self.refresh_overlay_peer_consumers()
        if self._save_settings is not None:
            self._save_settings()
