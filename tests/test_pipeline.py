"""Tests for core.pipeline.pipeline.Pipeline.

Main orchestration pipeline for STT → VAD → Translation → Overlay/OSC.
"""

import pytest

# TODO: import from core.pipeline.pipeline
# from puripuly_heart.core.pipeline.pipeline import Pipeline


class TestPipeline:
    """Test suite for Pipeline orchestration."""

    # STT event handling
    # TODO: test_handle_stt_final_event
    # TODO: test_handle_stt_partial_event
    # TODO: test_handle_stt_error_event
    # TODO: test_handle_stt_session_state_event

    # VAD event handling
    # TODO: test_handle_vad_event_speech_start
    # TODO: test_handle_vad_event_speech_chunk
    # TODO: test_handle_vad_event_speech_end

    # Translation
    # TODO: test_translate_and_enqueue
    # TODO: test_translate_with_fallback
    # TODO: test_translate_failure

    # Overlay
    # TODO: test_overlay_emit
    # TODO: test_overlay_refresh_burst

    # Edge cases
    # TODO: test_concurrent_stt_and_vad_events
    # TODO: test_translation_timeout
    # TODO: test_overlay_bridge_disconnect
