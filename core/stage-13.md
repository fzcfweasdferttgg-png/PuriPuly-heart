# Этап 13: Перемещение OSC adapter + OscSink port

Pipeline использует ChatboxPaginator напрямую вместо порта. VrchatOscUdpSender — adapter в core/.

## Порядок

1. Расширить `ports/osc.py` — добавить OscSink Protocol:
   ```python
   class OscSink(Protocol):
       def enqueue(self, message: OSCMessage) -> None: ...
       def send_immediate(self, text: str) -> bool: ...
       def send_typing(self, is_typing: bool) -> None: ...
       def set_typing_reason(self, reason: str, active: bool) -> None: ...
       def clear_typing_reasons(self) -> None: ...
       def process_due(self) -> None: ...
   ```

2. Создать `adapters/osc/__init__.py`

3. Переместить:
   - `core/osc/chatbox_paginator.py` → `adapters/osc/chatbox_paginator.py`
   - `core/osc/udp_sender.py` → `adapters/osc/udp_sender.py`

4. Обновить типы в Pipeline:
   - `core/pipeline/pipeline.py`: `osc: ChatboxPaginator` → `osc: OscSink`
   - Импорт: `from core.osc.chatbox_paginator` → `from ports.osc`

5. Обновить импорты инстанцирования:
   - `ui/controller.py:70` → `from adapters.osc.chatbox_paginator import ChatboxPaginator`
   - `ui/controller.py:77` → `from adapters.osc.udp_sender import VrchatOscUdpSender`
   - `app/headless_stdin.py:11` → `from adapters.osc.chatbox_paginator import ChatboxPaginator`
   - `app/headless_mic.py:36` → `from adapters.osc.chatbox_paginator import ChatboxPaginator`

6. Удалить `core/osc/sender.py` (3 строки, re-export из ports/osc.py)

7. Обновить `__init__.py` файлы:
   - `ports/__init__.py`: добавить `OscSink` в импорт из `ports.osc` и в `__all__`
   - `adapters/__init__.py`: добавить `"osc"` в `__all__`
   - `core/osc/__init__.py`: убрать `"chatbox_paginator"` и `"udp_sender"` из `__all__`, оставить `"receiver"`

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `app/src/puripuly_heart/ports/osc.py` | Расширить (добавить OscSink) |
| `app/src/puripuly_heart/ports/__init__.py` | Добавить OscSink в re-exports |
| `app/src/puripuly_heart/adapters/osc/__init__.py` | Создать |
| `app/src/puripuly_heart/adapters/__init__.py` | Добавить "osc" в __all__ |
| `core/osc/chatbox_paginator.py` | Переместить → `adapters/osc/` |
| `core/osc/udp_sender.py` | Переместить → `adapters/osc/` |
| `core/osc/sender.py` | Удалить (re-export, нигде не используется) |
| `core/osc/__init__.py` | Обновить __all__ |
| `core/pipeline/pipeline.py` | Изменить тип osc + импорт |
| `ui/controller.py` | Обновить 2 импорта |
| `app/headless_stdin.py` | Обновить 1 импорт |
| `app/headless_mic.py` | Обновить 1 импорт |

## Неочевидное

- ChatboxPaginator зависит от OscSender (порт) и Clock (порт). После перемещения в adapters/ — dependency direction корректный: adapters → ports
- `core/osc/` после перемещения останется: `__init__.py`, `sender.py` (re-export), `receiver.py` (84 строки — OSC receiver). Receiver не используется Pipeline напрямую
- Pipeline вызывает 6 методов ChatboxPaginator: `enqueue`, `send_immediate`, `send_typing`, `set_typing_reason`, `clear_typing_reasons`, `process_due` — все есть в OscSink

## Цепочки

- `_run_osc_flush_loop` → `self.osc.process_due()` — метод есть в OscSink
- `_enqueue_osc` → `self.osc.enqueue(msg)` — метод есть в OscSink
- controller.py создаёт ChatboxPaginator напрямую — импорт обновляется, инстанцирование не меняется

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/ports/osc.py`
- `python -m py_compile app/src/puripuly_heart/adapters/osc/chatbox_paginator.py`
- `python -m py_compile app/src/puripuly_heart/adapters/osc/udp_sender.py`
- `python -m py_compile app/src/puripuly_heart/core/pipeline/pipeline.py`
- `python -m py_compile app/src/puripuly_heart/ui/controller.py`
