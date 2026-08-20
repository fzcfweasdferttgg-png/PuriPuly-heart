"""Peer translation flag computation — pure functions extracted from PeerFlagsMixin.

Stateless, testable functions for computing peer translation flags.
No side effects, no controller state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from puripuly_heart.domain.overlay_contract import (
    OverlayPeerConsumerContract,
    build_overlay_peer_consumer_contract,
)

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings


def peer_translation_eula_accepted(settings: AppSettings) -> bool:
    return bool(settings.ui.peer_translation_eula_accepted)


def peer_translation_activation_requested(settings: AppSettings) -> bool:
    return bool(
        settings.ui.peer_translation_enabled
        and peer_translation_eula_accepted(settings)
    )


def effective_peer_overlay_enabled(overlay_state: str) -> bool:
    return overlay_state == "connected"


def effective_peer_translation_enabled(
    settings: AppSettings,
    overlay_state: str,
    hub_has_peer_stt: bool,
) -> bool:
    return bool(
        peer_translation_activation_requested(settings)
        and effective_peer_overlay_enabled(overlay_state)
        and hub_has_peer_stt
    )


def effective_integrated_context_enabled(
    settings: AppSettings,
    overlay_state: str,
    hub_has_peer_stt: bool,
) -> bool:
    return bool(
        settings.ui.integrated_context_enabled
        and effective_peer_translation_enabled(settings, overlay_state, hub_has_peer_stt)
    )


def peer_runtime_should_be_active(
    settings: AppSettings,
    overlay_state: str,
    overlay_bridge_present: bool,
) -> bool:
    return bool(
        peer_translation_activation_requested(settings)
        and effective_peer_overlay_enabled(overlay_state)
        and overlay_bridge_present
    )


def build_overlay_peer_consumer_contract_from_state(
    settings: AppSettings,
    overlay_state: str,
    failure_reason: str | None,
    effective_peer_enabled: bool,
) -> OverlayPeerConsumerContract | None:
    if settings is None:
        return None
    return build_overlay_peer_consumer_contract(
        overlay_intent_enabled=bool(settings.ui.overlay_enabled),
        overlay_state=overlay_state,
        overlay_failure_reason=failure_reason,
        peer_intent_enabled=bool(settings.ui.peer_translation_enabled),
        peer_effective_enabled=effective_peer_enabled,
    )
