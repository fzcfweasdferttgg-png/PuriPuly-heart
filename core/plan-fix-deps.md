# План исправления dependency direction violations

После stages 12-18. Три нарушения. Проверено агентами + ручная верификация.

---

## 1. application/translation_service.py → core.language

**Проблема:** `application/` импортирует из `core/`.

**Модуль:** `core/language.py` (140 строк) — чистый utility (маппинг кодов языков → имён). Zero внутренних зависимостей (только stdlib). Leaf module.

**Импорты:** 8 файлов, 9 импортов:

| Файл | Строка | Символ |
|------|--------|--------|
| `application/translation_service.py` | 11 | `get_llm_language_name` |
| `ui/views/stt_section.py` | 8 | `get_stt_compatibility_warning` |
| `ui/views/settings.py` | 32 | `get_stt_compatibility_warning` |
| `ui/views/dashboard.py` | 5 | `get_all_language_options` |
| `ui/i18n.py` | 8 | `get_language_info` |
| `ui/fonts.py` | 6 | `get_language_info` |
| `ui/app.py` | 14 | `get_stt_compatibility_warning` |
| `app/wiring.py` | 217, 342 | `get_local_qwen_language_hint` |

**Решение:** `core/language.py` → `domain/language.py`

**Порядок:**
1. `git mv app/src/puripuly_heart/core/language.py app/src/puripuly_heart/domain/language.py`
2. Заменить 9 импортов: `from puripuly_heart.core.language` → `from puripuly_heart.domain.language`
3. `py_compile` всех 8 файлов + domain/language.py

**Риск:** Низкий. Чистый move, zero reverse deps.

---

## 2. application/translation_service.py → core.runtime_logging

**Проблема:** `application/` импортирует из `core/`.

**Модуль:** `core/runtime_logging.py` (561 строка). Используется 14 файлами ВСЕХ слоёв.

**Зависимости модуля:**
```python
from puripuly_heart.config.paths import user_config_dir
from puripuly_heart.domain.overlay_types import SessionLoggingMode
from puripuly_heart.ports.logging_sink import RealtimeLogSink
```

**Почему нельзя просто переместить в domain/:** `ports.logging_sink` импорт создаст `domain/ → ports/` — запрещённое направление.

**Решение:** Разделить модуль на две части.

### Часть A: domain/logging_types.py (чистые типы)

Переместить из `core/runtime_logging.py`:
- `SessionLoggingMode` (re-export из domain/overlay_types — уже там)
- Формат latency: `LATENCY_CAUSE_E2E_THRESHOLD_MS`, `LATENCY_DOMINANT_STAGE_NORMAL`, `compute_latency_dominant_stage`, `format_basic_latency_summary`, `format_detailed_latency_breakdown`, `format_detailed_latency_trace`, `format_latency_cause_metric`
- `format_translation_ready_for_output`

Эти компоненты — чистые функции/константы без внутренних зависимостей.

### Часть B: core/runtime_logging.py (остаётся)

Оставить в `core/`:
- `SessionRuntimeLoggingService` (зависит от `RealtimeLogSink`, `config.paths`)
- `configure_main_logging`
- `RuntimeLoggingSinks`

Импортировать типы из `domain/logging_types.py`.

### Обновление импортов

14 файлов импортируют из `core.runtime_logging`. Из них:
- 8 файлов используют только `SessionLoggingMode` и/или формат latency → импортировать из `domain/logging_types.py`
- 6 файлов используют `SessionRuntimeLoggingService` → оставить импорт из `core/runtime_logging.py`
- `application/translation_service.py` использует `SessionRuntimeLoggingService` → оставить импорт из `core/runtime_logging.py` (нарушение сохраняется, но `core.runtime_logging` теперь чище)

**Альтернатива:** Протокол `SessionLoggingProtocol` в `ports/`. Тогда `application/` импортирует из `ports/`, а не из `core/`. Но это больше работы.

**Риск:** Средний. Нужно аккуратно разбить файл и обновить импорты.

---

## 3. core/pipeline/pipeline.py → adapters.overlay.sink (OverlayEventAdapter)

**Проблема:** Pipeline (core/) импортирует OverlayEventAdapter (adapters/).

