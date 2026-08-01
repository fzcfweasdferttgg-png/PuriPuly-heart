from __future__ import annotations

import logging
from typing import Callable, Protocol

from puripuly_heart.domain.overlay_types import SessionLoggingMode


class SessionLogger(Protocol):
    @property
    def mode(self) -> SessionLoggingMode: ...

    def emit_basic(self, message: str, *, level: int = logging.INFO) -> None: ...

    def emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool: ...

    def emit_detailed_lazy(
        self,
        build_message: Callable[[], str],
        *,
        level: int = logging.INFO,
    ) -> bool: ...
