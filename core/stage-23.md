# Этап 23: STT error types → domain/stt_errors.py

`ui/controller.py:98-102` импортирует 5 error types из `adapters.stt.*` — ui → adapters.

## Анализ

- 5 классов для перемещения: `LocalQwenSherpaLoadError`, `LocalGigaamRnntLoadError`, `LocalParakeetTdtLoadError`, `LocalParakeetCtcLoadError`, `LocalTranscribecppLoadError`
- Все наследуют `RuntimeError`, пустое тело (только docstring)
- Используются в 2 except блоках в controller.py (строки 622, 659) — defensive catch
- 6-й класс `LocalTranscribecppInferenceError` — НЕ используется в controller, не трогаем
- **Все 5 публичных *LoadError — dead code.** Ни один не поднимается (`raise`) ни в одном файле проекта. Adapters поднимают приватные `_*ImportError(ImportError)` — это отдельная иерархия, не связанная с *LoadError. Except блоки в controller никогда не срабатывают для этих типов

## Порядок

1. Создать `domain/stt_errors.py`:
   ```python
   class LocalQwenSherpaLoadError(RuntimeError): ...
   class LocalGigaamRnntLoadError(RuntimeError): ...
   class LocalParakeetTdtLoadError(RuntimeError): ...
   class LocalParakeetCtcLoadError(RuntimeError): ...
   class LocalTranscribecppLoadError(RuntimeError): ...
   ```

2. `ui/controller.py:98-102`: изменить импорты:
   - `from puripuly_heart.adapters.stt.local_qwen_sherpa import LocalQwenSherpaLoadError` → `from puripuly_heart.domain.stt_errors import LocalQwenSherpaLoadError`
   - Аналогично для остальных 4

3. Adapters НЕ трогать — они не импортируют и не поднимают *LoadError

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `domain/stt_errors.py` | Создать (5 классов) |
| `ui/controller.py` | Изменить 5 импортов (строки 98-102) |

## Неочевидное

- **Adapters НЕ используют *LoadError для raise.** Каждый adapter поднимает приватный `_*ImportError(ImportError)`, который не связан с *LoadError иерархией. Перемещение *LoadError в domain/ не влияет на adapters — им не нужно импортировать их обратно
- `_*ImportError` классы — внутренние sentinel'ы, не трогаем
- `local_transcribecpp.py` содержит ещё `LocalTranscribecppInferenceError` — не используется в controller, не трогаем
- except блоки в controller (строки 622, 659) также ловят `LocalSTTModelMissingError`, `LocalSTTManifestInvalidError`, `SubprocessSTTError` — эти НЕ из adapters, не трогаем
- После перемещения adapters НЕ нужно менять — они не импортируют и не поднимают *LoadError

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/domain/stt_errors.py`
- `python -m py_compile app/src/puripuly_heart/adapters/stt/local_qwen_sherpa.py`
- `python -m py_compile app/src/puripuly_heart/ui/controller.py`
- Grep: `from puripuly_heart.adapters.stt.*LoadError` в controller.py — 0 совпадений
