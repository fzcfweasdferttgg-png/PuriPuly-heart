"""Settings mutation commands — command pattern for decoupled settings mutations.

Every UI-driven settings mutation is a frozen dataclass command.
SettingsCommandExecutor dispatches by type and applies the mutation to
AppSettings.  Commands are pure data — no side effects, no validation.

Two pattern categories:
- Pattern A (direct): mutates settings fields in-place.
- Pattern B (draft): writes to intermediate draft objects that get
  materialized later via materialize_translation_settings().

Adding a new command: create the frozen dataclass here, add handler in
settings_command_executor.py, wire the UI call site to call
_command_executor.execute(NewCommand(...)).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    """Returned by the settings handler after applying a command."""

    success: bool
    settings: AppSettings | None = None  # noqa: F821 — forward ref resolved by __future__
    error: str | None = None


# ---------------------------------------------------------------------------
# Marker protocol for type-hinting
# ---------------------------------------------------------------------------


@runtime_checkable
class SettingsCommand(Protocol):
    """Marker protocol for settings commands.

    All frozen dataclasses below satisfy this protocol structurally —
    no explicit inheritance needed.  Use ``SettingsChange`` union for
    exhaustive type checking instead.
    """

    ...


# ===================================================================
# Pattern A — direct mutations (write directly to AppSettings fields)
# ===================================================================


@dataclass(frozen=True)
class ChangeLocale:
    """Change the UI display language.

    Modifies:
        settings.ui.locale
    """

    locale: str


@dataclass(frozen=True)
class ChangeVADThreshold:
    """Change the VAD speech-detection threshold for a channel.

    Modifies:
        channel="self"  → settings.stt.vad_speech_threshold
        channel="peer"  → settings.desktop_audio.vad_speech_threshold
    """

    threshold: float
    channel: str  # "self" or "peer"


@dataclass(frozen=True)
class ChangeHangover:
    """Change the VAD hangover duration (ms) for a channel.

    Modifies:
        channel="self"  → settings.stt.low_latency_vad_hangover_ms
        channel="peer"  → settings.desktop_audio.vad_hangover_ms
    """

    hangover_ms: int
    channel: str  # "self" or "peer"


@dataclass(frozen=True)
class ChangePreRoll:
    """Change the audio pre-roll buffer duration (ms) for peer channel.

    Modifies:
        settings.desktop_audio.vad_pre_roll_ms  (peer only currently)
    """

    pre_roll_ms: int
    channel: str  # "peer" only currently


@dataclass(frozen=True)
class ChangeLowLatency:
    """Toggle low-latency pipeline mode.

    Modifies:
        settings.stt.low_latency_mode
    """

    enabled: bool


@dataclass(frozen=True)
class ChangeClipboardAutoTranslate:
    """Toggle automatic clipboard translation.

    Modifies:
        settings.ui.clipboard_auto_translate_enabled
    """

    enabled: bool


@dataclass(frozen=True)
class ChangeVRCMic:
    """Toggle VRC microphone intercept (mute mic when translating).

    Modifies:
        settings.osc.vrc_mic_intercept
    """

    enabled: bool


@dataclass(frozen=True)
class ChangeChatboxSource:
    """Toggle whether the chatbox message includes the source language label.

    Modifies:
        settings.osc.chatbox_include_source
    """

    include_source: bool


@dataclass(frozen=True)
class ChangeIntegratedContext:
    """Toggle integrated context mode (extra translation context from audio).

    Modifies:
        settings.ui.integrated_context_enabled
    """

    enabled: bool


@dataclass(frozen=True)
class ChangeAudioDevice:
    """Change audio input/output device selection.

    Modifies:
        settings.audio.input_host_api
        settings.audio.input_device
        settings.desktop_audio.output_device
    """

    host_api: str
    input_device: str
    desktop_output_device: str


@dataclass(frozen=True)
class ChangeCustomVocabulary:
    """Update custom vocabulary terms and enable/disable flag.

    Modifies:
        settings.stt.custom_terms
        settings.stt.custom_vocabulary_enabled
    """

    terms: dict[str, list[str]]  # language → list of terms
    enabled: bool


@dataclass(frozen=True)
class ChangeSTTCompute:
    """Change STT compute target (GPU vs CPU) for a channel.

    Modifies:
        channel="self"  → settings.provider.stt_compute
        channel="peer"  → settings.provider.peer_stt_compute
    """

    compute: str  # "gpu" or "cpu"
    channel: str  # "self" or "peer"


# ===================================================================
# Pattern B — draft mutations (write to draft objects, materialized later)
# ===================================================================


@dataclass(frozen=True)
class ChangeSTTProvider:
    """Change the self-channel STT provider.

    May trigger backend/quant auto-adjustment when incompatible.

    Modifies:
        settings.provider.stt
        settings.provider.stt_backend  (auto-set if incompatible)
        settings.provider.stt_quant    (auto-set if incompatible)
    """

    provider: str  # STTProviderName value
    backend: str | None = None  # auto-set based on provider
    quant: str | None = None  # auto-set if incompatible


@dataclass(frozen=True)
class ChangePeerSTTProvider:
    """Change the peer-channel STT provider.

    May trigger backend/quant auto-adjustment when incompatible.

    Modifies:
        settings.provider.peer_stt
        settings.provider.peer_stt_backend  (auto-set if incompatible)
        settings.provider.peer_stt_quant    (auto-set if incompatible)
    """

    provider: str
    backend: str | None = None
    quant: str | None = None


@dataclass(frozen=True)
class ChangeSTTQuant:
    """Change the STT quantization level for a channel.

    Modifies:
        channel="self"  → settings.provider.stt_quant
        channel="peer"  → settings.provider.peer_stt_quant
    """

    quant: str
    channel: str  # "self" or "peer"


@dataclass(frozen=True)
class ChangeSTTBackend:
    """Change the STT backend (onnx vs gguf) for a channel.

    Modifies:
        channel="self"  → settings.provider.stt_backend
        channel="peer"  → settings.provider.peer_stt_backend
    """

    backend: str  # "onnx" or "gguf"
    channel: str  # "self" or "peer"


@dataclass(frozen=True)
class ChangeLocalLLMField:
    """Update a single field in the local LLM settings.

    Modifies:
        settings.local_llm.{field}  where field ∈ {"base_url", "model", "extra_body"}
    """

    field: str  # "base_url", "model", "extra_body"
    value: object


@dataclass(frozen=True)
class ChangeOpenAICompatibleField:
    """Update a single field in the OpenAI-compatible translation settings.

    Modifies:
        settings.provider.openai_compatible.{field}
        where field ∈ {"base_url", "model", "provider"}
    """

    field: str  # "base_url", "model", "provider"
    value: str


@dataclass(frozen=True)
class ChangeFallbackOpenAIField:
    """Update a single field in the fallback (backup) OpenAI-compatible settings.

    Modifies:
        settings.backup_translation.openai_compatible.{field}
        where field ∈ {"base_url", "model", "provider"}
    """

    field: str  # "base_url", "model", "provider"
    value: str


@dataclass(frozen=True)
class ChangeFallbackLocalLLMField:
    """Update a single field in the fallback (backup) local LLM settings.

    Modifies:
        settings.backup_translation.local_llm.{field}
        where field ∈ {"base_url", "model", "extra_body"}
    """

    field: str  # "base_url", "model", "extra_body"
    value: object


@dataclass(frozen=True)
class ChangeBackupTranslation:
    """Toggle backup translation and optionally set its mode.

    Modifies:
        settings.backup_translation.enabled
        settings.backup_translation.mode  (if mode is not None)
    """

    enabled: bool
    mode: str | None = None  # "local_llm", "openai_compatible"


@dataclass(frozen=True)
class ChangeOverlayTarget:
    """Switch overlay rendering target.

    Modifies:
        settings.overlay.target  ("steamvr" or "desktop")
    """

    target: str  # "steamvr", "desktop"


@dataclass(frozen=True)
class ChangeOverlayDesktopSize:
    """Change the desktop overlay size preset.

    Modifies:
        settings.overlay.desktop_flet.size_preset
    """

    size_preset: str


@dataclass(frozen=True)
class ChangeOverlayDesktopBackgroundAlpha:
    """Change the desktop overlay background transparency.

    Modifies:
        settings.overlay.desktop_flet.visual.background_alpha

    Value range: 0.0 (fully transparent) to 1.0 (fully opaque).
    """

    alpha: float  # 0.0–1.0


@dataclass(frozen=True)
class ChangeOverlayTranslation:
    """Toggle overlay display of translated text.

    Modifies:
        settings.overlay.show_translation
    """

    show: bool


@dataclass(frozen=True)
class ChangeOverlayPeerOriginal:
    """Toggle overlay display of peer's original (untranslated) text.

    Modifies:
        settings.overlay.show_peer_original
    """

    show: bool


@dataclass(frozen=True)
class ChangeOverlayDesktopLock:
    """Toggle desktop overlay position lock.

    Modifies:
        settings.overlay.desktop_flet.locked
    """

    locked: bool


@dataclass(frozen=True)
class ChangeOverlayDesktopPositionReset:
    """Reset desktop overlay position to defaults.

    Modifies:
        settings.overlay.desktop_flet.position  (reset to x=0, y=0)
    """

    pass  # no fields


@dataclass(frozen=True)
class ChangeCalibrationField:
    """Update a single field in the overlay calibration draft.

    Modifies (draft, committed on ChangeCalibrationCommit):
        settings.overlay.calibration.{field_name}
        where field_name ∈ {"offset_x", "offset_y", "distance",
                            "text_scale", "background_alpha"}
    """

    field_name: str
    value: float


@dataclass(frozen=True)
class ChangeCalibrationCommit:
    """Commit the calibration draft into settings.

    Modifies:
        settings.overlay.calibration  (bulk replace from draft)
    """

    pass  # no fields


@dataclass(frozen=True)
class ChangeTranslationSelection:
    """Full translation config change from the translation model selector.

    Selecting a provider triggers materialize_translation_settings which
    resets translation.*, connection, and model fields to normalized defaults
    for the chosen provider.

    Modifies:
        settings.provider.llm
        settings.translation.model
        settings.translation.connection
        settings.translation.connection_history
    """

    provider: str  # LLMProviderName value


# ---------------------------------------------------------------------------
# Union type for exhaustive matching / type hints
# ---------------------------------------------------------------------------

SettingsChange = (
    ChangeLocale
    | ChangeVADThreshold
    | ChangeHangover
    | ChangePreRoll
    | ChangeLowLatency
    | ChangeClipboardAutoTranslate
    | ChangeVRCMic
    | ChangeChatboxSource
    | ChangeIntegratedContext
    | ChangeAudioDevice
    | ChangeCustomVocabulary
    | ChangeSTTCompute
    | ChangeSTTProvider
    | ChangePeerSTTProvider
    | ChangeSTTQuant
    | ChangeSTTBackend
    | ChangeLocalLLMField
    | ChangeOpenAICompatibleField
    | ChangeFallbackOpenAIField
    | ChangeFallbackLocalLLMField
    | ChangeBackupTranslation
    | ChangeOverlayTarget
    | ChangeOverlayDesktopSize
    | ChangeOverlayDesktopBackgroundAlpha
    | ChangeOverlayTranslation
    | ChangeOverlayPeerOriginal
    | ChangeOverlayDesktopLock
    | ChangeOverlayDesktopPositionReset
    | ChangeCalibrationField
    | ChangeCalibrationCommit
    | ChangeTranslationSelection
)
