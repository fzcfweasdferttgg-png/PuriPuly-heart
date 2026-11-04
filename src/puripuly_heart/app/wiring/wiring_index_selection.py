"""Compat shim left after index selection was removed.

Index selection auto-switched between the public index-translate provider and
managed OpenRouter keys; both are gone, so the observer always reports
"unknown" and the fallback helper is inert.
"""

from __future__ import annotations

from puripuly_heart.app.services.index_selection import IndexManagedAllowanceObservation


def gemma_fallback_settings(settings: object, connection: str) -> object:
    _ = connection
    return settings


async def check_index_availability() -> None:
    return None


def unknown_managed_allowance(reason: str) -> IndexManagedAllowanceObservation:
    _ = reason
    return IndexManagedAllowanceObservation("unknown")


class IndexManagedAllowanceObserver:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def observe(self) -> IndexManagedAllowanceObservation:
        return IndexManagedAllowanceObservation("unknown")
