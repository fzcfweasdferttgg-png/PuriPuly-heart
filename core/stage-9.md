# Этап 9: providers/ → adapters/

Переименовать providers/ в adapters/ — canonical location в гексоганальной архитектуре.

## Текущая структура

```
providers/
├── __init__.py (__all__ = ["llm", "stt"])
├── llm/
│   ├── __init__.py (__all__ = ["local_openai", "openai_compatible"])
│   ├── openai_compatible.py    # OpenAICompatibleLLMProvider
│   ├── local_openai.py         # HttpxLocalOpenAIClient, LocalOpenAICompatibleLLMProvider
│   └── messages.py             # build_translation_user_message
└── stt/
    ├── __init__.py (__all__ = [...])
    ├── local_qwen_sherpa.py    # SherpaOnnxSTTBackend
    ├── local_gigaam_rnnt.py    # GigaamRnntSTTBackend
    ├── local_parakeet_ctc.py   # ParakeetCTCBackend
    ├── local_parakeet_tdt.py   # ParakeetTDTBackend
    └── local_transcribecpp.py  # TranscribecppBackend
```

## Проблема: adapters/ уже существует

`adapters/` создан в Stage 0 (пустой `__init__.py`). `git mv providers/ adapters/` поместит providers ВНУТРЬ adapters. Решение: удалить `adapters/__init__.py`, затем `git mv`.

## Файлы для обновления импортов (14 импортов в 5 файлах)

| Файл | Строки | Что импортирует |
|------|--------|----------------|
| `app/wiring.py` | 29, 30 | `LocalOpenAICompatibleLLMProvider`, `OpenAICompatibleLLMProvider` |
| `ui/controller.py` | 97–101, 828 | 5 STT LoadError классов + `OpenAICompatibleLLMProvider` |
| `core/inference/worker.py` | 64, 85, 106, 127 | 4 STT-провайдера (lazy imports) |
| `providers/llm/openai_compatible.py` | 13 | `build_translation_user_message` (внутренний) |
| `providers/llm/local_openai.py` | 18 | `build_translation_user_message` (внутренний) |

## Порядок

1. Удалить `adapters/__init__.py` (Stage 0 создал пустой)
2. `git rm -r adapters/` — удалить пустую директорию
3. `git mv app/src/puripuly_heart/providers app/src/puripuly_heart/adapters`
4. Заменить `puripuly_heart.providers` → `puripuly_heart.adapters` во всех 5 файлах
5. Проверить `__init__.py` в adapters/ — `__all__` ссылается на подмодули по имени, не пути, не сломается
6. Обновить `adapters/__init__.py` если нужно

## Могу проверить

- `python -m py_compile` на каждом файле
- `grep -r "from puripuly_heart.providers" app/src/puripuly_heart/` — 0 совпадений
- `ls app/src/puripuly_heart/adapters/` — структура сохранена
- Весь проект компилируется

## Риск

Низкий. Переименование + замена 14 импортов. Главная ловушка — adapters/ уже существует.
