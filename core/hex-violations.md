# Нарушения гексагональной архитектуры

Все пути относительно `app/src/puripuly_heart/`.

---

## Нарушение 1: core → config (STTProviderName)

**Файл:** `core/stt/controller.py`

```
БЫЛО (строка 12):
from puripuly_heart.config.settings import STTProviderName

СТАЛО:
from puripuly_heart.domain.providers import STTProviderName
```

---

## Нарушение 2: adapters → core (Clock вместо ports)

**Файл:** `adapters/osc/chatbox_paginator.py`

```
БЫЛО (строка 7):
from puripuly_heart.core.clock import Clock

СТАЛО:
from puripuly_heart.ports.clock import Clock
```

---

## Нарушение 3: adapters → core (SystemClock concrete)

**Файл:** `adapters/overlay/sink.py`

```
БЫЛО (строка 6):
from puripuly_heart.core.clock import Clock, SystemClock

СТАЛО:
from puripuly_heart.ports.clock import Clock

Дополнительно: убрать default_factory, сделать clock обязательным:
  clock: Clock = field(default_factory=SystemClock)  →  clock: Clock

В wiring/controller при создании OverlayEventAdapter передавать:
  OverlayEventAdapter(clock=SystemClock())
```

---

## Нарушение 4: core → config (VAD default)

**Файл:** `core/vad/gating.py`

```
БЫЛО (строка 13):
from puripuly_heart.config.vad_defaults import DEFAULT_STABLE_VAD_HANGOVER_MS

СТАЛО:
удалить строку

В строке 73 заменить default:
  hangover_ms: int = DEFAULT_STABLE_VAD_HANGOVER_MS  →  hangover_ms: int = 1100
```

---

## Нарушение 5: core → config (VAD default в pipeline)

**Файл:** `core/pipeline/pipeline.py`

```
БЫЛО (строка 14):
from puripuly_heart.config.vad_defaults import DEFAULT_STABLE_VAD_HANGOVER_MS

СТАЛО:
удалить строку

В строке 90 заменить:
  hangover_s: float = DEFAULT_STABLE_VAD_HANGOVER_MS / 1000.0  →  hangover_s: float = 1.1
```

---

## Нарушение 6: core → config (warm_prompt_cache в pipeline)

**Файл:** `core/pipeline/pipeline.py`

```
БЫЛО (строка 13 + строка 164):
from puripuly_heart.config.prompts import warm_prompt_cache
...
warm_prompt_cache()  # в __post_init__

СТАЛО:
удалить импорт (строка 13)
удалить вызов warm_prompt_cache() из __post_init__ (строка 164)

В controller.py / headless_mic.py вызывать warm_prompt_cache() ПЕРЕД созданием Pipeline
```

---

## Нарушение 7: core → config (paths в runtime_logging)

**Файл:** `core/runtime_logging.py`

```
БЫЛО (строка 12 + строка 89):
from puripuly_heart.config.paths import user_config_dir
...
def default_main_log_file(*, log_dir: Path | None = None) -> Path:
    resolved_log_dir = log_dir or user_config_dir()

СТАЛО:
удалить импорт (строка 12)
def default_main_log_file(*, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / MAIN_LOG_FILENAME

В wiring/controller передавать log_dir=user_config_dir() при вызове
```

---

## Нарушение 8: core → config (paths в local_stt_assets)

**Файл:** `core/local_stt_assets.py`

```
БЫЛО (строка 10 + строка 272):
from puripuly_heart.config import paths
...
def default_local_stt_model_root() -> Path:
    return paths.default_models_dir()

СТАЛО:
удалить импорт (строка 10)
def default_local_stt_model_root(data_dir: Path) -> Path:
    return data_dir / "models"

Все вызовы default_local_stt_model_root() → default_local_stt_model_root(data_dir)
В wiring передавать data_dir=paths.default_models_dir()
```

---

## Нарушение 9: core → config (AppSettings в custom_vocab)

**Файл:** `core/stt/custom_vocab.py`

```
БЫЛО (строка 3):
from puripuly_heart.config.settings import MAX_CUSTOM_VOCAB_TERMS, AppSettings

СТАЛО:
создать domain/custom_vocab.py с функциями, принимающими narrow-тип:

  MAX_CUSTOM_VOCAB_TERMS = 100

  def get_effective_custom_terms(
      custom_terms: dict[str, list[str]],
      custom_vocabulary_enabled: bool,
      source_language: str,
  ) -> list[str]:
      ...

Вместо settings.stt.custom_terms передавать конкретные поля из AppSettings
```

---

## Порядок выполнения

| Шаг | Нарушение | Файлов | Сложность |
|-----|-----------|--------|-----------|
| 1   | #1 STTProviderName → domain | 1 | тривиально |
| 2   | #2 Clock adapters → ports | 1 | тривиально |
| 3   | #3 SystemClock → инъекция | 2-3 | простой |
| 4   | #4 VAD constant → литерал | 1 | простой |
| 5   | #5 VAD constant → литерал | 1 | простой |
| 6   | #6 warm_prompt_cache → wiring | 2-3 | простой |
| 7   | #7 log_dir → обязательный параметр | 2-3 | средний |
| 8   | #8 data_dir → обязательный параметр | 2-3 | средний |
| 9   | #9 custom_vocab → domain | 3-4 | сложный |