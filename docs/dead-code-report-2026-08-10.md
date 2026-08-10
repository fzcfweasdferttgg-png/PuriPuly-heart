# 🔍 ИТОГОВЫЙ ОТЧЁТ: Мёртвый код в `puripuly_heart/`

**Дата:** 2026-08-10
**Агент:** local-orchestrator → local-search + local-verify
**Модель:** GRM-2.6-Opus-Heretic (local)

## Категория 1: Неиспользуемые импорты (4 шт.)

| # | Файл | Строка | Импорт | Почему мёртвый |
|---|------|--------|--------|----------------|
| 1 | `ui/controller.py` | 10 | `import secrets` | Модуль `secrets` не вызывается нигде (переменная `secrets` в файле — локальная, не из этого модуля) |
| 2 | `ui/controller.py` | 9 | `import math` | `math.` нигде не используется в файле (1699 строк) |
| 3 | `ui/views/settings.py` | 9 | `import math` | `math.` нигде не используется в файле (2061 строка) |
| 4 | `ui/views/settings.py` | 32 | `from ...domain.language import get_stt_compatibility_warning` | Функция импортируется, но нигде в файле не вызывается (функция сама живая — используется в `stt_section.py` и `app.py`) |

## Категория 2: Код после return/break/continue

**Не найдено.** Код чист от unreachable участков.

## Категория 3: Закомментированный код (1 шт.)

| # | Файл | Строка | Содержимое | Комментарий |
|---|------|--------|------------|-------------|
| 1 | `ui/components/power_button.py` | 69 | `# alignment=ft.alignment.center,` | Закомментированный параметр конструктора с пометкой `REMOVED: This was crushing the stack` |

## Категория 4: Неиспользуемые функции/классы (15 шт.)

| # | Файл | Строка | Имя | Тип | Почему мёртвый |
|---|------|--------|-----|-----|----------------|
| 1 | `ui/desktop_overlay.py` | 756 | `desktop_overlay_preview_fixture_data_sources` | функция | Grep: 1 совпадение (только def). Не импортируется, не вызывается |
| 2 | `core/local_stt_assets.py` | 319 | `default_local_stt_source_for_locale` | функция | Grep: 1 совпадение (только def). Есть замена — другой путь выбора источника |
| 3 | `core/local_stt_assets.py` | 449 | `validate_local_stt_install` | функция | Grep: 4 совпадения (def + 3 комментария). Заменена на `validate_local_stt_runtime_ready` |
| 4 | `app/wiring.py` | 114 | `assign_to_job` | функция | Grep: 0 импортов. В `subprocess_backend.py` есть своя копия `_assign_to_job` |
| 5 | `core/pipeline/latency_tracker.py` | 71 | `latency_key` | функция | В `__all__`, но 0 внешних импортов. Класс использует приватную `_latency_key` |
| 6 | `core/pipeline/latency_tracker.py` | 75 | `elapsed_latency_ms` | функция | В `__all__`, но 0 внешних импортов. Класс использует приватную `_elapsed_latency_ms` |
| 7 | `config/settings/base.py` | 147 | `_parse_bool` | функция | Экспортирована через `__init__.py`, но 0 вызовов |
| 8 | `config/settings/base.py` | 153 | `_parse_non_negative_int` | функция | Экспортирована через `__init__.py`, но 0 вызовов |
| 9 | `config/settings/base.py` | 198 | `_parse_utc_iso8601_timestamp` | функция | Экспортирована через `__init__.py`, но 0 вызовов |
| 10 | `core/clock.py` | 24 | `FakeClock` | класс | В `__all__`, но 0 импортов. Тестовая утилита без тестов |
| 11 | `ui/components/settings/audio_settings.py` | 104 | `_build_numeric_field` | метод | Grep: 1 совпадение (только def). Хелпер не вызывается |
| 12 | `ui/components/settings/audio_settings.py` | 388 | `_on_desktop_vad_threshold_change` | метод | Callback без привязанного TextField (часть незавершённой фичи) |
| 13 | `ui/components/settings/audio_settings.py` | 397 | `_on_desktop_hangover_change` | метод | Callback без привязанного TextField |
| 14 | `ui/components/settings/audio_settings.py` | 405 | `_on_desktop_pre_roll_change` | метод | Callback без привязанного TextField |
| 15 | `ui/components/settings/audio_settings.py` | 413+431 | `_parse_float` / `_parse_int` | методы | Вызываются ТОЛЬКО из мёртвых callbacks #12-14 |

## Сводка

| Категория | Кол-во |
|-----------|--------|
| Неиспользуемые импорты | **4** |
| Unreachable код | **0** |
| Закомментированный код | **1** |
| Неиспользуемые функции/классы | **15** (в 6 файлах) |
| **ИТОГО** | **20** |

### Паттерны

- **`config/settings/base.py`** — 3 утилитарные функции-парсеры экспортированы, но никто не вызывает. Вероятно, заготовки «на будущее».
- **`ui/components/settings/audio_settings.py`** — 6 функций (включая `_parse_float`/`_parse_int`) — незавершённая фича десктопных аудио-настроек (VAD threshold, hangover, pre_roll). Есть setter'ы, но нет создания UI-полей.
- **`core/pipeline/latency_tracker.py`** — 2 функции в `__all__` дублируют приватные методы класса. Уже помечены комментарием `DEAD CODE`.
- **`core/local_stt_assets.py`** — 2 функции устарели после рефакторинга (заменены на аналоги).
- **`app/wiring.py`** + **`core/inference/subprocess_backend.py`** — дублирование `assign_to_job` / `_assign_to_job`.
