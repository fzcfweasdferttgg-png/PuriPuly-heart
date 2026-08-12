from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from puripuly_heart.config.settings import STTProviderName
from puripuly_heart.domain.overlay_contract import (
    OverlayPeerConsumerContract,
    build_overlay_peer_consumer_contract,
)

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


class PeerFlagsMixin:

    @property
    def effective_peer_translation_enabled(self) -> bool:
        if self.settings is None:
            return False
        return self._effective_peer_translation_enabled_for(self.settings)

    def _effective_peer_translation_enabled_for(self, settings: AppSettings) -> bool:
        return bool(
            self._peer_translation_activation_requested_for(settings)
            and self._effective_peer_overlay_enabled_for(settings)
            and self.hub is not None
            and getattr(self.hub, "peer_stt", None) is not None
        )

    def _peer_translation_eula_accepted_for(self, settings: AppSettings) -> bool:
        return bool(settings.ui.peer_translation_eula_accepted)

    def _peer_translation_activation_requested_for(self, settings: AppSettings) -> bool:
        return bool(
            settings.ui.peer_translation_enabled
            and self._peer_translation_eula_accepted_for(settings)
        )

    def _effective_peer_overlay_enabled_for(self, settings: AppSettings) -> bool:
        _ = settings
        return self.overlay_state == "connected"

    def _effective_integrated_context_enabled_for(self, settings: AppSettings) -> bool:
        return bool(
            settings.ui.integrated_context_enabled
            and self._effective_peer_translation_enabled_for(settings)
        )

    def _sync_effective_hub_flags(self, settings: AppSettings | None = None) -> None:
        resolved_settings = settings or self.settings
        if resolved_settings is None or self.hub is None:
            return
        self.hub.peer_translation_enabled = self._effective_peer_translation_enabled_for(
            resolved_settings
        )
        self.hub.integrated_context_enabled = self._effective_integrated_context_enabled_for(
            resolved_settings
        )

    def build_overlay_peer_consumer_contract(self) -> OverlayPeerConsumerContract | None:
        if self.settings is None:
            return None
        return build_overlay_peer_consumer_contract(
            overlay_intent_enabled=bool(self.settings.ui.overlay_enabled),
            overlay_state=self.overlay_state,
            overlay_failure_reason=self.failure_reason,
            peer_intent_enabled=bool(self.settings.ui.peer_translation_enabled),
            peer_effective_enabled=self._effective_peer_translation_enabled_for(self.settings),
        )

    def _refresh_overlay_peer_consumers(self) -> None:
        refresh_contract = getattr(self, "refresh_overlay_peer_contract", None)
        if callable(refresh_contract):
            with contextlib.suppress(Exception):
                refresh_contract()

    def _peer_runtime_should_be_active(self, settings: AppSettings) -> bool:
        return bool(
            self._peer_translation_activation_requested_for(settings)
            and self._effective_peer_overlay_enabled_for(settings)
            and self.hub is not None
            and self._overlay_bridge is not None
        )

    async def set_peer_translation_enabled(self, enabled: bool) -> None:
        if self.settings is None:
            return

        enabled = bool(enabled)
        self.log_basic(f"[Peer] Toggle request: enabled={enabled}")
        self.log_detailed(
            "[Peer] Toggle detail: "
            f"overlay_enabled={self.settings.ui.overlay_enabled} "
            f"overlay_state={self.overlay_state} "
            f"peer_stt_available={self.hub is not None and getattr(self.hub, 'peer_stt', None) is not None} "
            f"eula_accepted={self.settings.ui.peer_translation_eula_accepted}"
        )

        if enabled and not self._peer_translation_eula_accepted_for(self.settings):
            self.settings.ui.peer_translation_enabled = False
            self._last_peer_translation_enabled = False
            self._last_peer_translation_activation_requested = False
            self._sync_effective_hub_flags(self.settings)
            self._refresh_overlay_peer_consumers()
            self.log_basic("[Peer] Toggle ignored: eula_accepted=False")
            return

        if enabled and self.settings.provider.peer_stt in (STTProviderName.LOCAL_QWEN, STTProviderName.LOCAL_QWEN_17B, STTProviderName.LOCAL_GIGAAM_RNNT, STTProviderName.LOCAL_PARAKEET_TDT, STTProviderName.LOCAL_GIGAAM_RNNT_GGUF, STTProviderName.LOCAL_PARAKEET_TDT_GGUF, STTProviderName.LOCAL_QWEN3_ASR_GGUF, STTProviderName.LOCAL_QWEN_17B_GGUF):
            peer_ready = await self._ensure_peer_local_stt_ready()
            if not peer_ready:
                self._clear_local_stt_pending_enable_if_provider_switched_away()
                self._sync_local_stt_notice()
                return
        if enabled and not self.settings.ui.overlay_enabled:
            self.settings.ui.overlay_enabled = True
        self.settings.ui.peer_translation_enabled = enabled
        self._last_peer_translation_enabled = enabled
        self._last_peer_translation_activation_requested = (
            self._peer_translation_activation_requested_for(self.settings)
        )
        self._clear_local_stt_pending_enable_if_provider_switched_away()
        self._sync_local_stt_notice()
        self._refresh_overlay_peer_consumers()

        if enabled and self.overlay_state not in {"starting", "connected"}:
            await self._begin_overlay_start()
        else:
            await self._refresh_overlay_runtime_dependencies()
        self._sync_effective_hub_flags(self.settings)
        if enabled:
            self._enqueue_peer_translation_disclosure()
        self._refresh_overlay_peer_consumers()
        self.save_settings()
