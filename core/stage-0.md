# Этап 0: Папки

Создать каркас. Ничего не менять в существующем коде.

## Неочевидное

- `__init__.py` должны быть пустыми. Не добавлять туда re-export — это будет в этапе 1.
- Не создавать `adapters/audio/`, `adapters/stt/` и т.д. заранее если не уверен что они нужны. Начать с минимального набора, расширять по мере необходимости.
- Путь: `app/src/puripuly_heart/ports/`, `app/src/puripuly_heart/adapters/`, `app/src/puripuly_heart/core/pipeline/`.

## Порядок

1. ports/ — первым, будет использоваться всеми
2. core/pipeline/ — вторым, ядро
3. adapters/ — последним, зависит и от ports и от существующих модулей

## Могу проверить (CLI)

- Папки существуют
- `__init__.py` на месте
- `python -m py_compile app/src/puripuly_heart/ports/__init__.py`
