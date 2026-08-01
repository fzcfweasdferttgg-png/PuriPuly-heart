# Этап 16: Формализация state + удаление forwarding boilerplate

Pipeline (1,473 строки) содержит 12 forwarding-методов для LatencyTracker и bidirectional aliasing с ChannelRuntime.

## Что удаляем

### 12 forwarding-методов latency (~60 строк)

Все делегируют в `self._latency`:
- `_get_latency_timeline`
- `_latency_hangover_ms`
- `_emit_latency_trace_if_ready`
- `_emit_latency_summary_if_ready`
- `_emit_latency_contract_if_ready`
- `_record_latency_stage`
- `_inherit_latency_for_output`
- `_clear_latency_timeline`
- `_clear_latency_state`
- `_clear_runtime_latency_bookkeeping`
- `_finalize_latency_timeline`
- `_translation_ready_elapsed_ms`

### `__setattr__` override (~30 строк)

Bidirectional aliasing Pipeline ↔ ChannelRuntime. После удаления forwarding-методов mixin'ы обращаются к `self._latency` напрямую — aliasing не нужен.

### `_sync_self_runtime_aliases` (~10 строк)

Дублирует state из ChannelRuntime в Pipeline поля. После удаления __setattr__ не нужна.

## Что меняем в mixin'ах

Все mixin'ы: заменить `self._record_latency_stage(...)` → `self._latency._record_latency_stage(...)` (прямой доступ к LatencyTracker).

### buffer_manager.py

Замены (grep для точных мест):
- `self._record_latency_stage(` → `self._latency._record_latency_stage(`
- `self._emit_latency_contract_if_ready(` → `self._latency._emit_latency_contract_if_ready(`
- `self._inherit_latency_for_output(` → `self._latency._inherit_latency_for_output(`
- `self._clear_latency_timeline(` → `self._latency._clear_latency_timeline(`
- `self._promote_spec_latency_to_output(` → `self._latency._promote_spec_latency_to_output(` (если есть)

### overlay_helpers.py

Замены:
- `self._record_latency_stage(` → `self._latency._record_latency_stage(`
- `self._finalize_latency_timeline(` → `self._latency._finalize_latency_timeline(`

### peer_turns.py

Замены:
- `self._record_latency_stage(` → `self._latency._record_latency_stage(`
- `self._inherit_latency_for_output(` → `self._latency._inherit_latency_for_output(`
- `self._clear_latency_timeline(` → `self._latency._clear_latency_timeline(`

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `core/pipeline/pipeline.py` | Удалить ~100 строк (forwarding + __setattr__ + _sync_self_runtime_aliases) |
| `core/pipeline/stages/buffer_manager.py` | Заменить вызовы latency на прямой доступ |
| `core/pipeline/stages/overlay_helpers.py` | Заменить вызовы latency на прямой доступ |
| `core/pipeline/stages/peer_turns.py` | Заменить вызовы latency на прямой доступ |

## Неочевидное

- LatencyTracker (269 строк) — уже чисто извлечён. Принимает `emit_basic`/`emit_detailed` callbacks + Clock. Не имеет обратных зависимостей на Pipeline
- ChannelRuntime (182 строки) — уже держит per-channel state (utterances, translation_tasks и т.д.) с bidirectional aliasing. После удаления __setattr__ — ChannelRuntime становится единственным state holder'ом
- Нужно проверить: обращается ли кто-то к Pipeline полям напрямую (например `self._utterances` вместо `self.self_runtime.utterances`). Если да — заменить на runtime доступ

## Цепочки

- Latency tracking: те же вызовы, только прямой доступ к `self._latency` вместо forwarding. LatencyTracker не меняется
- State access: mixin'ы обращаются к state через `self.self_runtime`, `self.peer_runtime` — это ChannelRuntime. Удаление дублирующих полей Pipeline не влияет

## Результат

Pipeline: ~1,473 → ~1,370 строк. Убирается boilerplate, state формализован в ChannelRuntime.

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/core/pipeline/pipeline.py`
- `python -m py_compile app/src/puripuly_heart/core/pipeline/stages/buffer_manager.py`
- `python -m py_compile app/src/puripuly_heart/core/pipeline/stages/overlay_helpers.py`
- `python -m py_compile app/src/puripuly_heart/core/pipeline/stages/peer_turns.py`
