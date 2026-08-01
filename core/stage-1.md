# Этап 1: Порты

Перенести Protocol-ы в `ports/`. Оригиналы реэкспортируют.

## Неочевидное

**1. Protocol — это не только class.**
Некоторые Protocol определены рядом с типами, которые они используют. Например `STTBackendSession` использует `AudioChunk`, `Transcript` из domain/. При переносе Protocol нужно убедиться что типы доступны — импортировать из domain/, не тащить за собой.

**2. Не все Protocol — порты.**
`OverlayPresentationEntry` в state.py — это не порт, это внутренний интерфейс overlay. `MuteState` в gate.py — enum, не порт. Не всё что называется Protocol нужно переносить в ports/. Переносим только то что является границей между слоями.

**3. Re-export через `__init__.py` не всегда нужен.**
Если Protocol импортируется в 1-2 файлах — достаточно обновить импорт. Re-export нужен только если Protocol используется повсюду через старый путь. Проверять импорты перед принятием решения.

**4. `LLMProvider` — base class, не Protocol.**
В `core/llm/provider.py` `LLMProvider` — это обычный класс с методом `translate()`. Не Protocol. Его тоже переносим в ports/ но как абстрактный класс, не Protocol.

**5. `SemaphoreLLMProvider` — decorator, остаётся.**
Decorator над LLMProvider. Остаётся в core/llm/. После переноса LLMProvider в ports/ — обновить импорт.

**6. Количество переносимых Protocol может быть не 24.**
При детальном анализе может оказаться что часть Protocol не нужно переносить (внутренние интерфейсы) или что нашлись неучтённые. Ориентироваться на принцип: "порт = граница между слоями".

## Порядок переноса

1. Нулевые зависимости: Clock, OscSender, SecretStore — переносим первыми, ничего не тянут
2. Same-file зависимости: STTBackend + STTBackendSession + STTBackendTranscriptEvent — переносим вместе
3. Domain зависимости: LLMProvider (импортирует Translation из domain)
4. Cross-core зависимости: VadEventSink (зависит от VadEvent из gating.py), AudioSource (зависит от AudioFrameF32)
5. UI Protocol-ы: ClipboardWatcherRuntime, LifecycleSink, RendererWindow, ParentMonitor — последними

## Могу проверить (CLI)

- `python -m py_compile` на каждом файле в ports/
- `grep -r "from.*core.*import.*Protocol"` — не должно быть (все Protocol в ports/)
- `grep -r "from.*ports.*import"` — все потребители обновлены
- Проверить что ports/ не импортирует из core/, providers/, ui/ (dependency inversion)
