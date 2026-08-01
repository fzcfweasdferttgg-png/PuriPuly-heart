# Этап 17: Извлечение Application Services из Pipeline

Pipeline = use case handler + orchestrator + state machine. Извлекаем TranslationService и OutputDispatcher.

## Критическая связность mixin'ов

| Mixin | Строк | Pipeline методов вызывает |
|-------|------:|--------------------------|
| BufferManagerMixin | 738 | ~20 (translate, handle_transcript, enqueue_osc, emit overlay, latency, logging) |
| OverlayHelpersMixin | 622 | ~10 (record_latency, finalize_latency, merge_text, source_language_for, complete_peer_turn) |
| PeerTurnsMixin | 138 | 3 (latency methods — уже переведены на прямой доступ в этапе 16) |

**Ключевая проблема:** `BufferManagerMixin._commit_merge` вызывает `_handle_transcript`, `_translate_and_enqueue`, `_enqueue_osc` — три разных use case в одном методе.

## Порядок

### 1. Создать `application/__init__.py`

### 2. TranslationService (`application/translation_service.py`)

Извлекает из Pipeline:
- `_translate_text` (lines ~1006-1053) — вызов LLM
- `_prepare_llm_request` (line ~968) — базовая подготовка
- `_prepare_llm_request_with_mode` (lines ~982-1004) — подготовка с контекстом
- `_format_system_prompt` (lines ~1018-1034) — форматирование промпта
- `_normalize_translation` (lines ~1093-1130) — нормализация результата
- `_remember_context_entry` — добавление в context history

```python
class TranslationService:
    """Use case: text → prompt → LLM → Translation"""

    def __init__(self, *, llm: LLMProvider | None,
                 fallback_llm: LLMProvider | None,
                 context_resolver: ContextResolver,
                 clock: Clock,
                 system_prompt: str,
                 second_target_language: str,
                 integrated_context_enabled: bool,
                 runtime_logging: SessionRuntimeLoggingService | None): ...

    async def translate(self, text: str, *, runtime: ChannelRuntime,
                        record_latency: bool = True) -> Translation | None: ...

    def prepare_request(self, text: str, *, runtime: ChannelRuntime,
                       ) -> tuple[str, str, float, ContextMode]: ...

    def remember_context(self, text: str, timestamp: float,
                        *, runtime: ChannelRuntime) -> None: ...
```

Зависимости: LLMProvider (порт), ContextResolver (domain), Clock (порт).

### 3. OutputDispatcher (`application/output_dispatcher.py`)

Извлекает из Pipeline + OverlayHelpersMixin:
- `_enqueue_osc` (lines ~1417-1454)
- `_run_osc_flush_loop` (lines ~1471-1474)
- `_emit_final_transcript_to_overlay` (overlay_helpers.py:47)
- `_emit_translation_to_overlay` (overlay_helpers.py:99)
- `_emit_peer_translation_to_overlay` (overlay_helpers.py:125)
- `_emit_overlay_utterance_closed` (overlay_helpers.py:80)
- `_emit_overlay_event` (overlay_helpers.py:155)
- Вспомогательные методы overlay (language resolution, diagnostics)

```python
class OutputDispatcher:
    """Use case: Translation → OSC + overlay"""

    def __init__(self, *, osc: OscSink,
                 overlay: OverlaySink | None,
                 overlay_event_adapter: OverlayEventAdapter,
                 overlay_diagnostics: OverlayDiagnosticsRecorder | None,
                 clock: Clock,
                 chatbox_include_source: bool,
                 runtime_logging: SessionRuntimeLoggingService | None): ...

    async def dispatch_translation(self, *, translation: Translation,
                                   transcript_text: str,
                                   runtime: ChannelRuntime,
                                   publish_chatbox: bool,
                                   applied_context_mode: ContextMode | None) -> None: ...

    async def dispatch_transcript(self, transcript: Transcript) -> None: ...

    async def close_utterance(self, *, utterance_id: UUID,
                              channel: ChannelId, is_final: bool) -> None: ...

    async def run_osc_flush(self) -> None: ...
```

