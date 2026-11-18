from __future__ import annotations

from uuid import uuid4

import httpx2
import pytest
from llm_lib import LLM

from puripuly_heart.core.error_messages import provider_failure_report
from puripuly_heart.providers.llm.error_details import LLMResponseError
from puripuly_heart.providers.llm.local_llm_lib import LLMLibLocalLLMProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception_type", "expected_category"),
    [
        (httpx2.ConnectTimeout, "timeout"),
        (httpx2.ConnectError, "network"),
    ],
)
async def test_llm_lib_transport_failures_keep_diagnostic_category(
    exception_type,
    expected_category: str,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exception_type("test transport failure", request=request)

    provider = LLMLibLocalLLMProvider(
        backend="llamacpp",
        base_url="http://127.0.0.1:8080/v1",
        model="test-model",
    )
    provider.client = LLM(
        provider._settings(),
        async_transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(LLMResponseError) as caught:
        await provider.translate(
            utterance_id=uuid4(),
            text="source",
            system_prompt="translate",
            source_language="en",
            target_language="ru",
        )
    await provider.close()

    report = provider_failure_report(
        caught.value,
        provider="llm",
        operation="translate",
    )
    assert report.diagnostics.fields["provider"] == "local_llm"
    assert report.diagnostics.category == expected_category
