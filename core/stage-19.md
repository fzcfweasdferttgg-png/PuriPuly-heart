# Этап 19: STTProviderName → domain/providers.py

`domain/peer_types.py:5` импортирует `STTProviderName` из `config.settings` — нарушение domain → config.

## Порядок

1. Создать `domain/providers.py`:
   ```python
   from __future__ import annotations
   from enum import Enum

   class STTProviderName(str, Enum):
       LOCAL_QWEN = "local_qwen"
       LOCAL_QWEN_17B = "local_qwen_17b"
       LOCAL_GIGAAM_RNNT = "local_gigaam_rnnt"
       LOCAL_PARAKEET_TDT = "local_parakeet_tdt"
       LOCAL_GIGAAM_RNNT_GGUF = "local_gigaam_rnnt_gguf"
       LOCAL_PARAKEET_TDT_GGUF = "local_parakeet_tdt_gguf"
       LOCAL_QWEN3_ASR_GGUF = "local_qwen3_asr_gguf"
       LOCAL_QWEN_17B_GGUF = "local_qwen_17b_gguf"

   class LLMProviderName(str, Enum):
       LOCAL_LLM = "local_llm"
       OPENAI_COMPATIBLE = "openai_compatible"
   ```

2. `config/settings/enums.py`: удалить определения `STTProviderName` и `LLMProviderName`, добавить:
   ```python
   from puripuly_heart.domain.providers import LLMProviderName, STTProviderName
   ```

3. `domain/peer_types.py:5`: изменить импорт:
   - `from puripuly_heart.config.settings import STTProviderName` → `from puripuly_heart.domain.providers import STTProviderName`

4. Остальные 7 файлов импортируют через `config.settings` — т.к. enums.py re-export, импорты НЕ меняются.

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `domain/providers.py` | Создать |
| `config/settings/enums.py` | Удалить 2 enum, добавить re-import |
| `domain/peer_types.py` | Изменить импорт (строка 5) |

## Неочевидное

- `enums.py` содержит 7 enum + ~10 функций-парсеров. Перемещаем только 2 enum, остальное остаётся
- `_parse_stt_provider()` и `_parse_llm_provider()` в enums.py используют эти enum — после перемещения enums.py импортирует их из domain/ (config → domain — разрешено)
- `config/settings/__init__.py` re-export из enums.py — цепочка сохраняется, потребители не меняются
- Нет circular import: `domain/providers.py` зависит только от stdlib

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/domain/providers.py`
- `python -m py_compile app/src/puripuly_heart/config/settings/enums.py`
- `python -m py_compile app/src/puripuly_heart/domain/peer_types.py`