Зависимости: OscSink (порт), OverlaySink (порт), OverlayEventAdapter (adapter).

### 4. Обновить Pipeline

Pipeline получает сервисы как поля:
```python
@dataclass(slots=True)
class Pipeline:
    translation_service: TranslationService
    output_dispatcher: OutputDispatcher
    ...
```

Pipeline._ensure_translation: вызывает `self.translation_service.translate(...)` + `self.output_dispatcher.dispatch_translation(...)`
Pipeline._handle_stt_event: routing логика остаётся, делегирует в сервисы
Pipeline._enqueue_osc → удалить (в OutputDispatcher)
Pipeline overlay методы → удалить (в OutputDispatcher)

### 5. Обновить mixin'ы

BufferManagerMixin:
- `self._translate_text(...)` → `self.translation_service.translate(...)`
- `self._translate_and_enqueue(...)` → `self.translation_service.translate(...)` + `self.output_dispatcher.dispatch_translation(...)`
- `self._handle_transcript(...)` → остаётся в Pipeline (routing)
- `self._enqueue_osc(...)` → `self.output_dispatcher.dispatch_osc(...)` (или через Pipeline)

OverlayHelpersMixin:
- Большинство методов переезжает в OutputDispatcher
- Mixin существенно сокращается или удаляется

### 6. Обновить wiring.py

Создавать TranslationService и OutputDispatcher, передавать в Pipeline.

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `app/src/puripuly_heart/application/__init__.py` | Создать |
| `app/src/puripuly_heart/application/translation_service.py` | Создать |
| `app/src/puripuly_heart/application/output_dispatcher.py` | Создать |
| `core/pipeline/pipeline.py` | Сократить: делегировать в сервисы |
| `core/pipeline/stages/buffer_manager.py` | Вызывать сервисы вместо Pipeline методов |
| `core/pipeline/stages/overlay_helpers.py` | Сократить или удалить |
| `app/wiring.py` | Создавать сервисы |

## Неочевидное

- `_translate_and_enqueue` (~230 строк) — содержит и translate, и output, и error handling. Разделение на TranslationService + OutputDispatcher требует аккуратного разделения error handling и UIEvent dispatch
- `ui_events: asyncio.Queue[UIEvent]` — Pipeline кладёт UIEvents из разных мест. OutputDispatcher тоже должен класть UIEvents. Варианты: передать queue в OutputDispatcher или возвращать events из dispatch методов
- `_commit_merge` в BufferManagerMixin вызывает и `_handle_transcript`, и `_translate_and_enqueue` — после извлечения: три вызова вместо одного. Функционально эквивалентно
- OverlayHelpersMixin после извлечения OutputDispatcher: большая часть методов переезжает. Mixin может стать пустым — тогда удалить

## Цепочки

**Translate flow (STT → LLM → output):**
- Было: Pipeline._ensure_translation → Pipeline._translate_and_enqueue → Pipeline._translate_text → LLM → Pipeline._enqueue_osc
- Стало: Pipeline._ensure_translation → TranslationService.translate → LLM → OutputDispatcher.dispatch

**Low-latency flow (BufferManagerMixin):**
- Было: _commit_merge → self._translate_and_enqueue (Pipeline метод)
- Стало: _commit_merge → self.translation_service.translate → self.output_dispatcher.dispatch

**Overlay flow:**
- Было: self._emit_translation_to_overlay (метод mixin'а)
- Стало: self.output_dispatcher.dispatch_translation (делегирование)

**STT event flow:**
- Pipeline._handle_stt_event остаётся (оркестрация), вызывает сервисы

## Результат

Pipeline: ~1,370 → ~400-500 строк. Чистый оркестратор: lifecycle + STT event routing + wiring.

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/application/translation_service.py`
- `python -m py_compile app/src/puripuly_heart/application/output_dispatcher.py`
- `python -m py_compile app/src/puripuly_heart/core/pipeline/pipeline.py`
- `python -m py_compile app/src/puripuly_heart/core/pipeline/stages/buffer_manager.py`
- `python -m py_compile app/src/puripuly_heart/app/wiring.py`
