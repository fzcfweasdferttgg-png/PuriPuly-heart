# core/ — AI-ориентированное комментирование кода

Путь: `app/src/puripuly_heart/core/`
Дата начала: 2026-08-03

---

## Принципы

Комментарии пишутся **для AI-разработчика**, не для человека. AI читает код быстро, но не может вывести инварианты и cross-module constraints без подсказок.

### Что комментировать
- Неочевидные связи между модулями (alias mechanism, sync invariants)
- State machines и переходы состояний
- «Почему» — constraints, edge cases, нелогичные решения
- Потоки данных (STT → VAD → Translation → Overlay/OSC)
- Cross-module invariants (например: при обновлении hub — обновлять и вложенные объекты)

### Что НЕ комментировать
- «Что делает метод» — AI поймёт из имени и кода
- Типы и сигнатуры — уже есть в коде
- Объяснение очевидного

### Формат
- **Module-level docstring** — что модуль делает, key invariant, кто вызывает
- **Class-level docstring** — state machine, lifecycle, данные которые хранит
- **Method-level** — только если поведение неочевидно из имени
- **Inline** — только state machine transitions и неочевидные invariants

### Правила
- Комментарии должны быть **самодостаточны** — не ссылаться на даты, коммиты, PR, GitHub
- Не писать "(добавлено в commit X)" или "(исправлено 2026-08-02)" — AI не имеет доступа к git
- Если важно указать где происходит sync — писать файл и метод, не хеш коммита

### Тест
Перед комментарием: «AI прочитает этот метод и поймёт что делает? Если да — комментарий лишний. Если нет — что именно непонятно?»

---

## Подход (4-step workflow)

1. **Plan** — читаем файл, определяем что нужно комментировать
2. **Verify plan** — проверяем что plan соответствует реальности (читаем код, не опираемся на память)
3. **Implement** — пишем комментарии. Перед написанием "Called by ..." в module docstring — **grep реальных импортов** файла, не опираться на контекст
4. **Compile check** — `py_compile` на изменённый файл (ловит синтаксические ошибки, сломанные отступы, незакрытые кавычки)
5. **Verify result** — deep-verify agent проверяет: импорты, side effects, architectural constraints, edge cases. **Один агент в фоне** — дождаться завершения перед запуском следующего
6. **Fix + re-verify** — если deep-verify нашёл ошибки: самостоятельно проверить каждую (прочитать код, не доверять агенту слепо), исправить подтверждённые, повторить compile check

---

## Таблица файлов

| Строк | Путь | AI-комментарии |
|------:|------|:-:|
| 1850 | `overlay/state.py` | ⏳ |
| 977 | `pipeline/pipeline.py` | ⏳ |
| 879 | `overlay/process.py` | ⏳ |
| 809 | `stt/controller.py` | ⏳ |
| 681 | `overlay/presenter.py` | ⏳ |
| 670 | `pipeline/stages/buffer_manager.py` | ⏳ |
| 578 | `pipeline/stages/overlay_helpers.py` | ⏳ |
| 571 | `audio/source.py` | ✅ |
| 380 | `local_stt_assets.py` | ✅ |
| 359 | `vad/gating.py` | ✅ |
| 356 | `runtime_logging.py` | ✅ |
| 345 | `inference/subprocess_backend.py` | ✅ |
| 340 | `overlay/bridge.py` | ✅ |
| 317 | `overlay/presenter_refresh_burst.py` | ✅ |
| 314 | `audio/diagnostics.py` | ✅ |
| 298 | `clipboard/watcher.py` | ✅ |
| 293 | `overlay/presenter_logging.py` | ✅ |
| 269 | `pipeline/latency_tracker.py` | ✅ |
| 269 | `runtime/peer_channel.py` | ✅ |
| 259 | `translation_service.py` | ✅ |
| 241 | `audio/desktop_source.py` | ✅ |
| 235 | `overlay/presenter_entry_mgmt.py` | ✅ |
| 178 | `pipeline/channel_runtime.py` | ✅ |
| 164 | `vad/gating.py` | ✅ |
| 152 | `pipeline/text_merge.py` | ✅ |
| 125 | `pipeline/stages/peer_turns.py` | ✅ |
| 115 | `pipeline/context.py` | ✅ |
| 105 | `audio/desktop_pipeline.py` | ✅ |
| 96 | `overlay/diagnostics.py` | ✅ |
| 85 | `overlay/openvr_vendor.py` | ✅ |
| 84 | `osc/receiver.py` | ✅ |
| 60 | `audio/streaming_resampler.py` | ✅ |
| 54 | `output_dispatcher.py` | ✅ |
| 54 | `audio/ring_buffer.py` | ✅ |
| 53 | `soxr_runtime.py` | ✅ |
| 49 | `audio/gate.py` | ✅ |
| 40 | `stt/custom_vocab.py` | ✅ |
| 37 | `audio/format.py` | ✅ |
| 30 | `llm/provider.py` | ✅ |
| 26 | `vad/bundled.py` | ✅ |
| 24 | `overlay/sink.py` | ✅ stub |
| 24 | `overlay/protocol.py` | ✅ stub |
| 24 | `overlay/presenter_retry.py` | ⏳ |
| 17 | `clock.py` | ✅ |
| 13 | `stt/backend.py` | ⏳ |
| 12 | `overlay/manifest.py` | ⏳ |
| 11 | `overlay/presenter_constants.py` | ⏳ |
| 5 | `stt/local_qwen_hallucination.py` | ⏳ |
| 3 | `vad/sink.py` | ⏳ |
| 1 | `runtime/local_qwen_lifecycle.py` | ⏳ |

---

**Итого:** 50 файлов, ~13,050 строк. Закомментирован: 34.
