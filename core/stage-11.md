# Этап 11: Pipeline координатор

Создать pipeline.py и stages, заменить hub.py.

## Архитектура

```
Pipeline (координатор)
  ├── STT event loop
  ├── stages/filter.py     — дедуп, language filter
  ├── stages/context.py    — prompt enrichment
  ├── stages/translation.py — LLM вызов
  ├── stages/output.py     — OSC + overlay emit
  └── runner.py            — async event loop
```

Mixin'ы (latency_tracker, text_merge, overlay_helpers, peer_turns, buffer_manager) интегрируются в pipeline.py и stages.

## Что создаём

| Файл | Строк | Откуда логика |
|------|------:|---------------|
| `pipeline/pipeline.py` | ~1200 | hub.py (fields, __init__, STT lifecycle, event routing, context, error/logging) |
| `pipeline/stages/filter.py` | ~100 | Новое — дедуп/language |
| `pipeline/stages/context.py` | ~150 | Новое — prompt enrichment |
| `pipeline/stages/translation.py` | ~200 | _translate_and_enqueue (LLM вызов) |
| `pipeline/stages/output.py` | ~400 | overlay_helpers.py + OSC emit |
| `pipeline/stages/peer.py` | ~400 | peer_turns.py + peer translation |
| `pipeline/stages/buffer.py` | ~738 | buffer_manager.py |
| `pipeline/runner.py` | ~150 | Новое — async event loop |
| **Итого** | **~3338** | |

## Что удаляем

- `orchestrator/hub.py`
- `pipeline/overlay_helpers.py` (переехал в Stage 10, теперь интегрируется в stages)
- `pipeline/peer_turns.py` (переехал в Stage 10, теперь интегрируется в stages)
- `pipeline/buffer_manager.py` (переехал в Stage 10, теперь интегрируется в stages)

## Потребители для обновления (3 файла)

| Файл | Ссылок на ClientHub | Что менять |
|------|--------------------:|-----------|
| `ui/controller.py` | 47 | `ClientHub` → `Pipeline`, все `self.hub.*` → `self.pipeline.*` |
| `app/headless_mic.py` | ~10 | `ClientHub(...)` → `Pipeline(...)`, `hub.start/stop/handle` |
| `core/runtime/peer_channel.py` | ~8 | `ClientHub` → `Pipeline`, `hub.replace_peer_stt_provider` |

## Порядок

1. Создать `pipeline/pipeline.py` — координатор с полями ClientHub
2. Создать `pipeline/stages/filter.py` — дедуп/language
3. Создать `pipeline/stages/context.py` — prompt enrichment
4. Создать `pipeline/stages/translation.py` — LLM вызов
5. Создать `pipeline/stages/output.py` — OSC + overlay emit
6. Создать `pipeline/stages/peer.py` — переезд peer_turns
7. Создать `pipeline/stages/buffer.py` — переезд buffer_manager
8. Создать `pipeline/runner.py` — async event loop
9. Обновить `ui/controller.py`: ClientHub → Pipeline (47 ссылок)
10. Обновить `app/headless_mic.py`: ClientHub → Pipeline
11. Обновить `core/runtime/peer_channel.py`: ClientHub → Pipeline
12. Удалить `orchestrator/hub.py`, `pipeline/overlay_helpers.py`, `pipeline/peer_turns.py`, `pipeline/buffer_manager.py`
13. Проверить: py_compile + grep

## Могу проверить

- `python -m py_compile` на каждом файле
- `grep -r "from.*orchestrator.hub" app/` — 0 совпадений
- `grep -r "from.*orchestrator.overlay_helpers" app/` — 0 совпадений
- `grep -r "from.*orchestrator.peer_turns" app/` — 0 совпадений
- `grep -r "from.*orchestrator.buffer_manager" app/` — 0 совпадений
- Весь проект компилируется

## Риск

Высокий. Переписываем hub.py + интегрируем mixin'ы в stages. Все импорты ClientHub ломаются.
Миграция по шагам (пункты 1-13) снижает риск.
