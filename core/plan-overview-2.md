# План: приведение к чистой hexagonal architecture

## Контекст

4 архитектурных отклонения. База для модификации — архитектура должна быть готова к расширению.

**Текущее состояние (187 .py файлов):**
- ports/: 16 файлов, 493 строки — чистые Protocol, dependency inversion не нарушена
- adapters/: 10 файлов, 1,053 строки — LLM + STT реализации
- core/pipeline/: 9 файлов, 3,367 строки — pipeline.py = 1,473 строки (god object)
- core/osc/: 5 файлов, 271 строк — ChatboxPaginator + VrchatOscUdpSender (adapters внутри core/)
- core/overlay/: 16 файлов, 5,456 строк — OverlayEventAdapter (adapter внутри core/), state.py = 1,850 строк
- core/storage/: 2 файла, 85 строк — SecretStore реализации (adapter внутри core/)
- core/llm/provider.py: 30 строк — SemaphoreLLMProvider (наследует от LLMProvider)
- ui/controller.py: 1,501 строк — импортирует из core/osc (3 файла) + core/overlay (4 файла)
- app/wiring.py: 366 строк

## Проблемы

| # | Проблема | Файлы |
|---|---------|-------|
| 1 | LLMProvider — plain class, остальные порты — Protocol | ports/llm.py |
| 2 | Адаптеры (secrets, osc, overlay) лежат в core/ | core/storage/, core/osc/, core/overlay/sink.py |
| 3 | Нет Application Services — Pipeline = use case + orchestrator + state | core/pipeline/pipeline.py |
| 4 | Pipeline = god object 1,473 строки, смешивает 7+ ответственностей | core/pipeline/pipeline.py |

## Стадии

### Стадия 12: Единый паттерн портов (LLMProvider → Protocol)

**Проблема:** `LLMProvider` — plain class с `raise NotImplementedError`. Все остальные порты — `Protocol`. `SemaphoreLLMProvider` наследует от `LLMProvider`.

**Решение:**
1. `ports/llm.py`: `class LLMProvider` → `class LLMProvider(Protocol)` + `@runtime_checkable`
2. `core/llm/provider.py`: `SemaphoreLLMProvider` убирает наследование, остаётся composition-only:
   ```python
   @dataclass(slots=True)
   class SemaphoreLLMProvider:
       inner: LLMProvider
       semaphore: asyncio.Semaphore
       # translate() и close() — делегирование через composition
   ```
3. Проверить: `isinstance(llm, SemaphoreLLMProvider)` в controller.py:428 — продолжит работать (concrete class)
4. `isinstance(llm, LLMProvider)` — нигде не используется (проверено grep). Только `isinstance(..., LLMProviderName)` в settings — это enum, не порт.

**Затронутые файлы:**
- `app/src/puripuly_heart/ports/llm.py` (~18 строк)
- `app/src/puripuly_heart/core/llm/provider.py` (~30 строк)

**Риск:** Низкий. Не меняет поведение, только тип контракта.

**Проверено:**
- `OpenAICompatibleLLMProvider` и `LocalOpenAICompatibleLLMProvider` **не наследуют** от LLMProvider — они duck-typed. После изменения на Protocol они станут структурными сатисфайерами автоматически.
- Адаптерные файлы не трогаем.

---

### Стадия 13: Перемещение OSC adapter + OscSink port

**Проблема:** Pipeline использует `ChatboxPaginator` напрямую вместо порта. `VrchatOscUdpSender` — adapter-реализация, лежит в `core/osc/`.

**Проверено:**
- `ports/osc.py`: `OscSender(Protocol)` — 2 метода: `send_chatbox`, `send_typing`
- Pipeline вызывает ChatboxPaginator: `enqueue`, `send_immediate`, `send_typing`, `set_typing_reason`, `clear_typing_reasons`, `process_due` (6 методов)
- `core/osc/udp_sender.py`: `VrchatOscUdpSender(OscSender)` — concrete adapter (UDP + pythonosc)
- `core/osc/sender.py`: re-export `OscSender` из ports/osc.py (3 строки)
- Импорты ChatboxPaginator: pipeline.py:28, controller.py:70, headless_stdin.py:11, headless_mic.py:36
- Импорты VrchatOscUdpSender: controller.py:77

