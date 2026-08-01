# Этап 20: OverlayEventAdapter создание → wiring

`pipeline.py:27` импортирует `OverlayEventAdapter` из `adapters.overlay.sink` — core → adapter.

## Контекст

- Поле `overlay_event_adapter: OverlayEventFactory = field(init=False)` (строка 130)
- Инстанцирование в `__post_init__`: `self.overlay_event_adapter = OverlayEventAdapter(clock=self.clock)` (строка 144)
- Ничего после строки 144 в `__post_init__` не зависит от overlay_event_adapter
- 9 вызовов через mixin'ы (overlay_helpers.py: 8, buffer_manager.py: 1) — все через `self.overlay_event_adapter` атрибут
- Тип поля уже `OverlayEventFactory` (Protocol) — корректно

## Порядок

1. `core/pipeline/pipeline.py`:
   - Удалить импорт: `from puripuly_heart.adapters.overlay.sink import OverlayEventAdapter` (строка 27)
   - Изменить поле: `overlay_event_adapter: OverlayEventFactory = field(init=False)` → `overlay_event_adapter: OverlayEventFactory`
   - Удалить строку 144: `self.overlay_event_adapter = OverlayEventAdapter(clock=self.clock)`

2. `ui/controller.py`: добавить в Pipeline(...) вызов:
   ```python
   from puripuly_heart.adapters.overlay.sink import OverlayEventAdapter
   ...
   hub = Pipeline(
       ...
       overlay_event_adapter=OverlayEventAdapter(clock=self.clock),
       ...
   )
   ```

3. `app/headless_mic.py`: аналогично добавить `overlay_event_adapter=OverlayEventAdapter(clock=self.clock)` в Pipeline(...)

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `core/pipeline/pipeline.py` | Удалить импорт + изменить поле + удалить строку __post_init__ |
| `ui/controller.py` | Добавить импорт OverlayEventAdapter + параметр в Pipeline() |
| `app/headless_mic.py` | Добавить импорт OverlayEventAdapter + параметр в Pipeline() |

## Неочевидное

- Поле становится обязательным параметром конструктора (нет default). Все Pipeline() вызовы должны передать overlay_event_adapter
- `OverlayEventAdapter` — единственный импорт из adapters/ в pipeline.py. После удаления — core/pipeline/ не импортирует из adapters/
- mixin'ы не меняются — продолжают использовать `self.overlay_event_adapter` атрибут

## Цепочки

- `__post_init__` → OverlayEventAdapter создаётся в wiring, передаётся как параметр
- `overlay_helpers.py` → `self.overlay_event_adapter.transcript_final(...)` — не меняется
- `buffer_manager.py` → `self.overlay_event_adapter.self_active_update(...)` — не меняется

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/core/pipeline/pipeline.py`
- `python -m py_compile app/src/puripuly_heart/ui/controller.py`
- `python -m py_compile app/src/puripuly_heart/app/headless_mic.py`
- Grep: `from puripuly_heart.adapters` в pipeline.py — 0 совпадений
