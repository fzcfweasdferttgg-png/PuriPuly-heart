"""Translation orchestration — LLM call + fallback chain + context + normalization.

Coordinates the full translation request:
1. resolve context (local or integrated) via ContextResolver
2. format system prompt (single or dual-target)
3. call primary LLM, fallback to secondary on failure
4. normalize Translation domain object (fill metadata fields)

Fields (source_language, target_language, etc.) are synced from settings
by settings_manager.py on settings change.

Called at runtime by Pipeline._translate_and_enqueue() and Pipeline._translate_text().
Constructed by controller._create_translation_service() and headless_mic wiring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Protocol

from puripuly_heart.core.language import source_language_for, target_language_for
from puripuly_heart.domain.language import get_llm_language_name
from puripuly_heart.ports.logging import SessionLogger
from puripuly_heart.domain.models import ChannelId, Translation

logger = logging.getLogger(__name__)


class _ContextResolverLike(Protocol):
    def resolve_for_request(
        self,
        *,
        runtime: object,
        other_runtime: object,
        requested_mode: str,
        peer_translation_enabled: bool,
        source_language: str,
        target_language: str,
        other_source_language: str,
        other_target_language: str,
    ) -> tuple[str, str]: ...


class _ChannelRuntimeLike(Protocol):
    channel: ChannelId

    def remember_context(
        self,
        text: str,
        *,
        timestamp: float,
        source_language: str,
        target_language: str,
        max_entries: int,
    ) -> None: ...


class _ClockLike(Protocol):
    def now(self) -> float: ...


class _LLMProviderLike(Protocol):
    async def translate(
        self,
        *,
        utterance_id: object,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation: ...


@dataclass(slots=True)
class TranslationService:
    llm: _LLMProviderLike | None
    fallback_llm: _LLMProviderLike | None
    context_resolver: _ContextResolverLike
    clock: _ClockLike
    system_prompt: str
    second_target_language: str
    integrated_context_enabled: bool
    peer_translation_enabled: bool
    source_language: str
    target_language: str
    peer_source_language: str
    peer_target_language: str
    runtime_logging: SessionLogger | None = None
    render_prompt: Callable[..., str] | None = None
    render_dual_prompt: Callable[..., str] | None = None
    _last_logged_context_modes: dict[ChannelId, str | None] = field(
        init=False, default_factory=lambda: {"self": None, "peer": None}
    )

    def _source_language_for(self, runtime: _ChannelRuntimeLike) -> str:
        return source_language_for(self, runtime)

    def _target_language_for(self, runtime: _ChannelRuntimeLike) -> str:
        return target_language_for(self, runtime)

    def _other_runtime(
        self, runtime: _ChannelRuntimeLike, self_rt: object, peer_rt: object
    ) -> object:
        return peer_rt if runtime is self_rt else self_rt

    def format_system_prompt(
        self, runtime: _ChannelRuntimeLike | None = None, *, default_runtime: object | None = None
    ) -> str:
        if runtime is None:
            runtime = default_runtime  # type: ignore[assignment]
        source_name = get_llm_language_name(self._source_language_for(runtime))
        target_name = get_llm_language_name(self._target_language_for(runtime))
        # Dual-target prompt: when second_target_language is set (self channel only),
        # the LLM is asked to produce two translations in one call.
        # Peer excluded — dual-target is a self-channel UX feature (chatbox
        # shows both translations); peer overlay only shows one translation.
        if self.second_target_language and runtime.channel != "peer":
            second_name = get_llm_language_name(self.second_target_language)
            if self.render_dual_prompt:
                try:
                    return self.render_dual_prompt(source_name=source_name, target_name=target_name, second_target_name=second_name)
                except FileNotFoundError:
                    logger.warning("Dual translation prompt template not found, falling back to single")
        if self.render_prompt:
            return self.render_prompt(self.system_prompt, source_name=source_name, target_name=target_name)
        return self.system_prompt

    def prepare_request(
        self,
        text: str,
        *,
        runtime: _ChannelRuntimeLike,
        self_rt: object,
        peer_rt: object,
    ) -> tuple[str, str, float, str]:
        requested_mode: str = "integrated" if self.integrated_context_enabled else "local"
        now = self.clock.now()
        other_runtime = self._other_runtime(runtime, self_rt, peer_rt)
        context_str, applied_mode = self.context_resolver.resolve_for_request(
            runtime=runtime,
            other_runtime=other_runtime,
            requested_mode=requested_mode,
            peer_translation_enabled=self.peer_translation_enabled,
            source_language=self._source_language_for(runtime),
            target_language=self._target_language_for(runtime),
            other_source_language=self._source_language_for(other_runtime),  # type: ignore[arg-type]
            other_target_language=self._target_language_for(other_runtime),  # type: ignore[arg-type]
        )
        self._log_context_mode_change(runtime=runtime, applied_mode=applied_mode)
        self._log_context_application(text=text, runtime=runtime, context=context_str)
        formatted_prompt = self.format_system_prompt(runtime)
        return formatted_prompt, context_str, now, applied_mode

    def remember_context(
        self,
        text: str,
        timestamp: float,
        *,
        runtime: _ChannelRuntimeLike,
    ) -> None:
        runtime.remember_context(
            text,
            timestamp=timestamp,
            source_language=self._source_language_for(runtime),
            target_language=self._target_language_for(runtime),
            # Must match ContextResolver.integrated_max_entries (4) so that
            # integrated mode has enough entries when only one channel is active.
            # Local mode caps retrieval at local_max_entries (3), so storing 4
            # wastes only one entry — acceptable trade-off for correctness.
            max_entries=4,
        )

    def normalize_translation(
        self,
        translation: Translation,
        *,
        runtime: _ChannelRuntimeLike,
        text: str,
        source_language: str,
        target_language: str,
    ) -> Translation:
        return Translation(
            utterance_id=translation.utterance_id,
            translated_text=translation.text,
            source_text=text,
            source_language=self._language_or_fallback(
                translation.source_language,
                source_language,
            ),
            target_language=self._language_or_fallback(
                translation.target_language,
                target_language,
            ),
            channel=runtime.channel,
            created_at=translation.created_at,
            update_id=translation.update_id,
            origin_wall_clock_ms=translation.origin_wall_clock_ms,
            session_scope=translation.session_scope,
            source_text_hash=translation.source_text_hash,
            source_text_len=translation.source_text_len,
            logical_turn_key=f"{runtime.channel}:{translation.utterance_id}",
        )

    async def translate(
        self,
        text: str,
        *,
        utterance_id: object,
        runtime: _ChannelRuntimeLike,
        self_rt: object,
        peer_rt: object,
        _prepared_prompt: str | None = None,
        _prepared_context: str | None = None,
    ) -> Translation | None:
        if self.llm is None:
            return None

        if _prepared_prompt is not None and _prepared_context is not None:
            formatted_prompt = _prepared_prompt
            context_str = _prepared_context
        else:
            formatted_prompt, context_str, _now, _mode = self.prepare_request(
                text, runtime=runtime, self_rt=self_rt, peer_rt=peer_rt,
            )
        request_source_language = self._source_language_for(runtime)
        request_target_language = self._target_language_for(runtime)

        # Fallback chain: try primary LLM, then fallback_llm if set.
        # On primary failure → warning + retry. On fallback failure → error + raise.
        providers = [self.llm]
        if self.fallback_llm is not None:
            providers.append(self.fallback_llm)

        last_error = None
        for provider in providers:
            try:
                raw = await provider.translate(
                    utterance_id=utterance_id,
                    text=text,
                    system_prompt=formatted_prompt,
                    source_language=request_source_language,
                    target_language=request_target_language,
                    context=context_str,
                )
                return self.normalize_translation(
                    raw,
                    runtime=runtime,
                    text=text,
                    source_language=request_source_language,
                    target_language=request_target_language,
                )
            except Exception as exc:
                last_error = exc
                provider_model = getattr(provider, "model", "?")
                provider_base_url = getattr(provider, "base_url", "?")
                if provider is not self.fallback_llm:
                    logger.warning(
                        "[LLM] Primary provider failed (model=%s base_url=%s): %s — trying fallback",
                        provider_model, provider_base_url, exc,
                    )
                else:
                    logger.error(
                        "[LLM] Fallback provider also failed (model=%s base_url=%s): %s",
                        provider_model, provider_base_url, exc,
                    )

        if last_error is not None:
            raise last_error
        return None

    def _log_context_mode_change(
        self,
        *,
        runtime: _ChannelRuntimeLike,
        applied_mode: str,
    ) -> None:
        last_mode = self._last_logged_context_modes.get(runtime.channel)
        if last_mode == applied_mode:
            return
        self._last_logged_context_modes[runtime.channel] = applied_mode
        if self.runtime_logging is not None:
            self.runtime_logging.emit_basic(
                f"[Hub] Context mode: channel={runtime.channel} mode={applied_mode}"
            )

    def _log_context_application(
        self,
        *,
        text: str,
        runtime: _ChannelRuntimeLike,
        context: str,
    ) -> None:
        context_lines = context.splitlines() if context else []
        applied_mode = self._last_logged_context_modes.get(runtime.channel)
        if runtime.channel == "peer" and applied_mode in (None, "local"):
            peer_entries = len(context_lines)
            self_entries = 0
        else:
            peer_entries = sum(
                1 for line in context_lines
                if line.startswith("- [peer,") or line.startswith("- [others,")
            )
            self_entries = len(context_lines) - peer_entries
        if self.runtime_logging is not None:
            self.runtime_logging.emit_basic(
                f"[Hub] Context apply: channel={runtime.channel} mode={applied_mode} "
                f"request_chars={len(text)} entries={len(context_lines)} "
                f"self_entries={self_entries} peer_entries={peer_entries} "
                f"context_chars={len(context)}"
            )

    @staticmethod
    def _language_or_fallback(language: str | None, fallback: str) -> str:
        if language is not None and language.strip():
            return language
        return fallback
