# tmp.md — наблюдения после stages 12-18

## Dependency direction violations (не из наших этапов)

### 1. application/ → core/ (утилиты)

`application/translation_service.py` импортирует:
- `from puripuly_heart.core.language import get_llm_language_name`
- `from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService`

Нарушает правило "application/ must not import from core.". Оба модуля — shared utilities, не adapter implementations.

**Устранить:** переместить `core/language.py` и `core/runtime_logging.py` в `domain/` или отдельный `common/` слой.

### 2. core/pipeline → adapters.overlay.sink

`core/pipeline/pipeline.py` импортирует `OverlayEventAdapter` из `adapters.overlay.sink`.

OverlayEventAdapter — factory для overlay event dataclass'ов. Pipeline использует его для создания событий.

**Устранить:** создать порт `OverlayEventFactory` в `ports/overlay.py` или передавать через DI.

### 3. core/inference/worker → adapters.stt.* (предсуществующее)

`core/inference/worker.py` импортирует из 4 adapters.stt модулей. Не из наших этапов.
