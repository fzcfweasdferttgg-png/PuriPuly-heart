# Этап 21: SessionLogger Protocol в ports/

`application/translation_service.py:12` и 3 adapters импортируют `SessionRuntimeLoggingService` из `core.runtime_logging` — application/adapters → core.

## Анализ использования

| Файл | Методы |
|------|--------|
| `application/translation_service.py` | `emit_basic()` |
| `adapters/osc/chatbox_paginator.py` | `emit_basic()`, `emit_detailed()` |
| `adapters/llm/openai_compatible.py` | `emit_basic()` |
| `adapters/llm/local_openai.py` | только передаёт дальше (не вызывает) |
| `ui/overlay_manager.py` | TYPE_CHECKING — `.mode` property (через controller) |

## Порядок

1. Создать `ports/logging.py`:
   ```python
   from __future__ import annotations
   import logging
   from typing import Callable, Protocol

   from puripuly_heart.domain.overlay_types import SessionLoggingMode

   class SessionLogger(Protocol):
       @property
       def mode(self) -> SessionLoggingMode: ...
       def emit_basic(self, message: str, *, level: int = logging.INFO) -> None: ...
       def emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool: ...
       def emit_detailed_lazy(self, build_message: Callable[[], str], *, level: int = logging.INFO) -> bool: ...
   ```

2. `ports/__init__.py`: добавить `SessionLogger` в импорт и `__all__`

3. Обновить импорты (4 файла):
   - `application/translation_service.py:12`: `from core.runtime_logging` → `from ports.logging import SessionLogger`
   - `adapters/osc/chatbox_paginator.py:9`: аналогично
   - `adapters/llm/openai_compatible.py:11`: аналогично
   - `adapters/llm/local_openai.py:15`: аналогично

4. Обновить аннотации типов:
   - `SessionRuntimeLoggingService | None` → `SessionLogger | None` в полях

5. `ui/overlay_manager.py:25` (TYPE_CHECKING): оставить как есть или изменить на `SessionLogger`

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `ports/logging.py` | Создать |
| `ports/__init__.py` | Добавить SessionLogger в re-exports |
| `application/translation_service.py` | Изменить импорт + аннотацию |
| `adapters/osc/chatbox_paginator.py` | Изменить импорт + аннотацию |
| `adapters/llm/openai_compatible.py` | Изменить импорт + аннотацию |
| `adapters/llm/local_openai.py` | Изменить импорт + аннотацию |
| `ui/overlay_manager.py` | Изменить импорт (TYPE_CHECKING) |

## Неочевидное

- `SessionRuntimeLoggingService` — конкретный класс в core/. `SessionLogger` — Protocol в ports/. Класс реализует Protocol структурно (duck typing)
- `mode` — property с типом `SessionLoggingMode` (определён в `domain/overlay_types.py`). Protocol импортирует из domain/ — разрешено
- `core/runtime_logging.py` НЕ меняется — остаётся как есть. Все файлы в core/ продолжают импортировать `SessionRuntimeLoggingService` напрямую
- `controller.py` НЕ меняется — он создаёт `SessionRuntimeLoggingService` и передаёт в Pipeline/adapters

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/ports/logging.py`
- `python -m py_compile app/src/puripuly_heart/application/translation_service.py`
- `python -m py_compile app/src/puripuly_heart/adapters/osc/chatbox_paginator.py`
- `python -m py_compile app/src/puripuly_heart/adapters/llm/openai_compatible.py`
- `python -m py_compile app/src/puripuly_heart/adapters/llm/local_openai.py`
