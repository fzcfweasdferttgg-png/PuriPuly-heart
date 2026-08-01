# План исправления нарушений гексагональной архитектуры

После stages 12-18 + plan-fix-deps. 5 нарушений.

---

## 1. core → adapter: OverlayEventAdapter в pipeline.py

**Нарушение:** `pipeline.py:27` импортирует `OverlayEventAdapter` из `adapters.overlay.sink`. Строго говоря — core не должен знать о конкретных адаптерах.

**Контекст:** Импорт нужен только для инстанцирования в `__post_init__` (строка 144). Тип поля уже `OverlayEventFactory` (Protocol). Mixin'ы используют через Protocol.

**Решение:** Вынести создание `OverlayEventAdapter` в wiring (controller/headless_mic), передавать готовый объект в Pipeline.

**Порядок:**
1. `pipeline.py`: удалить импорт `OverlayEventAdapter` (строка 27)
2. `pipeline.py:144`: `self.overlay_event_adapter = OverlayEventAdapter(clock=self.clock)` → удалить
3. `pipeline.py`: изменить поле `overlay_event_adapter: OverlayEventFactory = field(init=False)` → `overlay_event_adapter: OverlayEventFactory` (init=True, без default)
4. `controller.py`: `OverlayEventAdapter(clock=self.clock)` передать в `Pipeline(... overlay_event_adapter=...)`
5. `headless_mic.py`: аналогично

**Затронутые файлы:** 3 (pipeline.py, controller.py, headless_mic.py)

**Риск:** Низкий. Поле становится обязательным параметром конструктора.

---

## 2. application → core: SessionRuntimeLoggingService

**Нарушение:** `translation_service.py:12` импортирует `SessionRuntimeLoggingService` из `core.runtime_logging`. Также `adapters/osc/chatbox_paginator.py:9`, `adapters/llm/openai_compatible.py:11`, `adapters/llm/local_openai.py:15`.

**Контекст:** TranslationService использует только `emit_basic()`. Adapters используют `emit_basic()`, `emit_detailed()`, `emit_detailed_lazy()`, `mode`.

**Решение:** Protocol `SessionLogger` в `ports/logging.py` с методами `emit_basic`, `emit_detailed`, `emit_detailed_lazy`, `mode`. Имплементация `SessionRuntimeLoggingService` остаётся в `core/runtime_logging.py`.

**Порядок:**
1. Создать `ports/logging.py`:
   ```python
   class SessionLogger(Protocol):
       mode: SessionLoggingMode
       def emit_basic(self, message: str, *, level: int = logging.INFO) -> None: ...
       def emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool: ...
       def emit_detailed_lazy(self, build_message: Callable[[], str], *, level: int = logging.INFO) -> bool: ...
   ```
2. `ports/__init__.py`: добавить `SessionLogger` в re-exports
3. `application/translation_service.py:12`: `from core.runtime_logging` → `from ports.logging`
4. `adapters/osc/chatbox_paginator.py:9`: аналогично
5. `adapters/llm/openai_compatible.py:11`: аналогично
6. `adapters/llm/local_openai.py:15`: аналогично
7. `ui/overlay_manager.py:25`: аналогично (TYPE_CHECKING guard)
8. Аннотации типов: `SessionRuntimeLoggingService | None` → `SessionLogger | None`

**Затронутые файлов:** 7

**Риск:** Низкий. Duck typing — `SessionRuntimeLoggingService` уже реализует все методы Protocol.

---

## 3. application → config: config.prompts

**Нарушение:** `translation_service.py:7` импортирует `render_translation_prompt_template`, `render_dual_translation_prompt_template` из `config.prompts`.

**Контекст:** Функции — простой string formatting с кэшем. TranslationService вызывает их в `format_system_prompt()`. Также `pipeline.py:13` импортирует `warm_prompt_cache` из того же модуля.

**Варианты:**

### A. Инжектировать как callable (рекомендуется)

TranslationService получает `prompt_renderer: Callable` вместо прямого импорта.

**Порядок:**
1. `application/translation_service.py`: удалить импорт из `config.prompts`
2. Добавить поле `render_prompt: Callable[..., str]` в TranslationService
3. `controller.py` / `headless_mic.py`: передать `render_prompt=render_translation_prompt_template` при создании
4. `format_system_prompt()`: вызывать `self.render_prompt(...)` вместо `render_translation_prompt_template(...)`

**Затронутые файлов:** 3 (translation_service.py, controller.py, headless_mic.py)

**Риск:** Низкий. Простая инъекция зависимости.

### B. Перенести render функции в domain/

