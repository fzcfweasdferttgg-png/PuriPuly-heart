# Этап 7: Wiring

Переписать импорты в app/wiring.py (327 строк). Самый простой этап.

## Неочевидное

**1. Wiring — единственный файл который знает и про порты и про адаптеры.**
Это composition root. Он создаёт адаптеры и передаёт их как порты. Вся инъекция зависимостей — здесь.

**2. headless_mic.py и headless_stdin.py — тоже composition roots.**
Они создают свой pipeline без UI. Их тоже нужно обновить — импорты из ports/ вместо core/.

**3. local_qwen_runtime_check.py и soxr_runtime_check.py — утилиты.**
Не являются частью pipeline. Не трогаем, кроме обновления импортов если нужно.

**4. Enum auto-migration.**
Wiring содержит `_migrate_old_provider_name()` — маппинг GEMINI → openai_compatible. Это логика миграции enum-ов. Остаётся, но enum импортируется из config/settings/.

**5. Порядок импортов имеет значение.**
Wiring создаёт объекты в определённом порядке (сначала settings, потом secrets, потом STT/LLM). Порядок не меняется.

## Порядок обновления

1. Обновить импорты в wiring.py: ports/ вместо core/, adapters/ вместо providers/
2. Обновить импорты в headless_mic.py (импортирует ClientHub + wiring)
3. Обновить импорты в headless_stdin.py
4. Проверить local_qwen_runtime_check.py и soxr_runtime_check.py — могут не требовать изменений

## Могу проверить (CLI)

- `python -m py_compile app/src/puripuly_heart/app/wiring.py`
- `python -m py_compile app/src/puripuly_heart/app/headless_mic.py`
- `python -m py_compile app/src/puripuly_heart/app/headless_stdin.py`
- `grep -r "from.*providers.*import" app/src/puripuly_heart/app/` — не должно быть