**Решение:**
1. Расширить `ports/osc.py` — добавить `OscSink` Protocol:
   ```python
   class OscSink(Protocol):
       def enqueue(self, message: OSCMessage) -> None: ...
       def send_immediate(self, text: str) -> bool: ...
       def send_typing(self, is_typing: bool) -> None: ...
       def set_typing_reason(self, reason: str, active: bool) -> None: ...
       def clear_typing_reasons(self) -> None: ...
       def process_due(self) -> None: ...
   ```
2. Переместить:
   - `core/osc/chatbox_paginator.py` → `adapters/osc/chatbox_paginator.py`
   - `core/osc/udp_sender.py` → `adapters/osc/udp_sender.py`
3. Обновить типы:
   - Pipeline: `osc: ChatboxPaginator` → `osc: OscSink`
   - Импорты pipeline.py: `from core.osc.chatbox_paginator` → `from ports.osc`
4. Обновить импорты инстанцирования:
   - controller.py:70 → `from adapters.osc.chatbox_paginator import ChatboxPaginator`
   - controller.py:77 → `from adapters.osc.udp_sender import VrchatOscUdpSender`
   - headless_stdin.py:11, headless_mic.py:36 → аналогично
5. `core/osc/sender.py` (re-export) — удалить или оставить как legacy redirect

**Затронутые файлы:**
- `app/src/puripuly_heart/ports/osc.py` (расширить)
- `core/osc/chatbox_paginator.py` → `adapters/osc/chatbox_paginator.py`
- `core/osc/udp_sender.py` → `adapters/osc/udp_sender.py`
- `core/pipeline/pipeline.py` (тип osc, импорт)
- `ui/controller.py` (2 импорта)
- `app/headless_stdin.py` (1 импорт)
- `app/headless_mic.py` (1 импорт)

**Риск:** Низкий-средний. Механическое перемещение + изменение типов.

**Последствия для рабочих цепочек:**
- Pipeline.osc тип меняется с ChatboxPaginator на OscSink — все вызовы совпадают (6 методов из порта)
- `_run_osc_flush_loop` в pipeline.py вызывает `self.osc.process_due()` — метод есть в OscSink
- `_enqueue_osc` в pipeline.py вызывает `self.osc.enqueue(msg)` — метод есть в OscSink
- controller.py создаёт ChatboxPaginator напрямую (line ~1115) — импорт обновляется, инстанцирование не меняется

**Неочевидное:**
- `core/osc/` после перемещения останется: `__init__.py`, `sender.py` (3 строки re-export), `receiver.py` (84 строки — OSC receiver, это adapter). Receiver можно переместить отдельно, но он не используется Pipeline напрямую.

---

### Стадия 14: Перемещение storage adapter

**Проблема:** `core/storage/secrets.py` (84 строки) — конкретные реализации порта `SecretStore`.

**Проверено:**
- Импортируется только в `app/wiring.py:21`
- `ports/secrets.py`: `SecretStore(Protocol)` — 3 метода: `get`, `set`, `delete`

**Решение:**
1. `core/storage/secrets.py` → `adapters/storage/secrets.py`
2. Обновить 1 импорт в `app/wiring.py:21`

**Затронутые файлы:**
- `core/storage/secrets.py` → переместить
- `app/wiring.py` (1 строка импорта)

**Риск:** Минимальный.

**Последствия:** Нет. Единственный потребитель — wiring.py, который создаёт instances и передаёт через порты.

---

### Стадия 15: Overlay — перемещение OverlayEventAdapter + фикс импортов state.py

**Проблема:** `core/overlay/sink.py` (251 строк) содержит `OverlayEventAdapter` — adapter, создающий overlay events из доменных объектов. Pipeline использует его напрямую.

