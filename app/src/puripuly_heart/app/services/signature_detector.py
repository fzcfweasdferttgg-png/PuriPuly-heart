"""Encapsulates signature state for settings change detection.

Tracks previous signatures to determine what changed between apply_settings
calls. Used by SettingsManagerMixin (writes and reads),
PeerRuntimeManagerMixin, MicTestManagerMixin, PeerFlagsMixin, and
OverlayLifecycleMixin.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SignatureChangeDetector:
    """Tracks previous provider/runtime signatures for change detection.

    Each field stores the last-known signature tuple so that apply_settings()
    can compare old vs new and decide which subsystems need rebuilding.
    """

    # STT runtime signatures — changed when STT backend config changes
    last_stt_runtime_signature: tuple[object, ...] | None = None
    last_self_stt_runtime_signature: tuple[object, ...] | None = None
    last_peer_stt_runtime_signature: tuple[object, ...] | None = None

    # Peer STT desired active state
    last_peer_stt_desired_active: bool | None = None

    # Provider signatures — changed when provider selection changes
    last_self_stt_provider_signature: tuple[object, ...] | None = None
    last_peer_stt_provider_signature: tuple[object, ...] | None = None
    last_llm_provider_signature: tuple[object, ...] | None = None

    # Microphone test audio settings signature
    last_microphone_test_audio_settings_signature: tuple[object, ...] | None = None

    # Peer translation state
    last_peer_translation_enabled: bool | None = None
    last_peer_translation_activation_requested: bool | None = None
