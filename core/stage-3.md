# Этап 3: Пайплайн-ядро

Разбить hub.py (2996 строк, 107 методов) на pipeline stages. Самый опасный этап.

## Реальные зависимости

Импортируют ClientHub из hub.py: 3 файла:
- `ui/controller.py:68`
- `app/headless_mic.py:35`
- `core/runtime/peer_channel.py:12`

STTProvider Protocol определён в hub.py но НЕ импортируется никем снаружи — можно перенести в ports/ при разбиении.

## Что содержит ClientHub (107 методов)

Группы методов по ответственности:

- **Latency tracking (14 методов):** _latency_key, _get_latency_timeline, _elapsed_latency_ms, _latency_hangover_ms, _emit_latency_trace_if_ready, _emit_latency_summary_if_ready, _emit_latency_contract_if_ready, _record_latency_stage, _inherit_latency_for_output, _clear_latency_timeline, _clear_latency_state, _clear_runtime_latency_bookkeeping, _finalize_latency_timeline
- **Overlay helpers (12 методов):** _overlay_translation_will_follow, _peer_terminal_work_will_follow, _language_or_fallback, _metadata_language, _active_self_display_languages_for_utterance, и др.
- **Text merge (10 методов):** _merge_text, _merge_with_overlap, _relaxed_overlap_merge, _strip_trailing_boundary, _strip_leading_boundary, _is_boundary_char, и др.
- **Buffer/speculative state (14 методов):** _clear_resume_state, _clear_spec_latency_state, _record_spec_latency_stage, _promote_spec_latency_to_output, _clear_spec_state, и др.
- **Peer logical turns (9 методов):** _clear_peer_logical_turn_state, _peer_parent_speech_end_time, _register_peer_logical_turn, и др.
- **Translation (8 методов):** _translation_skip_reason, _log_translation_skipped, _prepare_llm_request, _normalize_translation, и др.
- **Context (5 методов):** mark_promo_eligible, clear_context, _remember_context_entry, и др.
- **Source language (4 метода):** _remember_source, _get_source, _source_language_for, _target_language_for
- **Error/logging (4 метода):** _emit_exception_summary, _format_log_message, _emit_basic, _emit_detailed
- **Runtime/channel (6 методов):** _runtime_for_channel, _runtime_for_utterance, get_or_create_bundle, и др.

## Неочевидное

**1. Hub — не просто последовательность вызовов.**
Hub содержит event loop, который реагирует на STT-события в реальном времени. Это не "прочитал → обработал → отдал". Это "слушай события → реагируй". Pipeline stages должны работать как async-поток, а не как синхронная цепочка.

**2. Два канала share LLM provider.**
Self и peer каналы параллельны, но используют один LLMProvider с Semaphore. Если вынести LLM в отдельный stage — нужно сохранить concurrency control. Не создавать два LLM provider.

**3. Utterance assembly — скрытая сложность.**
Hub собирает partial transcripts в финальный utterance. Это stateful логика: "получил partial → жду ещё → получил final → отправляю дальше". Это не просто функция, это состояние. Pipeline context должен нести это состояние.

**4. Latency tracking — 14 методов, пронизывает всё.**
Latency timestamps ставятся в разных местах. 14 методов отвечают за это. При разбиении — latency_tracker.py забирает все 14 методов. Каждый stage вызывает `tracker.mark("stage_name")`.

**5. Text merge — неочевидная ответственность.**
10 методов для склеивания partial transcripts (_merge_text, _merge_with_overlap и др.). Это не pipeline stage, а утилита. Выделять в отдельный файл text_merge.py.

**6. Buffer/speculative state — 14 методов.**
Логика "подождать, вдруг будет ещё текст" (_clear_resume_state, _maybe_start_finalize_wait, _cancel_awaiting_vad_timeout и др.). Stateful, зависит от timing. Выделять в buffer_manager.py.

**7. Error handling неявный.**
Hub ловит исключения от STT/LLM и решает что делать (retry? skip? показать ошибку?). При разбиении — error handling должен быть в каждом stage или в orchestrator. Не потерять ни одного try/except.

**8. Overlay presenter получает события через hub.**
Hub вызывает presenter напрямую. При разбиении — output_dispatch stage должен вызывать presenter через порт.

**9. peer_channel.py зависит от hub.**
peer_channel.py импортирует ClientHub. Это циклическая зависимость при разбиении — peer_channel нужен hub, а hub нужен peer_channel. Решение: инвертировать зависимость — peer_channel получает hub через инъекцию, не импорт.

## Порядок извлечения (принципы)

1. Сначала изолированное, потом связанное
2. Сначала отдельные группы, потом оркестратор

Конкретно:
- latency tracking (14) → первыми, изолированы
- text merge (10) → вторыми, чистая утилита
- source language (4) → третьими, простые
- overlay helpers (12) → четвёртыми, связаны с presenter
- peer logical turns (9) → пятыми, stateful
- buffer/speculative (14) → последними, самые переплетённые

## Могу проверить (CLI)

- `python -m py_compile` на каждом pipeline stage
- `python -m py_compile` на orchestrator
- `grep -r "from.*hub import"` — обновить все 3 импорта
- `grep -r "from.*orchestrator.*import"` — проверить новые импорты
- Проверить что orchestrator не импортирует из providers/ или adapters/
- `python -m py_compile core/runtime/peer_channel.py` — после инверсии зависимости
