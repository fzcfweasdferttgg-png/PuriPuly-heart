from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from types import SimpleNamespace

from puripuly_heart.app.adapters.peer_application_state import PeerApplicationSettings
from puripuly_heart.app.adapters.peer_capture.peer_capture_admission import (
    PeerCaptureAdmissionAdapter,
)
from puripuly_heart.app.adapters.ui_runtime import UiPeerCaptureRuntimeAdapter
from puripuly_heart.app.services.canonical_settings_persistence import compose_settings_owner
from puripuly_heart.app.services.settings.settings_transaction_result import (
    SettingsTransactionResultOwner,
)
from puripuly_heart.app.wiring.wiring_peer_application import compose_peer_application
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.peer_capture import (
    PeerCaptureAdmission,
    PeerCaptureAdmissionStatus,
    PeerCaptureLanguageFacts,
    PeerCaptureProviderMutation,
    PeerCaptureProviderMutationStatus,
    PeerCaptureResolvedTarget,
    PeerCaptureSessionConfig,
    PeerCaptureTargetIntent,
    PeerCaptureTargetResolution,
    PeerCaptureTargetStatus,
)
from puripuly_heart.core.runtime.peer_channel import PeerCaptureSessionOwner
from tests.helpers.ui_application import compose_test_ui_application_boundary


@dataclass(slots=True)
class FakeSource:
    close_calls: int = 0
    terminal_reason: str | None = None

    async def close(self) -> None:
        self.close_calls += 1


class FailingCloseSource:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise RuntimeError("close failed")


class FakeAdmission:
    def __init__(self) -> None:
        self.result = PeerCaptureAdmission(PeerCaptureAdmissionStatus.ADMITTED)
        self.calls: list[PeerCaptureSessionConfig] = []
        self.gate: asyncio.Event | None = None

    async def admit(self, config: PeerCaptureSessionConfig) -> PeerCaptureAdmission:
        self.calls.append(config)
        if self.gate is not None:
            await self.gate.wait()
        return self.result


class FakeTargetResolver:
    def __init__(self) -> None:
        self.calls: list[PeerCaptureTargetIntent] = []
        self.results: list[PeerCaptureTargetResolution] = []
        self.gate: asyncio.Event | None = None

    async def resolve(
        self,
        target: PeerCaptureTargetIntent,
    ) -> PeerCaptureTargetResolution:
        self.calls.append(target)
        if self.gate is not None:
            await self.gate.wait()
        if self.results:
            return self.results.pop(0)
        return PeerCaptureTargetResolution(
            PeerCaptureTargetStatus.RESOLVED,
            target=PeerCaptureResolvedTarget(intent=target),
        )


class FakeProvider:
    def __init__(self) -> None:
        self.provider_id: str | None = None
        self.requests: list[object] = []
        self.handoffs: list[object] = []
        self.releases: list[tuple[str, float | None]] = []
        self.start_calls = 0
        self.warmup_calls = 0
        self.cancel_calls = 0
        self.replace_result = PeerCaptureProviderMutation(PeerCaptureProviderMutationStatus.APPLIED)
        self.start_gate: asyncio.Event | None = None
        self.release_gate: asyncio.Event | None = None
        self.handoff_result = PeerCaptureProviderMutation(PeerCaptureProviderMutationStatus.APPLIED)
        self.replace_gate: asyncio.Event | None = None
        self.handoff_gate: asyncio.Event | None = None
        self.start_error: Exception | None = None
        self.replace_terminal_error: Exception | None = None
        self.terminal_handlers: list = []

    def is_ready(self, config: PeerCaptureSessionConfig) -> bool:
        return self.provider_id == config.provider_id

    async def replace(self, request, *, start: bool, on_terminal_failure):
        _ = start
        self.requests.append(request)
        self.terminal_handlers.append(on_terminal_failure)
        if self.replace_terminal_error is not None:
            await on_terminal_failure(self.replace_terminal_error)
        if self.replace_gate is not None:
            await self.replace_gate.wait()
        if self.replace_result.status is PeerCaptureProviderMutationStatus.APPLIED:
            self.provider_id = request[0]
        return self.replace_result

    async def handoff(self, request, *, start: bool, on_terminal_failure):
        _ = start
        self.handoffs.append(request)
        self.terminal_handlers.append(on_terminal_failure)
        if self.handoff_gate is not None:
            await self.handoff_gate.wait()
        if self.handoff_result.status is PeerCaptureProviderMutationStatus.APPLIED:
            self.provider_id = request[0]
        return self.handoff_result

    async def cancel_handoff(self) -> bool:
        self.cancel_calls += 1
        return True

    async def start_ingress(self) -> None:
        self.start_calls += 1
        if self.start_gate is not None:
            await self.start_gate.wait()
        if self.start_error is not None:
            raise self.start_error

    async def warmup(self) -> None:
        self.warmup_calls += 1

    async def release(
        self,
        *,
        mode: str,
        release_backend_after: float | None = None,
    ) -> None:
        self.releases.append((mode, release_backend_after))
        if self.release_gate is not None:
            await self.release_gate.wait()
        if mode == "abort":
            self.provider_id = None


class FakeVadSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def handle_owned_vad_event(self, event: object) -> None:
        self.events.append(event)


def make_config(
    *,
    provider_id: str = "soniox",
    target: PeerCaptureTargetIntent | None = None,
    language: PeerCaptureLanguageFacts | None = None,
    capture_signature: tuple[object, ...] | None = None,
) -> PeerCaptureSessionConfig:
    resolved_target = target or PeerCaptureTargetIntent(kind="default_output_device")
    language_facts = language or PeerCaptureLanguageFacts(
        source_mode="manual",
        source_language="ko",
    )
    signature = capture_signature or (resolved_target, 16000)
    return PeerCaptureSessionConfig(
        provider_id=provider_id,
        provider_signature=(provider_id, language_facts),
        runtime_signature=(provider_id, signature, language_facts, 0.6, 900, 500),
        capture_signature=signature,
        capture_target=resolved_target,
        language=language_facts,
        target_sample_rate_hz=16000,
        vad_speech_threshold=0.6,
        vad_hangover_ms=900,
        vad_pre_roll_ms=500,
        local_provider=provider_id.startswith("local_"),
        release_backend_after=600.0 if provider_id == "local_fun_asr_mlt_nano" else None,
    )


def make_owner(
    *,
    admission: FakeAdmission | None = None,
    resolver: FakeTargetResolver | None = None,
    provider: FakeProvider | None = None,
    sources: list[object] | None = None,
    source_factory=None,
    vad_factory=None,
    run_audio_loop=None,
    sink: FakeVadSink | None = None,
    smart_turn_owner=None,
    diagnostics: list | None = None,
    state_changes: list | None = None,
    clock=None,
) -> tuple[
    PeerCaptureSessionOwner,
    FakeAdmission,
    FakeTargetResolver,
    FakeProvider,
    list[object],
    FakeVadSink,
]:
    admission_port = admission or FakeAdmission()
    resolver_port = resolver or FakeTargetResolver()
    provider_port = provider or FakeProvider()
    created_sources = sources if sources is not None else []
    vad_sink = sink or FakeVadSink()

    def create_source(config, target):
        if source_factory is not None:
            return source_factory(config, target)
        source = FakeSource()
        created_sources.append(source)
        return source

    async def default_loop(**_kwargs) -> None:
        await asyncio.Event().wait()

    owner = PeerCaptureSessionOwner(
        admission=admission_port,
        target_resolver=resolver_port,
        provider=provider_port,
        clock=clock or FakeClock(),
        provider_request_factory=lambda config, warmup: (config.provider_id, warmup),
        source_factory=create_source,
        vad_factory=vad_factory or (lambda _config: object()),
        run_audio_loop=run_audio_loop or default_loop,
        vad_sink=vad_sink,
        smart_turn_owner=smart_turn_owner,
        state_changed=state_changes.append if state_changes is not None else None,
        diagnostic_sink=diagnostics.append if diagnostics is not None else None,
    )
    return owner, admission_port, resolver_port, provider_port, created_sources, vad_sink


