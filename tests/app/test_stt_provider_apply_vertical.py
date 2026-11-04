from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from puripuly_heart.app.adapters.peer_capture.peer_capture_provider import (
    PeerCaptureProviderAdapter,
)
from puripuly_heart.app.adapters.peer_capture.peer_capture_vad_sink import (
    PeerCaptureVadSinkAdapter,
)
from puripuly_heart.app.adapters.self_capture.self_capture_provider import (
    SelfCaptureProviderAdapter,
)
from puripuly_heart.app.adapters.ui_runtime import (
    UiProviderRuntimeAdapter,
    UiSettingsRuntimeAdapter,
)
from puripuly_heart.app.ports.settings_view import ProviderApplyIntent
from puripuly_heart.app.services.canonical_settings_persistence import compose_settings_owner
from puripuly_heart.app.services.capture.self_capture_application import (
    SelfCaptureApplicationOwner,
    SelfCaptureApplicationSettings,
)
from puripuly_heart.app.services.provider.provider_settings import ProviderApplicationOwner
from puripuly_heart.app.services.settings.settings_application import (
    settings_view_surface_snapshots,
)
from puripuly_heart.app.services.settings.settings_transaction_result import (
    SettingsTransactionResultOwner,
)
from puripuly_heart.app.services.ui_application import UiApplicationBoundary
from puripuly_heart.app.wiring.wiring_provider_runtime import compose_provider_runtime
from puripuly_heart.app.wiring.wiring_stt_factory import (
    build_peer_capture_session_config_from_vnext,
    build_peer_stt_provider_request,
    build_self_capture_session_config_from_vnext,
    build_self_stt_provider_request_from_config,
    build_self_stt_provider_request_from_vnext,
)
from puripuly_heart.config.provider_values import STTProviderName
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.audio.format import AudioCaptureSpan
from puripuly_heart.core.clock import SystemClock
from puripuly_heart.core.local_asr.local_asr_provider_runtime import (
    LocalASRProviderRuntimeCallbacks,
)
from puripuly_heart.core.messages import (
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_DEGRADED,
)
from puripuly_heart.core.peer_capture import (
    PeerCaptureAdmission,
    PeerCaptureAdmissionStatus,
    PeerCaptureResolvedTarget,
    PeerCaptureTargetResolution,
    PeerCaptureTargetStatus,
)
from puripuly_heart.core.runtime.local_asr_provider_runtime import (
    LocalASRProviderRuntimeOwner,
)
from puripuly_heart.core.runtime.peer_channel import PeerCaptureSessionOwner
from puripuly_heart.core.runtime.provider_handle import ProviderRuntimeHandle
from puripuly_heart.core.runtime.self_capture import SelfCaptureSessionOwner
from puripuly_heart.core.self_capture import (
    SelfCaptureAdmission,
    SelfCaptureAdmissionStatus,
)
from puripuly_heart.core.stt.backend import (
    STTProviderTurnIdentity,
    STTProviderTurnRequest,
    STTProviderTurnTerminal,
)
from puripuly_heart.core.stt.rolling import RollingProviderDefinition, RollingSTTBackend
from puripuly_heart.core.stt.scoped_engine import ScopedRecognitionEngine
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart
from puripuly_heart.domain.events import UIEventType
from puripuly_heart.ui.views.settings import SettingsView
from tests.core.runtime.test_local_asr_provider_runtime import (
    FakeGpuRuntimeFactory,
    FakeProvisioningPort,
)
from tests.helpers.fakes import RecordingOscQueue
from tests.helpers.translation_owners import compose_translation_test_harness


class _TransportSession:
    def __init__(self, *, terminal_text: str = "") -> None:
        self.closed = asyncio.Event()
        self.speech_ends: list[object] = []
        self.terminal_text = terminal_text
        self._events: asyncio.Queue[object] = asyncio.Queue()
        self.defer_terminal = False
        self.pending_terminal: STTProviderTurnTerminal | None = None

    async def begin_turn(self, _request: STTProviderTurnRequest) -> None:
        return None

    async def send_turn_audio(self, *_args, **_kwargs) -> None:
        return None

    async def seal_turn(self, identity: STTProviderTurnIdentity, **kwargs) -> None:
        self.speech_ends.append((kwargs.get("trailing_silence_ms"), kwargs.get("reason")))
        self.pending_terminal = STTProviderTurnTerminal(
            identity,
            "final" if self.terminal_text else "empty",
            text=self.terminal_text,
            text_authority="authoritative",
        )
        if not self.defer_terminal:
            await self.complete_turn()

    async def complete_turn(self) -> None:
        assert self.pending_terminal is not None
        await self._events.put(self.pending_terminal)
        self.pending_terminal = None

    async def turn_events(self):
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def abort_turn(self, _identity, *, reason) -> None:
        return None

    async def stop(self) -> None:
        await self.close()

    async def close(self) -> None:
        self.closed.set()
        await self._events.put(None)


