"""Translation logging extracted from Pipeline.

Handles translation skip/failure logging and translation-ready emission.
Dependencies: PipelineContext only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puripuly_heart.core.pipeline.channel_runtime import ChannelRuntime
    from puripuly_heart.core.pipeline.pipeline_context import PipelineContext
    from puripuly_heart.domain.models import Translation

__all__ = ["TranslationLogger"]


class TranslationLogger:
    """Translation logging extracted from Pipeline.

    Dependencies injected via constructor:
    - ctx: PipelineContext (logging, runtime_logging, latency)
    """

    def __init__(self, ctx: PipelineContext) -> None:
        self._ctx = ctx

    def skip_reason(self, runtime: ChannelRuntime) -> str:
        return self._ctx._translation_skip_reason(runtime)

    def log_skipped(
        self,
        *,
        stage: str,
        runtime: ChannelRuntime,
        publish_chatbox: bool,
    ) -> None:
        self._ctx._log_translation_skipped(
            stage=stage, runtime=runtime, publish_chatbox=publish_chatbox,
        )

    def log_failure(
        self,
        *,
        stage: str,
        runtime: ChannelRuntime,
        exc: Exception,
        detailed: bool = False,
    ) -> None:
        self._ctx._log_translation_failure(
            stage=stage, runtime=runtime, exc=exc, detailed=detailed,
        )

    def emit_ready(
        self,
        *,
        translation: Translation,
        runtime: ChannelRuntime,
    ) -> bool:
        return self._ctx._emit_translation_ready_for_output(
            translation=translation, runtime=runtime,
        )
