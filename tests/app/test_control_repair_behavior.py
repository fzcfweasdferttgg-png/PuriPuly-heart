from __future__ import annotations

import asyncio
import io
import json
import logging
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from puripuly_heart.app.ports.settings_view import LocaleSettingsIntent
from puripuly_heart.app.services.canonical_settings_persistence import (
    CanonicalSettingsPatchRepository,
)
from puripuly_heart.cli.host import HostedApplication
from puripuly_heart.composition import application_runtime
from puripuly_heart.composition.headless_application import compose_headless_application
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext, SecretsIntent
from puripuly_heart.config.settings_vnext.serialization import to_dict
from puripuly_heart.core.runtime_logging import RuntimeLoggingSinks


def isolated_settings(path):
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            secrets=SecretsIntent(backend="encrypted_file", encrypted_file_path="secrets.json"),
            osc=replace(settings.intent.osc, connection_mode="off"),
            stt=replace(settings.intent.stt, custom_terms={"ko": ["private phrase"]}),
        ),
    )
    path.write_text(json.dumps(to_dict(settings)), encoding="utf-8")


@pytest.fixture(autouse=True)
async def isolated_composed_logging(tmp_path, monkeypatch):
    root = logging.getLogger()
    level = root.level
    monkeypatch.setattr(root, "handlers", list(root.handlers))
    monkeypatch.setattr(root, "filters", list(root.filters))
    monkeypatch.setattr(root, "level", level)
    for handler in root.handlers:
        monkeypatch.setattr(handler, "filters", list(handler.filters))
    compose = compose_headless_application
    applications = []

    def tracked_compose(path):
        log_file = tmp_path / f"headless-{len(applications)}.log"
        sinks = RuntimeLoggingSinks(
            logging.StreamHandler(io.StringIO()),
            logging.FileHandler(log_file, encoding="utf-8"),
            log_file,
        )
        try:
            application = compose(path, runtime_logging_sinks=sinks)
        except BaseException:
            sinks.close()
            raise
        applications.append((application, sinks))
        return application

    monkeypatch.setitem(globals(), "compose_headless_application", tracked_compose)
    try:
        yield
    finally:
        try:
            for application, sinks in reversed(applications):
                try:
                    if application.control().pipeline.self_translation_channel is not None:
                        await application.stop()
                    else:
                        service = application._runtime_logging.installed_service
                        if service is not None:
                            service.close()
                finally:
                    sinks.close()
                    sinks.stream_handler.close()
        finally:
            root.setLevel(level)


@pytest.fixture
def offline(tmp_path, monkeypatch):
    monkeypatch.setenv("PURIPULY_HEART_SECRETS_PASSPHRASE", "isolated-test-passphrase")
    monkeypatch.setattr(
        application_runtime, "default_http_extensions_dir", lambda: tmp_path / "extensions"
    )
    path = tmp_path / "settings.json"
    isolated_settings(path)
    return path


@pytest.mark.parametrize(
    ("demand", "ready", "status"),
    [(True, False, "degraded"), (True, True, "applied"), (False, False, "applied")],
)
async def test_audio_target_control_receipt_matches_effective_listen_application(
    tmp_path, monkeypatch, demand, ready, status
):
    from tests.helpers.peer_capture_fakes import capture_application

    harness = capture_application(
        tmp_path,
        monkeypatch,
        demand=demand,
        ready=ready,
        peer_provider="local_qwen_gpu",
    )
    try:
        accepted = await harness.control.submit(
            "audio.target.set", {"value": "device:Synthetic A"}, request_id="target-state"
        )
        receipt = await harness.control.wait(accepted["operation_id"], timeout=5)
        assert receipt["terminal"] is True
        assert receipt["status"] == status
        assert receipt["transaction"]["status"] == (
            "settings_commit_success_runtime_applied"
            if status == "applied"
            else "settings_commit_success_runtime_degraded"
        )
        assert (
            harness.settings.require_committed().intent.desktop_audio.capture_target.device_name
            == "Synthetic A"
        )
        assert harness.settings.peer_translation_enabled() is demand
        assert harness.runtime.snapshot.desired_active is demand
        assert harness.runtime.snapshot.effective_active is (demand and ready)
        assert harness.peer.owner.snapshot().effective_enabled is (demand and ready)
    finally:
        await harness.peer.owner.close()
        await harness.control.drain_operations()


@pytest.mark.parametrize("unavailable", [False, True])
async def test_audio_target_control_preserves_invalid_and_unavailable_rejection(
    tmp_path, monkeypatch, unavailable
):
    from puripuly_heart.app.ports.ui_models import OptionItem
    from tests.helpers.peer_capture_fakes import capture_application

    harness = capture_application(tmp_path, monkeypatch)
    if unavailable:
        monkeypatch.setattr(
            harness.app,
            "list_loopback_capture_options",
            lambda: [OptionItem(value="device:Unknown", label="Unknown", disabled=True)],
        )
    try:
        accepted = await harness.control.submit(
            "audio.target.set", {"value": "device:Unknown"}, request_id="target-rejected"
        )
        receipt = await harness.control.wait(accepted["operation_id"], timeout=5)
        assert receipt["status"] == ("action_required" if unavailable else "rejected")
        assert receipt["error"]["code"] == (
            "capture_target_unavailable" if unavailable else "invalid_arguments"
        )
        assert harness.sources == []
        assert harness.provider.requests == []
    finally:
        await harness.peer.owner.close()
        await harness.control.drain_operations()


async def test_audio_target_control_preserves_failed_runtime_exception(tmp_path, monkeypatch):
    from tests.helpers.peer_capture_fakes import capture_application

    harness = capture_application(tmp_path, monkeypatch)
    harness.app._runtime_logging = None

    async def fail_refresh():
        raise RuntimeError("controlled capture runtime failure")

    effects = harness.peer.target.runtime_effects
    harness.peer.target.runtime_effects = replace(effects, refresh_peer=fail_refresh)
    try:
        accepted = await harness.control.submit(
            "audio.target.set", {"value": "device:Synthetic A"}, request_id="target-failed"
        )
        receipt = await harness.control.wait(accepted["operation_id"], timeout=5)
        assert receipt["status"] == "failed"
        assert receipt["error"]["code"] == "application_operation_failed"
        assert (
            harness.settings.require_committed().intent.desktop_audio.capture_target.device_name
            == "Synthetic A"
        )
        assert harness.settings.peer_translation_enabled() is True
    finally:
        await harness.peer.owner.close()
        await harness.control.drain_operations()


