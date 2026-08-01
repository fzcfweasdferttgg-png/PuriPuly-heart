# План миграции — фаза 2

Продолжение plan-overview.md. Четыре этапа: domain/ чистка, adapters/ миграция, pipeline/.

## Что сделано (фаза 1)

- 24 Protocol → ports/ (16 файлов)
- 5 god objects разбиты на mixin'ы и пакеты
- config/settings.py → settings/ пакет (11 файлов)
- wiring/headless файлы используют ports/ импорты
- 185 .py файлов, все компилируются

## Нарушения dependency inversion (ports/ импортирует из core/)

| Файл ports/ | Импортирует из | Тип |
|-------------|---------------|-----|
| overlay_transport.py | core/overlay/protocol.py | OverlayPresentationSnapshot |
| ui.py | core/overlay/protocol.py | OverlayPresentationSnapshot |
| overlay_process.py | core/overlay/manifest.py | OverlayLaunchManifest |
| peer.py | app/wiring.py | ResolvedPeerSTTConfig |

## Этапы

| # | Файл | Действие | Риск |
|---|------|---------|------|
| 8 | stage-8.md | Data types → domain/ | Средний |
| 9 | stage-9.md | providers/ → adapters/ | Низкий |
| 10 | stage-10.md | Перенос mixin'ов в pipeline/ | Низкий |
| 11 | stage-11.md | Pipeline координатор, замена hub.py | Высокий |

## Порядок

8 → 9 → 10 → 11 (последовательно)

## Правила (те же)

- Каждый этап — отдельный коммит
- После каждого этапа: `python -m py_compile` на затронутых файлах
- Строки — ориентир, не догма
