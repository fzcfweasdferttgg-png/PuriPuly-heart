# Этап 10: Перенос в pipeline/

Перенести существующие модули из orchestrator/ в pipeline/ без изменения логики.

## Что переезжает

| Файл | Строки | Куда | Изменения |
|------|-------:|------|-----------|
| `orchestrator/latency_tracker.py` | ~304 | `pipeline/latency_tracker.py` | Нет (без изменений) |
| `orchestrator/text_merge.py` | ~169 | `pipeline/text_merge.py` | Нет (без изменений) |
| `orchestrator/overlay_helpers.py` | ~622 | `pipeline/overlay_helpers.py` | Обновить импорты |
| `orchestrator/peer_turns.py` | ~138 | `pipeline/peer_turns.py` | Обновить импорты |
| `orchestrator/buffer_manager.py` | ~738 | `pipeline/buffer_manager.py` | Обновить импорты |
| `orchestrator/channel_runtime.py` | ~207 | `pipeline/channel_runtime.py` | Обновить импорты |
| `orchestrator/context.py` | ~130 | `pipeline/context.py` | Обновить импорты |

7 файлов, не 5. `channel_runtime.py` и `context.py` — data types используемые hub.py и mixin'ами.

## Порядок

1. Создать `core/pipeline/stages/__init__.py`
2. Перенести 5 файлов из orchestrator/ в pipeline/
3. Обновить импорты внутри mixin'ов (relative → absolute, orchestrator → pipeline)
4. Обновить hub.py — импорты mixin'ов из pipeline/ вместо orchestrator/
5. Проверить: py_compile

## Результат

| Файл | Строк |
|------|------:|
| `pipeline/latency_tracker.py` | ~304 |
| `pipeline/text_merge.py` | ~169 |
| `pipeline/overlay_helpers.py` | ~622 |
| `pipeline/peer_turns.py` | ~138 |
| `pipeline/buffer_manager.py` | ~738 |
| `pipeline/channel_runtime.py` | ~207 |
| `pipeline/context.py` | ~130 |
| **Итого** | **~2308** |

hub.py остаётся в orchestrator/, но импортирует всё из pipeline/.

## Могу проверить

- `python -m py_compile` на каждом файле
- `grep -r "from.*orchestrator.overlay_helpers" app/` — 0 совпадений
- `grep -r "from.*orchestrator.peer_turns" app/` — 0 совпадений
- `grep -r "from.*orchestrator.buffer_manager" app/` — 0 совпадений
- `grep -r "from.*orchestrator.channel_runtime" app/` — только hub.py (пока)
- `grep -r "from.*orchestrator.context" app/` — только hub.py (пока)
- Весь проект компилируется

## Риск

Низкий. Перенос файлов + обновление импортов. Логика не меняется.
