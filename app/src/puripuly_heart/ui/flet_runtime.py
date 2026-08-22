"""Flet 0.86.1 runtime utilities.

Safe helpers for common Flet operations that can fail in async/overlay contexts.
"""

from __future__ import annotations

from typing import Any


def is_hover_active(event: Any) -> bool:
    """Parse hover event data — works with bool, str, and object forms."""
    data = getattr(event, "data", None)
    if isinstance(data, bool):
        return data
    if isinstance(data, str):
        return data.lower() == "true"
    return bool(data)


def control_page(control: Any) -> Any:
    """Safely get a control's page reference. Returns None if unmounted."""
    try:
        return getattr(control, "page", None)
    except (AssertionError, RuntimeError):
        return None


def is_control_mounted(control: Any) -> bool:
    """Check if a control is currently added to a page."""
    page = control_page(control)
    return page is not None


def update_control_if_mounted(control: Any) -> bool:
    """Safely update a control. Returns True if updated, False if unmounted."""
    page = control_page(control)
    if page is None:
        return False
    try:
        control.update()
        return True
    except Exception:
        return False


def safe_page_update(control: Any) -> None:
    """Safely update a control if it has a page. No-op if unmounted."""
    try:
        if control.page:
            control.update()
    except (AssertionError, RuntimeError):
        pass


def has_page(control: Any) -> bool:
    """Safely check if a control has a page. Returns False if unmounted or error."""
    try:
        return control.page is not None
    except (AssertionError, RuntimeError):
        return False


async def invoke_control_method(control: Any, method_name: str, *args: Any) -> Any:
    """Async method invocation on a control. Skips if unmounted."""
    page = control_page(control)
    if page is None:
        return None
    method = getattr(control, method_name, None)
    if method is None:
        return None
    return await method(*args)


def run_control_method(control: Any, method_name: str, *args: Any) -> None:
    """Schedule a method call via page.run_task. No-op if unmounted."""
    page = control_page(control)
    if page is None:
        return
    method = getattr(control, method_name, None)
    if method is None:
        return
    page.run_task(method, *args)