**Проверено:**
- Event types (SelfTranscriptFinal, TranslationFinal и т.д.) **уже определены в `ports/overlay.py`**
- `core/overlay/sink.py` — только `OverlayEventAdapter` (7 factory-методов) + re-exports из ports/overlay.py
- `core/overlay/state.py` (1,850 строк) импортирует event types **через sink.py** вместо ports/overlay.py — это избыточная зависимость
- OverlayHelpersMixin (622 строки) использует `self.overlay_event_adapter` через Pipeline — не импортирует sink.py напрямую
- controller.py импортирует из core/overlay: bridge, diagnostics, presenter, process (4 файла) — это adapter-реализации, но они **не в scope** этой стадии

**Решение:**
1. Переместить `core/overlay/sink.py` → `adapters/overlay/sink.py` (только OverlayEventAdapter)
2. `core/overlay/state.py`: заменить импорт `from core.overlay.sink import (EventTypes...)` на `from ports.overlay import (EventTypes...)` — event types уже там
3. Pipeline: `overlay_event_adapter` создаётся в `__post_init__` — обновить импорт на `from adapters.overlay.sink import OverlayEventAdapter`
4. OverlayHelpersMixin не трогаем — он использует `self.overlay_event_adapter` (атрибут Pipeline), не импорт

**Затронутые файлы:**
- `core/overlay/sink.py` → `adapters/overlay/sink.py`
- `core/overlay/state.py` (строки 14-23: импорт event types из ports/overlay вместо core/overlay/sink)
- `core/pipeline/pipeline.py` (строки 30-33: импорт OverlayEventAdapter)

**Риск:** Средний-низкий.

**Последствия для рабочих цепочек:**
- `state.py` импортирует 8 event types — они уже в `ports/overlay.py`, просто меняем путь импорта. Типы те же, ничего не ломается.
- Pipeline создаёт `OverlayEventAdapter(clock=self.clock)` в `__post_init__` — только импорт меняется.
- OverlayHelpersMixin вызывает `self.overlay_event_adapter.transcript_final(...)` и т.д. — атрибут Pipeline, не импорт. Не трогаем.

**Неочевидное:**
- `core/overlay/sink.py` делает re-export `OverlaySink` из `ports/overlay.py`. После перемещения: кто импортировал `OverlaySink` через `core.overlay.sink` — должен импортировать из `ports.overlay`. Проверено: pipeline.py импортирует `OverlaySink` из `core.overlay.sink` (line 31) — нужно переключить на `ports.overlay`.
- `core/overlay/` после перемещения: sink.py исчезает, остаются 15 файлов (bridge, diagnostics, presenter, process, state и др.). Эти файлы — adapter-реализации для overlay, но они не в scope этой стадии.

---

### Стадия 16: Формализация state + удаление forwarding boilerplate

**Проблема:** Pipeline (1,473 строки) содержит:
- ~20 мутабельных полей состояния
- 15+ forwarding-методов для LatencyTracker (каждый — одна строка делегирования)
- `__setattr__` override для bidirectional aliasing с ChannelRuntime (~30 строк)
- ChannelRuntime (182 строки) уже держит per-channel state с теми же полями

**Проверено:**
- LatencyTracker (269 строк) — **уже чисто извлечён**. Принимает `emit_basic`/`emit_detailed` callbacks + Clock. Не имеет обратных зависимостей на Pipeline.
- Pipeline содержит 12 forwarding-методов latency: `_get_latency_timeline`, `_latency_hangover_ms`, `_emit_latency_trace_if_ready`, `_emit_latency_summary_if_ready`, `_emit_latency_contract_if_ready`, `_record_latency_stage`, `_inherit_latency_for_output`, `_clear_latency_timeline`, `_clear_latency_state`, `_clear_runtime_latency_bookkeeping`, `_finalize_latency_timeline`, `_translation_ready_elapsed_ms`
- Все mixin'ы вызывают latency методы через `self._record_latency_stage(...)` — то есть через Pipeline
- ChannelRuntime хранит: utterances, translation_tasks, utterance_sources, utterance_start_times, translation_history, speech_ended_ids, merge_buffer
- Pipeline дублирует те же поля + bidirectional sync через __setattr__