**Факты:**
- 1 файл импортирует напрямую: `pipeline.py:27`
- 8 вызовов через `self.overlay_event_adapter.*` (overlay_helpers.py: 7, buffer_manager.py: 1)
- overlay_helpers.py и buffer_manager.py НЕ импортируют OverlayEventAdapter — используют через атрибут Pipeline
- Используемых методов: 5 из 7 (`transcript_final`, `translation_final`, `utterance_closed`, `self_active_update`, `self_active_clear`)
- Dead code: `translation_stream_update`, `peer_active_update` (0 вызовов)

**Решение:** OverlayEventFactory Protocol в `ports/overlay.py`.

**Порядок:**
1. Добавить в `ports/overlay.py` Protocol `OverlayEventFactory` с 7 методами (включая dead code для полноты)
2. `pipeline.py:27`: оставить `from adapters.overlay.sink import OverlayEventAdapter` (для инстанцирования в `__post_init__`)
3. `pipeline.py:130`: изменить тип `overlay_event_adapter: OverlayEventAdapter` → `overlay_event_adapter: OverlayEventFactory`
4. `ports/__init__.py`: добавить `OverlayEventFactory` в re-exports
5. overlay_helpers.py и buffer_manager.py — не трогать (используют атрибут)

**Сигнатуры Protocol (7 методов):**
```python
class OverlayEventFactory(Protocol):
    def transcript_final(self, transcript: Transcript, *, source_language: str, target_language: str) -> SelfTranscriptFinal | PeerTranscriptFinal: ...
    def translation_stream_update(self, *, utterance_id: UUID, channel: ChannelId, text: str, ...) -> TranslationStreamUpdate: ...
    def self_active_update(self, *, text: str, utterance_id: UUID, ...) -> SelfActiveUpdate: ...
    def peer_active_update(self, *, text: str, utterance_id: UUID, ...) -> PeerActiveUpdate: ...
    def self_active_clear(self, *, created_at: float | None = None) -> SelfActiveClear: ...
    def translation_final(self, *, utterance_id: UUID, channel: ChannelId, text: str, ...) -> TranslationFinal: ...
    def utterance_closed(self, *, utterance_id: UUID, channel: ChannelId, is_final: bool = True, created_at: float | None = None) -> UtteranceClosed: ...
```

**Затронутые файлы:** 3 (ports/overlay.py, ports/__init__.py, pipeline.py)

**Риск:** Средний. Сигнатуры длинные, но Protocol их просто описывает.

---

## 4. core/inference/worker → adapters.stt.* (предсуществующее)

**Проблема:** `core/inference/worker.py` лениво импортирует 4 adapters.stt модуля.

**Факты:**
- Worker — **subprocess** (303 строки), запускается через `subprocess.Popen`. Родительский процесс НЕ импортирует worker
- Lazy imports внутри `_create_recognizer()` (строки 64-153), 4 ветки if/elif по `provider_name`
- Вызывается **один раз** за жизнь subprocess (при получении JSON init команды)
- Каждый адаптер экспортирует `create_*_recognizer()` + `*_SAMPLE_RATE_HZ`
- 2 handle класса: `_RecognizerHandle` (3 адаптера) и `_TranscribecppHandle` (1 адаптер)
- `transcribecpp` имеет другую сигнатуру конструктора и `.gguf` discovery логику

**Почему DI не нужен:**
1. Subprocess boundary — импорты не влияют на main process
2. Вызов один раз — нет overhead
3. if/elif читабельный и локальный
4. `transcribecpp` существенно отличается от остальных — нормализация добавит сложность
5. Дублирования dispatch логики нет — одна точка (`_create_recognizer`)

**Решение:** Оставить как есть. Lazy imports в subprocess — acceptable trade-off.

Если всё-таки рефакторить: registry pattern `dict[str, AdapterSpec]` с wrapper функциями для нормализации сигнатур. Но ROI низкий.

---

## Порядок реализации

| Шаг | Описание | Файлов | Риск | Статус |
|-----|----------|--------|------|--------|
| 1 | `core/language.py` → `domain/language.py` | 9 | Низкий | Готов к реализации |
| 2 | Разделить `core/runtime_logging.py` на domain/ + core/ | ~20 | Средний | Нужна доп. проработка |
| 3 | OverlayEventFactory Protocol | 3 | Средний | Готов к реализации |
| 4 | Worker DI | 0 | — | Не нужен (оставить как есть) |

Шаги 1 и 3 — независимы, можно параллельно. Шаг 2 — самостоятельная задача. Шаг 4 — отменён.
