# План миграции: гексагональная архитектура + пайплайн

## Контекст

PuriPuly-heart portable-core. 127 Python файлов. 7 God Objects >1000 строк.
Разработчик — AI (я). План — для меня.

## Архитектура

Порты (Protocol) в `ports/`. Адаптеры (реализации) в `adapters/`. Ядро (пайплайн) в `core/pipeline/`. Связь — прямые вызовы через порты.

## God Objects (реальные цифры)

| Файл | Строк |
|------|------:|
| ui/views/settings.py | 4,687 |
| ui/controller.py | 4,374 (123 метода) |
| core/orchestrator/hub.py | 2,996 (107 методов) |
| config/settings.py | 1,842 (8 enum + 18 dataclass) |
| core/overlay/presenter.py | 1,628 (68 методов) |
| core/stt/controller.py | 902 |
| app/wiring.py | 327 (12 функций) |

Protocol-ов: 24 (подтверждено).
Импорт hub.py: 3 файла (controller.py, headless_mic.py, peer_channel.py).
Импорт controller.py: 1 файл (ui/app.py).

## Этапы

| # | Файл | Действие | Риск | Файлов |
|---|------|---------|------|--------|
| 0 | stage-0.md | Папки: ports/, adapters/, core/pipeline/ | 0 | 0 |
| 1 | stage-1.md | 24 Protocol → ports/ | низкий | ~24 |
| 2 | stage-2.md | settings.py → файлы по доменам | низкий | ~15 |
| 3 | stage-3.md | hub.py → pipeline stages | **высокий** | ~20 |
| 4 | stage-4.md | controller.py → менеджеры | **высокий** | ~15 |
| 5 | stage-5.md | settings view → секции | средний | ~10 |
| 6 | stage-6.md | presenter → компоненты | средний | ~8 |
| 7 | stage-7.md | wiring → импорты из ports/adapters | низкий | ~5 |

## Порядок

0 → 1 → 2 → 3 → (4, 5, 6 параллельно) → 7

## Правила

- Каждый этап — отдельный коммит
- После каждого этапа: `python -m py_compile` на затронутых файлах
- Строки — ориентир, не догма
