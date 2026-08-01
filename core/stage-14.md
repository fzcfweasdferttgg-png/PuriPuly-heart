# Этап 14: Перемещение storage adapter

`core/storage/secrets.py` — конкретные реализации порта SecretStore. Единственный потребитель — wiring.py.

## Порядок

1. Создать `adapters/storage/__init__.py`
2. Переместить `core/storage/secrets.py` → `adapters/storage/secrets.py`
3. Обновить импорт в `app/wiring.py:21`:
   - `from core.storage.secrets import ...` → `from adapters.storage.secrets import ...`
4. Обновить `adapters/__init__.py`: добавить `"storage"` в `__all__`
5. Удалить `core/storage/` (включая `__init__.py`)

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `app/src/puripuly_heart/adapters/storage/__init__.py` | Создать |
| `app/src/puripuly_heart/adapters/__init__.py` | Добавить "storage" в __all__ |
| `core/storage/secrets.py` | Переместить → `adapters/storage/` |
| `core/storage/__init__.py` | Удалить (вместе с директорией) |
| `app/wiring.py` | Обновить 1 импорт (строка 21) |

## Неочевидное

- `ports/secrets.py` (6 строк) — SecretStore Protocol. Не трогаем
- `core/` после перемещения: storage/ удаляется полностью

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/adapters/storage/secrets.py`
- `python -m py_compile app/src/puripuly_heart/app/wiring.py`
