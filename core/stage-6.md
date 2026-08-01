# Этап 6: Presenter

Разбить core/overlay/presenter.py (1628 строк, 68 методов) на компоненты.

## Реальная структура

OverlayPresenter реализует OverlaySink Protocol. Содержит 68 методов в основных группах:
- Init/state (12): __post_init__, _entries, _retired_preview_self_seqs, getters/setters, attach/detach_bridge, snapshot, reset_scene
- Rendering (4): _rendered_text_sources, _rendered_pair_state, _terminal_update_reason, _remember_scene_terminal_reason
- Event application (10): _apply_event, _apply_self_event, _apply_peer_event и др.
- Entry management (12): _entry_key, _is_tombstoned, _live_entry_for_channel, _prune_displaced_finalized_entries, _schedule_expiration, _expire_closed_entries и др.
- Retry logic (3): _active_python_retry_targets, _active_native_retry_targets, _synchronize_native_retry_targets
- Refresh burst (10): _create_self_presentation_refresh_burst_task, _cancel_peer_presentation_refresh_burst_task и др.
- Logging (8): _emit_detailed, _emit_turn_decision, _emit_pair_state и др.

## Неочевидное

**1. Presenter — stateful.**
Он хранит текущее состояние оверлея: какие строки показываются, какие устарели, какие ждут обновления. Это не stateless-обработчик.

**2. Two overlay targets with different protocols.**
SteamVR overlay (через bridge.py, subprocess) и desktop overlay (через Flet). Presenter абстрагирует оба. При разбиении — presenter остаётся координатором, не разделяем по target.

**3. Coalescing — не тривиальный.**
Частые обновления (10+ в секунду от STT partials) coalescятся в одно обновление. Логика coalescing зависит от timing, debounce interval, и приоритета (final > partial). Выделять в render_scheduler аккуратно — не потерять приоритеты.

**4. OverlaySink — это порт, но presenter его создаёт.**
Presenter получает OverlaySink через конструктор. При разбиении — presenter передаёт sink в render_scheduler. Не наоборот.

**5. state.py уже существует.**
`core/overlay/state.py` уже содержит `OverlayPresentationEntry` (Protocol). Это не дублирование с presentation_state.py. state.py — внутренний интерфейс. presentation_state.py — состояние для рендера.

## Порядок извлечения

1. Logging (8) → первыми, изолированы
2. Entry management (12) → вторыми, stateful но замкнуты
3. Retry logic (3) → третьими, маленькая группа
4. Refresh burst (10) → четвёртыми, зависят от entry management
5. Event application (10) → пятыми, зависят от entry management
6. Init/state (12) → последними, связаны со всем
7. Rendering (4) → остаются в presenter (координатор)

## Могу проверить (CLI)

- `python -m py_compile` на каждом компоненте
- `grep -r "from.*presenter import"` — обновить импорты
