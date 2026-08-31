"""SettingsCommandExecutor — applies settings commands to AppSettings.

Each command type is dispatched to a private ``_apply_*`` handler that
validates, mutates, and returns a ``CommandResult``.

Two mutation categories:

  Pattern A — DIRECT: handler writes directly to ``self._settings``.
    Used for simple scalar toggles, sliders, device names, overlay fields.
    The section handler (caller) is responsible for UI sync, snackbar, logging.

  Pattern B — DRAFT: handler mutates via ``self._draft_service``.
    Used for provider-level fields (STT provider, LLM model, backend, quant,
    compute, fallback) that accumulate in a lazy-deepcopy draft until the
    user clicks "Apply".  ``draft_service.has_provider_changes`` is set True.

The executor does NOT handle UI synchronization, snackbar warnings, or
emit_settings_changed — those stay in the section handlers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from puripuly_heart.config.settings.enums import TranslationModel
from puripuly_heart.domain.providers import LLMProviderName, STTProviderName
from puripuly_heart.domain.settings_commands import (
    ChangeAudioDevice,
    ChangeBackupTranslation,
    ChangeCalibrationCommit,
    ChangeCalibrationField,
    ChangeChatboxSource,
    ChangeClipboardAutoTranslate,
    ChangeFallbackLocalLLMField,
    ChangeFallbackOpenAIField,
    ChangeHangover,
    ChangeIntegratedContext,
    ChangeLocalLLMField,
    ChangeLocale,
    ChangeLowLatency,
    ChangeOpenAICompatibleField,
    ChangeOverlayDesktopBackgroundAlpha,
    ChangeOverlayDesktopLock,
    ChangeOverlayDesktopPositionReset,
    ChangeOverlayDesktopSize,
    ChangeOverlayPeerOriginal,
    ChangeOverlayTarget,
    ChangeOverlayTranslation,
    ChangePeerSTTProvider,
    ChangePreRoll,
    ChangeSTTBackend,
    ChangeSTTCompute,
    ChangeSTTProvider,
    ChangeSTTQuant,
    ChangeTranslationSelection,
    ChangeVADThreshold,
    ChangeVRCMic,
    CommandResult,
)

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings
    from puripuly_heart.ports.settings_draft import SettingsDraftServiceProtocol as SettingsDraftService

logger = logging.getLogger(__name__)

# GGUF↔ONNX provider mapping — mirrors the dicts in SttSectionMixin.
# When the backend field changes, the provider enum must be switched to
# match (ONNX providers have no GGUF suffix and vice versa).
_ONNX_TO_GGUF: dict[STTProviderName, STTProviderName] = {
    STTProviderName.LOCAL_GIGAAM_RNNT: STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
    STTProviderName.LOCAL_PARAKEET_TDT: STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
    STTProviderName.LOCAL_QWEN: STTProviderName.LOCAL_QWEN3_ASR_GGUF,
    STTProviderName.LOCAL_QWEN_17B: STTProviderName.LOCAL_QWEN_17B_GGUF,
}
_GGUF_TO_ONNX: dict[STTProviderName, STTProviderName] = {
    v: k for k, v in _ONNX_TO_GGUF.items()
}


class SettingsCommandExecutor:
    """Applies settings commands to AppSettings.

    Holds a mutable reference to the live ``AppSettings`` and an optional
    ``SettingsDraftService`` for provider-field drafts.

    Usage (called by section handlers)::

        result = executor.execute(ChangeVADThreshold(channel="self", threshold=0.6))
        if result.success:
            # UI sync, emit_settings_changed, etc.
    """

    # Dispatch table — maps command type → handler method.
    # _resolve_dispatch() populates this once on first execute() call
    # to avoid calling getattr() on every invocation.
    _HANDLERS: dict[type, object] = {}

    def __init__(
        self,
        settings: AppSettings,
        draft_service: SettingsDraftService | None = None,
        materialize_fn: object | None = None,
    ) -> None:
        self._settings = settings
        self._draft_service = draft_service
        self._materialize_fn = materialize_fn

    def update_settings(self, settings: AppSettings) -> None:
        """Update the live settings reference (called after apply_settings)."""
        self._settings = settings

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def execute(self, command: object) -> CommandResult:
        """Dispatch a command to its type-specific handler.

        Returns ``CommandResult(success=False)`` if the command type is
        unknown or the handler raises.
        """
        handler = self._resolve_dispatch(type(command))
        if handler is None:
            return CommandResult(
                success=False,
                error=f"Unknown command type: {type(command).__name__}",
            )
        try:
            return handler(self, command)
        except Exception as exc:
            logger.error(
                "[SettingsCommandExecutor] handler %s failed: %s",
                type(command).__name__,
                exc,
                exc_info=True,
            )
            return CommandResult(success=False, error=str(exc))

    @classmethod
    def _resolve_dispatch(cls, command_type: type):
        """Return handler for *command_type*, populating _HANDLERS lazily."""
        if not cls._HANDLERS:
            cls._HANDLERS = {
                ChangeLocale: cls._apply_locale,
                ChangeVADThreshold: cls._apply_vad_threshold,
                ChangeHangover: cls._apply_hangover,
                ChangePreRoll: cls._apply_pre_roll,
                ChangeLowLatency: cls._apply_low_latency,
                ChangeClipboardAutoTranslate: cls._apply_clipboard_auto_translate,
                ChangeVRCMic: cls._apply_vrc_mic,
                ChangeChatboxSource: cls._apply_chatbox_source,
                ChangeIntegratedContext: cls._apply_integrated_context,
                ChangeAudioDevice: cls._apply_audio_device,
                ChangeOverlayTarget: cls._apply_overlay_target,
                ChangeOverlayDesktopSize: cls._apply_overlay_desktop_size,
                ChangeOverlayDesktopBackgroundAlpha: cls._apply_overlay_desktop_background_alpha,
                ChangeOverlayTranslation: cls._apply_overlay_translation,
                ChangeOverlayPeerOriginal: cls._apply_overlay_peer_original,
                ChangeOverlayDesktopLock: cls._apply_overlay_desktop_lock,
                ChangeOverlayDesktopPositionReset: cls._apply_overlay_desktop_position_reset,
                ChangeCalibrationField: cls._apply_calibration_field,
                ChangeCalibrationCommit: cls._apply_calibration_commit,
                ChangeSTTProvider: cls._apply_stt_provider,
                ChangePeerSTTProvider: cls._apply_peer_stt_provider,
                ChangeSTTQuant: cls._apply_stt_quant,
                ChangeSTTCompute: cls._apply_stt_compute,
                ChangeSTTBackend: cls._apply_stt_backend,
                ChangeLocalLLMField: cls._apply_local_llm_field,
                ChangeOpenAICompatibleField: cls._apply_openai_compatible_field,
                ChangeFallbackOpenAIField: cls._apply_fallback_openai_field,
                ChangeFallbackLocalLLMField: cls._apply_fallback_local_llm_field,
                ChangeBackupTranslation: cls._apply_backup_translation,
                ChangeTranslationSelection: cls._apply_translation_selection,
            }
        return cls._HANDLERS.get(command_type)

    # ------------------------------------------------------------------
    # Pattern A — DIRECT mutations (write to self._settings)
    # ------------------------------------------------------------------

    def _apply_locale(self, cmd: ChangeLocale) -> CommandResult:
        self._settings.ui.locale = cmd.locale
        return CommandResult(success=True, settings=self._settings)

    def _apply_vad_threshold(self, cmd: ChangeVADThreshold) -> CommandResult:
        clamped = max(0.0, min(1.0, cmd.threshold))
        if cmd.channel == "self":
            self._settings.stt.vad_speech_threshold = clamped
        elif cmd.channel == "peer":
            self._settings.desktop_audio.vad_speech_threshold = clamped
        return CommandResult(success=True, settings=self._settings)

    def _apply_hangover(self, cmd: ChangeHangover) -> CommandResult:
        value = max(0, cmd.hangover_ms)
        if cmd.channel == "self":
            self._settings.stt.low_latency_vad_hangover_ms = value
        elif cmd.channel == "peer":
            self._settings.desktop_audio.vad_hangover_ms = value
        return CommandResult(success=True, settings=self._settings)

    def _apply_pre_roll(self, cmd: ChangePreRoll) -> CommandResult:
        value = max(0, cmd.pre_roll_ms)
        if cmd.channel == "peer":
            self._settings.desktop_audio.vad_pre_roll_ms = value
        return CommandResult(success=True, settings=self._settings)

    def _apply_low_latency(self, cmd: ChangeLowLatency) -> CommandResult:
        self._settings.stt.low_latency_mode = cmd.enabled
        return CommandResult(success=True, settings=self._settings)

    def _apply_clipboard_auto_translate(self, cmd: ChangeClipboardAutoTranslate) -> CommandResult:
        self._settings.ui.clipboard_auto_translate_enabled = cmd.enabled
        return CommandResult(success=True, settings=self._settings)

    def _apply_vrc_mic(self, cmd: ChangeVRCMic) -> CommandResult:
        self._settings.osc.vrc_mic_intercept = cmd.enabled
        return CommandResult(success=True, settings=self._settings)

    def _apply_chatbox_source(self, cmd: ChangeChatboxSource) -> CommandResult:
        self._settings.osc.chatbox_include_source = cmd.include_source
        return CommandResult(success=True, settings=self._settings)

    def _apply_integrated_context(self, cmd: ChangeIntegratedContext) -> CommandResult:
        # Overlay control sync is the section handler's responsibility.
        self._settings.ui.integrated_context_enabled = cmd.enabled
        return CommandResult(success=True, settings=self._settings)

    def _apply_audio_device(self, cmd: ChangeAudioDevice) -> CommandResult:
        self._settings.audio.input_host_api = cmd.host_api
        self._settings.audio.input_device = cmd.input_device
        self._settings.desktop_audio.output_device = cmd.desktop_output_device
        return CommandResult(success=True, settings=self._settings)

    def _apply_overlay_target(self, cmd: ChangeOverlayTarget) -> CommandResult:
        self._settings.overlay.target = cmd.target
        return CommandResult(success=True, settings=self._settings)

    def _apply_overlay_desktop_size(self, cmd: ChangeOverlayDesktopSize) -> CommandResult:
        self._settings.overlay.desktop_flet.size_preset = cmd.size_preset
        return CommandResult(success=True, settings=self._settings)

    def _apply_overlay_desktop_background_alpha(
        self, cmd: ChangeOverlayDesktopBackgroundAlpha
    ) -> CommandResult:
        alpha = max(0.0, min(1.0, cmd.alpha))
        self._settings.overlay.desktop_flet.visual.background_alpha = alpha
        return CommandResult(success=True, settings=self._settings)

    def _apply_overlay_translation(self, cmd: ChangeOverlayTranslation) -> CommandResult:
        self._settings.overlay.show_translation = cmd.show
        return CommandResult(success=True, settings=self._settings)

    def _apply_overlay_peer_original(self, cmd: ChangeOverlayPeerOriginal) -> CommandResult:
        self._settings.overlay.show_peer_original = cmd.show
        return CommandResult(success=True, settings=self._settings)

    def _apply_overlay_desktop_lock(self, cmd: ChangeOverlayDesktopLock) -> CommandResult:
        self._settings.overlay.desktop_flet.locked = cmd.locked
        return CommandResult(success=True, settings=self._settings)

    def _apply_overlay_desktop_position_reset(
        self, cmd: ChangeOverlayDesktopPositionReset
    ) -> CommandResult:
        # Position reset is handled at the runtime/overlay level —
        # the executor just acknowledges the command.
        return CommandResult(success=True, settings=self._settings)

    def _apply_calibration_field(self, cmd: ChangeCalibrationField) -> CommandResult:
        setattr(self._settings.overlay.calibration, cmd.field_name, cmd.value)
        return CommandResult(success=True, settings=self._settings)

    def _apply_calibration_commit(self, cmd: ChangeCalibrationCommit) -> CommandResult:
        # Calibration commit is handled by the calibration section's
        # own draft mechanism — the executor just acknowledges.
        return CommandResult(success=True, settings=self._settings)

    # ------------------------------------------------------------------
    # Pattern B — DRAFT mutations (write via self._draft_service)
    # ------------------------------------------------------------------

    def _require_draft(self, action: str) -> SettingsDraftService | None:
        """Return draft_service or None (caller returns error CommandResult)."""
        if self._draft_service is None:
            logger.error("[SettingsCommandExecutor] no draft service for %s", action)
        return self._draft_service

    def _apply_stt_provider(self, cmd: ChangeSTTProvider) -> CommandResult:
        draft_svc = self._require_draft("stt_provider")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for STT provider")
        draft = draft_svc._ensure_provider_settings_draft()
        draft.provider.stt = STTProviderName(cmd.provider)
        if cmd.backend:
            draft.provider.stt_backend = cmd.backend
        if cmd.quant:
            draft.provider.stt_quant = cmd.quant
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)

    def _apply_peer_stt_provider(self, cmd: ChangePeerSTTProvider) -> CommandResult:
        draft_svc = self._require_draft("peer_stt_provider")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for peer STT provider")
        draft = draft_svc._ensure_provider_settings_draft()
        draft.provider.peer_stt = STTProviderName(cmd.provider)
        if cmd.backend:
            draft.provider.peer_stt_backend = cmd.backend
        if cmd.quant:
            draft.provider.peer_stt_quant = cmd.quant
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)

    def _apply_stt_quant(self, cmd: ChangeSTTQuant) -> CommandResult:
        draft_svc = self._require_draft("stt_quant")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for STT quant")
        draft = draft_svc._ensure_provider_settings_draft()
        if cmd.channel == "peer":
            draft.provider.peer_stt_quant = cmd.quant
        else:
            draft.provider.stt_quant = cmd.quant
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)

    def _apply_stt_compute(self, cmd: ChangeSTTCompute) -> CommandResult:
        draft_svc = self._require_draft("stt_compute")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for STT compute")
        draft = draft_svc._ensure_provider_settings_draft()
        if cmd.channel == "peer":
            draft.provider.peer_stt_compute = cmd.compute
        else:
            draft.provider.stt_compute = cmd.compute
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)

    def _apply_stt_backend(self, cmd: ChangeSTTBackend) -> CommandResult:
        draft_svc = self._require_draft("stt_backend")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for STT backend")
        draft = draft_svc._ensure_provider_settings_draft()
        if cmd.channel == "peer":
            draft.provider.peer_stt_backend = cmd.backend
            current = draft.provider.peer_stt
            if cmd.backend == "gguf" and current in _ONNX_TO_GGUF:
                draft.provider.peer_stt = _ONNX_TO_GGUF[current]
            elif cmd.backend == "onnx" and current in _GGUF_TO_ONNX:
                draft.provider.peer_stt = _GGUF_TO_ONNX[current]
        else:
            draft.provider.stt_backend = cmd.backend
            current = draft.provider.stt
            if cmd.backend == "gguf" and current in _ONNX_TO_GGUF:
                draft.provider.stt = _ONNX_TO_GGUF[current]
            elif cmd.backend == "onnx" and current in _GGUF_TO_ONNX:
                draft.provider.stt = _GGUF_TO_ONNX[current]
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)

    def _apply_local_llm_field(self, cmd: ChangeLocalLLMField) -> CommandResult:
        draft_svc = self._require_draft("local_llm_field")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for local LLM field")
        draft = draft_svc._ensure_provider_settings_draft()
        setattr(draft.local_llm, cmd.field, cmd.value)
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)

    def _apply_openai_compatible_field(
        self, cmd: ChangeOpenAICompatibleField
    ) -> CommandResult:
        draft_svc = self._require_draft("openai_compatible_field")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for OpenAI compatible field")
        draft = draft_svc._ensure_provider_settings_draft()
        setattr(draft.provider.openai_compatible, cmd.field, cmd.value)
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)

    def _apply_fallback_openai_field(
        self, cmd: ChangeFallbackOpenAIField
    ) -> CommandResult:
        draft_svc = self._require_draft("fallback_openai_field")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for fallback OpenAI field")
        draft = draft_svc._ensure_provider_settings_draft()
        setattr(draft.backup_translation.openai_compatible, cmd.field, cmd.value)
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)

    def _apply_fallback_local_llm_field(
        self, cmd: ChangeFallbackLocalLLMField
    ) -> CommandResult:
        draft_svc = self._require_draft("fallback_local_llm_field")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for fallback local LLM field")
        draft = draft_svc._ensure_provider_settings_draft()
        setattr(draft.backup_translation.local_llm, cmd.field, cmd.value)
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)

    def _apply_backup_translation(self, cmd: ChangeBackupTranslation) -> CommandResult:
        draft_svc = self._require_draft("backup_translation")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for backup translation")
        draft = draft_svc._ensure_provider_settings_draft()
        draft.backup_translation.enabled = cmd.enabled
        if cmd.mode is not None:
            from puripuly_heart.domain.providers import LLMProviderName
            draft.backup_translation.mode = LLMProviderName(cmd.mode)
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)

    def _apply_translation_selection(
        self, cmd: ChangeTranslationSelection
    ) -> CommandResult:
        draft_svc = self._require_draft("translation_selection")
        if draft_svc is None:
            return CommandResult(success=False, error="No draft service for translation selection")
        draft = draft_svc._ensure_provider_settings_draft()
        draft.provider.llm = LLMProviderName(cmd.provider)
        draft.translation.model = TranslationModel(cmd.provider)
        if self._materialize_fn is None:
            return CommandResult(success=False, error="materialize_fn not injected")
        self._materialize_fn(draft)
        draft_svc.has_provider_changes = True
        return CommandResult(success=True, settings=self._settings)