**Решение:**
1. Удалить 12 forwarding-методов latency из Pipeline (~60 строк)
2. Mixin'ы: заменить `self._record_latency_stage(...)` на `self._latency._record_latency_stage(...)` (прямой доступ к полю)
3. Удалить `__setattr__` override Pipeline — bidirectional aliasing больше не нужен если mixin'ы обращаются к `self._latency` напрямую
4. Удалить `_sync_self_runtime_aliases` — дублирование state
5. Оставить ChannelRuntime как единственный per-channel state holder

**Затронутые файлы:**
- `core/pipeline/pipeline.py` (удалить ~100 строк forwarding + __setattr__)
- `core/pipeline/stages/buffer_manager.py` (заменить `self._record_latency_*` → `self._latency._record_latency_*`)
- `core/pipeline/stages/overlay_helpers.py` (аналогично)
- `core/pipeline/stages/peer_turns.py` (аналогично)

**Риск:** Средний. Механическая замена, но затрагивает все mixin'ы.

**Последствия для рабочих цепочек:**
- Latency tracking: те же вызовы, только прямой доступ вместо forwarding. LatencyTracker не меняется.
- State access: mixin'ы обращаются к state через `self.self_runtime`, `self.peer_runtime` — это ChannelRuntime, не Pipeline поля. Удаление дублирующих полей Pipeline не влияет.
- Единственный risk: если кто-то обращается к Pipeline полям напрямую (например `self._utterances` вместо `self.self_runtime.utterances`). Проверить grep'ом.

**Результат:** Pipeline сокращается с ~1,473 до ~1,370 строк. Убирается boilerplate, state формализован в ChannelRuntime.

---

### Стадия 17: Извлечение Application Services из Pipeline

**Проблема:** Pipeline = use case handler + orchestrator + state machine. Нет промежуточного слоя.

**Проверено — критическая связность mixin'ов:**

| Mixin | Строк | Pipeline методов вызывает | Pipeline атрибутов читает |
|-------|------:|--------------------------|--------------------------|
| BufferManagerMixin | 738 | ~20 (translate, handle_transcript, enqueue_osc, emit overlay, latency, logging) | clock, llm, translation_enabled, low_latency_*, overlay_sink, self_runtime, merge_buffer, ui_events |
| OverlayHelpersMixin | 622 | ~10 (record_latency, finalize_latency, merge_text, source_language_for, complete_peer_turn, emit_detailed) | overlay_sink, overlay_event_adapter, overlay_diagnostics, runtime_logging, clock, llm, source_language, target_language, peer_*, last_error_source |
| PeerTurnsMixin | 138 | 3 (record_latency_stage, inherit_latency_for_output, clear_latency_timeline) | peer_runtime |

**Ключевая проблема:** `BufferManagerMixin._commit_merge` (line ~368) вызывает `_handle_transcript`, `_translate_and_enqueue`, `_enqueue_osc` — это три разных use case, пересекающихся в одном методе mixin'а.

**Решение:** Извлечь 2 Application Service. Mixin'ы остаются, но делегируют в сервисы вместо Pipeline методов.

#### TranslationService

Извлекает из Pipeline:
- `_translate_text` (lines ~1006-1053) — вызов LLM
- `_prepare_llm_request_with_mode` (lines ~982-1004) — подготовка промпта
- `_prepare_llm_request` (линия ~968) — базовая подготовка
- `_format_system_prompt` (lines ~1018-1034) — форматирование промпта
- `_normalize_translation` (lines ~1093-1130) — нормализация результата
- `_remember_context_entry` — добавление в context history