@pytest.mark.asyncio
async def test_committed_gui_event_is_unsolicited_and_staged_failure_is_invisible(
    offline, monkeypatch
):
    app = compose_headless_application(offline)
    try:
        await app.start()
        control = app.control()
        control.bind_instance("offline-commit")
        initial = await control.query("settings.current", {})
        assert initial["settings"]["intent"]["stt"]["custom_terms"] == "<redacted>"
        assert "private phrase" not in json.dumps(initial)
        stream = control.subscribe(topics=["settings"])
        event_task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        await app.apply_settings_intent(LocaleSettingsIntent("ja"))
        event = await asyncio.wait_for(event_task, 5)
        updated = await control.query("settings.current", {})
        assert event["revision"] == updated["revision"] == initial["revision"] + 1
        assert updated["settings"]["intent"]["ui"]["locale"] == "ja"
        await app.apply_settings_intent(LocaleSettingsIntent("ja"))
        assert (await control.query("settings.current", {}))["revision"] == updated["revision"]
        await stream.aclose()

        settings_app = app._settings.application
        entered = asyncio.Event()
        release = asyncio.Event()
        original = type(settings_app.runtime_effects).prepare

        async def staged(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(type(settings_app.runtime_effects), "prepare", staged)
        before = await control.query("settings.current", {})
        candidate = replace(
            app.compatibility_settings(),
            intent=replace(
                app.compatibility_settings().intent,
                ui=replace(app.compatibility_settings().intent.ui, locale="en"),
            ),
        )
        original_persist = type(control.settings.persistence).persist

        def fail_persist(self, *_args):
            raise OSError("isolated save failure")

        monkeypatch.setattr(type(control.settings.persistence), "persist", fail_persist)
        pending = asyncio.create_task(settings_app.apply_direct(candidate))
        await asyncio.wait_for(entered.wait(), 5)
        staged_query = await control.query("settings.current", {})
        assert staged_query == before
        release.set()
        assert await pending is False
        assert await control.query("settings.current", {}) == before
        monkeypatch.setattr(type(control.settings.persistence), "persist", original_persist)
        assert not [
            item
            for item in control.events.history
            if item["topic"] == "settings" and item["revision"] > before["revision"]
        ]
    finally:
        await app.stop()


@pytest.mark.asyncio
async def test_gui_commit_reaches_idle_subscriber_without_any_prior_query(offline):
    app = compose_headless_application(offline)
    try:
        await app.start()
        control = app.control()
        control.bind_instance("offline-subscriber")
        stream = control.subscribe(topics=["settings"])
        event_task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        await app.apply_settings_intent(LocaleSettingsIntent("ja"))
        event = await asyncio.wait_for(event_task, 5)
        current = await control.query("settings.current", {})
        assert event["revision"] == current["revision"]
        assert current["settings"]["intent"]["ui"]["locale"] == "ja"
        await stream.aclose()
    finally:
        await app.stop()


@pytest.mark.asyncio
async def test_shutdown_before_accepted_task_first_runs_reports_terminal_interruption(offline):
    app = compose_headless_application(offline)
    control = app.control()
    control.bind_instance("offline-early-freeze")
    accepted = await control.submit(
        "settings.apply", {"changes": {"locale": "ja"}}, request_id="early"
    )
    control.freeze_ingress()
    await control.drain_operations()
    receipt = await control.operation(accepted["operation_id"])
    assert receipt["status"] == "interrupted"
    assert receipt["terminal"] is True
    assert "transaction" not in receipt
    assert any(
        event.get("topic") == "operation"
        and event.get("operation_id") == accepted["operation_id"]
        and event.get("status") == "interrupted"
        for event in control.events.history
    )
    assert json.loads(offline.read_text(encoding="utf-8"))["intent"]["ui"]["locale"] == "en"


@pytest.mark.asyncio
async def test_completed_settings_receipt_retains_applied_transaction(offline):
    app = compose_headless_application(offline)
    try:
        await app.start()
        control = app.control()
        control.bind_instance("offline-completed-settings")
        initial = await control.query("settings.current", {})
        submitted = await control.submit(
            "settings.apply",
            {"changes": {"locale": "ja"}},
            request_id="completed-locale",
        )
        receipt = await control.wait(submitted["operation_id"], timeout=5)
        assert receipt["status"] == "applied"
        assert receipt["terminal"] is True
        assert receipt["transaction"]["status"] == "settings_commit_success_runtime_applied"
        assert receipt["revision"] == initial["revision"] + 1
        assert (await control.query("settings.current", {}))["revision"] == receipt["revision"]
    finally:
        await app.stop()
    assert (await control.operation(submitted["operation_id"])) == receipt


@pytest.mark.asyncio
async def test_host_shutdown_interrupts_active_and_queued_settings_without_late_commit(
    offline, monkeypatch
):
    app = compose_headless_application(offline)

    class Lease:
        def publish(self, **_kwargs):
            pass

        def close(self):
            pass

    host = HostedApplication(app, Lease())
    entered = asyncio.Event()
    release = asyncio.Event()
    original = CanonicalSettingsPatchRepository.save

    async def slow_save(self, request):
        entered.set()
        await release.wait()
        return await original(self, request)

    try:
        await host.start()
        monkeypatch.setattr(CanonicalSettingsPatchRepository, "save", slow_save)
        control = host.control
        initial = await control.query("settings.current", {})
        active = await control.submit(
            "settings.apply", {"changes": {"locale": "ja"}}, request_id="slow-active"
        )
        await asyncio.wait_for(entered.wait(), 5)
        queued = await control.submit(
            "settings.apply", {"changes": {"locale": "en"}}, request_id="queued"
        )
        await asyncio.sleep(0)
        stop = await control.submit("app.stop", {}, request_id="stop")
        assert (await control.wait(stop["operation_id"], timeout=5))["status"] == "applied"
        await asyncio.wait_for(host.wait_for_stop(), 6)
        await asyncio.wait_for(host.close(), 6)
        active_receipt = await control.operation(active["operation_id"])
        queued_receipt = await control.operation(queued["operation_id"])
        assert active_receipt["status"] == queued_receipt["status"] == "interrupted"
        assert "transaction" not in active_receipt
        assert "transaction" not in queued_receipt
        assert queued_receipt["revision"] == initial["revision"]
        assert (await control.query("settings.current", {})) == initial
        assert not [
            operation.task
            for operation in control._operations.values()
            if operation.task is not None and not operation.task.done()
        ]
    finally:
        release.set()
        await host.close()


@pytest.mark.asyncio
async def test_noninterruptible_persistence_drains_before_host_closes(offline, monkeypatch):
    app = compose_headless_application(offline)

    class Lease:
        def publish(self, **_kwargs):
            pass

        def close(self):
            pass

    host = HostedApplication(app, Lease())
    entered = threading.Event()
    release = threading.Event()
    original = type(host.control.settings.persistence).persist

    def slow_save(self, path, settings):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("save gate not released")
        return original(self, path, settings)

    try:
        await host.start()
        assert not entered.is_set()
        monkeypatch.setattr(type(host.control.settings.persistence), "persist", slow_save)
        control = host.control
        initial = await control.query("settings.current", {})
        stream = control.subscribe(topics=["settings"])
        event_task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        active = await control.submit(
            "settings.apply", {"changes": {"locale": "ja"}}, request_id="persist-active"
        )
        assert await asyncio.to_thread(entered.wait, 5)
        assert (await control.query("settings.current", {})) == initial
        assert not event_task.done()
        queued = await control.submit(
            "settings.apply", {"changes": {"locale": "en"}}, request_id="persist-queued"
        )
        stop = await control.submit("app.stop", {}, request_id="persist-stop")
        await control.wait(stop["operation_id"], timeout=5)
        closing = asyncio.create_task(host.close())
        await asyncio.sleep(0.05)
        assert not closing.done()
        release.set()
        await asyncio.wait_for(closing, 6)
        delivered = await asyncio.wait_for(event_task, 5)
        assert delivered["revision"] == initial["revision"] + 1
        await stream.aclose()
        active_receipt = await control.operation(active["operation_id"])
        queued_receipt = await control.operation(queued["operation_id"])
        current = await control.query("settings.current", {})
        assert active_receipt["status"] == "interrupted"
        assert active_receipt["terminal"] is True
        assert active_receipt["revision"] == delivered["revision"] == current["revision"]
        assert active_receipt["transaction"]["status"] in {
            "settings_commit_success_runtime_interrupted",
            "settings_commit_success_runtime_applied",
            "settings_commit_success_runtime_degraded",
        }
        assert queued_receipt["status"] == "interrupted"
        assert queued_receipt["terminal"] is True
        assert queued_receipt["revision"] == initial["revision"]
        assert "transaction" not in queued_receipt
        assert current["settings"]["intent"]["ui"]["locale"] == "ja"
        assert not [
            operation.task
            for operation in control._operations.values()
            if operation.task is not None and not operation.task.done()
        ]
        assert json.loads(offline.read_text(encoding="utf-8"))["intent"]["ui"]["locale"] == "ja"
    finally:
        release.set()
        await host.close()


@pytest.mark.asyncio
async def test_shutdown_after_commit_before_runtime_reports_interrupted_transaction(
    offline, monkeypatch
):

    app = compose_headless_application(offline)

    class Lease:
        def publish(self, **_kwargs):
            pass

        def close(self):
            pass

    host = HostedApplication(app, Lease())
    persist_entered = threading.Event()
    persist_release = threading.Event()
    runtime_entered = asyncio.Event()
    runtime_release = asyncio.Event()
    runtime_cancelled = asyncio.Event()
    original_persist = type(host.control.settings.persistence).persist

    def slow_save(self, path, settings):
        persist_entered.set()
        if not persist_release.wait(5):
            raise TimeoutError("save gate not released")
        return original_persist(self, path, settings)

    async def gated_clipboard_sync(self, *, enabled, strict_runtime_errors=None):
        if self is clipboard and enabled:
            runtime_entered.set()
            try:
                await runtime_release.wait()
            except asyncio.CancelledError:
                runtime_cancelled.set()
                raise
        await original_sync(self, enabled=enabled, strict_runtime_errors=strict_runtime_errors)

    try:
        await host.start()
        clipboard = app._settings.application.runtime_effects._clipboard
        original_sync = type(clipboard).sync
        initial_strict_runtime_errors = clipboard.strict_runtime_errors
        assert clipboard.runtime is None
        monkeypatch.setattr(type(host.control.settings.persistence), "persist", slow_save)
        monkeypatch.setattr(type(clipboard), "sync", gated_clipboard_sync)
        control = host.control
        initial = await control.query("settings.current", {})
        active = await control.submit(
            "settings.apply",
            {"changes": {"locale": "ja", "clipboard.enabled": True}},
            request_id="commit-before-runtime",
        )
        assert await asyncio.to_thread(persist_entered.wait, 5)
        queued = await control.submit(
            "settings.apply",
            {"changes": {"locale": "en"}},
            request_id="uncommitted-queued",
        )
        persist_release.set()
        await asyncio.wait_for(runtime_entered.wait(), 5)
        committed = await control.query("settings.current", {})
        assert committed["revision"] == initial["revision"] + 1
        assert committed["settings"]["intent"]["ui"]["locale"] == "ja"
        assert committed["settings"]["intent"]["clipboard"]["auto_translate_enabled"] is True
        assert json.loads(offline.read_text(encoding="utf-8"))["intent"]["ui"]["locale"] == "ja"
        assert clipboard.runtime is None
        assert not control._operations[active["operation_id"]].task.done()
        closing = asyncio.create_task(host.close())
        await asyncio.wait_for(closing, 6)
        active_receipt = await control.operation(active["operation_id"])
        queued_receipt = await control.operation(queued["operation_id"])
        current = await control.query("settings.current", {})
        assert active_receipt["status"] == "interrupted"
        assert active_receipt["terminal"] is True
        assert (
            active_receipt["transaction"]["status"] == "settings_commit_success_runtime_interrupted"
        )
        assert active_receipt["revision"] == current["revision"] == initial["revision"] + 1
        assert "private phrase" not in json.dumps(active_receipt)
        assert queued_receipt["status"] == "interrupted"
        assert queued_receipt["revision"] == initial["revision"]
        assert "transaction" not in queued_receipt
        assert current["settings"]["intent"]["ui"]["locale"] == "ja"
        assert json.loads(offline.read_text(encoding="utf-8"))["intent"]["ui"]["locale"] == "ja"
        assert current["settings"]["intent"]["clipboard"]["auto_translate_enabled"] is True
        assert (
            json.loads(offline.read_text(encoding="utf-8"))["intent"]["clipboard"][
                "auto_translate_enabled"
            ]
            is True
        )
        assert runtime_cancelled.is_set()
        assert clipboard.runtime is None
        assert clipboard.strict_runtime_errors == initial_strict_runtime_errors
        assert not [
            operation.task
            for operation in control._operations.values()
            if operation.task is not None and not operation.task.done()
        ]
    finally:
        runtime_release.set()
        persist_release.set()
        if "active" in locals():
            operation = host.control._operations[active["operation_id"]]
            if operation.task is not None and not operation.task.done():
                operation.task.cancel()
        await host.close()


@pytest.mark.asyncio
async def test_model_install_wait_releases_global_lock_but_keeps_backend_order(offline):
    app = compose_headless_application(offline)
    started = asyncio.Event()
    release = asyncio.Event()
    installs = []
    snapshot = SimpleNamespace(
        gpu_model_id="gpu-test",
        models=(SimpleNamespace(model_id="gpu-test", backend="gpu"),),
    )

    class Provisioning:
        def __init__(self):
            self.snapshot = snapshot

        def start_install(self, request):
            installs.append(request)

            async def finish():
                started.set()
                await release.wait()
                return SimpleNamespace(cancelled=False, failed_model_ids=(), snapshot=snapshot)

            return asyncio.create_task(finish())

    try:
        await app.start()
        control = app.control()
        control.bind_instance("isolated-model-wait")
        control.provisioning = lambda: Provisioning()
        first = await control.submit(
            "models.install",
            {"backend": "gpu", "model_ids": ["gpu-test"]},
            request_id="first-install",
        )
        await asyncio.wait_for(started.wait(), 3)
        second = await control.submit(
            "models.install",
            {"backend": "gpu", "model_ids": ["gpu-test"]},
            request_id="second-install",
        )
        initial = (await control.query("settings.current", {}))["revision"]
        settings = await control.submit(
            "settings.apply",
            {"changes": {"locale": "ja"}},
            request_id="during-install",
            expected_revision=initial,
        )
        committed = await control.wait(settings["operation_id"], timeout=5)
        assert committed["status"] == "applied"
        assert committed["transaction"]["status"] == "settings_commit_success_runtime_applied"
        assert (await control.wait(first["operation_id"], timeout=0.01))["terminal"] is False
        assert len(installs) == 1
        off = await control.submit(
            "capture.set",
            {"channel": "self", "enabled": False},
            request_id="off-during-install",
        )
        assert (await control.wait(off["operation_id"], timeout=5))["status"] == "applied"
        release.set()
        assert (await control.wait(first["operation_id"], timeout=5))["status"] == "applied"
        assert (await control.wait(second["operation_id"], timeout=5))["status"] == "applied"
        assert len(installs) == 2
    finally:
        release.set()
        await app.stop()


@pytest.mark.asyncio
async def test_freeze_cancels_active_install_and_never_starts_queued_backend_work(offline):
    app = compose_headless_application(offline)
    started = asyncio.Event()
    release = asyncio.Event()
    installs = []
    snapshot = SimpleNamespace(
        gpu_model_id="gpu-test",
        models=(SimpleNamespace(model_id="gpu-test", backend="gpu"),),
    )

    class Provisioning:
        def __init__(self):
            self.snapshot = snapshot

        def start_install(self, request):
            installs.append(request)

            async def finish():
                started.set()
                await release.wait()
                return SimpleNamespace(cancelled=False, failed_model_ids=(), snapshot=snapshot)

            return asyncio.create_task(finish())

    try:
        await app.start()
        control = app.control()
        control.bind_instance("isolated-model-freeze")
        owner = Provisioning()
        control.provisioning = lambda: owner
        first = await control.submit(
            "models.install", {"backend": "gpu", "model_ids": ["gpu-test"]}, request_id="active-install"
        )
        await asyncio.wait_for(started.wait(), 3)
        second = await control.submit(
            "models.install", {"backend": "gpu", "model_ids": ["gpu-test"]}, request_id="queued-install"
        )
        await asyncio.sleep(0)
        control.freeze_ingress()
        await asyncio.wait_for(control.drain_operations(), 5)
        assert (await control.operation(first["operation_id"]))["status"] == "interrupted"
        queued = await control.operation(second["operation_id"])
        assert queued["status"] == "interrupted"
        assert "transaction" not in queued
        assert len(installs) == 1
    finally:
        release.set()
        await app.stop()


@pytest.mark.asyncio
async def test_install_cancel_still_targets_started_owner_work(offline):
    app = compose_headless_application(offline)
    started = asyncio.Event()
    cancelled = []
    snapshot = SimpleNamespace(
        gpu_model_id="gpu-test",
        models=(SimpleNamespace(model_id="gpu-test", backend="gpu"),),
    )

    class Provisioning:
        def __init__(self):
            self.snapshot = snapshot

        def start_install(self, _request):
            async def finish():
                started.set()
                await asyncio.Event().wait()

            return asyncio.create_task(finish())

        async def cancel_install(self, backend):
            cancelled.append(backend)

    try:
        await app.start()
        control = app.control()
        control.bind_instance("isolated-model-cancel")
        owner = Provisioning()
        control.provisioning = lambda: owner
        submitted = await control.submit(
            "models.install",
            {"backend": "gpu", "model_ids": ["gpu-test"]},
            request_id="cancel-install",
        )
        await asyncio.wait_for(started.wait(), 3)
        receipt = await asyncio.wait_for(control.cancel(submitted["operation_id"]), 5)
        assert receipt["status"] == "cancelled"
        assert receipt["terminal"] is True
        assert cancelled == ["gpu"]
    finally:
        await app.stop()


@pytest.mark.asyncio
async def test_manual_output_wait_allows_safe_mutations_but_orders_provider_transition(
    offline, monkeypatch
):
    app = compose_headless_application(offline)
    entered = asyncio.Event()
    release = asyncio.Event()
    translated = []

    from uuid import uuid4

    from puripuly_heart.core.orchestrator.self_translation_channel import TextSubmissionAdmission

    async def slow_output(_text):
        entered.set()
        await release.wait()
        return TextSubmissionAdmission("admitted", uuid4(), ())

    async def translation(enabled, **_kwargs):
        translated.append(enabled)
        return True

    try:
        await app.start()
        control = app.control()
        control.bind_instance("isolated-manual-wait")
        monkeypatch.setattr(app, "submit_text", slow_output)
        monkeypatch.setattr(app, "set_translation_enabled", translation)
        manual = await control.submit("text.submit", {"text": "safe text"}, request_id="manual")
        await asyncio.wait_for(entered.wait(), 3)
        old_revision = (await control.query("settings.current", {}))["revision"]
        safe = await control.submit(
            "settings.apply",
            {"changes": {"locale": "ja"}},
            request_id="manual-safe",
        )
        committed = await control.wait(safe["operation_id"], timeout=5)
        assert committed["status"] == "applied"
        off = await control.submit(
            "capture.set",
            {"channel": "self", "enabled": False},
            request_id="manual-off",
        )
        assert (await control.wait(off["operation_id"], timeout=5))["status"] == "applied"
        stale = await control.submit(
            "translation.set",
            {"enabled": False},
            request_id="manual-stale",
            expected_revision=old_revision,
        )
        conflicting = await control.submit(
            "translation.set",
            {"enabled": False},
            request_id="manual-translation",
        )
        assert (await control.wait(conflicting["operation_id"], timeout=0.01))["terminal"] is False
        assert (await control.wait(stale["operation_id"], timeout=0.01))["terminal"] is False
        osc_runtime = app._runtime_shutdown.vrc_mic_sync()
        assert osc_runtime is not None
        osc_change = asyncio.create_task(
            osc_runtime.router._application.set_secondary_target_language("zh-CN")
        )
        await asyncio.sleep(0)
        assert not osc_change.done()
        assert translated == []
        assert (await control.wait(manual["operation_id"], timeout=0.01))["terminal"] is False
        release.set()
        assert (await control.wait(manual["operation_id"], timeout=5))["status"] == "applied"
        rejected = await control.wait(stale["operation_id"], timeout=5)
        assert rejected["status"] == "rejected"
        assert rejected["error"] == {
            "code": "revision_conflict",
            "current_revision": committed["revision"],
        }
        assert (await control.wait(conflicting["operation_id"], timeout=5))["status"] == "applied"
        assert translated == [False]
        await asyncio.wait_for(osc_change, 5)
        assert (await control.query("settings.current", {}))["settings"]["intent"]["languages"][
            "secondary_target_language"
        ] == "zh-CN"
    finally:
        release.set()
        await app.stop()


@pytest.mark.asyncio
async def test_long_output_wait_orders_cli_overlay_audio_and_gui_settings_capture(
    offline, monkeypatch
):
    app = compose_headless_application(offline)
    entered = asyncio.Event()
    release = asyncio.Event()

    from uuid import uuid4

    from puripuly_heart.core.orchestrator.self_translation_channel import TextSubmissionAdmission

    async def slow_output(_text):
        entered.set()
        await release.wait()
        return TextSubmissionAdmission("admitted", uuid4(), ())

    async def no_audio_effects(_settings) -> bool:
        return True

    try:
        await app.start()
        control = app.control()
        control.bind_instance("isolated-complete-resource-order")
        monkeypatch.setattr(app, "submit_text", slow_output)
        target = app._peer_capture.peer.target
        target.devices = SimpleNamespace(names=lambda: ["Simulated A", "Simulated B"])
        target.processes = SimpleNamespace(candidates=lambda: ())
        target.runtime_effects = SimpleNamespace(apply_capture_target=no_audio_effects)
        app.begin_overlay_calibration()
        app.set_overlay_calibration_field("offset_x", 37)
        before = await control.query("settings.current", {})
        assert app.current_loopback_capture_option_value() == "device:"

        manual = await control.submit(
            "text.submit", {"text": "gated output"}, request_id="order-manual"
        )
        await asyncio.wait_for(entered.wait(), 3)
        mutations = [
            ("overlay.size", {"preset": "large"}),
            ("overlay.lock", {"locked": True}),
            ("overlay.position.reset", {}),
            ("overlay.calibrate", {"action": "apply"}),
            ("audio.target.set", {"value": "device:Simulated A"}),
        ]
        pending = [
            await control.submit(command, arguments, request_id=f"order-{index}")
            for index, (command, arguments) in enumerate(mutations)
        ]
        await asyncio.sleep(0)
        assert all(
            not receipt["terminal"]
            for receipt in [await control.operation(item["operation_id"]) for item in pending]
        )
        assert (await control.query("settings.current", {})) == before
        assert app.current_loopback_capture_option_value() == "device:"
        release.set()
        assert (await control.wait(manual["operation_id"], timeout=5))["status"] == "applied"
        outcomes = [await control.wait(item["operation_id"], timeout=5) for item in pending]
        assert [item["status"] for item in outcomes] == [
            "applied",
            "action_required",
            "action_required",
            "applied",
            "applied",
        ]
        after = await control.query("settings.current", {})
        assert after["revision"] > before["revision"]
        assert after["settings"]["intent"]["overlay"]["desktop_flet"]["size_preset"] == "large"
        assert after["settings"]["intent"]["overlay"]["calibration"]["offset_x"] == 37
        assert app.current_loopback_capture_option_value() == "device:Simulated A"

        entered.clear()
        release = asyncio.Event()
        second = await control.submit(
            "text.submit", {"text": "second gated output"}, request_id="order-manual-2"
        )
        await asyncio.wait_for(entered.wait(), 3)
        gui_capture = asyncio.create_task(app.apply_loopback_capture_option("device:Simulated B"))
        await asyncio.sleep(0)
        assert not gui_capture.done()
        assert app.current_loopback_capture_option_value() == "device:Simulated A"
        release.set()
        assert (await control.wait(second["operation_id"], timeout=5))["status"] == "applied"
        await asyncio.wait_for(gui_capture, 5)
        assert app.current_loopback_capture_option_value() == "device:Simulated B"
    finally:
        release.set()
        await app.stop()


@pytest.mark.asyncio
async def test_freeze_interrupts_manual_output_and_queued_provider_without_late_dispatch(
    offline, monkeypatch
):
    app = compose_headless_application(offline)
    entered = asyncio.Event()
    release = asyncio.Event()
    transitions = []

    async def slow_output(_text):
        entered.set()
        await release.wait()

    async def translation(enabled, **_kwargs):
        transitions.append(enabled)
        return True

    try:
        await app.start()
        control = app.control()
        control.bind_instance("isolated-manual-freeze")
        monkeypatch.setattr(app, "submit_text", slow_output)
        monkeypatch.setattr(app, "set_translation_enabled", translation)
        manual = await control.submit(
            "text.submit", {"text": "pending"}, request_id="freeze-manual"
        )
        await asyncio.wait_for(entered.wait(), 3)
        queued = await control.submit(
            "translation.set",
            {"enabled": False},
            request_id="freeze-transition",
        )
        await asyncio.sleep(0)
        control.freeze_ingress()
        await asyncio.wait_for(control.drain_operations(), 5)
        assert (await control.operation(manual["operation_id"]))["status"] == "interrupted"
        assert (await control.operation(queued["operation_id"]))["status"] == "interrupted"
        assert transitions == []
    finally:
        release.set()
        await app.stop()


@pytest.mark.asyncio
async def test_control_overlay_receipts_preserve_owner_outcomes_and_captured_transaction(
    offline, monkeypatch
):
    from puripuly_heart.config.resolved import OVERLAY_TARGET_DESKTOP
    from puripuly_heart.config.settings_vnext.schema import DesktopFletOverlayPositionIntent

    app = compose_headless_application(offline)
    owner = app._settings.settings
    owner.start()
    app._settings.projection.remember_all(owner.canonical)
    control = app.control()
    control.bind_instance("offline-overlay-outcomes")

    async def command(name, args):
        accepted = await control.submit(name, args, request_id=f"{name}-{len(control._operations)}")
        return await control.wait(accepted["operation_id"], timeout=5)

    absent = await command("overlay.lock", {"locked": True})
    assert absent["status"] == "action_required"
    reset_absent = await command("overlay.position.reset", {})
    assert reset_absent["status"] == "action_required"

    def locked_file(_path, _settings):
        raise PermissionError("isolated persistence failure")

    persist = owner.persistence.persist
    monkeypatch.setattr(owner.persistence, "persist", locked_file)
    failed = await command("overlay.size", {"preset": "large"})
    assert failed["status"] == "persistence_failed"
    assert failed["transaction"]["status"] == "settings_commit_failed"
    monkeypatch.setattr(owner.persistence, "persist", persist)

    owner.canonical = replace(
        owner.canonical,
        intent=replace(
            owner.canonical.intent,
            overlay=replace(
                owner.canonical.intent.overlay,
                target=OVERLAY_TARGET_DESKTOP,
                desktop_flet=replace(
                    owner.canonical.intent.overlay.desktop_flet,
                    position=DesktopFletOverlayPositionIntent(x=140, y=85),
                ),
            ),
        ),
    )
    owner.persist_current()
    app._settings.projection.remember_all(owner.canonical)
    overlay = app._overlay.overlay
    overlay.active_target = OVERLAY_TARGET_DESKTOP
    overlay.state = "connected"

    class Bridge:
        def __init__(self):
            self.calls = 0

        async def broadcast_desktop_runtime_control(self, _payload):
            self.calls += 1
            if self.calls == 2:
                raise OSError("isolated bridge failure")

    bridge = Bridge()
    overlay.runtime = SimpleNamespace(
        current_bridge_for_runtime_command=lambda: bridge,
        renderer_events_or_none=lambda: None,
    )
    reset = await command("overlay.position.reset", {})
    assert reset["status"] == "failed"
    assert reset["reason"] == "desktop_broadcast_failed"
    assert reset["transaction"]["status"] == "settings_commit_success_runtime_degraded"


@pytest.mark.asyncio
async def test_gpu_discovery_receipt_follows_owner_snapshot(offline):
    from puripuly_heart.core.asr_worker import ASRWorkerDevice
    from puripuly_heart.core.local_asr.local_asr_provider_runtime import (
        LocalASRProviderRuntimeSnapshot,
        ProviderRuntimeGpuSnapshot,
    )

    app = compose_headless_application(offline)
    try:
        await app.start()
        control = app.control()
        control.bind_instance("offline-gpu")
        owner = control.gpu()
        device = ASRWorkerDevice("gpu:0", 0, "isolated", "fake GPU", "discrete", 1024, 512)
        phase = "unsupported"

        async def discover_gpu(*, force):
            assert force is False
            return LocalASRProviderRuntimeSnapshot(
                channels=(),
                gpu=ProviderRuntimeGpuSnapshot(
                    phase=phase,
                    devices=(device,) if phase == "available" else (),
                    active_channels=frozenset(),
                    pending_count=0,
                    worker_pid=None,
                    configured_device_id=None,
                    model_resident=False,
                    retry_required=False,
                    failure_code="isolated_failure" if phase == "failed" else None,
                ),
            )

        owner.runtime_provider = lambda: SimpleNamespace(discover_gpu=discover_gpu)
        for phase, expected in (
            ("unsupported", "action_required"),
            ("failed", "failed"),
            ("available", "applied"),
        ):
            accepted = await control.submit("gpu.discover", {}, request_id=f"gpu-{phase}")
            receipt = await control.wait(accepted["operation_id"], timeout=5)
            assert receipt["status"] == expected
            assert receipt["gpu"]["discovery_failed"] is (phase != "available")
            queried = await control.query("gpu.status", {})
            assert queried["state"]["discovery_failed"] is (phase != "available")
    finally:
        await app.stop()



@pytest.mark.asyncio
async def test_settings_failure_is_joinable_after_event_retention_and_state_inspection(
    offline, monkeypatch
):
    from uuid import uuid4

    monkeypatch.setenv("LOCALAPPDATA", str(offline.parent / "isolated-diagnostics-data"))
    app = compose_headless_application(offline)
    try:
        await app.start()
        control = app.control()
        control.bind_instance(str(uuid4()))
        before = await control.query("settings.current", {})

        def denied(_self, *_args):
            raise PermissionError(13, "FORBIDDEN_SETTINGS_EXCEPTION_SENTINEL")

        monkeypatch.setattr(type(control.settings.persistence), "persist", denied)
        accepted = await control.submit(
            "settings.apply",
            {"changes": {"locale": "ja"}},
            request_id=uuid4().hex,
        )
        receipt = await control.wait(accepted["operation_id"], timeout=5)
        assert receipt["status"] == "persistence_failed"
        cause = receipt["transaction"]["diagnostics"]
        assert cause["code"] == "settings_save_failed"
        assert cause["operation_id"] == receipt["operation_id"]
        assert cause["instance_id"] == receipt["instance_id"]
        assert (await control.query("settings.current", {}))["revision"] == before["revision"]
        retained = await control.query(
            "diagnostics.history", {"operation_id": receipt["operation_id"]}
        )
        assert any(item["diagnostic_id"] == cause["diagnostic_id"] for item in retained["records"])
        stream = control.subscribe(topics=["logs", "operation"], after=0)
        async with asyncio.timeout(5):
            while True:
                event = await anext(stream)
                if event.get("diagnostic", {}).get("diagnostic_id") == cause["diagnostic_id"]:
                    assert event["diagnostic"] == cause
                    break
        await stream.aclose()
        operation_event = next(
            item
            for item in control.events.history
            if item["topic"] == "operation" and item["operation_id"] == receipt["operation_id"]
        )
        assert operation_event["diagnostic_id"] == cause["diagnostic_id"]
        assert "FORBIDDEN" not in json.dumps(retained)
    finally:
        await app.stop()


@pytest.mark.parametrize("exception_type", [ValueError, TypeError, KeyError])
@pytest.mark.asyncio
async def test_execution_defects_are_not_argument_rejections_and_keep_safe_causes(
    offline, monkeypatch, exception_type
):
    from uuid import uuid4

    monkeypatch.setenv("LOCALAPPDATA", str(offline.parent / "isolated-control-errors"))
    app = compose_headless_application(offline)
    try:
        await app.start()
        control = app.control()
        control.bind_instance(str(uuid4()))
        invalid = await control.submit(
            "translation.set", {"enabled": "wrong"}, request_id=uuid4().hex
        )
        rejected = await control.wait(invalid["operation_id"], timeout=5)
        assert rejected["status"] == "rejected" and rejected["error"]["code"] == "invalid_arguments"

        async def broken(_self, _command, _arguments, **_kwargs):
            try:
                raise PermissionError(13, "FORBIDDEN_NATIVE_SENTINEL")
            except PermissionError as native:
                raise exception_type("FORBIDDEN_INTERNAL_SENTINEL") from native

        monkeypatch.setattr(type(control), "_dispatch", broken)
        accepted = await control.submit(
            "translation.set", {"enabled": False}, request_id=uuid4().hex
        )
        receipt = await control.wait(accepted["operation_id"], timeout=5)
        assert (
            receipt["status"] == "failed"
            and receipt["error"]["code"] == "application_operation_failed"
        )
        record = receipt["diagnostics"]
        assert record["operation_id"] == receipt["operation_id"]
        assert record["stage"] == "dispatch" and record["category"] == "unknown"
        assert record["fields"]["exception_class"] == exception_type.__name__
        assert record["fields"]["cause_class_1"] == "PermissionError"
        assert record["fields"]["errno"] == 13
        assert "FORBIDDEN" not in json.dumps(receipt)
        assert (
            await control.query("diagnostics.history", {"diagnostic_id": record["diagnostic_id"]})
        )["records"] == [record]
    finally:
        await app.stop()


@pytest.mark.asyncio
async def test_runtime_error_report_crosses_composed_event_bridge_with_source_identity(
    offline, monkeypatch
):
    from uuid import uuid4

    from puripuly_heart.core.messages import ErrorDiagnostics, UserErrorReport, UserMessageRef
    from puripuly_heart.domain.events import UIEvent, UIEventType

    monkeypatch.setenv("LOCALAPPDATA", str(offline.parent / "isolated-report-bridge"))
    app = compose_headless_application(offline)
    try:
        await app.start()
        control = app.control()
        control.bind_instance(str(uuid4()))
        turn = uuid4()
        cause = ErrorDiagnostics(
            component="provider.stt",
            operation="receive",
            code="provider_receive_failed",
            category="network",
            visibility="basic",
            content_policy="metadata_only",
            status_code=503,
            retry_after_ms=None,
            fields={"message": "FORBIDDEN_PROVIDER_BODY_SENTINEL", "provider": "soniox"},
            channel="self",
            turn_id=str(turn),
            generation=5,
        )
        report = UserErrorReport(UserMessageRef("stt.failure", {}, "error"), cause)
        stream = control.subscribe(topics=["error"], after=control.events.sequence)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        await control.pipeline.ui_events.put(
            UIEvent(
                UIEventType.ERROR, payload=report, channel="self", source="stt", utterance_id=turn
            )
        )
        event = await asyncio.wait_for(pending, 5)
        await stream.aclose()
        record = event["error"]["diagnostics"]
        assert record["diagnostic_id"] == cause.diagnostic_id
        assert record["code"] == "provider_receive_failed"
        assert record["turn_id"] == str(turn) and record["generation"] == 5
        assert record["operation_id"] is None
        assert record["detail"]["state"] == "rejected"
        assert "FORBIDDEN" not in json.dumps(event)
        assert (await control.query("diagnostics.history", {"diagnostic_id": cause.diagnostic_id}))[
            "records"
        ] == [record]
    finally:
        await app.stop()


def test_native_manual_admission_preserves_ids_without_output_ack():
    from uuid import uuid4

    from puripuly_heart.app.services.application_control import ApplicationControlOwner
    from puripuly_heart.core.orchestrator.self_translation_channel import TextSubmissionAdmission

    turn, child = uuid4(), uuid4()
    result = ApplicationControlOwner._text_admission_data(
        TextSubmissionAdmission("admitted", turn, (child,))
    )
    assert result == {
        "status": "applied",
        "admission": "admitted",
        "turn_id": str(turn),
        "child_turn_ids": [str(child)],
        "output_delivery": "unobserved",
    }
    rejected = ApplicationControlOwner._text_admission_data(None)
    assert rejected["status"] == "rejected" and rejected["turn_id"] is None
    with pytest.raises(TypeError):
        ApplicationControlOwner._text_admission_data(True)


@pytest.mark.asyncio
async def test_real_peer_fault_queries_project_safe_original_cause(offline):
    from uuid import uuid4

    from puripuly_heart.core.messages import ErrorDiagnostics
    from puripuly_heart.core.peer_capture import (
        PeerCaptureFailureReason,
        PeerCaptureLanguageFacts,
        PeerCaptureSessionConfig,
        PeerCaptureSessionState,
        PeerCaptureTargetIntent,
    )

    app = compose_headless_application(offline)
    try:
        await app.start()
        control = app.control()
        control.bind_instance(str(uuid4()))
        owner = control.pipeline.current.peer_capture
        config = PeerCaptureSessionConfig(
            provider_id="soniox",
            provider_signature=("soniox",),
            runtime_signature=("soniox",),
            capture_signature=("fixture",),
            capture_target=PeerCaptureTargetIntent(kind="default_output_device"),
            language=PeerCaptureLanguageFacts(source_mode="manual", source_language="en"),
            target_sample_rate_hz=16000,
            vad_speech_threshold=0.4,
            vad_hangover_ms=500,
            vad_pre_roll_ms=500,
        )
        cause = ErrorDiagnostics(
            component="capture.peer",
            operation="receive",
            code="capture.peer.provider_failed",
            category="network",
            visibility="diagnostic_only",
            content_policy="metadata_only",
            status_code=999,
            retry_after_ms=-1,
            fields={"errno": 13, "body": "PRIVATE_PEER_FIELD_SENTINEL"},
            channel="PRIVATE_PEER_CHANNEL_SENTINEL",
            generation=-1,
        )
        await owner._fault_current_generation(
            owner.snapshot.generation,
            config=config,
            reason=PeerCaptureFailureReason.PROVIDER_FAILED,
            exception=cause,
        )
        assert owner.snapshot.state is PeerCaptureSessionState.FAULTED
        assert owner.snapshot.failure_diagnostics.diagnostic_id == cause.diagnostic_id
        for query in ("capture.channels", "providers.status", "app.status"):
            result = await control.query(query, {})
            text = json.dumps(result)
            assert cause.diagnostic_id in text and "PRIVATE_PEER" not in text
        retained = (
            await control.query("diagnostics.history", {"diagnostic_id": cause.diagnostic_id})
        )["records"][0]
        assert retained["code"] == cause.code
        assert retained["channel"] is None and retained["generation"] is None
        assert retained["status_code"] is None and retained["retry_after_ms"] is None
        assert retained["detail"]["rejected_fields"] == 5
        self_snapshot = replace(control.pipeline.self_capture.snapshot, failure_diagnostics=cause)
        safe_self = control._snapshot_json(self_snapshot)["failure_diagnostics"]
        assert safe_self["diagnostic_id"] == cause.diagnostic_id
        assert safe_self["detail"]["rejected_fields"] == 5
        assert "PRIVATE_PEER" not in json.dumps(safe_self)
    finally:
        await app.stop()


def test_transaction_receipt_reports_owner_secondary_omissions():
    from puripuly_heart.app.services.application_control import ApplicationControlOwner
    from puripuly_heart.core.messages import ErrorDiagnostics, TransactionResult

    causes = tuple(
        ErrorDiagnostics(
            component="settings_repository",
            operation="save",
            code="settings_save_failed",
            category="transaction",
            visibility="diagnostic_only",
            content_policy="metadata_only",
            status_code=None,
            retry_after_ms=None,
            fields={},
        )
        for _ in range(6)
    )
    transaction = TransactionResult(
        "settings_commit_failed",
        None,
        causes[0],
        secondary_diagnostics=causes,
        secondary_omitted_count=2,
    )
    owner = SimpleNamespace(
        _retain_diagnostic=lambda diagnostic, **_: {"diagnostic_id": diagnostic.diagnostic_id}
    )
    receipt = ApplicationControlOwner._transaction_receipt(owner, transaction, "persistence_failed")
    assert [item["diagnostic_id"] for item in receipt["secondary_diagnostics"]] == [
        cause.diagnostic_id for cause in causes[:4]
    ]
    assert receipt["secondary_omitted_count"] == 4 and receipt["secondary_truncated"] is True


@pytest.mark.parametrize("carrier", ["provider_convergence", "self_runtime", "legacy_diagnostics"])
@pytest.mark.asyncio
async def test_execution_receipt_preserves_approved_native_failure_carrier(
    offline, monkeypatch, carrier
):
    from uuid import uuid4

    from puripuly_heart.app.services.capture.self_capture_application import (
        SelfCaptureRuntimeApplyError,
    )
    from puripuly_heart.app.services.provider.provider_runtime_apply import (
        ProviderRuntimeConvergenceError,
    )
    from puripuly_heart.core.messages import ErrorDiagnostics

    app = compose_headless_application(offline)
    request_id = uuid4().hex
    native = []
    try:
        await app.start()
        control = app.control()
        control.bind_instance(str(uuid4()))

        async def fail_dispatch(_self, _command, _arguments, *, operation_id):
            cause = ErrorDiagnostics(
                component="capture.peer",
                operation="provider_build",
                code="capture.peer.provider_failed",
                category="lifecycle",
                visibility="diagnostic_only",
                content_policy="metadata_only",
                status_code=None,
                retry_after_ms=None,
                fields={"errno": 13},
                channel="peer",
                generation=1,
                operation_id=operation_id,
                request_id=request_id,
            )
            native.append(cause)
            control._retain_diagnostic(cause, disposition="failed")
            if carrier == "provider_convergence":
                error = ProviderRuntimeConvergenceError(
                    "PRIVATE_WRAPPER_MARKER", failure_diagnostics=cause
                )
                error.diagnostics = replace(cause, diagnostic_id=uuid4().hex)
            elif carrier == "self_runtime":
                error = SelfCaptureRuntimeApplyError(
                    "PRIVATE_WRAPPER_MARKER", failure_diagnostics=cause
                )
            else:
                error = RuntimeError("PRIVATE_WRAPPER_MARKER")
                error.failure_diagnostics = "PRIVATE_NON_TYPED_MARKER"
                error.diagnostics = cause
            raise error

        monkeypatch.setattr(type(control), "_dispatch", fail_dispatch)
        admitted = await control.submit(
            "translation.set", {"enabled": False}, request_id=request_id
        )
        receipt = await control.wait(admitted["operation_id"], timeout=5)
        cause = native[0]
        assert (
            receipt["status"] == "failed"
            and receipt["error"]["code"] == "application_operation_failed"
        )
        assert receipt["cause_diagnostics"]["diagnostic_id"] == cause.diagnostic_id
        assert receipt["diagnostics"]["cause_id"] == cause.diagnostic_id
        assert receipt["diagnostics"]["diagnostic_id"] != cause.diagnostic_id
        assert receipt["cause_diagnostics"]["operation_id"] == admitted["operation_id"]
        assert receipt["cause_diagnostics"]["request_id"] == request_id
        assert receipt["cause_diagnostics"]["category"] == "lifecycle"
        assert "PRIVATE_" not in json.dumps(receipt)
        records = (
            await control.query("diagnostics.history", {"operation_id": admitted["operation_id"]})
        )["records"]
        assert receipt["cause_diagnostics"] in records and receipt["diagnostics"] in records
    finally:
        await app.stop()



@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ("gui", "control"))
@pytest.mark.parametrize("admission_phase", ("loading", "pending"))
@pytest.mark.parametrize("channel", ("self", "peer"))
async def test_capture_loading_stop_reaches_real_capture_owner(
    offline, monkeypatch, surface, admission_phase, channel
):
    from puripuly_heart.core.peer_capture import (
        PeerCaptureAdmission,
        PeerCaptureAdmissionStatus,
    )
    from puripuly_heart.core.self_capture import (
        SelfCaptureAdmission,
        SelfCaptureAdmissionStatus,
    )

    settings = json.loads(offline.read_text(encoding="utf-8"))
    settings["intent"]["stt"]["provider"] = "local_qwen_gpu" if channel == "self" else "soniox"
    settings["intent"]["peer_stt"]["provider"] = "local_qwen_gpu" if channel == "peer" else "soniox"
    settings["state"]["peer_translation"]["eula_accepted"] = True
    offline.write_text(json.dumps(settings), encoding="utf-8")
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    admission_type, admission_status = (
        (SelfCaptureAdmission, SelfCaptureAdmissionStatus)
        if channel == "self"
        else (PeerCaptureAdmission, PeerCaptureAdmissionStatus)
    )

    class LoadingAdmission:
        async def admit(self, config):
            assert config.provider_id == "local_qwen_gpu"
            entered.set()
            if admission_phase == "pending":
                return admission_type(admission_status.PENDING)
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return admission_type(admission_status.ADMITTED)

    async def begin_overlay_start():
        control.peer().state.overlay_owner_provider().state = "starting"

    app = compose_headless_application(offline)
    initial_task = None
    try:
        await app.start()
        control = app.control()
        control.bind_instance(f"capture-loading-{surface}-{admission_phase}-{channel}")
        monkeypatch.setattr(control.peer().owner, "begin_overlay_start", begin_overlay_start)
        capture = (
            control.pipeline.self_capture
            if channel == "self"
            else control.pipeline.current.peer_capture
        )
        set_enabled = app.set_stt_enabled if channel == "self" else app.set_peer_translation_enabled
        capture._admission = LoadingAdmission()
        if surface == "gui":
            initial_task = asyncio.create_task(set_enabled(True))
        else:
            initial = await control.submit(
                "capture.set",
                {"channel": channel, "enabled": True},
                request_id="capture-on",
            )
        await asyncio.wait_for(entered.wait(), 5)
        assert capture.snapshot.desired_active is True
        if surface == "gui":
            await asyncio.wait_for(set_enabled(False), 5)
        else:
            off = await control.submit(
                "capture.set",
                {"channel": channel, "enabled": False},
                request_id="capture-off",
            )
            receipt = await control.wait(off["operation_id"], timeout=5)
            assert receipt["status"] == "applied"
            assert (await control.wait(initial["operation_id"], timeout=5))["status"] == "cancelled"
        snapshot = capture.snapshot
        assert snapshot.desired_active is False
        assert snapshot.effective_active is False
        assert not snapshot.has_loop_task
        assert not snapshot.has_source
        assert not release.is_set()
        if admission_phase == "loading":
            assert cancelled.is_set()
        release.set()
        await asyncio.sleep(0)
        assert capture.snapshot.desired_active is False
        assert capture.snapshot.effective_active is False
    finally:
        release.set()
        if initial_task is not None:
            if not initial_task.done():
                initial_task.cancel()
            await asyncio.gather(initial_task, return_exceptions=True)
        await app.stop()
