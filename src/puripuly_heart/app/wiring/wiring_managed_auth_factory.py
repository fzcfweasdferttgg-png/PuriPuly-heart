"""Compat shim left after the managed auth factory was removed.

QQ/Discord managed auth, managed identity and OpenRouter managed-key wiring
are gone. The names that root/wiring re-export stay as inert placeholders so
composition keeps building; managed auth interactions do nothing.
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


_INERT_CLASS_NAMES = (
    "DiscordManagedBrokerClientAdapter",
    "DiscordOAuthAuthAdapter",
    "DiscordOAuthCallbackRunner",
    "DiscordOAuthListenerFactory",
    "HardwareFingerprintProvider",
    "ManagedAuthIngressProvider",
    "ManagedAuthReleaseServiceProvider",
    "ManagedAuthRuntimeAdapter",
    "ManagedAuthRuntimePresenceProvider",
    "ManagedAuthSecretStoreFactory",
    "ManagedAuthSettingsRepositoryFactory",
    "ManagedIdentityPreflightAdapter",
    "ManagedIdentityStateAdapter",
    "ManagedTranslationFounderDialog",
    "ManagedTranslationIngressProvider",
    "ManagedTranslationPersistSettings",
    "ManagedTranslationReleaseServiceProvider",
    "ManagedTranslationRuntimeAdapter",
    "ManagedTranslationRuntimeSnapshotProvider",
)

for _name in _INERT_CLASS_NAMES:
    globals()[_name] = type(_name, (_Inert,), {})
del _name

DISCORD_AUTH_ERROR_KEY_BY_SUBCODE: dict[str, str] = {}


def managed_openrouter_selected_from_vnext(*args: object, **kwargs: object) -> bool:
    return False


def build_openrouter_credential_runtime_config(*args: object, **kwargs: object) -> None:
    return None


def build_openrouter_credential_runtime_config_from_vnext(
    *args: object, **kwargs: object
) -> None:
    return None


def build_openrouter_release_runtime_config(*args: object, **kwargs: object) -> None:
    return None


def build_openrouter_release_runtime_config_from_vnext(
    *args: object, **kwargs: object
) -> None:
    return None


def build_managed_identity_state_port(*args: object, **kwargs: object) -> _Inert:
    return _Inert()


def managed_identity_state_port_from_vnext(*args: object, **kwargs: object) -> _Inert:
    return _Inert()


_INERT_CACHE: dict[str, type] = {}


def __getattr__(name: str) -> type:
    cls = _INERT_CACHE.get(name)
    if cls is None:
        cls = type(name, (_Inert,), {})
        _INERT_CACHE[name] = cls
    globals()[name] = cls
    return cls