```python
# application/translation_service.py
class TranslationService:
    """Use case: text → prompt → LLM → Translation"""
    def __init__(self, llm: LLMProvider | None, fallback_llm: LLMProvider | None,
                 context_resolver: ContextResolver, clock: Clock,
                 system_prompt: str, ...): ...

    async def translate(self, text: str, *, runtime: ChannelRuntime,
                        record_latency: bool = True) -> Translation | None: ...
    def prepare_request(self, text: str, *, runtime: ChannelRuntime
                       ) -> tuple[str, str, float, ContextMode]: ...
```

Зависимости: LLMProvider (порт), ContextResolver (domain), Clock (порт).

#### OutputDispatcher

Извлекает из Pipeline + OverlayHelpersMixin:
- `_enqueue_osc` (lines ~1417-1454) — отправка в OSC
- `_run_osc_flush_loop` (lines ~1471-1474) — flush loop
- `_emit_final_transcript_to_overlay` (overlay_helpers.py:47)
- `_emit_translation_to_overlay` (overlay_helpers.py:99)
- `_emit_peer_translation_to_overlay` (overlay_helpers.py:125)
- `_emit_overlay_utterance_closed` (overlay_helpers.py:80)
- `_emit_overlay_event` (overlay_helpers.py:155)

```python
# application/output_dispatcher.py
class OutputDispatcher:
    """Use case: Translation → OSC + overlay"""
    def __init__(self, osc: OscSink, overlay: OverlaySink | None,
                 overlay_event_adapter: OverlayEventAdapter, clock: Clock, ...): ...

    async def dispatch_translation(self, translation: Translation, ...) -> None: ...
    async def dispatch_transcript(self, transcript: Transcript, ...) -> None: ...
    async def run_osc_flush(self) -> None: ...
```

Зависимости: OscSink (порт), OverlaySink (порт), OverlayEventAdapter (adapter — нужен для создания events).

**Затронутые файлы:**
- Новые: `app/src/puripuly_heart/application/__init__.py`, `application/translation_service.py`, `application/output_dispatcher.py`
- Изменён: `core/pipeline/pipeline.py` — делегирует в сервисы вместо собственных методов
- Изменён: `core/pipeline/stages/buffer_manager.py` — вызывает `self._translation_service.translate(...)` вместо `self._translate_text(...)`
- Изменён: `core/pipeline/stages/overlay_helpers.py` — делегирует в `self._output_dispatcher`
- Изменён: `app/wiring.py` — создаёт сервисы, передаёт в Pipeline

**Риск:** Высокий. Самая большая перестройка.

**Последствия для рабочих цепочек:**

1. **Translate flow** (STT → LLM → output):
   - Было: `Pipeline._ensure_translation` → `Pipeline._translate_and_enqueue` → `Pipeline._translate_text` → LLM → `Pipeline._enqueue_osc`
   - Стало: `Pipeline._ensure_translation` → `TranslationService.translate` → LLM → `OutputDispatcher.dispatch`
   - Цепочка сохраняется, делегирование через сервисы

2. **Low-latency flow** (BufferManagerMixin):
   - Было: `BufferManagerMixin._commit_merge` → `self._translate_and_enqueue(...)` (Pipeline метод)
   - Стало: `BufferManagerMixin._commit_merge` → `self._translation_service.translate(...)` → `self._output_dispatcher.dispatch(...)`
   - Mixin получает ссылки на сервисы через Pipeline атрибуты

