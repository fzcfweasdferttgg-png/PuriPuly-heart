"""Tests for core.stt.controller.ManagedSTTProvider.

State machine: DISCONNECTED → CONNECTING → STREAMING → DRAINING → CLOSED
"""

import pytest

# TODO: import from core.stt.controller
# from puripuly_heart.core.stt.controller import ManagedSTTProvider


class TestManagedSTTProvider:
    """Test suite for ManagedSTTProvider state machine and lifecycle."""

    # State machine transitions
    # TODO: test_initial_state_is_disconnected
    # TODO: test_connecting_transition
    # TODO: test_streaming_transition
    # TODO: test_draining_transition
    # TODO: test_closed_transition
    # TODO: test_invalid_transition_raises

    # Session lifecycle
    # TODO: test_open_session_success
    # TODO: test_open_session_failure_retries
    # TODO: test_close_session
    # TODO: test_close_session_when_already_closed

    # Reset timer
    # TODO: test_reset_timer_scheduled_on_session_open
    # TODO: test_reset_timer_fires_after_deadline
    # TODO: test_reset_timer_cancelled_on_close
    # TODO: test_reset_timer_retry_on_failure
    # TODO: test_reset_timer_max_retries_gives_up

    # Event handling
    # TODO: test_handle_vad_event_speech_start
    # TODO: test_handle_vad_event_speech_chunk
    # TODO: test_handle_vad_event_speech_end
    # TODO: test_handle_vad_event_unknown_raises

    # Idle release
    # TODO: test_clear_idle_state_clears_pending_ids
    # TODO: test_clear_idle_state_clears_audio_ring
    # TODO: test_clear_idle_state_drains_event_queue
    # TODO: test_reset_for_idle_clears_and_closes

    # Edge cases
    # TODO: test_concurrent_handle_vad_event
    # TODO: test_session_timeout
    # TODO: test_backend_failure_during_streaming