class _TransportBackend:
    def __init__(self, *, terminal_text: str = "") -> None:
        self.sessions: list[_TransportSession] = []
        self.terminal_text = terminal_text

    async def open_session(self, **_kwargs) -> _TransportSession:
        session = _TransportSession(terminal_text=self.terminal_text)
        self.sessions.append(session)
        return session


class _ScopedProviderFactory:
    def __init__(self) -> None:
        self.providers: list[ScopedRecognitionEngine] = []
        self.transports: list[_TransportBackend] = []
        self.backends: list[object] = []
        self.terminal_text = ""

    async def create(self, request, *, gpu_runtime, on_terminal_failure=None):
        transport = _TransportBackend(terminal_text=self.terminal_text)
        gpu_activate = None
        if request.provider_id == STTProviderName.LOCAL_QWEN_GPU.value:

            async def gpu_activate() -> None:
                await gpu_runtime.activate_channel(
                    request.channel,
                    owner=transport,
                    model_path=Path("model.gguf"),
                    model_id="qwen3-asr-1.7b-gguf-q6_k",
                    device_id=request.gpu_device_id,
                )
        self.transports.append(transport)
        if request.provider_id == STTProviderName.ROLLING_FREE.value:
            backend = RollingSTTBackend(
                providers=(
                    RollingProviderDefinition(
                        name=STTProviderName.GEMINI_TRANSCRIBE,
                        build_backend=lambda transport=transport: transport,
                        is_configured=lambda: True,
                    ),
                )
            )
        else:
            backend = transport
        self.backends.append(backend)

        async def open_session(_settings, epoch_id):
            return await backend.open_session(
                projection=SimpleNamespace(mode="scoped", provider_epoch_id=epoch_id)
            )

        provider = ScopedRecognitionEngine(
            session_factory=open_session,
            channel=request.channel,
            terminal_failure_sink=on_terminal_failure,
            backend_prepare=gpu_activate,
            accepted_settings_scope=(
                request.provider_id,
                request.provider_signature,
                request.runtime_signature,
            ),
            event_drain_timeout_s=0.2,
        )
        self.providers.append(provider)
        return provider


class _RuntimeFactory:
    def __init__(self) -> None:
        self.provider_factory = _ScopedProviderFactory()
        self.runtime: LocalASRProviderRuntimeOwner | None = None
        self.callbacks: LocalASRProviderRuntimeCallbacks | None = None

    def create(self, callbacks: LocalASRProviderRuntimeCallbacks):
        self.runtime = LocalASRProviderRuntimeOwner(
            provider_factory=self.provider_factory,
            gpu_runtime_factory=FakeGpuRuntimeFactory(),
            provisioning=FakeProvisioningPort(),
            self_event_handler=callbacks.self_event_handler,
            peer_event_handler=callbacks.peer_event_handler,
            retired_event_handler=callbacks.retired_event_handler,
            self_exception_handler=callbacks.self_exception_handler,
            peer_exception_handler=callbacks.peer_exception_handler,
        )
        self.callbacks = callbacks
        return self.runtime


class _Admission:
    async def admit(self, _config):
        return SelfCaptureAdmission(SelfCaptureAdmissionStatus.ADMITTED)


class _PeerAdmission:
    async def admit(self, _config):
        return PeerCaptureAdmission(PeerCaptureAdmissionStatus.ADMITTED)


class _PeerTargetResolver:
    async def resolve(self, target):
        return PeerCaptureTargetResolution(
            PeerCaptureTargetStatus.RESOLVED,
            target=PeerCaptureResolvedTarget(intent=target),
        )


class _Source:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _ChannelReset:
    async def reset_provider_channel(self, _channel: str) -> None:
        return None


class _Peer:
    runtime = None
    last_provider_signature = None
    last_runtime_signature = None
    last_intent_enabled = False
    last_activation_requested = False

    def effective_enabled(self) -> bool:
        return False

    def desired_active(self, _state) -> bool:
        return False

    def activation_requested(self, *, intent_enabled: bool, eula_accepted: bool) -> bool:
        return bool(intent_enabled and eula_accepted)

    def capture_runtime_convergence(self, _config, _state=None) -> None:
        return None


class _ManagedRelease:
    service = None

    async def rebuild(self, **_kwargs) -> None:
        return None


class _NoopPort:
    def __getattr__(self, _name):
        return _noop


async def _noop() -> None:
    return None