3. **Overlay flow** (OverlayHelpersMixin):
   - Было: `self._emit_translation_to_overlay(...)` (метод mixin'а, использующий self.overlay_event_adapter)
   - Стало: `self._output_dispatcher.dispatch_translation(...)` (делегирование)
   - OverlayHelpersMixin существенно сокращается или удаляется

4. **STT event flow**:
   - `Pipeline._handle_stt_event` остаётся в Pipeline (оркестрация)
   - Вызывает `TranslationService.translate` и `OutputDispatcher.dispatch`
   - Логика routing (self/peer, partial/final) остаётся в Pipeline

5. **Context flow**:
   - `ContextResolver` — уже извлечён (115 строк)
   - `TranslationService` использует его для подготовки контекста
   - `Pipeline._remember_context_entry` переезжает в `TranslationService`

**Неочевидное:**
- `BufferManagerMixin._commit_merge` вызывает и `_handle_transcript`, и `_translate_and_enqueue` — это пересечение use cases. После извлечения: `_commit_merge` вызывает `OutputDispatcher.dispatch_transcript` + `TranslationService.translate` + `OutputDispatcher.dispatch_translation` — три вызова вместо одного. Функционально эквивалентно, но логика разделена.
- `_translate_and_enqueue` (lines ~1065-1293) — самый большой метод (~230 строк). Он содержит и translate, и output, и error handling. Разделение на `TranslationService.translate` + `OutputDispatcher.dispatch` требует аккуратного разделения error handling.
- `OverlayHelpersMixin` после извлечения OutputDispatcher: большая часть методов переезжает в OutputDispatcher. Mixin может стать пустым — тогда удалить.
- `ui_events: asyncio.Queue[UIEvent]` — Pipeline кладёт UIEvents из разных мест. OutputDispatcher тоже должен класть UIEvents. Нужно либо передавать queue в OutputDispatcher, либо возвращать events из dispatch методов.

**Результат:** Pipeline сокращается с ~1,370 (после стадии 16) до ~400-500 строк. Становится чистым оркестратором: lifecycle + STT event routing + wiring use cases.

---

## Итоговое состояние после всех стадий

```
app/src/puripuly_heart/
├── ports/           Protocol (все uniform)
│   ├── llm.py       LLMProvider(Protocol) ✅
│   ├── osc.py       OscSender + OscSink ✅
│   ├── overlay.py   OverlaySink + event types ✅
│   ├── secrets.py   SecretStore ✅
│   └── ...
├── adapters/        Конкретные реализации
│   ├── llm/         OpenAI, LocalOpenAI
│   ├── stt/         Sherpa, GigaAM, Parakeet, TranscribeCpp
│   ├── osc/         ChatboxPaginator, VrchatOscUdpSender ✅ NEW
│   ├── storage/     KeyringSecretStore, EncryptedFileSecretStore ✅ NEW
│   └── overlay/     OverlayEventAdapter ✅ NEW
├── application/     Use cases
│   ├── translation_service.py ✅ NEW
│   └── output_dispatcher.py ✅ NEW
├── core/
│   ├── pipeline/
│   │   ├── pipeline.py      Оркестратор (~400-500 строк)
│   │   ├── stages/          Mixin'ы (сокращены)
│   │   ├── channel_runtime.py
│   │   ├── context.py
│   │   ├── latency_tracker.py
│   │   └── text_merge.py
│   ├── overlay/     state.py, presenter, bridge, process (остаются)
│   ├── osc/         receiver.py (остаётся)
│   └── storage/     удалена ✅
├── domain/          Модели, events
└── ui/              Controller, views
```

## Порядок выполнения

```
Стадия 12 (порты)         ─── независимая
Стадия 13 (OSC adapter)   ─── независимая
Стадия 14 (storage)       ─── независимая
Стадия 15 (overlay)       ─── независимая
         │
         ▼
Стадия 16 (state + forwarding cleanup)  ─── зависит от 12-15
         │
         ▼
Стадия 17 (application services)  ─── зависит от 16
         │
         ▼
Стадия 18 (верификация)  ─── зависит от 12-17
```

**Рекомендуемый порядок:** 12 → 14 → 13 → 15 → 16 → 17 → 18 (от простого к сложному, верификация в конце).

## Правила

- Каждая стадия — отдельный коммит
- После каждой стадии: `python -m py_compile` на всех затронутых файлах
- Строки — ориентир, не догма
- Если стадия 17 окажется слишком рискованной — стадии 12-16 уже дают значительное улучшение
- Стадия 18 — обязательна после любого набора выполненных стадий
