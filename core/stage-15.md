# Этап 15: Overlay — перемещение OverlayEventAdapter + фикс импортов state.py

`core/overlay/sink.py` содержит OverlayEventAdapter (adapter) + re-exports из ports/overlay.py. Event types уже в ports/overlay.py.

## Порядок

1. Создать `adapters/overlay/__init__.py`

2. Переместить `core/overlay/sink.py` → `adapters/overlay/sink.py`

3. `core/overlay/state.py` (строки 14-23): заменить импорт:
   - `from core.overlay.sink import (EventTypes...)` → `from ports.overlay import (EventTypes...)`
   - Event types уже определены в ports/overlay.py, sink.py только re-export их

4. `core/pipeline/pipeline.py`:
   - Строка 30-33: `from core.overlay.sink import (OverlayEventAdapter, OverlaySink)` → раздельные импорты:
     - `from adapters.overlay.sink import OverlayEventAdapter`
     - `from ports.overlay import OverlaySink`

5. `core/overlay/sink.py` делал re-export `OverlaySink` из ports. Проверить что никто не импортирует OverlaySink через sink.py кроме pipeline.py

6. Обновить `adapters/__init__.py`: добавить `"overlay"` в `__all__`

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `app/src/puripuly_heart/adapters/overlay/__init__.py` | Создать |
| `app/src/puripuly_heart/adapters/__init__.py` | Добавить "overlay" в __all__ |
| `core/overlay/sink.py` | Переместить → `adapters/overlay/` |
| `core/overlay/state.py` | Изменить импорт event types (строки 14-23) |
| `core/pipeline/pipeline.py` | Изменить 2 импорта (строки 30-33) |

## Неочевидное

- Event types (SelfTranscriptFinal, TranslationFinal и т.д.) **определены в ports/overlay.py**, не в sink.py. sink.py только re-export + OverlayEventAdapter
- OverlayHelpersMixin (622 строки) использует `self.overlay_event_adapter` — атрибут Pipeline, не импорт. Не трогаем
- `core/overlay/state.py` (1,850 строк) импортирует 8 event types через sink.py — после фикса импортирует из ports/overlay.py напрямую. Убираем избыточную зависимость

## Цепочки

- Pipeline создаёт `OverlayEventAdapter(clock=self.clock)` в `__post_init__` — только импорт меняется
- OverlayHelpersMixin вызывает `self.overlay_event_adapter.transcript_final(...)` и т.д. — атрибут Pipeline, не импорт
- state.py использует event types как типы параметров — типы те же, путь импорта меняется

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/adapters/overlay/sink.py`
- `python -m py_compile app/src/puripuly_heart/core/overlay/state.py`
- `python -m py_compile app/src/puripuly_heart/core/pipeline/pipeline.py`
