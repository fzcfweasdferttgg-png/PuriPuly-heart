from __future__ import annotations

from types import SimpleNamespace

import pytest

from puripuly_heart.app.ports.ui_models import OverlayPeerPresentationState
from puripuly_heart.app.services.settings.settings_application import (
    settings_view_surface_snapshots,
)
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.ui.event_bridge import UIEventBridge
from puripuly_heart.ui.presentation_adapter import FletUiPresentationAdapter
from tests.helpers.osc_presentation import osc_control_presentation_state
def test_presentation_adapter_owns_ui_event_bridge_composition() -> None:
    host = SimpleNamespace(
        view_dashboard=SimpleNamespace(),
        view_logs=SimpleNamespace(),
        get_event_language_codes=lambda: ("ko", "en"),
        is_event_translation_enabled=lambda: True,
        get_event_stt_state=lambda: None,
        show_snackbar=lambda *args: None,
        on_github_star_translation_success=lambda: None,
        on_overlay_state_changed=lambda **kwargs: None,
    )
    adapter = FletUiPresentationAdapter(host)

    bridge = adapter.create_ui_event_bridge(
        event_queue=object(),
        runtime_logging=SimpleNamespace(),
    )

    assert isinstance(bridge, UIEventBridge)


def test_presentation_adapter_projects_one_snapshot_to_both_ui_surfaces() -> None:
    events: list[tuple[str, object]] = []
    dashboard = SimpleNamespace(
        project_osc_control_state=lambda state: events.append(("dashboard", state))
    )
    settings = SimpleNamespace(
        project_osc_control_state=lambda state: events.append(("settings", state)),
        load_from_settings=lambda *_args, **_kwargs: events.append(("full-load", None)),
    )
    adapter = FletUiPresentationAdapter(
        SimpleNamespace(
            view_dashboard=dashboard,
            view_settings=settings,
        )
    )
    state = osc_control_presentation_state(
        "PuriPuly_MuteSync",
        mute_sync=True,
    )

    adapter.project_osc_control_state(state)

    assert events == [
        ("dashboard", state),
        ("settings", state),
    ]


def test_presentation_adapter_preserves_missing_optional_history_destination() -> None:
    adapter = FletUiPresentationAdapter(SimpleNamespace())

    adapter.add_history_entry("Mic", "hello", language_code="en")


@pytest.mark.asyncio
async def test_presentation_adapter_awaits_ui_owned_shutdown_hooks() -> None:
    events: list[str] = []

    async def record(name: str) -> None:
        events.append(name)

    host = SimpleNamespace(
        close_after_launch_tasks=lambda: record("after-launch"),
        close_github_star_prompt_runtime=lambda: record("star-runtime"),
        close_oauth_runtime=lambda: record("oauth-runtime"),
    )
    adapter = FletUiPresentationAdapter(host)

    await adapter.close_after_launch_tasks()
    await adapter.close_github_star_prompt_runtime()
    await adapter.close_oauth_runtime()

    assert events == ["after-launch", "star-runtime", "oauth-runtime"]


def test_set_dashboard_languages_forwards_the_secondary_target() -> None:
    from types import SimpleNamespace

    from puripuly_heart.ui.presentation_adapter import FletUiPresentationAdapter

    calls: list[tuple] = []
    extras: list[tuple] = []
    dashboard = SimpleNamespace(
        set_languages_from_codes=(
            lambda source, target, peer_source, peer_target, peer_mode, secondary: calls.append(
                (source, target, peer_source, peer_target, peer_mode, secondary)
            )
        ),
        set_recent_languages=lambda *args: extras.append(("recent", args)),
        set_peer_auto_detect_available=lambda value: extras.append(("peer-auto", value)),
    )
    adapter = FletUiPresentationAdapter(SimpleNamespace(view_dashboard=dashboard))

    adapter.set_dashboard_languages(
        source_language="ko",
        target_language="en",
        peer_source_language="ja",
        peer_target_language="fr",
        peer_source_mode="manual",
        recent_source_languages=["ko"],
        recent_target_languages=["en"],
        peer_auto_detect_available=True,
        secondary_target_language="ja",
    )

    assert calls == [("ko", "en", "ja", "fr", "manual", "ja")]
    assert extras == [("recent", (["ko"], ["en"])), ("peer-auto", True)]


def test_set_dashboard_languages_still_supports_a_dashboard_without_the_secondary() -> None:
    from types import SimpleNamespace

    from puripuly_heart.ui.presentation_adapter import FletUiPresentationAdapter

    calls: list[tuple] = []
    extras: list[tuple] = []
    dashboard = SimpleNamespace(
        set_languages_from_codes=(
            lambda source, target, peer_source, peer_target, peer_mode: calls.append(
                (source, target, peer_source, peer_target, peer_mode)
            )
        ),
        set_recent_languages=lambda *args: extras.append(("recent", args)),
        set_peer_auto_detect_available=lambda value: extras.append(("peer-auto", value)),
    )
    adapter = FletUiPresentationAdapter(SimpleNamespace(view_dashboard=dashboard))

    adapter.set_dashboard_languages(
        source_language="ko",
        target_language="en",
        peer_source_language="ja",
        peer_target_language="fr",
        peer_source_mode="manual",
        recent_source_languages=["ko"],
        recent_target_languages=["en"],
        peer_auto_detect_available=True,
        secondary_target_language="ja",
    )

    assert calls == [("ko", "en", "ja", "fr", "manual")]
    assert extras == [("recent", (["ko"], ["en"])), ("peer-auto", True)]
