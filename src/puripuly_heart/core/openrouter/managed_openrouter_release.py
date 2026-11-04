"""Compat shim left after the managed OpenRouter release flow was removed.

The managed key-delivery flow (broker, QQ/Discord claims) is gone; only the
vocabulary that surviving modules import stays. Any other name resolves to
an inert placeholder so old wiring keeps importing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from puripuly_heart.core.messages import ErrorDiagnostics


class _Any:
    """Benign value: callable, awaitable, iterable, always falsy."""

    def __call__(self, *args: object, **kwargs: object) -> "_Any":
        return self

    def __await__(self):
        async def _done() -> "_Any":
            return self

        return _done().__await__()

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def __getattr__(self, name: str) -> "_Any":
        return _Any()


class _Inert:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __getattr__(self, name: str) -> _Any:
        return _Any()


@dataclass(slots=True)
class ManagedOpenRouterUserFacingError(RuntimeError):
    message_key: str
    message_kwargs: Mapping[str, object] = field(default_factory=dict)
    diagnostics: object | None = None
    failure_diagnostics: ErrorDiagnostics | None = None

    def __post_init__(self) -> None:
        if self.failure_diagnostics is None and self.diagnostics is not None:
            self.failure_diagnostics = getattr(self.diagnostics, "failure_diagnostics", None)

    def __str__(self) -> str:
        diagnostics = self.failure_diagnostics
        if diagnostics is None:
            return "ManagedOpenRouterUserFacingError cause=unknown"
        return (
            f"ManagedOpenRouterUserFacingError code={diagnostics.code or 'unknown'} "
            f"category={diagnostics.category} status={diagnostics.status_code}"
        )


class ManagedOpenRouterReleaseService(_Inert):
    pass


class UnavailableManagedOpenRouterReleaseClient(_Inert):
    pass


_INERT_CACHE: dict[str, type] = {}


def __getattr__(name: str) -> type:
    cls = _INERT_CACHE.get(name)
    if cls is None:
        cls = type(name, (_Inert,), {})
        _INERT_CACHE[name] = cls
    globals()[name] = cls
    return cls
