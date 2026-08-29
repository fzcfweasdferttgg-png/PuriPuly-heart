"""Bridge between ``TkApp`` and ``GuiController``.

Provides synchronous wrappers that spin up an ``asyncio`` event loop in the
caller's thread (typically a daemon thread) to drive the async ``GuiController``
lifecycle.  Keeps all async plumbing out of the GUI module.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def start_controller_async(controller: Any) -> None:
    """Run ``controller.start()`` in a fresh asyncio event loop.

    Intended to be called from a background thread so that the CTk main loop
    remains responsive.

    After the loop is created the reference is stored on *controller* as
    ``_async_loop`` so that ``TkinterGuiController._get_page_run_task()``
    can schedule coroutines via ``asyncio.run_coroutine_threadsafe``.

    The loop is kept alive with ``run_forever()`` so background tasks
    (UIEventBridge, etc.) continue running after ``start()`` returns.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    controller._async_loop = loop

    try:
        loop.run_until_complete(controller.start())
        loop.run_forever()
    except Exception:
        logger.exception("GuiController.start() failed in background thread")
    finally:
        loop.close()


def stop_controller_async(controller: Any) -> None:
    """Run ``controller.stop()`` on the stored event loop.

    Must reuse the same loop where ``_bridge_task`` was created,
    otherwise cancelling it raises ``RuntimeError: Event loop is closed``.
    """
    loop = getattr(controller, "_async_loop", None)
    if loop is not None and not loop.is_closed():
        future = asyncio.run_coroutine_threadsafe(controller.stop(), loop)
        try:
            future.result(timeout=10)
        except Exception:
            logger.exception("GuiController.stop() failed")
        finally:
            loop.call_soon_threadsafe(loop.stop)
    else:
        try:
            asyncio.run(controller.stop())
        except Exception:
            logger.exception("GuiController.stop() failed (no stored loop)")


def apply_settings(controller: Any, settings: Any) -> None:
    """Forward *settings* to ``controller.apply_settings_with_sync()``."""
    try:
        controller.apply_settings_with_sync(settings)
    except Exception:
        logger.exception("apply_settings_with_sync() failed")