def _settings(provider: str) -> AppSettingsVNext:
    base = AppSettingsVNext()
    return replace(
        base,
        intent=replace(
            base.intent,
            stt=replace(base.intent.stt, provider=provider),
            languages=replace(base.intent.languages, source_language="ko"),
        ),
    )


def _peer_settings(provider: str) -> AppSettingsVNext:
    base = AppSettingsVNext()
    return replace(
        base,
        intent=replace(
            base.intent,
            peer_stt=replace(base.intent.peer_stt, provider=provider),
            languages=replace(
                base.intent.languages,
                peer_source_mode="manual",
                peer_source_language="ko",
                peer_expected_languages=("ko",),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_local_scope_change_and_capture_restart_replace_engines_without_losing_recognition() -> (
    None
):
    settings = _settings("local_qwen_gpu")
    settings_holder = {"settings": settings}
    runtime_factory = _RuntimeFactory()
    runtime_factory.provider_factory.terminal_text = "recognized"
    osc = RecordingOscQueue()
    harness = compose_translation_test_harness(
        stt=None,
        llm=None,
        osc=osc,
        local_asr_provider_runtime_factory=runtime_factory,
    )
    runtime = runtime_factory.runtime
    assert runtime is not None
    sources: list[_Source] = []

    async def source_factory(_config):
        source = _Source()
        sources.append(source)
        return source

    async def run_audio_loop(**_kwargs):
        await asyncio.Event().wait()

    capture = SelfCaptureSessionOwner(
        admission=_Admission(),
        provider=SelfCaptureProviderAdapter(runtime, _ChannelReset()),
        provider_request_factory=lambda _config, _warmup: replace(
            build_self_stt_provider_request_from_vnext(settings_holder["settings"]),
            warmup=_warmup,
        ),
        source_factory=source_factory,
        vad_factory=lambda _config: object(),
        run_audio_loop=run_audio_loop,
        vad_sink=harness.self_owner,
    )
    initial_config = build_self_capture_session_config_from_vnext(settings)
    await capture.apply_intent(initial_config, enabled=True)
    initial_provider = runtime.current_provider("self")
    assert isinstance(initial_provider, ScopedRecognitionEngine)

    japanese = replace(
        settings,
        intent=replace(
            settings.intent,
            languages=replace(settings.intent.languages, source_language="ja"),
        ),
    )
    settings_holder["settings"] = japanese
    japanese_request = build_self_stt_provider_request_from_vnext(japanese)
    japanese_config = build_self_capture_session_config_from_vnext(japanese)
    await capture.apply_intent(japanese_config, enabled=True)
    configured_provider = runtime.current_provider("self")
    assert isinstance(configured_provider, ScopedRecognitionEngine)
    assert configured_provider is not initial_provider
    assert configured_provider.accepted_settings_scope is not None
    assert configured_provider.accepted_settings_scope[2] == japanese_request.runtime_signature

    first_dispatch = capture._vad_dispatch
    assert first_dispatch is not None
    frame = np.zeros(512, dtype=np.float32)
    first_id = uuid4()
    await first_dispatch.handle_vad_event(SpeechStart(first_id, frame, frame))
    await first_dispatch.handle_vad_event(SpeechEnd(first_id))

    async def wait_for_completed_turn(
        transport: _TransportBackend,
        provider: ScopedRecognitionEngine,
    ) -> None:
        while (
            not transport.sessions
            or not transport.sessions[-1].speech_ends
            or not provider.is_at_utterance_boundary
        ):
            await asyncio.sleep(0)

    await asyncio.wait_for(
        wait_for_completed_turn(
            runtime_factory.provider_factory.transports[1],
            configured_provider,
        ),
        timeout=1.0,
    )

    restarted_config = replace(
        japanese_config,
        capture_signature=("changed-device",),
    )
    await capture.apply_intent(restarted_config, enabled=True, restart=True)

    restarted_provider = runtime.current_provider("self")
    assert isinstance(restarted_provider, ScopedRecognitionEngine)
    assert restarted_provider is not configured_provider
    assert configured_provider.is_live is False
    assert runtime.snapshot.channel_for("self").provider_live is True
    assert len(sources) == 2
    assert sources[0].close_calls == 1
    second_dispatch = capture._vad_dispatch

    assert second_dispatch is not None
    second_id = uuid4()
    await second_dispatch.handle_vad_event(SpeechStart(second_id, frame, frame))
    await second_dispatch.handle_vad_event(SpeechEnd(second_id))
    await asyncio.wait_for(
        wait_for_completed_turn(
            runtime_factory.provider_factory.transports[2],
            restarted_provider,
        ),
        timeout=1.0,
    )
    await capture.close()
    await runtime.close()


