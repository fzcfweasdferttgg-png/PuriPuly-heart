"""Tests for core.overlay.presenter.OverlayPresenter.

State machine for SteamVR subtitle display.
MRO: OverlaySink + 3 mixins (logging, entry_mgmt, refresh_burst).
"""

import pytest

# TODO: import from core.overlay.presenter
# from puripuly_heart.core.overlay.presenter import OverlayPresenter


class TestOverlayPresenter:
    """Test suite for OverlayPresenter state machine and event handling."""

    # State machine
    # TODO: test_initial_state
    # TODO: test_emit_self_event
    # TODO: test_emit_peer_event
    # TODO: test_emit_unknown_event_raises

    # Entry lifecycle
    # TODO: test_create_entry
    # TODO: test_expire_entry_after_ttl
    # TODO: test_tombstone_entry
    # TODO: test_tombstone_limit

    # Refresh burst
    # TODO: test_start_refresh_burst
    # TODO: test_stop_refresh_burst
    # TODO: test_refresh_burst_publishes_snapshots

    # Reset
    # TODO: test_reset_scene_clears_all_state
    # TODO: test_clear_for_runtime_detach_cancels_tasks
    # TODO: test_reset_scene_and_detach_stay_in_sync

    # Edge cases
    # TODO: test_concurrent_emit_calls
    # TODO: test_emit_during_refresh_burst
    # TODO: test_bridge_detach_during_emit
