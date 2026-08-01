# Этап 24: OSC создание → wiring.py

`ui/controller.py` создаёт `VrchatOscUdpSender` + `ChatboxPaginator` напрямую — ui → adapters.

## Анализ

Controller (строки 1108-1120):
```python
sender = VrchatOscUdpSender(host=..., port=..., chatbox_address=..., chatbox_send=..., chatbox_clear=...)
osc = ChatboxPaginator(sender=sender, clock=self.clock, max_chars=..., runtime_logging=self.runtime_logging)
```

Headless_mic (строки 128-139):
```python
sender = VrchatOscUdpSender(host=..., port=..., chatbox_address=..., chatbox_send=..., chatbox_clear=...)
osc = ChatboxPaginator(sender=sender, clock=self.clock, max_chars=...)
```

**Различие:** controller передаёт `runtime_logging=`, headless_mic — нет.

## Порядок

1. Добавить в `app/wiring.py` функцию:
   ```python
   from puripuly_heart.adapters.osc.chatbox_paginator import ChatboxPaginator
   from puripuly_heart.adapters.osc.udp_sender import VrchatOscUdpSender
   from puripuly_heart.ports.osc import OscSink
   from puripuly_heart.ports.logging import SessionLogger

   def create_osc_sink(
       settings: AppSettings,
       *,
       clock: Clock,
       runtime_logging: SessionLogger | None = None,
   ) -> OscSink:
       sender = VrchatOscUdpSender(
           host=settings.osc.host,
           port=settings.osc.port,
           chatbox_address=settings.osc.chatbox_address,
           chatbox_send=settings.osc.chatbox_send,
           chatbox_clear=settings.osc.chatbox_clear,
       )
       return ChatboxPaginator(
           sender=sender,
           clock=clock,
           max_chars=settings.osc.chatbox_max_chars,
           runtime_logging=runtime_logging,
       )
   ```

2. `ui/controller.py`: заменить ручное создание на:
   ```python
   from puripuly_heart.app.wiring import create_osc_sink
   osc = create_osc_sink(self.settings, clock=self.clock, runtime_logging=self.runtime_logging)
   ```
   Удалить импорты `VrchatOscUdpSender` и `ChatboxPaginator` (если больше не используются).

3. `app/headless_mic.py`: аналогично:
   ```python
   osc = create_osc_sink(self.settings, clock=self.clock)
   ```

## Затронутые файлы

| Файл | Действие |
|------|----------|
| `app/wiring.py` | Добавить `create_osc_sink()` |
| `ui/controller.py` | Заменить создание OSC, удалить 2 импорта |
| `app/headless_mic.py` | Заменить создание OSC, удалить 2 импорта |

## Неочевидное

- `controller.py` может использовать `VrchatOscUdpSender` ещё где-то — проверить перед удалением импорта
- `ChatboxPaginator` возвращает тип `ChatboxPaginator`, но функция аннотирована как `OscSink` (Protocol) — корректно
- `runtime_logging` параметр optional — headless_mic не передаёт, controller передаёт

## Могу проверить

- `python -m py_compile app/src/puripuly_heart/app/wiring.py`
- `python -m py_compile app/src/puripuly_heart/ui/controller.py`
- `python -m py_compile app/src/puripuly_heart/app/headless_mic.py`
- Grep: `from puripuly_heart.adapters.osc` в controller.py — 0 совпадений
