from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from puripuly_heart.core.error_messages import (
    format_error_report_for_log,
    provider_failure_report,
    stt_failure_report,
)
from puripuly_heart.core.openrouter.managed_openrouter_release import (
    ManagedOpenRouterReleaseError,
    ManagedOpenRouterUserFacingError,
)

PRIVATE_DETAIL = "speech-cause-private-provider-body-73042"


@pytest.mark.parametrize("mapper", [provider_failure_report, stt_failure_report])
def test_free_form_error_prose_is_not_diagnostic_authority(mapper) -> None:
    failure = RuntimeError(f"status=429 auth quota timeout {PRIVATE_DETAIL}")
    kwargs = {"channel": "self"} if mapper is stt_failure_report else {}
    report = mapper(failure, provider="openrouter", operation="translate", **kwargs)

    assert report.diagnostics.category == "unknown"
    assert report.diagnostics.status_code is None
    assert PRIVATE_DETAIL not in format_error_report_for_log(report)


def test_actual_http_status_is_diagnostic_authority() -> None:
    response = httpx.Response(429, request=httpx.Request("POST", "http://127.0.0.1/"))
    failure = httpx.HTTPStatusError(PRIVATE_DETAIL, request=response.request, response=response)
    report = provider_failure_report(failure, provider="openrouter", operation="translate")

    assert report.diagnostics.category == "rate_limit"
    assert report.diagnostics.status_code == 429
    assert PRIVATE_DETAIL not in format_error_report_for_log(report)


def test_builtin_timeout_is_classified_without_its_message() -> None:
    report = stt_failure_report(
        TimeoutError(PRIVATE_DETAIL), provider="soniox", operation="receive", channel="self"
    )

    assert report.diagnostics.category == "timeout"
    assert report.diagnostics.channel == "self"
    assert PRIVATE_DETAIL not in format_error_report_for_log(report)


def test_managed_message_key_does_not_invent_auth_failure() -> None:
    wrapper = ManagedOpenRouterUserFacingError(message_key="managed_release.restart")
    report = provider_failure_report(wrapper, provider="openrouter", operation="translate")

    assert report.diagnostics.category == "unknown"
    assert report.diagnostics.status_code is None


def test_reused_native_cause_preserves_explicit_request_relationship() -> None:
    from dataclasses import replace

    native = provider_failure_report(
        TimeoutError(PRIVATE_DETAIL), provider="openrouter", operation="translate"
    ).diagnostics
    scoped = replace(
        native,
        operation_id=uuid4().hex,
        request_id=uuid4().hex,
        channel="self",
        turn_id=str(uuid4()),
        generation=3,
    )
    report = provider_failure_report(scoped, provider="openrouter", operation="translate")

    assert report.diagnostics.diagnostic_id == native.diagnostic_id
    assert report.diagnostics.operation_id == scoped.operation_id
    assert report.diagnostics.request_id == scoped.request_id
    assert report.diagnostics.turn_id == scoped.turn_id
    assert report.diagnostics.generation == 3
