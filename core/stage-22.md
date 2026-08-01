# Этап 22: config.prompts → инъекция callable

`application/translation_service.py:7` импортирует `render_translation_prompt_template`, `render_dual_translation_prompt_template` из `config.prompts` — application → config.

## Анализ

- `format_system_prompt()` вызывает:
  - `render_dual_translation_prompt_template(source_name=..., target_name=..., second_target_name=...)` — все keyword
  - `render_translation_prompt_template(self.system_prompt, source_name=..., target_name=...)` — первый positional, остальные keyword
- Сигнатуры разные: dual не принимает template, single принимает
- `pipeline.py:13` импортирует `warm_prompt_cache` — это отдельный импорт, не трогаем

## Порядок

1. `application/translation_service.py`:
   - Удалить импорт из `config.prompts` (строки 7-10)
   - Добавить поля:
     ```python
     render_prompt: Callable[[str, str, str], str] | None = None
     render_dual_prompt: Callable[[str, str, str], str] | None = None
     ```
   - `format_system_prompt()`: заменить вызовы:
     - `render_dual_translation_prompt_template(source_name=..., target_name=..., second_target_name=...)` → `self.render_dual_prompt(source_name, target_name, second_name)` (guarded `if self.render_dual_prompt`)
     - `render_translation_prompt_template(self.system_prompt, source_name=..., target_name=...)` → `self.render_prompt(self.system_prompt, source_name, target_name)` (guarded `if self.render_prompt`)
   - Fallback: если callable None — вернуть `self.system_prompt` как есть

2. `ui/controller.py`: при создании TranslationService добавить:
   ```python
   from puripuly_heart.config.prompts import render_translation_prompt_template, render_dual_translation_prompt_template
   ...
   hub.translation_service = TranslationService(
       ...
       render_prompt=render_translation_prompt_template,
       render_dual_prompt=render_dual_translation_prompt_template,
   )
   ```

3. `app/headless_mic.py`: аналогично (или передать None если промпты не нужны)

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `application/translation_service.py` | Удалить импорт, добавить 2 поля, изменить format_system_prompt |
| `ui/controller.py` | Добавить импорт + параметры при создании TranslationService |
| `app/headless_mic.py` | Добавить параметры при создании TranslationService |

## Неочевидное

- `render_translation_prompt_template` — принимает `template: str` первым аргументом. TranslationService хранит `self.system_prompt` — передаёт его как template
- `render_dual_translation_prompt_template` — НЕ принимает template (загружает из кэша). Только 3 keyword аргумента → маппинг на positional `(source_name, target_name, second_target_name)`
- Fallback на `self.system_prompt` — если callable None, TranslationService работает без форматирования (dev/headless режим)

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/application/translation_service.py`
- `python -m py_compile app/src/puripuly_heart/ui/controller.py`
- `python -m py_compile app/src/puripuly_heart/app/headless_mic.py`
- Grep: `from puripuly_heart.config.prompts` в application/ — 0 совпадений