`render_translation_prompt_template` и `render_dual_translation_prompt_template` — pure string formatting. Можно перенести в `domain/prompt_formatting.py`.

**Риск:** Средний. Функции зависят от `_get_prompt_cache()` который читает файлы — это infrastructure. Нужно разделить чистый formatting от загрузки.

**Рекомендация:** Вариант A проще и чище.

---

## 4. ui → adapters: controller.py импорты

**Нарушение:** `controller.py` импортирует 8+ классов из `adapters/`.

**Анализ импортов:**

| Строка | Импорт | Назначение | Решение |
|--------|--------|------------|---------|
| 70 | `ChatboxPaginator` from `adapters.osc` | Создание OSC | Перенести в wiring.py |
| 77 | `VrchatOscUdpSender` from `adapters.osc` | Создание OSC | Перенести в wiring.py |
| 98-102 | 5× STT error types from `adapters.stt.*` | except блоки | Перенести типы в domain/ |
| 829 | `OpenAICompatibleLLMProvider` from `adapters.llm` | Lazy import | Оставить (lazy, composition root) |

### 4a. STT error types → domain/

**Порядок:**
1. Создать `domain/stt_errors.py`:
   ```python
   class LocalQwenSherpaLoadError(Exception): ...
   class LocalGigaamRnntLoadError(Exception): ...
   class LocalParakeetTdtLoadError(Exception): ...
   class LocalParakeetCtcLoadError(Exception): ...
   class LocalTranscribecppLoadError(Exception): ...
   ```
2. `adapters/stt/local_qwen_sherpa.py`: наследовать от `domain.stt_errors.LocalQwenSherpaLoadError`
3. Аналогично для остальных 4 adapters
4. `controller.py:98-102`: импортировать из `domain.stt_errors`

**Затронутые файлов:** 7 (1 новый + 4 adapters + controller + wiring)

### 4b. OSC создание → wiring.py

**Порядок:**
1. Добавить в `app/wiring.py` функцию `create_osc_sink(settings, clock) -> OscSink`
2. `controller.py`: заменить ручное создание `VrchatOscUdpSender` + `ChatboxPaginator` на `osc = create_osc_sink(...)`
3. `headless_mic.py`: аналогично

**Затронутые файлов:** 3 (wiring.py, controller.py, headless_mic.py)

**Риск:** Средний. OSC создание может иметь controller-specific параметры (runtime_logging). Нужно проверить.

---

## 5. domain → config: STTProviderName

**Нарушение:** `domain/peer_types.py:5` импортирует `STTProviderName` из `config.settings`. Domain не должен зависеть от config.

**Контекст:** `STTProviderName` — enum провайдеров STT. Определён в `config/settings/enums.py`. Используется 6 файлами.

**Решение:** Переместить `STTProviderName` (и попутно `LLMProviderName`) в `domain/providers.py`. `config/settings/enums.py` re-export.

**Порядок:**
1. Создать `domain/providers.py`:
   ```python
   class STTProviderName(str, Enum): ...
   class LLMProviderName(str, Enum): ...
   ```
2. `config/settings/enums.py`: удалить определения, добавить `from puripuly_heart.domain.providers import STTProviderName, LLMProviderName`
3. Все 6 файлов импортируют через `config.settings` — т.к. config re-export, импорты не меняются
4. `domain/peer_types.py:5`: изменить на `from puripuly_heart.domain.providers import STTProviderName`

**Затронутые файлов:** 3 (domain/providers.py, config/settings/enums.py, domain/peer_types.py)

**Риск:** Низкий. Re-export сохраняет обратную совместимость.

---

## Порядок реализации

| Шаг | Описание | Файлов | Риск | Зависимость |
|-----|----------|--------|------|-------------|
| 5 | STTProviderName → domain/ | 3 | Низкий | Нет |
| 1 | OverlayEventAdapter → wiring | 3 | Низкий | Нет |
| 2 | SessionLogger Protocol в ports/ | 7 | Низкий | Нет |
| 3 | config.prompts → инъекция | 3 | Низкий | Нет |
| 4a | STT error types → domain/ | 7 | Средний | Нет |
| 4b | OSC создание → wiring | 3 | Средний | Нет |

Шаги 1, 2, 3, 5 — независимы, можно параллельно. Шаг 4a и 4b — независимы друг от друга.

## Ожидаемый результат

После исправления:
- `domain/` → 0 нарушений (было 1)
- `application/` → 0 нарушений (было 2)
- `core/` → 0 нарушений (было 1)
- `ui/` → 1 нарушение (lazy LLM import — composition root, acceptable)
