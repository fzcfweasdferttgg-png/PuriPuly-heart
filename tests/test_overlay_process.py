"""Tests for core.overlay.process.OverlayProcessManager.

State machine: off → startup → connected → failed
"""

import pytest

# TODO: import from core.overlay.process
# from puripuly_heart.core.overlay.process import OverlayProcessManager


class TestOverlayProcessManager:
    """Test suite for OverlayProcessManager lifecycle."""

    # State machine
    # TODO: test_initial_state_is_off
    # TODO: test_startup_transition
    # TODO: test_connected_transition
    # TODO: test_failed_transition

    # Process lifecycle
    # TODO: test_start_spawns_process
    # TODO: test_start_writes_manifest
    # TODO: test_stop_terminates_process
    # TODO: test_stop_cleans_up_manifest

    # Manifest
    # TODO: test_write_manifest_success
    # TODO: test_write_manifest_cleanup_on_failure
    # TODO: test_cleanup_manifest_handles_missing_file

    # Error handling
    # TODO: test_fail_sets_state_to_failed
    # TODO: test_fail_logs_error
    # TODO: test_fail_cleans_up_manifest

    # Edge cases
    # TODO: test_start_when_already_started
    # TODO: test_stop_when_not_started
    # TODO: test_process_crash_during_startup
    # TODO: test_process_crash_during_connected
