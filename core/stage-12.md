# Этап 12: Единый паттерн портов (LLMProvider → Protocol)

`LLMProvider` — единственный порт как plain class. Остальные — Protocol.

## Порядок

1. `ports/llm.py`:
   - Добавить `from typing import Protocol, runtime_checkable`
   - `class LLMProvider:` → `@runtime_checkable class LLMProvider(Protocol):`
   - Тела методов: `raise NotImplementedError` → `...`

2. `core/llm/provider.py`:
   - Убрать наследование: `class SemaphoreLLMProvider(LLMProvider):` → `class SemaphoreLLMProvider:`
   - Добавить аннотацию типа: `inner: LLMProvider`
   - Методы `translate` и `close` — оставить как есть (делегирование в inner)

3. Проверить:
   - `isinstance(llm, SemaphoreLLMProvider)` в `ui/controller.py:428` — работает (concrete class)
   - `adapters/llm/openai_compatible.py` и `adapters/llm/local_openai.py` — не трогаем (duck-typed)

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `app/src/puripuly_heart/ports/llm.py` | Редактирование |
| `app/src/puripuly_heart/core/llm/provider.py` | Редактирование |

## Неочевидное

- `isinstance(llm, LLMProvider)` нигде не используется — только `isinstance(..., LLMProviderName)` в settings (это enum, не порт)
- `OpenAICompatibleLLMProvider` и `LocalOpenAICompatibleLLMProvider` не наследуют от LLMProvider — duck-typed. После изменения на Protocol станут структурными сатисфайерами автоматически

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/ports/llm.py`
- `python -m py_compile app/src/puripuly_heart/core/llm/provider.py`
- `python -c "from puripuly_heart.ports.llm import LLMProvider; print(LLMProvider)"` — не должно быть NotImplementedError при импорте