def capture_application(tmp_path, monkeypatch, *, demand=True, ready=False, peer_provider=""):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    settings = compose_settings_owner(tmp_path / "settings.json")
    initial = AppSettingsVNext()
    if peer_provider:
        initial = replace(
            initial,
            intent=replace(
                initial.intent,
                peer_stt=replace(initial.intent.peer_stt, provider=peer_provider),
            ),
        )
    initial = replace(
        initial,
        state=replace(
            initial.state,
            peer_translation=replace(initial.state.peer_translation, eula_accepted=True),
        ),
    )
    settings.persistence.persist(settings.path, initial)
    settings.start()
    settings.set_peer_translation_enabled(demand)
    readiness = SimpleNamespace(available=ready, calls=0)

    async def ensure_ready(*_args):
        readiness.calls += 1
        return readiness.available

    runtime, _, _, provider, sources, _ = make_owner(
        admission=PeerCaptureAdmissionAdapter(
            runtime_available=lambda: True,
            ensure_local_ready=ensure_ready,
        ),
    )
    local_runtime = SimpleNamespace(
        snapshot=SimpleNamespace(
            channel_for=lambda _channel: SimpleNamespace(provider_id=provider.provider_id)
        )
    )
    overlay = SimpleNamespace(
        snapshot=SimpleNamespace(state="connected"),
        record_lifecycle_trace=lambda *_args, **_kwargs: None,
    )
    results = SettingsTransactionResultOwner()
    presented = []
    peer = compose_peer_application(
        settings_provider=lambda: PeerApplicationSettings(
            intent_enabled=settings.peer_translation_enabled(),
            eula_accepted=settings.require_canonical().state.peer_translation.eula_accepted,
            overlay_intent_enabled=False,
            provider_id=settings.require_canonical().intent.peer_stt.provider,
        ),
        settings_owner=settings,
        canonical_settings=settings.require_canonical,
        peer_intent_sink=settings.set_peer_translation_enabled,
        overlay_intent_sink=settings.set_overlay_enabled,
        runtime_provider=lambda: local_runtime,
        translation_runtime_configuration_provider=lambda: None,
        overlay_provider=lambda: overlay,
        ingress_frozen=lambda: False,
        ensure_local_ready=ensure_ready,
        clear_cpu_pending=lambda: None,
        clear_gpu_pending=lambda: None,
        clear_switched_pending=lambda: None,
        sync_local_notice=lambda: None,
        presentation_changed=lambda: None,
        disclosure_sink=lambda: None,
        superseded_sink=lambda: None,
        localize=lambda key: key,
        settings_presentation_sink=presented.append,
        log_basic=lambda *_args, **_kwargs: None,
        log_diagnostic=lambda *_args, **_kwargs: None,
        stop_audio_test=lambda: asyncio.sleep(0),
        transaction_result_sink=results.set,
    )
    peer.owner.bind_runtime(runtime)
    peer.target.devices = SimpleNamespace(names=lambda: ("Synthetic A", "Synthetic B"))
    peer.target.processes = SimpleNamespace(candidates=lambda: ())
    app = compose_test_ui_application_boundary(SimpleNamespace(settings=initial))
    app._peer_capture = UiPeerCaptureRuntimeAdapter(peer=peer, overlay=overlay)
    app._settings_results = results
    control = app.control()
    control.settings = settings
    control.results = results
    control.peer = lambda: peer.owner
    settings.observe_commits(control._publish_committed)
    return SimpleNamespace(
        app=app,
        control=control,
        settings=settings,
        peer=peer,
        runtime=runtime,
        provider=provider,
        sources=sources,
        readiness=readiness,
        presented=presented,
    )
