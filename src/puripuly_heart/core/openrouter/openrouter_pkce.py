"""Compat shim left after the OpenRouter PKCE OAuth flow was removed.

Only inert placeholders remain so managed-account wiring keeps importing.
"""

from __future__ import annotations


class _Any:
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


class OpenRouterPKCEClient(_Inert):
    pass


class OpenRouterPKCEExchangeResult(_Inert):
    pass


_INERT_CACHE: dict[str, type] = {}


def __getattr__(name: str) -> type:
    cls = _INERT_CACHE.get(name)
    if cls is None:
        cls = type(name, (_Inert,), {})
        _INERT_CACHE[name] = cls
    globals()[name] = cls
    return cls
