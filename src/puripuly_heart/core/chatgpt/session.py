"""ChatGPT session vocabulary kept after the ChatGPT OAuth login was removed.

The login, token refresh and revocation flows are gone; the error types and
the access-token port stay so wiring type hints keep resolving. ChatGptSession
is inert: it never holds tokens.
"""

from __future__ import annotations

from typing import Protocol


class ChatGptAuthError(RuntimeError):
    pass


class ChatGptReauthRequired(RuntimeError):
    pass


class ChatGptAccessTokenPort(Protocol):
    async def access_token(self) -> str: ...


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


class ChatGptSession:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __getattr__(self, name: str) -> _Any:
        return _Any()


__all__ = [
    "ChatGptAccessTokenPort",
    "ChatGptAuthError",
    "ChatGptReauthRequired",
    "ChatGptSession",
]
